"""批量开关监控 + 后端文案兜底取值的回归测试。

两个 2026-11-24 修掉的坑：
1. `record_manager` / `stream_manager` 用 `self._["xxx"]` 下标取语言包文案，语言包读不到
   （隔离配置目录、精简部署、文件损坏）就 KeyError —— 停监控直接崩，直播检测写标题也崩；
2. `/api/recordings/batch-monitor` 曾把协程丢给 `run_coro` 就不管、`count` 取入参长度，
   rec_id 过期或协程抛异常都返回 `{"ok": true}`。
"""
import asyncio
from pathlib import Path

from helpers import FakeServices, make_manager, make_recording, set_recordings

from app.core.i18n_text import lang_text
from app.core.recording.record_manager import RecordingManager
from app.core.recording.stream_manager import LiveStreamRecorder
from app.models.recording.recording_status_model import RecordingStatus
from app.server.routers import recordings as recordings_router
from app.server.schemas import BatchMonitorRequest


class LoopServices(FakeServices):
    """run_coro 真把协程排到当前事件循环（父类只是 close 掉），这样才能验证接口有没有等它跑完。"""

    def __init__(self, settings=None):
        super().__init__(settings)
        self.loop = None
        self.persisted = 0

    def run_coro(self, coro):
        if coro is None:
            return None
        if self.loop is None:
            coro.close()
            return None
        return asyncio.run_coroutine_threadsafe(coro, self.loop)


def _setup(recordings):
    """装配 manager + services（run_coro 真的执行），并把会打网络的 check_if_live 换成计数桩"""
    set_recordings(recordings)
    mgr = make_manager()
    services = LoopServices(mgr.settings)
    mgr.services = services
    checks = []

    async def fake_check(recording, priority=False):
        checks.append((recording.rec_id, priority))

    async def fake_persist():
        services.persisted += 1

    mgr.check_if_live = fake_check
    mgr.persist_recordings = fake_persist
    services.recording_manager = mgr
    services.loop = asyncio.get_running_loop()
    return mgr, services, checks


def _run(coro_fn):
    return asyncio.run(coro_fn())


# ── batch-monitor ──────────────────────────────────────────────


def test_batch_monitor_counts_only_applied_and_reports_missing():
    async def scenario():
        mgr, services, _ = _setup([make_recording(rec_id="rid-1"), make_recording(rec_id="rid-2")])
        resp = await recordings_router.toggle_monitor_batch(
            BatchMonitorRequest(ids=["rid-1", "rid-nope"], enabled=False), user="admin", services=services
        )
        return mgr, services, resp

    mgr, services, resp = _run(scenario)
    by_id = {rec.rec_id: rec for rec in mgr.recordings}
    # 真正生效的只有 1 条：count 不再是入参长度
    assert resp["count"] == 1
    assert resp["requested"] == 2
    assert resp["missing"] == ["rid-nope"]
    assert resp["failed"] == []
    assert resp["ok"] is False
    # 关监控是真跑完了才返回（不是丢给 run_coro 就不管）
    assert by_id["rid-1"].monitor_status is False
    assert by_id["rid-1"].status_info == RecordingStatus.STOPPED_MONITORING
    assert by_id["rid-2"].monitor_status is True
    assert services.persisted == 1


def test_batch_monitor_reports_per_item_failure():
    async def scenario():
        mgr, services, _ = _setup([make_recording(rec_id="rid-1"), make_recording(rec_id="rid-2")])
        original = mgr.stop_monitor_recording

        async def flaky(recording, auto_save=True):
            if recording.rec_id == "rid-2":
                raise RuntimeError("locale boom")
            await original(recording, auto_save=auto_save)

        mgr.stop_monitor_recording = flaky
        resp = await recordings_router.toggle_monitor_batch(
            BatchMonitorRequest(ids=["rid-1", "rid-2"], enabled=False), user="admin", services=services
        )
        return mgr, resp

    mgr, resp = _run(scenario)
    by_id = {rec.rec_id: rec for rec in mgr.recordings}
    assert resp["count"] == 1
    assert resp["ok"] is False
    assert [item["id"] for item in resp["failed"]] == ["rid-2"]
    assert "locale boom" in resp["failed"][0]["error"]
    # 单条失败不能影响另一条
    assert by_id["rid-1"].monitor_status is False
    assert by_id["rid-2"].monitor_status is True


def test_batch_monitor_enable_triggers_immediate_check():
    async def scenario():
        rec = make_recording(rec_id="rid-1", monitor=False)
        rec.status_info = RecordingStatus.STOPPED_MONITORING
        mgr, services, checks = _setup([rec])
        resp = await recordings_router.toggle_monitor_batch(
            BatchMonitorRequest(ids=["rid-1"], enabled=True), user="admin", services=services
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return mgr, resp, checks

    mgr, resp, checks = _run(scenario)
    rec = mgr.recordings[0]
    assert resp["ok"] is True
    assert resp["count"] == 1
    assert rec.monitor_status is True
    assert rec.status_info == RecordingStatus.STATUS_CHECKING
    # 开启监控会立刻插一次优先检测
    assert ("rid-1", True) in checks


def test_batch_monitor_without_backend_loop_reports_failure():
    async def scenario():
        mgr, services, _ = _setup([make_recording(rec_id="rid-1")])
        services.loop = None  # 后台 loop 不可用
        return await recordings_router.toggle_monitor_batch(
            BatchMonitorRequest(ids=["rid-1"], enabled=False), user="admin", services=services
        )

    resp = _run(scenario)
    assert resp["ok"] is False
    assert resp["count"] == 0
    assert resp["failed"] == [{"id": "rid-1", "error": "backend loop unavailable"}]


# ── 语言包兜底 ────────────────────────────────────────────────


def test_lang_text_falls_back_when_dictionary_empty():
    assert lang_text({}, "monitor_stopped", {"monitor_stopped": "Stopped monitoring"}) == "Stopped monitoring"
    assert lang_text({}, "OD") == "OD"  # 没有兜底就原样回 key
    assert lang_text({"monitor_stopped": ""}, "monitor_stopped", {"monitor_stopped": "x"}) == "x"
    assert lang_text({"monitor_stopped": "已停止监控"}, "monitor_stopped") == "已停止监控"


def test_stop_monitor_survives_missing_locales():
    """语言包读不到时（self._ 为空字典）停监控不能再 KeyError。"""

    async def scenario():
        rec = make_recording(rec_id="rid-1")
        mgr, services, _ = _setup([rec])
        mgr._ = {}  # 模拟 locales/ 缺失
        await mgr.stop_monitor_recording(rec)
        return rec

    rec = _run(scenario)
    assert rec.monitor_status is False
    assert rec.status_info == RecordingStatus.STOPPED_MONITORING
    assert "Stopped monitoring" in rec.display_title


def test_text_helpers_never_subscript_language_dict():
    """两个 manager 的文案取值必须走 _t()，不许再出现 self._["xxx"]。"""
    for path in ("app/core/recording/record_manager.py", "app/core/recording/stream_manager.py"):
        source = Path(path).read_text(encoding="utf-8")
        assert "self._[" not in source, path

    # 兜底文案要覆盖「写标题」那条链路用到的键
    assert RecordingManager._t.__qualname__.startswith("RecordingManager")
    assert {"monitor_stopped", "is_live", "live_room"} <= set(RecordingManager._TEXT_FALLBACK)
    assert {"live_room", "record_stream_error"} <= set(LiveStreamRecorder._TEXT_FALLBACK)

    mgr = make_manager()
    mgr._ = {}
    assert mgr._t("is_live") == "Live streaming in progress"
    assert mgr._t("OD") == "OD"
