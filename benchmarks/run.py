"""StreamCap 后端基准测试。

对核心热路径做可重复的基准测量，输出可对比的量化指标（不设断言阈值，
用于性能回归对比）。运行：

    .venv/bin/python benchmarks/run.py            # 全部
    .venv/bin/python benchmarks/run.py api media  # 指定基准

基准项：
- recordings: 646 任务（Unraid 生产规模）的序列化 + 状态快照 + 列表过滤
- media: 媒体目录树扫描/统计（含递归 walk）
- settings: 配置读-合并-写循环
- pose_params: PoseParams 构造与判定
- api: FastAPI TestClient 下主要端点的 RPS（含应用层开销）
"""

from __future__ import annotations

import json
import os
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

RESULTS: dict[str, dict] = {}


def bench(name: str, iterations: int, func, *args, **kwargs):
    """计时并记录：返回每次迭代耗时（秒），取中位数。"""
    durations = []
    result = None
    for _ in range(iterations):
        start = time.perf_counter()
        result = func(*args, **kwargs)
        durations.append(time.perf_counter() - start)
    med = statistics.median(durations)
    RESULTS[name] = {
        "iterations": iterations,
        "median_ms": round(med * 1000, 3),
        "min_ms": round(min(durations) * 1000, 3),
        "max_ms": round(max(durations) * 1000, 3),
        "per_second": round(1 / med, 1) if med > 0 else None,
    }
    print(f"  {name:<28} {RESULTS[name]['median_ms']:>10.3f} ms/op  ({RESULTS[name]['per_second']}/s)")
    return result


# ── 测试数据构造 ─────────────────────────────────────────

def make_recordings(n: int) -> list:
    """构造 n 个 Recording（模拟 Unraid 生产规模的任务列表）。"""
    from app.models.recording.recording_model import Recording

    recordings = []
    for i in range(n):
        rec = Recording(
            rec_id=f"rec-{i:04d}",
            url=f"https://live.douyin.com/room{i}",
            streamer_name=f"主播{i}",
            record_format="MP4",
            quality="OD",
            segment_record=True,
            segment_time=1800,
            monitor_status=i % 5 != 0,  # 80% 启用，接近生产比例
            scheduled_recording=False,
            scheduled_start_time=None,
            monitor_hours=None,
            recording_dir=None,
            enabled_message_push=False,
            only_notify_no_record=False,
            flv_use_direct_download=False,
        )
        rec.platform = "抖音直播"
        rec.platform_key = "douyin"
        rec.status_info = "MONITORING"
        recordings.append(rec)
    return recordings


def make_media_tree(root: str, dirs: int = 10, files_per_dir: int = 30) -> None:
    """构造模拟媒体目录：dirs 个主播目录，各含 files_per_dir 个空视频文件。"""
    for d in range(dirs):
        dir_path = os.path.join(root, f"平台/主播{d:03d}")
        os.makedirs(dir_path, exist_ok=True)
        for f in range(files_per_dir):
            open(os.path.join(dir_path, f"video_{f:03d}.mp4"), "wb").close()


# ── 基准项 ────────────────────────────────────────────────

def bench_recordings():
    print("[recordings] 646 任务序列化/快照（生产规模）")
    from app.server.routers.recordings import serialize_recording

    recordings = make_recordings(646)

    bench("serialize_all(646)", 20, lambda: [serialize_recording(r) for r in recordings])
    bench("serialize_single", 2000, lambda: serialize_recording(recordings[0]))
    bench(
        "filter_by_name",
        200,
        lambda: [r for r in recordings if "主播1" in (r.streamer_name or "") or "room1" in r.url],
    )


def bench_media():
    print("[media] 媒体目录扫描")
    from app.server import media_service

    tmp = tempfile.mkdtemp(prefix="sc-bench-media-")
    try:
        make_media_tree(tmp, dirs=10, files_per_dir=30)  # 300 文件
        bench("list_dir(300 files)", 50, media_service.list_dir, "", tmp)
        bench("stats_walk(300 files)", 20, media_service.stats, "", tmp)
        bench("resolve_safe", 20000, media_service.resolve_safe, tmp, "平台/主播001/video_000.mp4")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def bench_settings():
    print("[settings] 配置读写")
    tmp = tempfile.mkdtemp(prefix="sc-bench-cfg-")
    try:
        from app.core.config.config_manager import ConfigManager

        run_path = tmp
        # 放入模板与既有配置，使 Manager 走真实读写路径
        cfg_dir = os.path.join(tmp, "config")
        os.makedirs(cfg_dir, exist_ok=True)
        shutil.copy(
            Path(__file__).resolve().parents[1] / "config" / "default_settings.json",
            cfg_dir,
        )
        cm = ConfigManager(run_path)
        user_cfg = cm.load_user_config()

        bench("load_user_config", 500, cm.load_user_config)
        bench(
            "get_config_value_nested",
            5000,
            cm.get_config_value,
            "pose_detection",
        )
        # 模拟设置页防抖保存（含原子写盘）
        async def _save():
            await cm.save_user_config(user_cfg)

        import asyncio

        loop = asyncio.new_event_loop()
        bench(
            "save_user_config(atomic)",
            50,
            lambda: loop.run_until_complete(_save()),
        )
        loop.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def bench_pose():
    print("[pose] 参数构造与启用判定")
    from app.core.pose.pose_params import DEFAULTS, PoseParams, is_pose_enabled

    bench("params_from_config", 50000, PoseParams.from_user_config, {})
    bench(
        "is_pose_enabled(None)",
        100000,
        is_pose_enabled,
        {"pose_detection": {"enabled": True}},
        None,
    )


def bench_api():
    print("[api] FastAPI 端点吞吐（TestClient，含应用层）")
    from fastapi.testclient import TestClient

    from app.server.app import create_app

    app = create_app()
    with TestClient(app) as client:
        def hit(path: str):
            response = client.get(path)
            assert response.status_code == 200, f"{path} -> {response.status_code}"
            return response

        bench("GET /api/system/info", 100, hit, "/api/system/info")
        bench("GET /api/auth/session", 100, hit, "/api/auth/session")

        # 录制列表（空列表基线；646 任务见 recordings 基准的序列化成本）
        bench("GET /api/recordings", 100, hit, "/api/recordings")

        # 媒体树（挂真实 downloads 目录）
        bench("GET /api/media/tree", 50, hit, "/api/media/tree")
        bench("GET /api/media/stats", 50, hit, "/api/media/stats")


BENCHES = {
    "recordings": bench_recordings,
    "media": bench_media,
    "settings": bench_settings,
    "pose": bench_pose,
    "api": bench_api,
}


def main() -> None:
    selected = sys.argv[1:] or list(BENCHES)
    print(f"StreamCap benchmarks: {', '.join(selected)}\n")

    for name in selected:
        if name not in BENCHES:
            print(f"unknown benchmark: {name} (available: {', '.join(BENCHES)})")
            sys.exit(1)
        BENCHES[name]()
        print()

    print("── summary ─────────────────────────────")
    print(json.dumps(RESULTS, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
