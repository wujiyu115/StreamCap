"""录制分析报告：只读聚合端点，数据来自 analytics 汇总存储与 recordings 状态。"""
from __future__ import annotations

import os
import time
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query

from ..deps import get_current_user, get_services

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


def _fmt_seconds(seconds: float) -> float:
    return round(seconds, 1)


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
    tasks_agg: dict[str, dict] = {}   # rec_id -> {"sessions", "seconds", "files"}
    platform_agg: dict[str, dict] = {}
    trend: list[dict] = []
    sessions_cur = sessions_prev = 0
    for offset in range(days + days):  # 当期窗口 + 前一对比窗口
        d = (start_day - timedelta(days=days) + timedelta(days=offset)).isoformat()
        buckets = daily.get(d) or {}
        t_buckets = buckets.get("t") or {}
        p_buckets = buckets.get("p") or {}
        day_sessions = sum(v[0] for v in t_buckets.values())
        day_seconds = sum(v[1] for v in t_buckets.values())
        day_files = sum(v[2] for v in t_buckets.values())
        if d >= start_str:
            trend.append({"date": d, "sessions": day_sessions, "seconds": _fmt_seconds(day_seconds), "files": day_files})
            sessions_cur += day_sessions
            for rid, v in t_buckets.items():
                agg = tasks_agg.setdefault(rid, {"sessions": 0, "seconds": 0.0, "files": 0})
                agg["sessions"] += v[0]
                agg["seconds"] += v[1]
                agg["files"] += v[2]
            for pk, v in p_buckets.items():
                agg = platform_agg.setdefault(pk, {"checks": 0, "failures": 0})
                agg["checks"] += v[0]
                agg["failures"] += v[1]
        elif d >= prev_start:
            sessions_prev += day_sessions

    change_pct = None
    if sessions_prev > 0:
        change_pct = round((sessions_cur - sessions_prev) / sessions_prev * 100, 1)

    name_of = {r.rec_id: (r.streamer_name or r.rec_id[:8]) for r in rm.recordings}

    def name_of_any(rid: str) -> str:
        return name_of.get(rid) or f"{rid[:8]}…"

    top_sessions = sorted(
        (
            {"rec_id": rid, "name": name_of_any(rid), **{k: (round(v, 1) if k == "seconds" else v) for k, v in agg.items()}}
            for rid, agg in tasks_agg.items()
        ),
        key=lambda x: x["seconds"],
        reverse=True,
    )[:10]
    top_single_day = []
    for d, buckets in daily.items():
        if not (start_str <= d <= end_str):
            continue
        for rid, v in (buckets.get("t") or {}).items():
            if v[1] > 0:
                top_single_day.append({"rec_id": rid, "name": name_of_any(rid), "date": d, "seconds": _fmt_seconds(v[1])})
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

    # ── 低效清单（监控中的任务） ──
    auto_stop_days = cfg["auto_stop_monitor_days"]
    idle, never_recorded = [], []
    for r in rm.recordings:
        if not r.monitor_status:
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
    for hours in rm.analytics.read_hours().values():
        for h, count in enumerate(hours):
            histogram[h] += count

    platform_checks = sorted(
        (
            {
                "platform": pk,
                "checks": v["checks"],
                "failures": v["failures"],
                "failure_rate": round(v["failures"] / v["checks"], 4) if v["checks"] else 0,
            }
            for pk, v in platform_agg.items()
        ),
        key=lambda x: -x["checks"],
    )

    return {
        "days": days,
        "summary": {
            "sessions": sessions_cur,
            "seconds": _fmt_seconds(sum(t["seconds"] for t in tasks_agg.values())),
            "files": sum(t["files"] for t in tasks_agg.values()),
            "active_anchors": len(tasks_agg),
            "monitoring": sum(1 for r in rm.recordings if r.monitor_status),
            "checks": sum(v["checks"] for v in platform_agg.values()),
            "check_failures": sum(v["failures"] for v in platform_agg.values()),
            "sessions_prev": sessions_prev,
            "sessions_change_pct": change_pct,
        },
        "trend": trend,
        "rankings": {
            "top_sessions": top_sessions,
            "top_single_day": top_single_day,
            "top_frequency": top_frequency,
        },
        "idle": idle,
        "never_recorded": never_recorded[:20],
        "histogram": histogram,
        "platform_checks": platform_checks,
        "storage": _analytics_storage(services),
    }
