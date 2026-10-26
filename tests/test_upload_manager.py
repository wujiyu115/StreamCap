from __future__ import annotations
import os

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
