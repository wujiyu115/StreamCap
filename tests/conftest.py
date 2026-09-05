"""pytest 夹具：任务列表隔离 + 网络层替换（fake 设施见 helpers.py）"""
import pytest

from app.core.recording import record_manager as rm_mod
from app.core.recording.record_manager import GlobalRecordingState
from helpers import FakeRecorder


@pytest.fixture(autouse=True)
def isolated_recordings():
    """每个用例独立的任务列表，避免 GlobalRecordingState 类属性串扰"""
    saved = GlobalRecordingState.recordings
    GlobalRecordingState.recordings = []
    yield
    GlobalRecordingState.recordings = saved


@pytest.fixture(autouse=True)
def no_network_recorder(monkeypatch):
    """替换网络层：监控相关测试永不发出真实平台请求"""
    monkeypatch.setattr(rm_mod, "LiveStreamRecorder", FakeRecorder)
    FakeRecorder.calls = 0
    FakeRecorder.stream_info = None
    yield
