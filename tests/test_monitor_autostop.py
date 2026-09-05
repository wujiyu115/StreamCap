"""N 天未开播自动停监控 + 失效房间自动停"""
import time

from app.models.recording.recording_status_model import RecordingStatus
from app.core.platforms import room_validity

from helpers import make_manager, make_recording, set_recordings

NOW = time.time()


def test_days_disabled_only_invalid_stops():
    """auto_stop_monitor_days=0：只按失效停，不按天数停；录制中跳过"""
    mgr = make_manager({"auto_stop_monitor_days": 0})
    dead = make_recording(rec_id="dead")
    offline_long = make_recording(rec_id="ok")
    offline_long.last_live_time = NOW - 8 * 86400
    recording_now = make_recording(rec_id="rec")
    recording_now.is_recording = True
    set_recordings([dead, offline_long, recording_now])
    mgr.validity_cache = {"dead": {"url": dead.url, "status": room_validity.STATUS_INVALID}}

    mgr._auto_stop_stale_monitors(mgr._monitor_config())

    assert dead.monitor_status is False
    assert offline_long.monitor_status is True, "days=0 不应按天停"
    assert recording_now.monitor_status is True, "录制中的任务跳过"
    assert mgr.services.persist_calls == 1, "有变更应触发持久化"


def test_days_seven_grace_expiry_and_invalid():
    mgr = make_manager({"auto_stop_monitor_days": 7})
    fresh_task = make_recording(rec_id="new")            # 升级迁移：从未有 last_live_time
    fresh_task.last_live_time = None
    stale = make_recording(rec_id="stale")
    stale.last_live_time = NOW - 8 * 86400
    fresh_live = make_recording(rec_id="fresh")
    fresh_live.last_live_time = NOW - 2 * 86400
    gone = make_recording(rec_id="gone")
    set_recordings([fresh_task, stale, fresh_live, gone])
    mgr.validity_cache = {"gone": {"url": gone.url, "status": room_validity.STATUS_INVALID}}

    mgr._auto_stop_stale_monitors(mgr._monitor_config())

    assert fresh_task.monitor_status is True, "None → 初始化宽限期不误停"
    assert abs(fresh_task.last_live_time - NOW) < 5, "宽限期从首次观察时刻起算"
    assert stale.monitor_status is False
    assert stale.status_info == RecordingStatus.STOPPED_MONITORING
    assert fresh_live.monitor_status is True
    assert gone.monitor_status is False, "失效直接停，不等天数"


def test_invalid_cache_url_mismatch_ignored():
    """URL 变更后旧失效缓存不误停（旧结果属于旧房间）"""
    mgr = make_manager({"auto_stop_monitor_days": 0})
    rec = make_recording(rec_id="x")
    set_recordings([rec])
    mgr.validity_cache = {"x": {"url": "https://live.douyin.com/old-room",
                                "status": room_validity.STATUS_INVALID}}

    mgr._auto_stop_stale_monitors(mgr._monitor_config())

    assert rec.monitor_status is True


def test_no_persist_when_nothing_changed():
    mgr = make_manager({"auto_stop_monitor_days": 0})
    rec = make_recording()
    rec.last_live_time = NOW
    set_recordings([rec])
    before = mgr.services.persist_calls
    mgr._auto_stop_stale_monitors(mgr._monitor_config())
    assert mgr.services.persist_calls == before, "无变更不触发持久化"
