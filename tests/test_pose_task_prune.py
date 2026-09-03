"""pose_task_manager 过期任务目录清理测试。

跑法: .venv/bin/python tests/test_pose_task_prune.py
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.pose.pose_task_manager import PoseTaskManager


def _make_task(root, name, finished_at=None, status="completed"):
    d = os.path.join(root, name)
    os.makedirs(d, exist_ok=True)
    state = {"status": status}
    if finished_at:
        state["finished_at"] = finished_at
    with open(os.path.join(d, "state.json"), "w", encoding="utf-8") as f:
        json.dump(state, f)
    with open(os.path.join(d, "task.log"), "w", encoding="utf-8") as f:
        f.write("log")
    return d


class TestPrune(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def _manager(self):
        # 只初始化数据结构，不启动 worker/adopt（构造函数副作用太多）
        mgr = PoseTaskManager.__new__(PoseTaskManager)
        mgr.tasks_root = os.path.join(self.tmp, "pose_tasks")
        os.makedirs(mgr.tasks_root, exist_ok=True)
        return mgr

    def test_old_tasks_pruned(self):
        mgr = self._manager()
        old = (datetime.now() - timedelta(days=10)).isoformat()
        # keep_recent 地板 = 20：超出的旧任务被删，最近 20 个保留
        for i in range(22):
            _make_task(mgr.tasks_root, f"task_20260101_{i:06d}", finished_at=old)
        mgr._prune_old_tasks()
        remaining = os.listdir(mgr.tasks_root)
        self.assertEqual(len(remaining), 20)

    def test_recent_tasks_kept(self):
        mgr = self._manager()
        recent = datetime.now().isoformat()
        d = _make_task(mgr.tasks_root, "task_20260101_000000", finished_at=recent)
        mgr._prune_old_tasks()
        self.assertTrue(os.path.isdir(d))

    def test_keep_recent_floor(self):
        """即使超过 7 天，最近 20 个也保留。"""
        mgr = self._manager()
        old = (datetime.now() - timedelta(days=30)).isoformat()
        for i in range(25):
            _make_task(mgr.tasks_root, f"task_20260101_{i:06d}", finished_at=old)
        mgr._prune_old_tasks()
        remaining = os.listdir(mgr.tasks_root)
        self.assertEqual(len(remaining), 20)

    def test_running_never_pruned(self):
        mgr = self._manager()
        old = (datetime.now() - timedelta(days=30)).isoformat()
        d = _make_task(mgr.tasks_root, "task_20260101_000000", finished_at=old, status="running")
        mgr._prune_old_tasks()
        self.assertTrue(os.path.isdir(d))

    def test_no_finished_at_kept(self):
        """没有 finished_at 的目录（异常遗留）不删——信息不明时保守。"""
        mgr = self._manager()
        d = _make_task(mgr.tasks_root, "task_20260101_000000")
        mgr._prune_old_tasks()
        self.assertTrue(os.path.isdir(d))

    def test_corrupt_state_kept(self):
        mgr = self._manager()
        d = _make_task(mgr.tasks_root, "task_20260101_000000")
        with open(os.path.join(d, "state.json"), "w") as f:
            f.write("not json")
        mgr._prune_old_tasks()
        self.assertTrue(os.path.isdir(d))

    def test_non_task_dirs_ignored(self):
        mgr = self._manager()
        other = os.path.join(mgr.tasks_root, "random_dir")
        os.makedirs(other)
        mgr._prune_old_tasks()
        self.assertTrue(os.path.isdir(other))


if __name__ == "__main__":
    unittest.main()
