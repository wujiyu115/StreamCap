"""录制分析埋点：失败归因分档、开播→录制覆盖率、只通知不录制的区分。

归因刻意不发额外请求（抖音对单 IP+cookie 有分钟级配额），全部由内存状态派生，
所以这里锁的正是「派生规则」本身。
"""
import asyncio

import pytest

from app.core.platforms import room_validity
from app.models.recording.recording_status_model import RecordingStatus

from helpers import (
    FakeRecorder,
    live_stream_info,
    make_manager,
    make_recording,
    offline_stream_info,
    set_recordings,
)


@pytest.fixture(autouse=True)
def reset_recorder():
    FakeRecorder.calls = 0
    FakeRecorder.stream_info = offline_stream_info()


# ── 失败归因分档 ────────────────────────────────────────


def test_classify_first_failure_is_transient():
    mgr = make_manager()
    rec = make_recording()
    assert mgr._classify_check_failure(rec) == "transient"


def test_classify_second_failure_is_repeated():
    mgr = make_manager()
    rec = make_recording()
    rec.consecutive_failures = 1
    assert mgr._classify_check_failure(rec) == "repeated"


def test_classify_at_unsupported_limit():
    """本次失败恰好触顶（计数尚未自增）→ 归到 unsupported，与随后的标记动作一致。"""
    mgr = make_manager({"monitor_unsupported_failure_limit": 3})
    rec = make_recording()
    rec.consecutive_failures = 2
    assert mgr._classify_check_failure(rec) == "unsupported"


def test_classify_limit_zero_never_unsupported():
    mgr = make_manager({"monitor_unsupported_failure_limit": 0})
    rec = make_recording()
    rec.consecutive_failures = 99
    assert mgr._classify_check_failure(rec) == "repeated"


def test_classify_invalid_wins_over_counters():
    mgr = make_manager()
    rec = make_recording()
    mgr.validity_cache[rec.rec_id] = {"url": rec.url, "status": room_validity.STATUS_INVALID}
    assert mgr._classify_check_failure(rec) == "invalid"


def test_classify_invalid_ignored_when_url_changed():
    """缓存里的判定属于旧 URL，改过地址后不能继续算作失效。"""
    mgr = make_manager()
    rec = make_recording()
    mgr.validity_cache[rec.rec_id] = {
        "url": "https://live.douyin.com/other",
        "status": room_validity.STATUS_INVALID,
    }
    assert mgr._classify_check_failure(rec) == "transient"


def test_classify_without_recording_returns_none():
    assert make_manager()._classify_check_failure(None) is None


# ── record_check 的入参 ─────────────────────────────────


def test_failed_check_carries_rec_id_and_reason():
    mgr = make_manager()
    rec = make_recording()
    mgr._record_request_result(ok=False, platform_key="douyin", recording=rec)
    platform, ok, _ts, rec_id, reason = mgr.analytics.checks[-1]
    assert (platform, ok, rec_id, reason) == ("douyin", False, rec.rec_id, "transient")


def test_successful_check_has_no_reason():
    mgr = make_manager()
    rec = make_recording()
    mgr._record_request_result(ok=True, platform_key="douyin", recording=rec)
    _platform, ok, _ts, rec_id, reason = mgr.analytics.checks[-1]
    assert (ok, rec_id, reason) == (True, rec.rec_id, None)


def test_check_without_platform_key_is_not_recorded():
    """平台未知时不记：桶是按平台建的，塞进去只会污染统计。"""
    mgr = make_manager()
    mgr._record_request_result(ok=False, platform_key=None, recording=make_recording())
    assert mgr.analytics.checks == []


# ── 开播 → 实际开录 ────────────────────────────────────


def test_live_check_records_session_and_record_start():
    mgr = make_manager()
    rec = make_recording()
    set_recordings([rec])
    FakeRecorder.stream_info = live_stream_info()

    asyncio.run(mgr._check_if_live_impl(rec))

    assert [(r, n) for r, _ts, n in mgr.analytics.sessions] == [(rec.rec_id, False)]
    assert [r for r, _ts in mgr.analytics.record_starts] == [rec.rec_id]
    assert rec.status_info == RecordingStatus.PREPARING_RECORDING


def test_notify_only_session_is_flagged_and_not_started():
    mgr = make_manager()
    rec = make_recording()
    rec.only_notify_no_record = True
    set_recordings([rec])
    FakeRecorder.stream_info = live_stream_info()

    asyncio.run(mgr._check_if_live_impl(rec))

    assert [(r, n) for r, _ts, n in mgr.analytics.sessions] == [(rec.rec_id, True)]
    assert mgr.analytics.record_starts == []
    assert rec.status_info == RecordingStatus.LIVE_BROADCASTING


def test_offline_check_records_neither():
    mgr = make_manager()
    rec = make_recording()
    set_recordings([rec])

    asyncio.run(mgr._check_if_live_impl(rec))

    assert mgr.analytics.sessions == []
    assert mgr.analytics.record_starts == []
