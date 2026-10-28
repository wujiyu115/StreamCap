from __future__ import annotations

import os
import sqlite3
import time

import pytest

from app.core.upload.manager import UploadManager
from app.core.upload.models import UploadAuthError, UploadRequest
from app.core.upload.providers.bilibili import BilibiliProvider


def _config(enabled=True):
    return {
        "uploads": {
            "enabled": enabled,
            "auto_after_pose": True,
            "max_attempts": 2,
            "providers": {
                "bilibili": {
                    "enabled": True,
                    "cookie_file": "config/bilibili/cookies.json",
                    "tid": 138,
                    "tags": "直播录像,录播",
                    "copyright": 1,
                }
            },
        }
    }


def test_bilibili_command_never_sends_source(tmp_path):
    provider = BilibiliProvider(str(tmp_path), executable="/usr/bin/biliup")
    request = UploadRequest("job", ["/tmp/a.mp4"], "标题")
    command = provider.build_command(request, _config()["uploads"]["providers"]["bilibili"])
    assert "--source" not in command
    assert command[-1] == "/tmp/a.mp4"
    assert command[command.index("--submit") + 1] == "web"


def test_pose_outputs_enqueue_once_and_survive_restart(tmp_path):
    output = tmp_path / "downloads" / "sample_merged.mp4"
    output.parent.mkdir()
    output.write_bytes(b"video")
    config = _config()

    manager = UploadManager(str(tmp_path), lambda: config)
    manager._stop.set()  # 测队列持久化，不让后台真的投稿
    manager._worker.join(timeout=3)
    state = {
        "status": "completed",
        "trigger": "auto",
        "rec_id": "rec-1",
        "summary": {"output_files": [str(output)]},
    }
    assert manager.enqueue_pose_outputs("pose-1", state) == 1
    assert manager.enqueue_pose_outputs("pose-1", state) == 0
    jobs = manager.list_jobs()
    assert len(jobs) == 1
    assert jobs[0]["status"] == "pending"
    assert jobs[0]["files"] == [str(output)]

    with sqlite3.connect(manager.db_path) as conn:
        conn.execute("UPDATE upload_jobs SET status='uploading'")
    restarted = UploadManager(str(tmp_path), lambda: config)
    restarted._stop.set()
    restarted._worker.join(timeout=3)
    assert restarted.list_jobs()[0]["status"] == "retrying"


def test_auth_failure_pauses_channel_and_resume_requeues(tmp_path):
    output = tmp_path / "sample_merged.mp4"
    output.write_bytes(b"video")
    config = _config()
    manager = UploadManager(str(tmp_path), lambda: config)
    manager._stop.set()
    manager._worker.join(timeout=3)
    state = {"status": "completed", "trigger": "auto", "summary": {"output_files": [str(output)]}}
    assert manager.enqueue_pose_outputs("auth-task", state) == 1

    class ExpiredProvider:
        def upload(self, request, provider_config):
            raise UploadAuthError("Bilibili login expired")

    manager._providers["bilibili"] = ExpiredProvider()
    row = manager._next_job()
    manager._process(row)
    assert manager.list_jobs()[0]["status"] == "blocked_auth"
    channel = manager.list_channels()[0]
    assert channel["status"] == "blocked_auth"
    assert channel["notified"] == 1

    # 被暂停的渠道不再取任务，也不继续消耗重试次数
    assert manager._next_job() is None
    assert manager.resume_channel("bilibili") is True
    assert manager.list_channels()[0]["status"] == "active"
    assert manager.list_jobs()[0]["status"] == "retrying"


def test_manual_or_disabled_pose_does_not_enqueue(tmp_path):
    output = tmp_path / "sample_merged.mp4"
    output.write_bytes(b"video")
    config = _config(enabled=False)
    manager = UploadManager(str(tmp_path), lambda: config)
    manager._stop.set()
    manager._worker.join(timeout=3)
    state = {"status": "completed", "trigger": "auto", "summary": {"output_files": [str(output)]}}
    assert manager.enqueue_pose_outputs("disabled", state) == 0
    config["uploads"]["enabled"] = True
    state["trigger"] = "manual"
    assert manager.enqueue_pose_outputs("manual", state) == 0


def test_jobs_kept_at_most_100_and_active_never_pruned(tmp_path):
    """保留上限：只删最旧的已结束记录；排队/上传中/等登录的一律留。"""
    output = tmp_path / "sample_merged.mp4"
    output.write_bytes(b"video")
    config = _config()
    manager = UploadManager(str(tmp_path), lambda: config)
    manager._stop.set()
    manager._worker.join(timeout=3)

    with sqlite3.connect(manager.db_path) as conn:
        # 1 条仍在排队 + 104 条已完成
        conn.execute(
            "INSERT INTO upload_jobs(id, source_task_id, provider, status, files_json, title, "
            "created_at, updated_at) VALUES('live-1','task-live','bilibili','pending','[]','live','"
            "2026-01-01T00:00:00','2026-01-01T00:00:00')"
        )
        for i in range(104):
            conn.execute(
                "INSERT INTO upload_jobs(id, source_task_id, provider, status, files_json, title, "
                "created_at, updated_at) VALUES(?,?, 'bilibili','completed','[]',?, ?, ?)",
                (f"done-{i}", f"task-{i}", f"t{i}", f"2026-02-01T00:{i // 60:02d}:{i % 60:02d}", "2026-02-02T00:00:00"),
            )

    assert manager.count_jobs() == 105
    assert manager._prune_jobs() == 5
    assert manager.count_jobs() == 100
    ids = {job["id"] for job in manager.list_jobs(page=1, per_page=100)}
    assert "live-1" in ids                      # 未结束的不会被删
    assert "done-0" not in ids                   # 最旧的已完成被删
    assert manager._prune_jobs() == 0            # 幂等


def test_list_jobs_pagination_is_stable_across_same_second(tmp_path):
    """created_at 只到秒：同秒多条时靠 rowid 兜底，翻页不重不漏。"""
    config = _config()
    manager = UploadManager(str(tmp_path), lambda: config)
    manager._stop.set()
    manager._worker.join(timeout=3)
    with sqlite3.connect(manager.db_path) as conn:
        for i in range(20):
            conn.execute(
                "INSERT INTO upload_jobs(id, source_task_id, provider, status, files_json, title, "
                "created_at, updated_at) VALUES(?,?, 'bilibili','completed','[]',?,"
                "'2026-03-01T00:00:00','2026-03-01T00:00:00')",
                (f"same-{i}", f"task-{i}", f"t{i}"),
            )

    pages = [manager.list_jobs(page=p, per_page=15) for p in (1, 2)]
    assert [len(p) for p in pages] == [15, 5]
    ids = [job["id"] for page in pages for job in page]
    assert len(ids) == len(set(ids)) == 20
    assert ids[0] == "same-19"                   # 新的在前
    assert manager.list_jobs(page=3, per_page=15) == []


def _mgr(tmp_path, config=None):
    manager = UploadManager(str(tmp_path), lambda: config or _config())
    manager._stop.set()  # 别让后台线程真的投稿
    manager._worker.join(timeout=3)
    return manager


def test_collect_videos_expands_folders_and_skips_non_video(tmp_path):
    from app.server.media_service import collect_videos

    root = tmp_path / "downloads"
    (root / "sub" / "deep").mkdir(parents=True)
    (root / "a.mp4").write_bytes(b"x")
    (root / "cover.jpg").write_bytes(b"x")
    (root / "sub" / "b.mkv").write_bytes(b"x")
    (root / "sub" / "deep" / "c.ts").write_bytes(b"x")

    got = collect_videos(["a.mp4", "..", "nope", "sub"], str(root))
    names = sorted(os.path.basename(p) for p in got["files"])
    assert names == ["a.mp4", "b.mkv", "c.ts"]
    assert sorted(got["missing"]) == ["..", "nope"]

    # 同一文件重复选中只算一次
    assert len(collect_videos(["a.mp4", "a.mp4"], str(root))["files"]) == 1


def test_enqueue_files_skips_uploaded_and_queued_but_retries_failed(tmp_path):
    files = []
    for name in ("one.mp4", "two.mp4", "three.mp4"):
        path = tmp_path / name
        path.write_bytes(b"video")
        files.append(str(path))

    manager = _mgr(tmp_path)
    first = manager.enqueue_files(files, ["bilibili"])
    assert first["queued"] == 3 and first["files"] == 3
    assert first["skipped_uploaded"] == 0 and first["skipped_queued"] == 0
    # 同一批又点一次：任务还在队列里，不重复入队
    again = manager.enqueue_files(files, ["bilibili"])
    assert again["queued"] == 0 and again["skipped_queued"] == 3
    assert manager.count_jobs() == 3

    # 一个成功、一个失败、一个仍在队
    with sqlite3.connect(manager.db_path) as conn:
        conn.execute(
            "UPDATE upload_jobs SET status='completed' WHERE files_json LIKE ?", (f"%{files[0]}%",)
        )
        conn.execute("UPDATE upload_jobs SET status='failed' WHERE files_json LIKE ?", (f"%{files[1]}%",))
    result = manager.enqueue_files(files, ["bilibili"])
    assert (result["queued"], result["skipped_uploaded"], result["skipped_queued"]) == (1, 1, 1)
    statuses = {j["files"][0]: j["status"] for j in manager.list_jobs()}
    assert statuses[files[0]] == "completed"   # 已上传的不动
    assert statuses[files[1]] == "pending"     # 失败的重投
    assert manager.count_jobs() == 3           # 复用同一行，不膨胀


def test_enqueue_files_skips_already_uploaded_pose_output(tmp_path):
    output = tmp_path / "downloads" / "clip_merged.mp4"
    output.parent.mkdir()
    output.write_bytes(b"video")
    manager = _mgr(tmp_path)
    assert manager.enqueue_pose_outputs(
        "pose-9", {"status": "completed", "trigger": "auto", "summary": {"output_files": [str(output)]}}
    ) == 1
    with sqlite3.connect(manager.db_path) as conn:
        conn.execute("UPDATE upload_jobs SET status='completed'")

    # 手动再选同一个文件：同渠道已上传过，跳过（来源不同也算）
    result = manager.enqueue_files([str(output)], ["bilibili"])
    assert result["queued"] == 0 and result["skipped_uploaded"] == 1
    assert manager.count_jobs() == 1


def test_enqueue_files_ignores_unknown_provider_and_missing_files(tmp_path):
    path = tmp_path / "a.mp4"
    path.write_bytes(b"video")
    manager = _mgr(tmp_path)
    assert manager.enqueue_files([str(path)], ["youtube"])["providers"] == []
    assert manager.count_jobs() == 0
    assert manager.enqueue_files([str(tmp_path / "gone.mp4")], ["bilibili"])["files"] == 0


# 2026-10-28 生产环境 biliup 真实输出（去掉 ANSI 色码，其余逐字保留）
_PRE_UPLOAD_SAMPLE = (
    'pre_upload: {"build":2140000,"name":"彤宝_2026-10-12_15-31-52_000_merged.mp4",'
    '"profile":"ugcupos/bup","r":"upos","size":27331032,"ssl":0,"version":"2.14.0"}'
)
REAL_406_OUTPUT = """biliup upload failed: 2026-10-28 11:28:09 INFO biliup_cli::uploader: user: bili_14714387372
2026-10-28 11:28:09 INFO biliup_cli::uploader: number of concurrent futures: 3
2026-10-28 11:28:09 INFO biliup_cli::uploader: No checkpoint found, starting fresh upload
2026-10-28 11:28:10 INFO biliup::uploader::line: __PRE_UPLOAD_SAMPLE__
Error: after retries
├╴at crates/biliup-cli/src/uploader.rs:579:14
│
╰─▶ Failed to pre_upload from {"OK":0,"info":"您上传视频过快，请您稍作休息后再继续","code":406,"message":"您上传视频过快，请您稍作休息后再继续"}
    ╰╴at crates/biliup-cli/src/uploader.rs:579:14""".replace(
    "__PRE_UPLOAD_SAMPLE__", _PRE_UPLOAD_SAMPLE
)


def test_rate_limit_detector_recognises_real_platform_output():
    """平台风控的 code 在不同版本/接口下不同，文案也不同，都要认出来。"""
    from app.core.upload.providers.bilibili import detect_rate_limit

    assert detect_rate_limit(REAL_406_OUTPUT) == (406, False)
    # issue #1565：601 且响应里带 detail.v_voucher —— 说明平台已挂着待过的验证
    assert detect_rate_limit(
        'Failed to pre_upload from {"OK":0,"info":"您上传视频过快","code":601,'
        '"detail":{"v_voucher":"voucher_09f7b9d6-10fa-4688-90"}}'
    ) == (601, True)
    # issue #1362：submit 回的是 Rust Debug 而不是 JSON，code 是 21566
    assert detect_rate_limit(
        'ResponseData { code: 21566, data: None, message: "投稿过于频繁，建议将APP升级至最新版本后再试", ttl: Some(1) }'
    ) == (21566, False)
    assert detect_rate_limit('Failed to pre_upload from {"code":21564,"message":"上传过于频繁请等待24小时"}') == (21564, False)
    # 普通失败不能被误判成限流
    assert detect_rate_limit("Error: file not found") is None
    assert detect_rate_limit('Web 接口投稿失败: ResponseData { code: 21001, message: "参数错误" }') is None


def test_provider_raises_rate_limited_for_real_406(tmp_path, monkeypatch):
    import subprocess

    from app.core.upload.models import UploadRateLimited

    cookie = tmp_path / "cookies.json"
    cookie.write_text("{}")
    video = tmp_path / "v_merged.mp4"
    video.write_bytes(b"video")
    provider = BilibiliProvider(str(tmp_path), executable="/usr/bin/biliup")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 1, stdout=REAL_406_OUTPUT),
    )
    provider_config = {**_config()["uploads"]["providers"]["bilibili"], "cookie_file": str(cookie)}
    with __import__("pytest").raises(UploadRateLimited) as info:
        provider.upload(UploadRequest("job", [str(video)], "标题"), provider_config)
    assert info.value.code == 406
    assert info.value.needs_verify is False


def test_rate_limit_cools_channel_without_burning_attempts(tmp_path):
    """406 是账号级风控：整个渠道冷却、任务重试预算不扣，而不是逐任务烧 5 次。"""
    from app.core.upload.models import UploadRateLimited

    output = tmp_path / "sample_merged.mp4"
    output.write_bytes(b"video")
    config = _config()
    config["uploads"]["rate_limit_cooldown_seconds"] = 1800
    manager = _mgr(tmp_path, config)
    state = {"status": "completed", "trigger": "auto", "summary": {"output_files": [str(output)]}}
    assert manager.enqueue_pose_outputs("rate-task", state) == 1

    class LimitedProvider:
        def upload(self, request, provider_config):
            raise UploadRateLimited("Bilibili rate limited (code=406)", code=406)

    manager._providers["bilibili"] = LimitedProvider()
    row = manager._next_job()
    manager._process(row)

    job = manager.list_jobs()[0]
    assert job["status"] == "retrying"
    assert job["attempts"] == 0            # 限流不消耗重试预算（max_attempts=2 只有 2 次）
    assert job["next_attempt_at"] > time.time()
    channel = manager.list_channels()[0]
    assert channel["status"] == "cooling"
    assert channel["cool_downs"] == 1
    assert channel["resume_at"] == pytest.approx(job["next_attempt_at"], abs=1)

    # 冷却期间任务即使到期也不取（否则队列会立刻把冷却打穿）
    with sqlite3.connect(manager.db_path) as conn:
        conn.execute("UPDATE upload_jobs SET next_attempt_at=0")
    assert manager._next_job() is None

    # 冷却到期后自己恢复
    with sqlite3.connect(manager.db_path) as conn:
        conn.execute("UPDATE upload_channels SET resume_at=0, status='active', last_attempt_at=0")
    resumed = manager._next_job()
    assert resumed["id"] == job["id"]
    assert resumed["attempts"] == 0


def test_cooldown_escalates_caps_and_notifies_once_per_episode(tmp_path):
    from app.core.upload.models import UploadRateLimited

    output = tmp_path / "sample_merged.mp4"
    output.write_bytes(b"video")
    config = _config()
    config["uploads"]["rate_limit_cooldown_seconds"] = 60
    config["uploads"]["rate_limit_max_cooldown_seconds"] = 240
    manager = _mgr(tmp_path, config)

    class Pusher:
        def __init__(self):
            self.calls = []

        def push_messages_sync(self, title, content):
            self.calls.append((title, content))

    pusher = Pusher()
    manager._message_pusher = pusher
    state = {"status": "completed", "trigger": "auto", "summary": {"output_files": [str(output)]}}
    manager.enqueue_pose_outputs("cool-task", state)

    class LimitedProvider:
        def upload(self, request, provider_config):
            raise UploadRateLimited("Bilibili rate limited (code=406)", code=406)

    manager._providers["bilibili"] = LimitedProvider()

    def cool_once() -> float:
        row = manager._next_job()
        assert row is not None
        manager._process(row)
        channel = manager.list_channels()[0]
        return channel["resume_at"] - time.time()

    def expire_cooldown():
        # 同时把节奏门计时也拨回去，模拟「冷却和最小间隔都过去了」
        with sqlite3.connect(manager.db_path) as conn:
            conn.execute(
                "UPDATE upload_channels SET resume_at=0, status='active', last_attempt_at=0"
            )
            conn.execute("UPDATE upload_jobs SET next_attempt_at=0")

    assert cool_once() == pytest.approx(60, abs=2)
    expire_cooldown()
    assert cool_once() == pytest.approx(120, abs=2)   # 连续第二次翻倍
    expire_cooldown()
    assert cool_once() == pytest.approx(240, abs=2)   # 到上限
    expire_cooldown()
    assert cool_once() == pytest.approx(240, abs=2)   # 封顶不再涨
    # 同一轮冷却只推一次（否则限流期间会被通知刷屏）
    assert len(pusher.calls) == 1
    assert "验证" in pusher.calls[0][1]

    # 用户点「恢复上传」又立刻撞上风控：退避继续翻倍、不重复推送
    assert manager.resume_channel("bilibili") is True
    expire_cooldown()
    assert cool_once() == pytest.approx(240, abs=2)
    assert len(pusher.calls) == 1


def test_resume_channel_clears_cooldown_and_releases_jobs(tmp_path):
    from app.core.upload.models import UploadRateLimited

    output = tmp_path / "sample_merged.mp4"
    output.write_bytes(b"video")
    config = _config()
    manager = _mgr(tmp_path, config)
    state = {"status": "completed", "trigger": "auto", "summary": {"output_files": [str(output)]}}
    manager.enqueue_pose_outputs("resume-task", state)

    class LimitedProvider:
        def upload(self, request, provider_config):
            raise UploadRateLimited("Bilibili rate limited (code=601)", code=601)

    manager._providers["bilibili"] = LimitedProvider()
    manager._process(manager._next_job())
    assert manager._next_job() is None

    assert manager.resume_channel("bilibili") is True
    channel = manager.list_channels()[0]
    assert channel["status"] == "active" and channel["resume_at"] == 0
    # 限流恢复保留连续计数：用户没先去过验证就又撞风控时，退避继续翻倍而不是回到 600s
    assert channel["cool_downs"] == 1 and channel["notified"] == 1
    job = manager.list_jobs()[0]
    assert job["status"] == "retrying" and job["next_attempt_at"] == 0
    assert job["attempts"] == 0
    assert manager._next_job() is not None


def test_success_resets_cooldown_counter(tmp_path):
    from app.core.upload.models import UploadResult

    output = tmp_path / "sample_merged.mp4"
    output.write_bytes(b"video")
    manager = _mgr(tmp_path)
    state = {"status": "completed", "trigger": "auto", "summary": {"output_files": [str(output)]}}
    manager.enqueue_pose_outputs("ok-task", state)
    with sqlite3.connect(manager.db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO upload_channels(provider,status,message,notified,updated_at,"
            "resume_at,cool_downs) VALUES('bilibili','active','',0,'2026-10-28T00:00:00',0,3)"
        )

    class OkProvider:
        def upload(self, request, provider_config):
            return UploadResult(provider="bilibili", remote_id="BV1xx411c7mD", message="ok")

    manager._providers["bilibili"] = OkProvider()
    manager._process(manager._next_job())
    channel = manager.list_channels()[0]
    assert channel["cool_downs"] == 0 and channel["resume_at"] == 0


def test_old_channel_table_is_migrated_for_cooldown(tmp_path):
    """生产库是旧 schema：没有 resume_at/cool_downs，启动时要补列且不动老数据。"""
    db = tmp_path / "config" / "uploads.sqlite3"
    db.parent.mkdir(parents=True)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE upload_channels (provider TEXT PRIMARY KEY, "
            "status TEXT NOT NULL DEFAULT 'active', message TEXT NOT NULL DEFAULT '', "
            "notified INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO upload_channels VALUES('bilibili','blocked_auth','登录失效',1,"
            "'2026-10-27T16:23:00')"
        )

    manager = _mgr(tmp_path)
    with sqlite3.connect(manager.db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(upload_channels)")}
    assert {"resume_at", "cool_downs", "last_attempt_at"} <= columns
    channel = manager.list_channels()[0]
    assert channel["status"] == "blocked_auth"   # 老状态原样保留
    assert channel["message"] == "登录失效"
    assert channel["resume_at"] == 0 and channel["cool_downs"] == 0


def test_resume_from_blocked_auth_resets_notification(tmp_path):
    """登录失效恢复后要能重新通知（换了 cookie 又过期时不能静默）。"""
    manager = _mgr(tmp_path)
    with sqlite3.connect(manager.db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO upload_channels(provider,status,message,notified,updated_at,"
            "resume_at,cool_downs) VALUES('bilibili','blocked_auth','登录失效',1,"
            "'2026-10-28T00:00:00',0,2)"
        )
    assert manager.resume_channel("bilibili") is True
    channel = manager.list_channels()[0]
    assert channel["status"] == "active"
    assert channel["notified"] == 0 and channel["cool_downs"] == 0


def test_rate_limit_gives_back_last_attempt_so_backlog_survives(tmp_path):
    """生产实况：48 条已烧到 attempts=4/5 的任务，撞 406 要退回 4 而不是直接终态失败。"""
    from app.core.upload.models import UploadRateLimited

    output = tmp_path / "sample_merged.mp4"
    output.write_bytes(b"video")
    config = _config()
    config["uploads"]["max_attempts"] = 5
    manager = _mgr(tmp_path, config)
    state = {"status": "completed", "trigger": "auto", "summary": {"output_files": [str(output)]}}
    manager.enqueue_pose_outputs("last-chance", state)
    with sqlite3.connect(manager.db_path) as conn:
        conn.execute("UPDATE upload_jobs SET attempts=4, next_attempt_at=0")

    class LimitedProvider:
        def upload(self, request, provider_config):
            raise UploadRateLimited("Bilibili rate limited (code=406)", code=406)

    manager._providers["bilibili"] = LimitedProvider()
    manager._process(manager._next_job())

    job = manager.list_jobs()[0]
    assert job["status"] == "retrying"      # 不是 failed
    assert job["attempts"] == 4             # 最后一次机会被退回


def test_min_interval_paces_consecutive_uploads(tmp_path):
    """节奏门：上一单开始后没到最小间隔，下一单不取（生产 10:40 一分钟 4 单就是这么出事的）。"""
    from app.core.upload.models import UploadResult

    files = []
    for i in range(2):
        path = tmp_path / f"clip{i}_merged.mp4"
        path.write_bytes(b"video")
        files.append(str(path))
    config = _config()
    config["uploads"]["min_interval_seconds"] = 1800
    manager = _mgr(tmp_path, config)
    for i, path in enumerate(files):
        state = {"status": "completed", "trigger": "auto", "summary": {"output_files": [path]}}
        manager.enqueue_pose_outputs(f"pace-{i}", state)

    class OkProvider:
        def upload(self, request, provider_config):
            return UploadResult(provider="bilibili", remote_id="BV1xx411c7mD", message="ok")

    manager._providers["bilibili"] = OkProvider()
    first = manager._next_job()
    assert first is not None
    manager._process(first)
    # 上一单刚投过：30 分钟内不再取任务
    assert manager._next_job() is None
    # 把计时拨回 30 分钟前，下一单才放行
    with sqlite3.connect(manager.db_path) as conn:
        conn.execute("UPDATE upload_channels SET last_attempt_at=? WHERE provider='bilibili'", (time.time() - 1801,))
    second = manager._next_job()
    assert second is not None and second["id"] != first["id"]


def test_resume_channel_skips_interval_gate(tmp_path):
    """用户点「恢复上传」是显式动作，不能被节奏门再拖 30 分钟。"""
    output = tmp_path / "sample_merged.mp4"
    output.write_bytes(b"video")
    config = _config()
    config["uploads"]["min_interval_seconds"] = 1800
    manager = _mgr(tmp_path, config)
    state = {"status": "completed", "trigger": "auto", "summary": {"output_files": [str(output)]}}
    manager.enqueue_pose_outputs("skip-gate", state)

    assert manager._next_job() is not None          # 先占掉节奏门
    with sqlite3.connect(manager.db_path) as conn:
        conn.execute("UPDATE upload_jobs SET status='retrying', next_attempt_at=0")
    assert manager._next_job() is None              # 被节奏门挡住

    assert manager.resume_channel("bilibili") is True
    assert manager._next_job() is not None          # 恢复后立即放行


def test_channel_interval_config_and_floor(tmp_path):
    config = _config()
    config["uploads"]["min_interval_seconds"] = 900
    manager = _mgr(tmp_path, config)
    assert manager._channel_interval("bilibili") == 900
    # 渠道配置覆盖全局
    config["uploads"]["providers"]["bilibili"]["min_interval_seconds"] = 600
    assert manager._channel_interval("bilibili") == 600
    # 下限 60s：手误设 0/负数不能把风控打穿
    config["uploads"]["providers"]["bilibili"]["min_interval_seconds"] = 0
    assert manager._channel_interval("bilibili") == 60
    # 脏值回落默认
    config["uploads"]["providers"]["bilibili"]["min_interval_seconds"] = "abc"
    assert manager._channel_interval("bilibili") == 1800
