"""定时监控（只在某时间段检测某主播）：时间归一化 / 窗口拼装 / 是否在窗口内。

「监控检查」这条链要保证两个失败模式：
1. 开始时间写成 ``18:30``（无秒）不能把检测协程打挂（原来会 ValueError → 卡在「检测中」）；
2. 勾了「定时监控」却没配出有效窗口时按「一直监控」处理，不能静默永不检测。
"""
import asyncio
from datetime import datetime, timedelta

from app.core.recording.record_manager import RecordingManager
from app.utils import utils


def _hhmmss(delta_hours: float) -> str:
    return (datetime.now() + timedelta(hours=delta_hours)).strftime("%H:%M:%S")


def test_normalize_time_str():
    assert utils.normalize_time_str("18:30") == "18:30:00"
    assert utils.normalize_time_str("18:30:00") == "18:30:00"
    assert utils.normalize_time_str(" 9:05 ") == "09:05:00"
    assert utils.normalize_time_str("25:00") is None
    assert utils.normalize_time_str("abc") is None
    assert utils.normalize_time_str("") is None
    assert utils.normalize_time_str(None) is None


def _ranges(start, hours):
    return asyncio.run(RecordingManager.get_scheduled_time_range(start, hours))


def test_ranges_normalize_seconds():
    """无秒的开始时间必须补成 HH:MM:SS，否则 is_current_time_within_range 会抛异常。"""
    ranges = _ranges("18:30", "2")
    assert ranges == ["18:30:00~20:30:00"]
    assert utils.is_current_time_within_range(ranges[0]) in (True, False)  # 不抛异常即可


def test_ranges_two_windows_and_float_hours():
    ranges = _ranges("09:00:00,21:00:00", "1.5,2")
    assert ranges == ["09:00:00~10:30:00", "21:00:00~23:00:00"]


def test_ranges_cross_midnight():
    """23:00 起 3 小时跨零点：区间字符串是 23:00:00~02:00:00，判定函数按跨天处理。"""
    ranges = _ranges("23:00:00", "3")
    assert ranges == ["23:00:00~02:00:00"]
    from datetime import time as _time

    now = datetime.now().time()
    assert utils.is_current_time_within_range(ranges[0]) == (
        now >= _time(23, 0) or now <= _time(2, 0)
    )


def test_ranges_skip_invalid_entries():
    # 时间写坏 / 缺小时数的那一条直接跳过，其余照旧
    assert _ranges("badtime,20:00:00", "2,1") == ["20:00:00~21:00:00"]
    assert _ranges("20:00:00", "") == []
    assert _ranges(None, "2") is None


def test_in_window_true_and_false():
    assert RecordingManager.is_in_scheduled_window([f"{_hhmmss(-1)}~{_hhmmss(1)}"]) is True
    assert RecordingManager.is_in_scheduled_window([f"{_hhmmss(3)}~{_hhmmss(5)}"]) is False


def test_in_window_fails_open_without_valid_range():
    """没配窗口（勾了定时但没填时间）→ 照常监控，不能静默停检。"""
    assert RecordingManager.is_in_scheduled_window(None) is True
    assert RecordingManager.is_in_scheduled_window([]) is True


def test_in_window_ignores_broken_range():
    """窗口串坏了只跳过该条，不抛异常（原来会把整个检测协程打挂）。"""
    assert RecordingManager.is_in_scheduled_window(["18:30~20:30:00"]) is False
    assert RecordingManager.is_in_scheduled_window(["18:30~20:30:00", f"{_hhmmss(-1)}~{_hhmmss(1)}"]) is True

class _FakeRec:
    """只装配走到定时窗口判断所需的字段（该路径在早退前就 return，不碰平台）。"""

    def __init__(self, *, start, hours, recording=False):
        self.rec_id = "rec-1"
        self.url = "https://live.douyin.com/42384417208"
        self.title = "兔子"
        self.quality = "OD"
        self.scheduled_recording = True
        self.scheduled_start_time = start
        self.monitor_hours = hours
        self.scheduled_time_range = None
        self.monitor_status = True
        self.is_recording = recording
        self.stopping_in_progress = False
        self.is_live = True
        self.is_checking = True
        self.manually_stopped = False


def _gate(monkeypatch, rec, *, active_recorders=None):
    """跑一次 _check_if_live_impl，返回 (是否调了 stop_recording, manager)。"""
    from helpers import FakeServices

    from app.core.recording.record_manager import RecordingManager

    manager = RecordingManager.__new__(RecordingManager)
    manager.services = FakeServices()
    manager.active_recorders = dict(active_recorders or {})
    stopped = []
    monkeypatch.setattr(manager, "stop_recording", lambda r, manually_stopped=True: stopped.append(r.url))
    asyncio.run(manager._check_if_live_impl(rec))
    return stopped, manager


def test_out_of_window_stops_running_recording(monkeypatch):
    """窗口结束时任务正在录：必须停录（窗口判断要早于 is_recording 的早退）。

    2026-11-25 生产事故：「兔子」23:07 开录，用户 23:38 配了 07:00~20:00，到点没停。
    """
    from app.models.recording.recording_status_model import RecordingStatus

    rec = _FakeRec(start=(datetime.now() + timedelta(hours=3)).strftime("%H:%M:%S"), hours="1", recording=True)
    stopped, _ = _gate(monkeypatch, rec)
    assert stopped == [rec.url]
    assert rec.status_info == RecordingStatus.NOT_IN_SCHEDULED_CHECK
    assert rec.is_live is False


def test_out_of_window_stops_when_recorder_still_active(monkeypatch):
    rec = _FakeRec(start=(datetime.now() + timedelta(hours=3)).strftime("%H:%M:%S"), hours="1", recording=False)
    stopped, _ = _gate(monkeypatch, rec, active_recorders={rec.rec_id: object()})
    assert stopped == [rec.url]


def test_out_of_window_does_not_stop_when_not_recording(monkeypatch):
    rec = _FakeRec(start=(datetime.now() + timedelta(hours=3)).strftime("%H:%M:%S"), hours="1")
    stopped, manager = _gate(monkeypatch, rec)
    assert stopped == []
    assert rec.rec_id in manager.services.updates  # 卡片状态有推送


def test_in_window_does_not_stop_recording(monkeypatch):
    """窗口内即使正在录也照常（这里只需要确认没被定时门停掉）。"""
    rec = _FakeRec(start=(datetime.now() - timedelta(hours=1)).strftime("%H:%M:%S"), hours="2", recording=True)
    stopped, _ = _gate(monkeypatch, rec)
    assert stopped == []

