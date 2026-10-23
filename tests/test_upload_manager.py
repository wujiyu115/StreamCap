from __future__ import annotations

import sqlite3

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
