"""两档活跃优先：新近度 + 开播频率（EMA）"""
import time

import pytest

from app.models.recording.recording_model import Recording
from helpers import make_manager, make_recording

DAY = 86400


def make_mgr():
    return make_manager({})


def make_rec(days_ago=None, avg_days=None):
    rec = make_recording()
    rec.last_live_time = time.time() - days_ago * DAY if days_ago is not None else None
    rec.avg_live_interval = avg_days * DAY if avg_days is not None else None
    return rec


@pytest.mark.parametrize(
    "days_ago, avg_days, expected",
    [
        (1, None, 1),     # 1 天前播过 → 高档
        (1.9, None, 1),   # 新近度边界内
        (3, None, 2),     # 超新近度、无频率样本 → 低档
        (10, 30, 2),      # 历史月播 → 低档
        (10, 1, 1),       # 10 天没播但历史天播 → 仍是高档（频率维度）
        (10, 2.9, 1),     # 频率边界内
        (10, 3.1, 2),     # 频率边界外
        (None, None, 2),  # 从未开播 → 低档
    ],
)
def test_two_tier_boundaries(days_ago, avg_days, expected):
    mgr = make_mgr()
    rec = make_rec(days_ago, avg_days)
    assert mgr._activity_tier_multiplier(rec, time.time()) == expected


def test_recency_priority_enabled_by_default():
    mgr = make_mgr()
    assert mgr._monitor_config()["recency_priority_enabled"] is True


def test_interval_composition_with_backoff():
    """调度间隔 = 基础 × 退避倍数 × 活跃档位"""
    mgr = make_mgr()
    rec = make_rec(3)  # 低档 2×
    rec.backoff_multiplier = 2
    interval = 180 * rec.backoff_multiplier * mgr._activity_tier_multiplier(rec, time.time())
    assert interval == 720


def test_ema_cadence_updates_on_live_transitions():
    """开播节奏统计：第 1 次无从统计 → 第 2 次 gap → 第 3 次起 EMA"""
    mgr = make_mgr()
    rec = make_rec(30)  # 30 天前创建（宽限起点）

    t1 = time.time()
    mgr._update_live_cadence(rec, t1)
    rec.last_live_time = t1  # 模拟 impl 在开播后刷新 last_live_time
    assert rec.live_count == 1 and rec.avg_live_interval is None

    t2 = t1 + 2 * DAY
    mgr._update_live_cadence(rec, t2)
    rec.last_live_time = t2
    assert rec.live_count == 2
    assert abs(rec.avg_live_interval - 2 * DAY) < 60, "第 2 次：avg = gap"

    t3 = t2 + 1 * DAY
    mgr._update_live_cadence(rec, t3)
    assert abs(rec.avg_live_interval - (0.5 * 2 * DAY + 0.5 * 1 * DAY)) < 60
    assert rec.live_count == 3
