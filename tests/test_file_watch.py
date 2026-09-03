"""file_watch 就绪判定测试。

跑法: .venv/bin/python -m pytest tests/test_file_watch.py -v
"""

import os
import subprocess
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.pose import file_watch
from app.core.pose.file_watch import is_file_ready, wait_until_ready


class _Log:
    def __init__(self):
        self.messages = []

    def info(self, msg):
        self.messages.append(("info", msg))

    def warning(self, msg):
        self.messages.append(("warning", msg))


class TestIsFileReady(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "video.mp4")
        with open(self.path, "wb") as f:
            f.write(b"x" * 1024)

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_missing_file_not_ready(self):
        self.assertFalse(is_file_ready(os.path.join(self.tmp, "nope.mp4")))

    def test_no_handle_ready(self):
        with mock.patch.object(file_watch, "_has_open_handle", return_value=False):
            self.assertTrue(is_file_ready(self.path))

    def test_open_handle_not_ready(self):
        with mock.patch.object(file_watch, "_has_open_handle", return_value=True):
            self.assertFalse(is_file_ready(self.path))

    def test_fallback_mtime_sample_no_tool(self):
        # 无句柄工具：文件静止，两轮采样一致 → 就绪
        with mock.patch.object(file_watch, "_has_open_handle", return_value=None):
            self.assertTrue(is_file_ready(self.path))


class TestWaitUntilReady(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.tmp = tempfile.mkdtemp()
        self.log = _Log()

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make(self, name):
        p = os.path.join(self.tmp, name)
        with open(p, "wb") as f:
            f.write(b"x" * 1024)
        return p

    def test_all_ready_immediately(self):
        a = self._make("a.mp4")
        b = self._make("b.mp4")
        with mock.patch.object(file_watch, "_has_open_handle", return_value=False):
            ready, abandoned = wait_until_ready([a, b], self.log)
        self.assertEqual(sorted(ready), sorted([a, b]))
        self.assertEqual(abandoned, [])

    def test_busy_file_stays_pending_then_ready(self):
        """句柄被持有时保持等待，句柄释放后转就绪。"""
        busy = self._make("busy.mp4")
        handle_states = iter([True, True, False])

        def fake_handle(path):
            return next(handle_states)

        with mock.patch.object(file_watch, "_has_open_handle", side_effect=fake_handle):
            ready, abandoned = wait_until_ready([busy], self.log, poll_interval=0.1)

        self.assertEqual(ready, [busy])
        self.assertEqual(abandoned, [])

    def test_stop_check_aborts(self):
        busy = self._make("busy.mp4")
        with mock.patch.object(file_watch, "_has_open_handle", return_value=True):
            ready, abandoned = wait_until_ready([busy], self.log, stop_check=lambda: True, poll_interval=0.1)
        self.assertEqual(ready, [])
        self.assertEqual(abandoned, [])

    def test_missing_file_abandoned_after_stale(self):
        """文件一直不出现，超过防御阈值后放弃。"""
        missing = os.path.join(self.tmp, "never.mp4")
        with mock.patch.object(file_watch, "STALE_ABANDON_SECONDS", 0.05):
            ready, abandoned = wait_until_ready([missing], self.log, poll_interval=0.1)
        self.assertEqual(ready, [])
        self.assertEqual(abandoned, [missing])
        self.assertTrue(any(level == "warning" for level, _ in self.log.messages))

    def test_pending_report_callback(self):
        """on_pending 回调拿到未就绪文件名（basename）。"""
        busy = self._make("busy.mp4")
        reports = []

        handle_states = iter([True, False])

        def fake_handle(path):
            try:
                return next(handle_states)
            except StopIteration:
                return False

        with mock.patch.object(file_watch, "_has_open_handle", side_effect=fake_handle):
            ready, _ = wait_until_ready(
                [busy], self.log, poll_interval=0.1, on_pending=lambda names: reports.append(list(names))
            )
        self.assertEqual(ready, [busy])
        self.assertEqual(reports, [["busy.mp4"]])


class TestHandleDetectionReal(unittest.TestCase):
    """真实 fuser/lsof 行为（环境无工具则跳过）。"""

    def setUp(self):
        if file_watch._FUSER is None and file_watch._LSOF is None:
            self.skipTest("no fuser/lsof available")

    def test_held_file_detected(self):
        proc = subprocess.Popen(
            ["sleep", "30"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            # 用 /proc/<pid>/fd 不可移植，改为直接探测 sleep 进程自身无法挂文件——
            # 用 python 持有句柄的方式
            proc.kill()
            proc.wait()

            import tempfile

            with tempfile.NamedTemporaryFile(delete=False) as f:
                path = f.name
                f.write(b"x")
            holder = subprocess.Popen(
                [sys.executable, "-c", f"import time; f=open(r'{path}','r'); time.sleep(30)"]
            )
            try:
                deadline = time.time() + 5
                result = None
                while time.time() < deadline:
                    result = file_watch._has_open_handle(path)
                    if result is True:
                        break
                    time.sleep(0.2)
                self.assertEqual(result, True)
            finally:
                holder.kill()
                holder.wait()
                os.unlink(path)
        finally:
            if proc.poll() is None:
                proc.kill()


if __name__ == "__main__":
    unittest.main()
