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
