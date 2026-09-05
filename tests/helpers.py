"""监控逻辑测试的公共测试设施（fake 服务、manager/recording 工厂）。

RecordingManager.__init__ 会加载真实配置/语言/任务文件，这里统一用
`make_manager` 绕过它，只装配被测逻辑所需的最小状态；网络层用
FakeRecorder 替换 LiveStreamRecorder，保证测试不发任何真实请求。
"""
import asyncio
import threading
from collections import defaultdict, deque

from app.core.recording.record_manager import GlobalRecordingState, RecordingManager
from app.models.recording.recording_model import Recording

I18N_KEYS = {
    "OD": "OD",
    "monitor_stopped": "已停止监控",
    "live_room": "直播间",
    "is_live": "直播中",
}


class FakeSettings:
    def __init__(self, user_config=None):
        self.user_config = {
            "recording_space_threshold": 0,
            "language": "Chinese",
            **(user_config or {}),
        }

    def get_video_save_path(self):
        return "/tmp"


class FakeServices:
    recording_enabled = True

    def __init__(self, settings=None):
        self.settings = settings
        self.updates = []       # broadcast_card_update 调用记录
        self.persist_calls = 0  # run_coro(persist_recordings) 次数
        self.snacks = []

    def broadcast_card_update(self, rec):
        self.updates.append(rec.rec_id)

    def broadcast_pubsub(self, *args):
        pass

    def broadcast_snack(self, *args, **kwargs):
        self.snacks.append(args)

    def run_coro(self, coro):
        self.persist_calls += 1
        coro.close()


class FakeRecorder:
    """替换 LiveStreamRecorder：记录调用次数，返回预设的 stream_info"""

    calls = 0
    stream_info = None

    def __init__(self, services, recording, info):
        self.recording = recording
        self.info = info

    async def fetch_stream(self):
        FakeRecorder.calls += 1
        return FakeRecorder.stream_info


def offline_stream_info(anchor="某主播"):
    return type("StreamData", (), {"anchor_name": anchor, "is_live": False, "title": "t"})()


class FakeAnalytics:
    """替换 AnalyticsStore：只记录调用，不落盘"""

    def __init__(self):
        self.sessions = []
        self.segments = []
        self.checks = []
        self.flushes = 0

    def record_session(self, rec_id, ts):
        self.sessions.append((rec_id, ts))

    def record_segment(self, rec_id, start_ts, duration_seconds, files):
        self.segments.append((rec_id, start_ts, duration_seconds, files))

    def record_check(self, platform_key, ok, ts):
        self.checks.append((platform_key, ok, ts))

    def maybe_flush(self):
        self.flushes += 1

    def flush(self):
        self.flushes += 1


def make_manager(user_config=None):
    """构造只含被测逻辑所需状态的 RecordingManager（不触发真实初始化）"""
    mgr = RecordingManager.__new__(RecordingManager)
    settings = FakeSettings(user_config)
    mgr.services = FakeServices(settings)
    mgr.settings = settings
    mgr._ = dict(I18N_KEYS)
    mgr.loop_time_seconds = 180
    mgr.analytics = FakeAnalytics()
    mgr.platform_semaphores = defaultdict(lambda: asyncio.Semaphore(3))
    mgr._request_spacing_locks = defaultdict(asyncio.Lock)
    mgr._next_slot_at = {}
    mgr.active_recorders = {}
    mgr.validity_cache = {}
    mgr._request_results = deque(maxlen=100)
    mgr._results_lock = threading.Lock()
    mgr._round_failures = 0
    return mgr


def make_recording(url="https://live.douyin.com/roomA", monitor=True, rec_id="rid-1"):
    return Recording(
        rec_id=rec_id,
        url=url,
        streamer_name="主播",
        record_format="MP4",
        quality="OD",
        segment_record=False,
        segment_time=1800,
        monitor_status=monitor,
        scheduled_recording=False,
        scheduled_start_time=None,
        monitor_hours=None,
        recording_dir=None,
        enabled_message_push=False,
        only_notify_no_record=False,
        flv_use_direct_download=False,
    )


def set_recordings(recs):
    GlobalRecordingState.recordings = recs
