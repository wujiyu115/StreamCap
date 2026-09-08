"""提交/停止识别任务不能卡住事件循环。

submit 要 os.walk 整个目录（NAS 共享）、跑 fuser 子进程、没句柄工具时还要
sleep 采样 mtime；stop 要等子进程退出最多 13s。这些如果直接在 async 处理器里
跑，整个 WebUI 会冻到接口返回为止（所有其他请求都排在同一个事件循环上）。

跑法: .venv/bin/python -m pytest tests/test_pose_submit_nonblocking.py -v
"""

import asyncio
import os
import sys
from types import SimpleNamespace
from unittest import mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.pose import file_watch
from app.server.routers.pose import stop_task, submit_task
from app.server.schemas import PoseTaskSubmitRequest

# 处理器阻塞事件循环时，心跳一次都跑不了；正常应该跑掉几十次
HEARTBEAT_INTERVAL = 0.01
MIN_TICKS = 5


class _FakeManager:
    def __init__(self):
        self.submitted = None

    def submit(self, **kwargs):
        self.submitted = kwargs
        return {"task_id": "task_x", "status": "running"}

    def stop(self):
        import time

        time.sleep(0.3)  # 冒充等子进程退出
        return {"status": "stopping"}


def _ctx(tmp_path, count=6):
    for i in range(count):
        (tmp_path / f"v{i}.mp4").write_bytes(b"x" * 128)
    manager = _FakeManager()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(pose_task_manager=manager)))
    services = SimpleNamespace(
        settings_config=SimpleNamespace(
            get_video_save_path=lambda: str(tmp_path),
            user_config={"pose_detection": {}},
        )
    )
    return manager, request, services


async def _with_heartbeat(coro):
    """跑 coro，同时数事件循环还能推进多少次心跳。"""
    ticks = 0

    async def beat():
        nonlocal ticks
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            ticks += 1

    beater = asyncio.create_task(beat())
    await asyncio.sleep(0)  # 让心跳先起来
    try:
        result = await coro
    finally:
        beater.cancel()
    return result, ticks


def test_submit_does_not_block_event_loop(tmp_path):
    manager, request, services = _ctx(tmp_path)
    body = PoseTaskSubmitRequest(paths=[""], trigger="manual")

    # 无 fuser/lsof 的容器：走 mtime 采样这条会 sleep 的路
    with mock.patch.object(file_watch, "_has_open_handle", return_value=None), \
         mock.patch.object(file_watch, "MTIME_SAMPLE_INTERVAL", 0.3):
        result, ticks = asyncio.run(_with_heartbeat(submit_task(request, body, "u", services)))

    assert result["status"] == "running"
    assert len(manager.submitted["videos"]) == 6
    assert ticks >= MIN_TICKS, f"提交期间事件循环被占住了（只跑了 {ticks} 次心跳）"


def test_stop_does_not_block_event_loop(tmp_path):
    _, request, services = _ctx(tmp_path, count=0)

    result, ticks = asyncio.run(_with_heartbeat(stop_task(request, "task_x", "u")))

    assert result["status"] == "stopping"
    assert ticks >= MIN_TICKS, f"停止期间事件循环被占住了（只跑了 {ticks} 次心跳）"


def test_submit_rejects_files_still_being_written(tmp_path):
    _, request, services = _ctx(tmp_path, count=2)
    body = PoseTaskSubmitRequest(paths=[""], trigger="manual")

    from fastapi import HTTPException

    with mock.patch.object(file_watch, "_has_open_handle", return_value=True):
        with pytest.raises(HTTPException) as e:
            asyncio.run(submit_task(request, body, "u", services))

    assert e.value.status_code == 400
    assert "v0.mp4" in e.value.detail and "v1.mp4" in e.value.detail
