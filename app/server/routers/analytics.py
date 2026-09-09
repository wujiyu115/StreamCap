"""录制分析报告：只读聚合端点，数据来自 analytics 汇总存储与 recordings 状态。"""
from __future__ import annotations

import asyncio
import os
import shutil
import time
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query

from ...core.analytics.analytics_store import (
    FAILURE_REASON_SLOTS,
    P_CHECKS,
    P_FAILURES,
    T_ABORTS,
    T_CHECK_FAILS,
    T_CHECKS,
    T_FILES,
    T_NOTIFY_ONLY,
    T_POSE_DEL,
    T_POSE_OUT,
    T_RAW_BYTES,
    T_SECONDS,
    T_SESSIONS,
    T_STARTS,
)
from .. import media_service
from ..deps import get_current_user, get_services

router = APIRouter(prefix="/api/analytics", tags=["analytics"])

# 归因分档的展示顺序：从「可能只是抖动」到「基本可以下线了」
FAILURE_REASON_ORDER = ("transient", "repeated", "unsupported", "invalid")

_DIR_STATS_TTL_SECONDS = 600
_dir_stats_cache: dict[str, tuple[float, dict]] = {}
_dir_stats_lock = asyncio.Lock()


def _fmt_seconds(seconds: float) -> float:
    return round(seconds, 1)


def _rate(part: float, whole: float) -> float | None:
    return round(part / whole, 4) if whole else None


def _analytics_storage(services) -> dict:
    """汇总存储的磁盘占用（让用户能盯住持久化数据的增长）。"""
    analytics_dir = services.config_manager.analytics_dir
    files = []
    try:
        for name in sorted(os.listdir(analytics_dir)):
            path = os.path.join(analytics_dir, name)
            if os.path.isfile(path):
                files.append({"name": name, "bytes": os.path.getsize(path)})
    except OSError:
        pass
    return {"total_bytes": sum(f["bytes"] for f in files), "files": files}


def _disk_usage(path: str) -> dict:
    """录制盘的真实容量。

    shutil.disk_usage 就是 statvfs 的封装，一次系统调用拿到全盘 总量/已用/可用，
    与目录内容多少无关，所以每次请求都能算，不需要缓存。
    """
    probe = path
    for _ in range(4):  # 目录可能还没建出来，往上找到最近的存在的祖先
        if os.path.isdir(probe):
            break
        parent = os.path.dirname(probe)
        if not parent or parent == probe:
            break
        probe = parent
    try:
        usage = shutil.disk_usage(probe)
    except OSError:
        return {"total_bytes": None, "used_bytes": None, "free_bytes": None}
    return {"total_bytes": usage.total, "used_bytes": usage.used, "free_bytes": usage.free}


async def _recordings_dir_stats(root: str) -> dict:
    """录制目录的递归统计（文件数与占用），带 TTL 缓存 + 线程池执行。

    走一遍上万个文件的目录树是几十到几百毫秒的阻塞 I/O：放在事件循环里会连带
    卡住录制调度和其它请求，而面板每刷新一次都重算一遍也没有意义。所以扔到线程
    里跑，结果缓存 10 分钟，并把采样时刻一起返回，让前端能标出「数据截至」。
    """
    now = time.time()
    cached = _dir_stats_cache.get(root)
    if cached and now - cached[0] < _DIR_STATS_TTL_SECONDS:
        return {**cached[1], "sampled_at": cached[0], "cached": True}

    async with _dir_stats_lock:
        # 等锁期间别的请求可能已经算完了，别重复遍历
        cached = _dir_stats_cache.get(root)
        if cached and time.time() - cached[0] < _DIR_STATS_TTL_SECONDS:
            return {**cached[1], "sampled_at": cached[0], "cached": True}
        try:
            data = await asyncio.to_thread(media_service.stats, "", root)
        except (OSError, media_service.MediaPathError):
            data = {"total_files": 0, "video_files": 0, "total_bytes": 0, "total_size": "0 B"}
        stamp = time.time()
        _dir_stats_cache[root] = (stamp, data)
    return {**data, "sampled_at": stamp, "cached": False}


@router.get("/overview")
async def get_overview(
    days: int = Query(default=30, ge=1, le=365),
    user: str = Depends(get_current_user),
    services=Depends(get_services),
):
    rm = services.recording_manager
    cfg = rm._monitor_config()
    now = time.time()
    today = date.today()
    start_day = today - timedelta(days=days - 1)
    start_str, end_str = start_day.isoformat(), today.isoformat()
    prev_start = (start_day - timedelta(days=days)).isoformat()

    daily = rm.analytics.read_daily_range(prev_start, end_str)

    # ── 窗口内汇总与趋势（当日窗口补零） ──
    tasks_agg: dict[str, dict] = {}
    platform_agg: dict[str, dict] = {}
    trend: list[dict] = []
    sessions_cur = sessions_prev = 0
    for offset in range(days + days):  # 当期窗口 + 前一对比窗口
        d = (start_day - timedelta(days=days) + timedelta(days=offset)).isoformat()
        buckets = daily.get(d) or {}
        t_buckets = buckets.get("t") or {}
        p_buckets = buckets.get("p") or {}
        day_sessions = sum(v[T_SESSIONS] for v in t_buckets.values())
        day_seconds = sum(v[T_SECONDS] for v in t_buckets.values())
        day_files = sum(v[T_FILES] for v in t_buckets.values())
        day_bytes = sum(v[T_RAW_BYTES] for v in t_buckets.values())
        if d >= start_str:
            trend.append(
                {
                    "date": d,
                    "sessions": day_sessions,
                    "seconds": _fmt_seconds(day_seconds),
                    "files": day_files,
                    "bytes": day_bytes,
                }
            )
            sessions_cur += day_sessions
            for rid, v in t_buckets.items():
                agg = tasks_agg.setdefault(
                    rid,
                    {
                        "sessions": 0,
                        "seconds": 0.0,
                        "files": 0,
                        "bytes": 0,
                        "starts": 0,
                        "aborts": 0,
                        "notify_only": 0,
                        "checks": 0,
                        "check_failures": 0,
                        "pose_out_bytes": 0,
                        "pose_deleted_bytes": 0,
                    },
                )
                agg["sessions"] += v[T_SESSIONS]
                agg["seconds"] += v[T_SECONDS]
                agg["files"] += v[T_FILES]
                agg["bytes"] += v[T_RAW_BYTES]
                agg["starts"] += v[T_STARTS]
                agg["aborts"] += v[T_ABORTS]
                agg["notify_only"] += v[T_NOTIFY_ONLY]
                agg["checks"] += v[T_CHECKS]
                agg["check_failures"] += v[T_CHECK_FAILS]
                agg["pose_out_bytes"] += v[T_POSE_OUT]
                agg["pose_deleted_bytes"] += v[T_POSE_DEL]
            for pk, v in p_buckets.items():
                agg = platform_agg.setdefault(
                    pk,
                    {"checks": 0, "failures": 0, **{r: 0 for r in FAILURE_REASON_ORDER}},
                )
                agg["checks"] += v[P_CHECKS]
                agg["failures"] += v[P_FAILURES]
                for reason in FAILURE_REASON_ORDER:
                    agg[reason] += v[FAILURE_REASON_SLOTS[reason]]
        elif d >= prev_start:
            sessions_prev += day_sessions

    change_pct = None
    if sessions_prev > 0:
        change_pct = round((sessions_cur - sessions_prev) / sessions_prev * 100, 1)

    name_of = {r.rec_id: (r.streamer_name or r.rec_id[:8]) for r in rm.recordings}
    # 已删除任务的孤儿聚合数据不进任何按主播视图（任务删除时已清，防历史残留兜底）
    current_ids = set(name_of)

    def name_of_any(rid: str) -> str:
        return name_of.get(rid) or f"{rid[:8]}…"

    live_tasks = [(rid, agg) for rid, agg in tasks_agg.items() if rid in current_ids]
    # 只被检测过（没开播过）的任务也会有 t 桶，算「活跃」得看是否真有开播/录制
    recorded_tasks = [(rid, agg) for rid, agg in live_tasks if agg["sessions"] or agg["seconds"] > 0]

    top_sessions = sorted(
        (
            {
                "rec_id": rid,
                "name": name_of_any(rid),
                "sessions": agg["sessions"],
                "seconds": _fmt_seconds(agg["seconds"]),
                "files": agg["files"],
                "bytes": agg["bytes"],
            }
            for rid, agg in recorded_tasks
        ),
        key=lambda x: x["seconds"],
        reverse=True,
    )[:10]

    # 最吃盘的任务：时长长≠占得多（码率、分辨率、是否转码差异很大）
    top_bytes = sorted(
        (
            {
                "rec_id": rid,
                "name": name_of_any(rid),
                "bytes": agg["bytes"],
                "files": agg["files"],
                "seconds": _fmt_seconds(agg["seconds"]),
                "bytes_per_hour": round(agg["bytes"] / (agg["seconds"] / 3600)) if agg["seconds"] > 0 else None,
            }
            for rid, agg in live_tasks
            if agg["bytes"] > 0
        ),
        key=lambda x: -x["bytes"],
    )[:10]

    # 最爱失败的任务：按失败率排，只看有一定检测量的，避免 1/1 冒到榜首
    top_failures = sorted(
        (
            {
                "rec_id": rid,
                "name": name_of_any(rid),
                "checks": agg["checks"],
                "failures": agg["check_failures"],
                "failure_rate": _rate(agg["check_failures"], agg["checks"]),
            }
            for rid, agg in live_tasks
            if agg["check_failures"] > 0 and agg["checks"] >= 5
        ),
        key=lambda x: (-(x["failure_rate"] or 0), -x["failures"]),
    )[:10]

    top_single_day = []
    for d, buckets in daily.items():
        if not (start_str <= d <= end_str):
            continue
        for rid, v in (buckets.get("t") or {}).items():
            if v[T_SECONDS] > 0 and rid in current_ids:
                top_single_day.append(
                    {"rec_id": rid, "name": name_of_any(rid), "date": d, "seconds": _fmt_seconds(v[T_SECONDS])}
                )
    top_single_day.sort(key=lambda x: x["seconds"], reverse=True)
    top_single_day = top_single_day[:10]

    top_frequency = sorted(
        (
            {
                "rec_id": r.rec_id,
                "name": r.streamer_name or r.rec_id[:8],
                "live_count": r.live_count,
                "avg_interval_hours": round(r.avg_live_interval / 3600, 1) if r.avg_live_interval else None,
            }
            for r in rm.recordings
            if r.live_count > 0
        ),
        key=lambda x: (-x["live_count"], x["avg_interval_hours"] if x["avg_interval_hours"] is not None else 1e9),
    )[:10]

    # ── 低效清单（监控中的任务；特别关注豁免自动停，列出只会误导） ──
    auto_stop_days = cfg["auto_stop_monitor_days"]
    idle, never_recorded = [], []
    for r in rm.recordings:
        if not r.monitor_status or r.special_attention:
            continue
        if r.live_count == 0:
            never_recorded.append({"rec_id": r.rec_id, "name": r.streamer_name or r.rec_id[:8]})
        if r.last_live_time:
            idle_days = round((now - r.last_live_time) / 86400, 1)
            if idle_days >= 3:
                days_left = round(auto_stop_days - idle_days, 1) if auto_stop_days > 0 else None
                idle.append({"rec_id": r.rec_id, "name": r.streamer_name or r.rec_id[:8], "idle_days": idle_days, "days_left": days_left})
    idle.sort(key=lambda x: -x["idle_days"])
    idle = idle[:20]

    histogram = [0] * 24
    hours_by_rec = rm.analytics.read_hours()
    for hours in hours_by_rec.values():
        for h, count in enumerate(hours):
            histogram[h] += count

    # 每个主播的开播小时分布（数据本就按 rec_id 落盘，这里原样暴露）
    streamer_hours = []
    for rid, hours in hours_by_rec.items():
        if rid not in current_ids:
            continue
        total = sum(hours)
        if total <= 0:
            continue
        streamer_hours.append(
            {
                "rec_id": rid,
                "name": name_of_any(rid),
                "hours": hours,
                "total": total,
                "peak_hour": max(range(24), key=lambda h: hours[h]),
            }
        )
    streamer_hours.sort(key=lambda x: -x["total"])

    platform_checks = sorted(
        (
            {
                "platform": pk,
                "checks": v["checks"],
                "failures": v["failures"],
                "failure_rate": round(v["failures"] / v["checks"], 4) if v["checks"] else 0,
                "reasons": {r: v[r] for r in FAILURE_REASON_ORDER},
            }
            for pk, v in platform_agg.items()
        ),
        key=lambda x: -x["checks"],
    )

    failure_reasons = {
        r: sum(v[r] for v in platform_agg.values()) for r in FAILURE_REASON_ORDER
    }

    # ── 磁盘：真实容量每次算，目录递归统计走缓存 ──
    save_root = services.settings_config.get_video_save_path()
    disk = _disk_usage(save_root)
    dir_stats = await _recordings_dir_stats(save_root)
    window_bytes = sum(t["bytes"] for t in tasks_agg.values())
    bytes_per_day = window_bytes / days if days else 0
    days_left_estimate = None
    if bytes_per_day > 0 and disk.get("free_bytes"):
        days_left_estimate = round(disk["free_bytes"] / bytes_per_day, 1)
    disk.update(
        {
            "path": save_root,
            "recordings_bytes": dir_stats.get("total_bytes", 0),
            "recordings_files": dir_stats.get("total_files", 0),
            "recordings_video_files": dir_stats.get("video_files", 0),
            "sampled_at": dir_stats.get("sampled_at"),
            "cached": dir_stats.get("cached", False),
            "bytes_per_day": round(bytes_per_day),
            "days_left_estimate": days_left_estimate,
        }
    )

    sessions_total = sum(t["sessions"] for t in tasks_agg.values())
    notify_only_total = sum(t["notify_only"] for t in tasks_agg.values())
    starts_total = sum(t["starts"] for t in tasks_agg.values())
    aborts_total = sum(t["aborts"] for t in tasks_agg.values())
    # 覆盖率分母排掉「只通知不录制」的场次——那是配置意图，不是漏录
    recordable_sessions = max(0, sessions_total - notify_only_total)

    return {
        "days": days,
        "summary": {
            "sessions": sessions_cur,
            "seconds": _fmt_seconds(sum(t["seconds"] for t in tasks_agg.values())),
            "files": sum(t["files"] for t in tasks_agg.values()),
            "bytes": window_bytes,
            "active_anchors": sum(1 for t in tasks_agg.values() if t["sessions"] or t["seconds"] > 0),
            "monitoring": sum(1 for r in rm.recordings if r.monitor_status),
            "checks": sum(v["checks"] for v in platform_agg.values()),
            "check_failures": sum(v["failures"] for v in platform_agg.values()),
            "sessions_prev": sessions_prev,
            "sessions_change_pct": change_pct,
            "starts": starts_total,
            "aborts": aborts_total,
            "notify_only": notify_only_total,
            "recordable_sessions": recordable_sessions,
            "record_coverage": _rate(starts_total, recordable_sessions),
            "abort_rate": _rate(aborts_total, starts_total),
        },
        "trend": trend,
        "rankings": {
            "top_sessions": top_sessions,
            "top_single_day": top_single_day,
            "top_frequency": top_frequency,
            "top_bytes": top_bytes,
            "top_failures": top_failures,
        },
        "idle": idle,
        "never_recorded": never_recorded[:20],
        "histogram": histogram,
        "streamer_hours": streamer_hours,
        "platform_checks": platform_checks,
        "failure_reasons": failure_reasons,
        "pose": {
            "output_bytes": sum(t["pose_out_bytes"] for t in tasks_agg.values()),
            "deleted_bytes": sum(t["pose_deleted_bytes"] for t in tasks_agg.values()),
        },
        "disk": disk,
        "storage": _analytics_storage(services),
    }
