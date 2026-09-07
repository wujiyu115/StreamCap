"""人体识别产出计入录制分析：跨进程补记 + 幂等。

识别跑在真子进程里（PoseTaskManager 只负责拉起/监控），进程内的锁跨不过进程边界，
所以字节数只能由父进程在任务落终态后从 state.json 补记；重启后重扫不能重复计入。
"""
import json
import os

import pytest

from app.core.pose.pose_task_manager import PoseTaskManager


def write_task(root, name, **state):
    task_dir = os.path.join(root, "logs", "pose_tasks", name)
    os.makedirs(task_dir, exist_ok=True)
    with open(os.path.join(task_dir, "state.json"), "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    return os.path.join(task_dir, "state.json")


def read_state(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


DONE = {
    "status": "completed",
    "finished_at": "2026-09-07T20:00:00",
    "summary": {"output_bytes": 1_500, "deleted_bytes": 9_000},
}


@pytest.fixture
def manager(tmp_path):
    mgr = PoseTaskManager(str(tmp_path))
    yield mgr
    mgr._stop_worker = True


def test_finished_auto_task_is_accounted_once(tmp_path, manager):
    path = write_task(str(tmp_path), "task_20260907_200000", rec_id="rid-1", trigger="auto", **DONE)
    calls = []
    manager.set_analytics_sink(lambda *args: calls.append(args))

    assert [(c[0], c[2], c[3]) for c in calls] == [("rid-1", 1_500, 9_000)]
    assert read_state(path)["analytics_accounted"] is True

    manager._account_analytics()   # 重复扫描不应再记
    assert len(calls) == 1


def test_already_flagged_task_is_skipped_after_restart(tmp_path, manager):
    """模拟重启：内存里的去重集是空的，靠磁盘上的标记兜住。"""
    write_task(
        str(tmp_path), "task_20260907_200000", rec_id="rid-1", trigger="auto",
        analytics_accounted=True, **DONE,
    )
    calls = []
    manager.set_analytics_sink(lambda *args: calls.append(args))
    assert calls == []


def test_running_task_is_not_accounted(tmp_path, manager):
    path = write_task(
        str(tmp_path), "task_20260907_200000", rec_id="rid-1", trigger="auto",
        status="running", summary={"output_bytes": 5, "deleted_bytes": 5},
    )
    calls = []
    manager.set_analytics_sink(lambda *args: calls.append(args))

    assert calls == []
    assert "analytics_accounted" not in read_state(path)


def test_manual_task_without_rec_id_is_flagged_but_not_recorded(tmp_path, manager):
    """手动跑的识别可能横跨多个主播的文件，无从归属，只打标记别反复扫。"""
    path = write_task(str(tmp_path), "task_20260907_200000", trigger="manual", **DONE)
    calls = []
    manager.set_analytics_sink(lambda *args: calls.append(args))

    assert calls == []
    assert read_state(path)["analytics_accounted"] is True


def test_zero_byte_task_is_flagged_but_not_recorded(tmp_path, manager):
    path = write_task(
        str(tmp_path), "task_20260907_200000", rec_id="rid-1", trigger="auto",
        status="completed", finished_at="2026-09-07T20:00:00",
        summary={"output_bytes": 0, "deleted_bytes": 0},
    )
    calls = []
    manager.set_analytics_sink(lambda *args: calls.append(args))

    assert calls == []
    assert read_state(path)["analytics_accounted"] is True


def test_failed_sink_is_retried_next_round(tmp_path, manager):
    """回调抛错时不打标记，下一轮重试，避免一次抖动就永久丢数。"""
    path = write_task(str(tmp_path), "task_20260907_200000", rec_id="rid-1", trigger="auto", **DONE)
    attempts = []

    def flaky(*args):
        attempts.append(args)
        if len(attempts) == 1:
            raise RuntimeError("boom")

    manager.set_analytics_sink(flaky)
    assert len(attempts) == 1
    assert "analytics_accounted" not in read_state(path)

    manager._account_analytics()
    assert len(attempts) == 2
    assert read_state(path)["analytics_accounted"] is True


def test_timestamp_falls_back_to_started_at(tmp_path, manager):
    """缺 finished_at 时用 started_at，宁可归错一天也别丢数。"""
    write_task(
        str(tmp_path), "task_20260907_200000", rec_id="rid-1", trigger="auto",
        status="failed", started_at="2026-09-06T10:00:00",
        summary={"output_bytes": 10, "deleted_bytes": 20},
    )
    calls = []
    manager.set_analytics_sink(lambda *args: calls.append(args))

    from datetime import datetime

    assert calls[0][1] == datetime.fromisoformat("2026-09-06T10:00:00").timestamp()


def test_no_sink_means_no_flag_written(tmp_path, manager):
    """回调还没绑上时什么都不做，别把待记的任务标成已记。"""
    path = write_task(str(tmp_path), "task_20260907_200000", rec_id="rid-1", trigger="auto", **DONE)
    manager._account_analytics()
    assert "analytics_accounted" not in read_state(path)


def test_submit_carries_rec_id_into_spec(tmp_path, manager):
    """rec_id 必须进 spec.json，子进程每次写 state 都会带回来。"""
    captured = {}
    manager._spawn = lambda spec: captured.update(spec) or "task_x"

    manager.submit(videos=["/tmp/a.ts"], media_root="/tmp", params={}, trigger="auto", rec_id="rid-9")

    assert captured["rec_id"] == "rid-9"
    assert captured["trigger"] == "auto"


def test_submit_defaults_rec_id_to_none(tmp_path, manager):
    captured = {}
    manager._spawn = lambda spec: captured.update(spec) or "task_x"

    manager.submit(videos=["/tmp/a.ts"], media_root="/tmp", params={})

    assert captured["rec_id"] is None
