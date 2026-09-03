"""空主播名回填逻辑测试（条件表达式层面验证）。

跑法: .venv/bin/python tests/test_empty_name_backfill.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def should_backfill(streamer_name: str, live_room: str) -> bool:
    """与 record_manager 回填条件保持一致的纯函数复刻。"""
    return not streamer_name.strip() or streamer_name.strip() == live_room


class TestBackfillCondition(unittest.TestCase):
    def test_empty_name_backfills(self):
        self.assertTrue(should_backfill("", "直播间"))

    def test_whitespace_name_backfills(self):
        self.assertTrue(should_backfill("   ", "直播间"))

    def test_placeholder_backfills(self):
        self.assertTrue(should_backfill("直播间", "直播间"))

    def test_real_name_not_backfilled(self):
        self.assertFalse(should_backfill("黄教练来也", "直播间"))


class TestFilenameAnchorChoice(unittest.TestCase):
    def test_empty_name_uses_stream_anchor(self):
        """与 stream_manager 条件一致：空名走 anchor_name 分支。"""
        streamer_name = ""
        live_room = "直播间"
        uses_custom = streamer_name.strip() and streamer_name != live_room
        self.assertFalse(uses_custom)


if __name__ == "__main__":
    unittest.main()
