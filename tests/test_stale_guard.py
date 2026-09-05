"""过期守卫：任务在节奏门前排队期间被停止监控/改 URL/删除，旧检查结果必须作废。

节奏门使检查协程排队成为常态（窗口从毫秒级放大到分钟级），没有守卫的话
旧检查会用旧房间数据覆盖任务，甚至对已停止监控/已删除的任务启动录制。
"""
import asyncio
import time
import pytest

from app.models.recording.recording_status_model import RecordingStatus

from helpers import FakeRecorder, make_manager, make_recording, offline_stream_info, set_recordings

GATE_WAIT = 0.3   # 强制任务在节奏门排队 0.3s
MUTATE_AT = 0.1   # 排队中途实施干扰


@pytest.fixture(autouse=True)
def offline_stream():
    FakeRecorder.stream_info = offline_stream_info()


async def run_checked(mgr, rec, mutate=None):
    mgr._next_slot_at["douyin"] = time.monotonic() + GATE_WAIT
    task = asyncio.create_task(mgr._check_if_live_impl(rec))
    await asyncio.sleep(MUTATE_AT)
    if mutate:
        mutate(rec)
    await task


def test_control_result_applied():
    """无干扰：offline 结果正常应用，状态 → MONITORING"""
    mgr = make_manager()
    rec = make_recording()
    set_recordings([rec])

    asyncio.run(run_checked(mgr, rec))

    assert rec.status_info == RecordingStatus.MONITORING
    assert rec.is_checking is False


def test_stale_when_monitor_stopped_during_queue():
    mgr = make_manager()
    rec = make_recording()
    set_recordings([rec])

    asyncio.run(run_checked(mgr, rec, mutate=lambda r: setattr(r, "monitor_status", False)))

    assert rec.status_info != RecordingStatus.MONITORING, "停止监控后旧结果不应应用"
    assert rec.is_checking is False


def test_stale_when_url_changed_during_queue():
    mgr = make_manager()
    rec = make_recording()
    set_recordings([rec])

    asyncio.run(run_checked(mgr, rec, mutate=lambda r: setattr(
        r, "url", "https://live.douyin.com/roomB")))

    assert rec.status_info != RecordingStatus.MONITORING, "改 URL 后旧房间结果不应应用"


def test_stale_when_deleted_during_queue():
    mgr = make_manager()
    rec = make_recording()
    set_recordings([rec])

    asyncio.run(run_checked(mgr, rec, mutate=lambda r: set_recordings([])))

    assert rec.status_info != RecordingStatus.MONITORING, "删除任务后结果作废，不会对已删任务开播"
