"""/api/analytics/overview 的聚合口径：体积、覆盖率、失败归因、磁盘、识别产出。

用真实 AnalyticsStore（临时目录）喂合成数据，直接调端点函数，验证的是算法而不是
HTTP 管道；HTTP 层由 API 级验证覆盖。
"""
import asyncio
import time

import pytest

from app.core.analytics.analytics_store import AnalyticsStore
from app.server.routers.analytics import get_overview

from helpers import make_recording, set_recordings


class FakeConfigManager:
    def __init__(self, analytics_dir):
        self.analytics_dir = analytics_dir


class FakeSettingsConfig:
    def __init__(self, save_path):
        self._save_path = save_path

    def get_video_save_path(self):
        return self._save_path


class FakeRM:
    def __init__(self, analytics, recordings):
        self.analytics = analytics
        self._recordings = recordings

    @property
    def recordings(self):
        return self._recordings

    def _monitor_config(self):
        return {"auto_stop_monitor_days": 0.0}


class FakeServices:
    def __init__(self, analytics_dir, save_path, analytics, recordings):
        self.config_manager = FakeConfigManager(analytics_dir)
        self.settings_config = FakeSettingsConfig(save_path)
        self.recording_manager = FakeRM(analytics, recordings)


@pytest.fixture
def overview(tmp_path):
    """返回 (store, recordings, call)；call(days) 跑一次端点。"""
    analytics_dir = str(tmp_path / "analytics")
    save_path = str(tmp_path / "downloads")
    (tmp_path / "downloads").mkdir()
    store = AnalyticsStore(analytics_dir)
    recordings = []
    set_recordings(recordings)
    services = FakeServices(analytics_dir, save_path, store, recordings)

    def call(days=30):
        return asyncio.run(get_overview(days=days, user="admin", services=services))

    return store, recordings, call


def test_bytes_and_abort_aggregation(overview):
    store, recordings, call = overview
    rec = make_recording(rec_id="rid-1")
    recordings.append(rec)
    now = time.time()

    store.record_session("rid-1", now)
    store.record_record_start("rid-1", now)
    store.record_segment("rid-1", now, 3600.0, 3, raw_bytes=7_000_000, aborted=True)

    data = call()

    assert data["summary"]["bytes"] == 7_000_000
    assert data["summary"]["starts"] == 1
    assert data["summary"]["aborts"] == 1
    assert data["summary"]["abort_rate"] == 1.0
    assert data["summary"]["record_coverage"] == 1.0
    assert data["trend"][-1]["bytes"] == 7_000_000
    assert data["rankings"]["top_bytes"][0]["bytes"] == 7_000_000
    # 1 小时 7MB → 每小时约 7MB
    assert data["rankings"]["top_bytes"][0]["bytes_per_hour"] == 7_000_000


def test_notify_only_excluded_from_coverage_denominator(overview):
    """「只通知不录制」是配置意图，不该被算成漏录。"""
    store, recordings, call = overview
    recordings.append(make_recording(rec_id="rid-1"))
    now = time.time()

    store.record_session("rid-1", now, notify_only=True)
    store.record_session("rid-1", now)
    store.record_record_start("rid-1", now)

    summary = call()["summary"]

    assert (summary["sessions"], summary["notify_only"]) == (2, 1)
    assert summary["recordable_sessions"] == 1
    assert summary["record_coverage"] == 1.0


def test_coverage_is_null_without_recordable_sessions(overview):
    _store, recordings, call = overview
    recordings.append(make_recording(rec_id="rid-1"))
    assert call()["summary"]["record_coverage"] is None


def test_checked_but_never_live_task_is_not_active_anchor(overview):
    """只被检测过的任务也有 t 桶，不能因此算进「活跃主播」或时长排行。"""
    store, recordings, call = overview
    recordings.append(make_recording(rec_id="rid-1"))

    store.record_check("douyin", False, time.time(), rec_id="rid-1", reason="transient")

    data = call()
    assert data["summary"]["active_anchors"] == 0
    assert data["rankings"]["top_sessions"] == []


def test_failure_reasons_and_platform_breakdown(overview):
    store, recordings, call = overview
    recordings.append(make_recording(rec_id="rid-1"))
    now = time.time()

    for _ in range(6):
        store.record_check("douyin", True, now, rec_id="rid-1")
    store.record_check("douyin", False, now, rec_id="rid-1", reason="transient")
    store.record_check("douyin", False, now, rec_id="rid-1", reason="invalid")

    data = call()

    assert data["failure_reasons"] == {"transient": 1, "repeated": 0, "unsupported": 0, "invalid": 1}
    platform = data["platform_checks"][0]
    assert (platform["platform"], platform["checks"], platform["failures"]) == ("douyin", 8, 2)
    assert platform["reasons"]["invalid"] == 1
    top = data["rankings"]["top_failures"][0]
    assert (top["checks"], top["failures"], top["failure_rate"]) == (8, 2, 0.25)


def test_low_volume_task_kept_out_of_failure_ranking(overview):
    """检测量太少时失败率没有意义，1/1 不该冒到榜首。"""
    store, recordings, call = overview
    recordings.append(make_recording(rec_id="rid-1"))
    store.record_check("douyin", False, time.time(), rec_id="rid-1", reason="transient")

    assert call()["rankings"]["top_failures"] == []


def test_pose_bytes_surface_in_overview(overview):
    store, recordings, call = overview
    recordings.append(make_recording(rec_id="rid-1"))
    store.record_pose("rid-1", time.time(), out_bytes=1_000, del_bytes=9_000)

    assert call()["pose"] == {"output_bytes": 1_000, "deleted_bytes": 9_000}


def test_disk_reports_real_capacity_and_burn_rate(overview):
    store, recordings, call = overview
    recordings.append(make_recording(rec_id="rid-1"))
    now = time.time()
    store.record_segment("rid-1", now, 60.0, 1, raw_bytes=30 * 1_000_000)

    disk = call(days=30)["disk"]

    assert disk["total_bytes"] and disk["total_bytes"] > 0
    assert disk["free_bytes"] is not None and disk["free_bytes"] >= 0
    assert disk["bytes_per_day"] == 1_000_000  # 30MB / 30 天
    assert disk["days_left_estimate"] == round(disk["free_bytes"] / 1_000_000, 1)


def test_orphan_rec_id_excluded_from_rankings(overview):
    """任务删除后残留的聚合数据不能出现在按主播的视图里。"""
    store, recordings, call = overview
    recordings.append(make_recording(rec_id="rid-1"))
    now = time.time()
    store.record_session("gone", now)
    store.record_segment("gone", now, 120.0, 1, raw_bytes=500)

    data = call()

    assert data["rankings"]["top_sessions"] == []
    assert data["rankings"]["top_bytes"] == []
    # 汇总仍算在内（总量口径不因任务删除而缩水）
    assert data["summary"]["bytes"] == 500


def test_special_attention_excluded_from_idle_list(overview):
    """特别关注豁免自动停监控，出现在低效清单里只会误导（不会被停）。"""
    store, recordings, call = overview
    stale = make_recording(rec_id="rid-stale")
    stale.last_live_time = time.time() - 20 * 86400  # 20 天未开播
    starred = make_recording(rec_id="rid-star")
    starred.last_live_time = time.time() - 20 * 86400
    starred.special_attention = True
    recordings.extend([stale, starred])

    data = call()

    idle_ids = [r["rec_id"] for r in data["idle"]]
    assert idle_ids == ["rid-stale"]
