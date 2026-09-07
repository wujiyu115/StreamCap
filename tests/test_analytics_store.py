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


def test_purge_rec_removes_current_and_disk_data(tmp_path):
    import json

    store = make_store(tmp_path)
    now = datetime.now()
    last_month_day = now.replace(day=1, hour=0, minute=1, microsecond=0) - timedelta(days=1)
    ts = now.timestamp()
    # 当月 + 上月各留 rid-del 的数据；rid-keep 不受影响
    store.record_session("rid-del", ts)
    store.record_segment("rid-del", ts, 300.0, 1)
    store.record_session("rid-keep", ts)
    store.record_session("rid-del", last_month_day.timestamp())

    month = now.strftime("%Y-%m")
    last_month = last_month_day.strftime("%Y-%m")
    dir_ = str(tmp_path / "analytics")
    # 上月文件此刻已在跨月读取时落盘？不一定——直接构造旧月文件模拟历史月
    old_path = os.path.join(dir_, f"analytics_{last_month}.json")
    os.makedirs(dir_, exist_ok=True)
    old_date = last_month_day.strftime("%Y-%m-%d")
    if last_month != month:
        json.dump({"daily": {old_date: {"t": {"rid-del": [1, 0.0, 0]}, "p": {}}}}, open(old_path, "w"))

    store.purge_rec("rid-del")
    store.flush()

    # 当月驻留数据已清且落盘
    cur = json.load(open(os.path.join(dir_, f"analytics_{month}.json")))
    cur_date = now.strftime("%Y-%m-%d")
    assert "rid-del" not in cur["daily"][cur_date]["t"]
    assert cur["daily"][cur_date]["t"]["rid-keep"] == [1, 0.0, 0]
    # hours 已清
    assert "rid-del" not in store.read_hours()
    assert "rid-keep" in store.read_hours()
    # 旧月文件已清（purge 后 _daily 为空，flush 会重写为空 daily）
    if last_month != month:
        old = json.load(open(old_path))
        assert "rid-del" not in json.dumps(old)


def test_purge_rec_noop_when_absent(tmp_path):
    store = make_store(tmp_path)
    store.record_session("rid-1", time.time())
    store.purge_rec("rid-不存在")
    assert "rid-1" in store.read_hours(), "无关 id 的 purge 不应影响现有数据"
