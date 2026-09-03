"""人体识别任务子进程入口。

python -m app.core.pose.task_runner --spec <spec.json>

spec 结构：
{
  "params": {...PoseParams 字段...},
  "videos": ["绝对路径", ...],
  "media_root": "媒体根目录（决定输出目录结构）",
  "task_dir": "state.json/task.log 所在目录",
  "wait_file": bool  # true 时等待文件写入完成（录制后自动触发用；就绪判定见 file_watch.py）
}

状态写 <task_dir>/state.json（原子替换），日志走 stderr（父进程重定向到
task.log）。SIGTERM 触发协作停止：当前推理批次结束后中止。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading
from datetime import datetime

# SIGTERM 处理器必须在模块导入时就装好（早于重依赖 import），
# 否则任务启动初期收到停止信号会来不及写终态。
_STOP_EVENT = threading.Event()


def _handle_sigterm(signum, frame):
    _STOP_EVENT.set()


signal.signal(signal.SIGTERM, _handle_sigterm)


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description="StreamCap 人体识别处理任务")
    parser.add_argument("--spec", required=True, help="任务 spec JSON 文件路径")
    return parser.parse_args(argv)


def _setup_logger() -> logging.Logger:
    lg = logging.getLogger("video_pose")
    lg.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    lg.addHandler(handler)
    lg.propagate = False
    return lg


def _atomic_write_json(path: str, payload: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp, path)


def main(argv=None) -> int:
    args = _parse_args(argv)
    log = _setup_logger()

    with open(args.spec, encoding="utf-8") as f:
        spec = json.load(f)

    task_dir = os.path.abspath(spec["task_dir"])
    state_path = os.path.join(task_dir, "state.json")
    stop_event = _STOP_EVENT

    def write_state(**payload):
        base = {
            "pid": os.getpid(),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        base.update(payload)
        _atomic_write_json(state_path, base)

    started_at = None
    if os.path.isfile(state_path):
        try:
            with open(state_path, encoding="utf-8") as f:
                started_at = json.load(f).get("started_at")
        except (OSError, ValueError):
            pass
    if not started_at:
        started_at = datetime.now().isoformat(timespec="seconds")

    videos = [os.path.abspath(v) for v in spec.get("videos", [])]
    media_root = spec.get("media_root")
    wait_file = bool(spec.get("wait_file"))
    params_dict = spec.get("params") or {}

    from .pose_params import PoseParams

    params = PoseParams.from_user_config(params_dict)
    write_state(status="running", state="starting", started_at=started_at, message="任务启动中…")

    summary = {
        "videos": len(videos),
        "frames": 0,
        "saved": 0,
        "segments": 0,
        "merged_segments": 0,
        "clips": 0,
    }

    try:
        if not videos:
            write_state(
                status="completed",
                started_at=started_at,
                finished_at=datetime.now().isoformat(timespec="seconds"),
                message="没有待处理的视频",
                summary=summary,
            )
            return 0

        if wait_file:
            write_state(status="running", state="waiting", started_at=started_at, message="等待录制文件写入完成…")

            def report_pending(names: list[str]) -> None:
                write_state(
                    status="running",
                    state="waiting",
                    started_at=started_at,
                    pending_files=names,
                    message=f"等待 {len(names)} 个文件写入完成…",
                )

            from .file_watch import wait_until_ready

            ready, abandoned = wait_until_ready(videos, log, stop_check=stop_event.is_set, on_pending=report_pending)
            videos = ready
            if abandoned:
                log.warning(f"放弃未就绪文件: {[os.path.basename(p) for p in abandoned]}")
            if not videos:
                write_state(
                    status="completed",
                    started_at=started_at,
                    finished_at=datetime.now().isoformat(timespec="seconds"),
                    message="文件写入未完成或已放弃，无视频可处理",
                    summary=summary,
                )
                return 0

        write_state(
            status="running", state="loading_model", started_at=started_at, message="加载模型中…"
        )
        from .detector import Detector
        from .video_processor import VideoProcessor

        detector = Detector(params)
        detector.load_models()
        processor = VideoProcessor(detector, params, media_root=media_root)

        total = len(videos)
        log.info(f"任务开始：处理 {total} 个视频")
        write_state(
            status="running",
            state="processing",
            started_at=started_at,
            video_idx=0,
            total_videos=total,
            video_percent=0,
            total_percent=0,
            message="开始处理",
        )

        for idx, video_path in enumerate(videos):
            if stop_event.is_set():
                break

            write_state(
                status="running",
                state="processing",
                started_at=started_at,
                video_name=os.path.basename(video_path),
                video_idx=idx,
                total_videos=total,
                video_percent=0,
                total_percent=round(idx / total * 100, 1),
                message=f"处理 {os.path.basename(video_path)}",
            )

            try:
                frames, saved, segments, merged, clips = processor.process_video_file(
                    video_path,
                    idx,
                    total,
                    progress_cb=lambda info: write_state(
                        status="running",
                        state="processing",
                        started_at=started_at,
                        video_name=info["video_name"],
                        video_idx=info["video_idx"],
                        total_videos=info["total_videos"],
                        video_percent=info["video_percent"],
                        total_percent=info["total_percent"],
                        message=f"处理 {info['video_name']}",
                    ),
                    stop_check=stop_event.is_set,
                )
                summary["frames"] += frames
                summary["saved"] += saved
                summary["segments"] += segments
                summary["merged_segments"] += merged
                summary["clips"] += clips

                write_state(
                    status="running",
                    state="processing",
                    started_at=started_at,
                    video_name=os.path.basename(video_path),
                    video_idx=idx,
                    total_videos=total,
                    video_percent=100,
                    total_percent=round((idx + 1) / total * 100, 1),
                    message=f"{os.path.basename(video_path)} 完成",
                )
            except Exception:
                log.exception(f"处理文件 {video_path} 时发生错误")

        stopped = stop_event.is_set()
        finished_at = datetime.now().isoformat(timespec="seconds")
        if stopped:
            log.info("任务已停止（部分完成）")
            write_state(
                status="cancelled",
                started_at=started_at,
                finished_at=finished_at,
                message="任务已停止",
                summary=summary,
            )
            return 0

        log.info(f"任务完成: {json.dumps(summary, ensure_ascii=False)}")
        write_state(
            status="completed",
            started_at=started_at,
            finished_at=finished_at,
            message="任务完成",
            summary=summary,
        )
        return 0
    except Exception as e:
        log.exception("任务失败")
        try:
            write_state(
                status="failed",
                started_at=started_at,
                finished_at=datetime.now().isoformat(timespec="seconds"),
                message=str(e),
                summary=summary,
            )
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
