"""AnalyticsStore 单元测试：聚合、防抖落盘、月度文件、跨月读取"""
import os
import time
from datetime import datetime, timedelta

from app.core.analytics.analytics_store import AnalyticsStore


def make_store(tmp_path):
    return AnalyticsStore(str(tmp_path / "analytics"))


def test_record_and_flush(tmp_path):
    store = make_store(tmp_path)
    ts = time.time()
    store.record_session("rid-1", ts)
    store.record_session("rid-1", ts + 60)
    store.record_segment("rid-1", ts, 3600.0, 2)
    store.record_check("douyin", True, ts)
    store.record_check("douyin", False, ts)
    store.flush()

    month = datetime.fromtimestamp(ts).strftime("%Y-%m")
    path = os.path.join(str(tmp_path / "analytics"), f"analytics_{month}.json")
    assert os.path.exists(path)
    import json
    data = json.load(open(path))
    date = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    day = data["daily"][date]
    assert day["t"]["rid-1"] == [2, 3600.0, 2]
    assert day["p"]["douyin"] == [2, 1]
    # 防抖后 dirty 复位，重复 flush 不再写
    assert store._dirty is False


def test_maybe_flush_debounce(tmp_path):
    store = make_store(tmp_path)
    store.record_session("rid-1", time.time())
    store.maybe_flush()  # 首次脏数据立即落盘（数据安全优先）
    directory = str(tmp_path / "analytics")
    assert os.path.isdir(directory) and os.listdir(directory), "首次应立即落盘"

    store.record_session("rid-1", time.time())
    store.maybe_flush()  # 距上次 flush < 60s，不重复写
    assert store._dirty is True, "防抖期内应保持脏标记"
    store._last_flush = 0  # 模拟超时
    store.maybe_flush()
    assert store._dirty is False, "超时后应落盘并清除脏标记"


def test_read_daily_range_merges_months(tmp_path):
    store = make_store(tmp_path)
    # 当月 1 号 00:00:02 与上月末各记一场
    now = datetime.now()
    this_month_start = now.replace(day=1, hour=0, minute=0, second=2, microsecond=0)
    last_month_day = this_month_start - timedelta(days=1)
    store.record_session("rid-A", this_month_start.timestamp())
    store.record_segment("rid-A", this_month_start.timestamp(), 600.0, 1)
    store.record_session("rid-B", last_month_day.timestamp())
    store.record_segment("rid-B", last_month_day.timestamp(), 1200.0, 3)

    start = last_month_day.date().isoformat()
    end = now.date().isoformat()
    merged = store.read_daily_range(start, end)

    assert len(merged) == 2, f"应合并两个月的数据，实际 {sorted(merged)}"
    d1 = merged[last_month_day.date().isoformat()]["t"]["rid-B"]
    d2 = merged[this_month_start.date().isoformat()]["t"]["rid-A"]
    assert d1 == [1, 1200.0, 3]
    assert d2 == [1, 600.0, 1]


def test_hours_histogram(tmp_path):
    store = make_store(tmp_path)
    ts = time.time()
    store.record_session("rid-1", ts)
    store.record_session("rid-1", ts + 3600)  # 相隔 1 小时 → 不同小时桶
    hours = store.read_hours()
    assert sum(hours["rid-1"]) == 2
    assert hours["rid-1"][datetime.fromtimestamp(ts).hour] == 1
    assert hours["rid-1"][(datetime.fromtimestamp(ts).hour + 1) % 24] == 1


def test_zero_duration_segment_ignored(tmp_path):
    store = make_store(tmp_path)
    store.record_segment("rid-1", time.time(), 0, 0)
    assert store._dirty is False, "空分段不应产生写入"
