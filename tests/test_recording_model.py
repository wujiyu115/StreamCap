"""Recording 模型持久化字段：last_live_time / live_count / avg_live_interval"""
import time

from app.models.recording.recording_model import Recording


def make_recording():
    return Recording(
        "rid-1", "https://live.douyin.com/x", "主播", "MP4", "OD",
        False, 1800, True, False, None, None, None, False, False, False,
    )


def test_new_recording_defaults():
    rec = make_recording()
    assert abs(rec.last_live_time - time.time()) < 5, "新建任务宽限期从创建时刻起算"
    assert rec.live_count == 0
    assert rec.avg_live_interval is None


def test_persistence_roundtrip():
    rec = make_recording()
    rec.last_live_time = 1788000000.0
    rec.live_count = 7
    rec.avg_live_interval = 86400.0
    restored = Recording.from_dict(rec.to_dict())
    assert restored.last_live_time == 1788000000.0
    assert restored.live_count == 7
    assert restored.avg_live_interval == 86400.0


def test_from_dict_legacy_data_without_new_fields():
    """升级前的存量数据没有新字段：last_live_time 置 None（由监控循环
    首次观察到时初始化宽限期，避免重启即误停），其余取默认值"""
    legacy = {
        "rec_id": "old", "url": "https://live.douyin.com/x", "streamer_name": "x",
        "record_format": "MP4", "quality": "OD", "segment_record": False,
        "segment_time": 1800, "monitor_status": True, "scheduled_recording": False,
    }
    rec = Recording.from_dict(legacy)
    assert rec.last_live_time is None
    assert rec.live_count == 0
    assert rec.avg_live_interval is None
