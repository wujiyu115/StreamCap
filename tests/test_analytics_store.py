"""AnalyticsStore 单元测试：聚合、防抖落盘、月度文件、跨月读取"""
import os
import time
from datetime import datetime, timedelta

from app.core.analytics.analytics_store import (
    P_CHECKS,
    P_F_INVALID,
    P_F_TRANSIENT,
    P_F_UNSUPPORTED,
    P_FAILURES,
    P_WIDTH,
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
    T_WIDTH,
    AnalyticsStore,
)


def make_store(tmp_path):
    return AnalyticsStore(str(tmp_path / "analytics"))


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


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
    t = day["t"]["rid-1"]
    assert len(t) == T_WIDTH
    assert (t[T_SESSIONS], t[T_SECONDS], t[T_FILES]) == (2, 3600.0, 2)
    p = day["p"]["douyin"]
    assert len(p) == P_WIDTH
    assert (p[P_CHECKS], p[P_FAILURES]) == (2, 1)
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
    assert (d1[T_SESSIONS], d1[T_SECONDS], d1[T_FILES]) == (1, 1200.0, 3)
    assert (d2[T_SESSIONS], d2[T_SECONDS], d2[T_FILES]) == (1, 600.0, 1)


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
    keep = cur["daily"][cur_date]["t"]["rid-keep"]
    assert keep[T_SESSIONS] == 1 and keep[T_SECONDS] == 0.0 and keep[T_FILES] == 0
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


def test_pad_reads_legacy_short_arrays(tmp_path):
    """升级前的月度文件只有 t=3 槽 / p=2 槽，读路径应补零而不是索引越界。"""
    import json

    now = datetime.now()
    last_month_day = now.replace(day=1, hour=12, minute=0, second=0, microsecond=0) - timedelta(days=1)
    last_month = last_month_day.strftime("%Y-%m")
    if last_month == now.strftime("%Y-%m"):
        return  # 极端情况（当月 1 号即上月）跳过
    dir_ = str(tmp_path / "analytics")
    os.makedirs(dir_, exist_ok=True)
    old_date = last_month_day.strftime("%Y-%m-%d")
    json.dump(
        {"daily": {old_date: {"t": {"rid-old": [3, 900.0, 4]}, "p": {"douyin": [10, 2]}}}},
        open(os.path.join(dir_, f"analytics_{last_month}.json"), "w"),
    )

    store = make_store(tmp_path)
    merged = store.read_daily_range(old_date, now.date().isoformat())
    t = merged[old_date]["t"]["rid-old"]
    p = merged[old_date]["p"]["douyin"]
    assert len(t) == T_WIDTH and len(p) == P_WIDTH, "旧数组应补齐到新宽度"
    # 原有槽位保值，新槽位为 0
    assert (t[T_SESSIONS], t[T_SECONDS], t[T_FILES]) == (3, 900.0, 4)
    assert t[T_RAW_BYTES] == 0 and t[T_STARTS] == 0 and t[T_POSE_OUT] == 0
    assert (p[P_CHECKS], p[P_FAILURES]) == (10, 2)
    assert p[P_F_TRANSIENT] == 0


def test_segment_records_bytes_and_abort(tmp_path):
    store = make_store(tmp_path)
    ts = time.time()
    store.record_segment("rid-1", ts, 120.0, 3, raw_bytes=4096, aborted=True)
    store.record_segment("rid-1", ts, 60.0, 1, raw_bytes=1024, aborted=False)
    merged = store.read_daily_range(_today(), _today())
    t = merged[_today()]["t"]["rid-1"]
    assert t[T_RAW_BYTES] == 5120
    assert t[T_ABORTS] == 1, "只有异常退出的那次计入中断"
    assert t[T_FILES] == 4


def test_abort_only_segment_is_recorded(tmp_path):
    """零时长零文件但异常退出：仍要记一次中断，不能被空分段守卫吞掉。"""
    store = make_store(tmp_path)
    store.record_segment("rid-1", time.time(), 0, 0, raw_bytes=0, aborted=True)
    assert store._dirty is True
    t = store.read_daily_range(_today(), _today())[_today()]["t"]["rid-1"]
    assert t[T_ABORTS] == 1


def test_notify_only_session_and_record_start(tmp_path):
    store = make_store(tmp_path)
    ts = time.time()
    store.record_session("rid-rec", ts)
    store.record_record_start("rid-rec", ts)
    store.record_session("rid-notify", ts, notify_only=True)

    day = store.read_daily_range(_today(), _today())[_today()]["t"]
    assert day["rid-rec"][T_SESSIONS] == 1 and day["rid-rec"][T_STARTS] == 1
    assert day["rid-notify"][T_SESSIONS] == 1 and day["rid-notify"][T_NOTIFY_ONLY] == 1
    assert day["rid-notify"][T_STARTS] == 0, "只通知的场次不应计入录制启动"


def test_check_reason_buckets_and_per_rec_counts(tmp_path):
    store = make_store(tmp_path)
    ts = time.time()
    store.record_check("douyin", True, ts, rec_id="rid-1")
    store.record_check("douyin", False, ts, rec_id="rid-1", reason="transient")
    store.record_check("douyin", False, ts, rec_id="rid-1", reason="unsupported")
    store.record_check("douyin", False, ts, rec_id="rid-2", reason="invalid")
    store.record_check("douyin", False, ts, rec_id="rid-2", reason="不认识的原因")

    buckets = store.read_daily_range(_today(), _today())[_today()]
    p = buckets["p"]["douyin"]
    assert p[P_CHECKS] == 5 and p[P_FAILURES] == 4
    assert p[P_F_TRANSIENT] == 1 and p[P_F_UNSUPPORTED] == 1 and p[P_F_INVALID] == 1
    # 未知原因只进总失败数，不落任何分档桶
    assert sum(p[P_F_TRANSIENT:]) == 3

    t1, t2 = buckets["t"]["rid-1"], buckets["t"]["rid-2"]
    assert (t1[T_CHECKS], t1[T_CHECK_FAILS]) == (3, 2)
    assert (t2[T_CHECKS], t2[T_CHECK_FAILS]) == (2, 2)


def test_check_without_rec_id_only_touches_platform_bucket(tmp_path):
    store = make_store(tmp_path)
    store.record_check("douyin", False, time.time(), reason="repeated")
    buckets = store.read_daily_range(_today(), _today())[_today()]
    assert buckets["t"] == {}, "无 rec_id 时不应凭空创建任务条目"
    assert buckets["p"]["douyin"][P_FAILURES] == 1


def test_record_pose_bytes(tmp_path):
    store = make_store(tmp_path)
    ts = time.time()
    store.record_pose("rid-1", ts, out_bytes=2048, del_bytes=8192)
    store.record_pose("rid-1", ts, out_bytes=1, del_bytes=0)
    t = store.read_daily_range(_today(), _today())[_today()]["t"]["rid-1"]
    assert t[T_POSE_OUT] == 2049 and t[T_POSE_DEL] == 8192


def test_record_pose_zero_is_noop(tmp_path):
    store = make_store(tmp_path)
    store.record_pose("rid-1", time.time(), 0, 0)
    assert store._dirty is False


def test_day_bucket_tolerates_missing_subbucket(tmp_path):
    """旧文件里可能存在缺 p 键的日桶，埋点不应 KeyError。"""
    import json

    store = make_store(tmp_path)
    store._ensure_loaded()
    store._daily[_today()] = {"t": {"rid-1": [1, 0.0, 0]}}  # 缺 "p"
    store.record_check("douyin", True, time.time(), rec_id="rid-1")
    buckets = store.read_daily_range(_today(), _today())[_today()]
    assert buckets["p"]["douyin"][P_CHECKS] == 1
    assert buckets["t"]["rid-1"][T_CHECKS] == 1
    store.flush()
    month = datetime.now().strftime("%Y-%m")
    json.load(open(os.path.join(str(tmp_path / "analytics"), f"analytics_{month}.json")))
