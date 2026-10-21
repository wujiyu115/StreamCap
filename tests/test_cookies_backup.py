"""Cookie 覆盖前备份：写坏了还能回滚。

cookies.json 里是各平台登录态（抖音那份手工换起来最麻烦），而写入路径有三条
（设置页 PUT / 扫码登录回填 / 初始化），任何一条写错都会把当前那份冲掉。
生产上就出过一次：有效 Cookie 被覆盖，只能从别处找回来。
所以覆盖前留一份带时间戳的备份，并且——只备份「看着有效」的当前内容。
"""
import asyncio
import json
import os

from app.core.config import config_manager as cm_module
from app.core.config.config_manager import ConfigManager


def save(cm, cookies):
    asyncio.run(cm.save_cookies_config(cookies))


def read_json(path):
    with open(path, encoding="utf-8") as file:
        return json.load(file)


def backups(cm):
    if not os.path.isdir(cm.cookies_backup_dir):
        return []
    return sorted(os.listdir(cm.cookies_backup_dir))


def test_first_save_on_fresh_install_makes_no_backup(tmp_path):
    """初始 cookies.json 是 {}：没有可备份的东西，别造一个空备份。"""
    cm = ConfigManager(str(tmp_path))
    save(cm, {"douyin": "sessionid=abc"})
    assert backups(cm) == []
    assert read_json(cm.cookies_config_path) == {"douyin": "sessionid=abc"}


def test_overwrite_backs_up_the_previous_cookies(tmp_path):
    cm = ConfigManager(str(tmp_path))
    save(cm, {"douyin": "sessionid=good; ttwid=1"})
    save(cm, {"douyin": "sessionid=new", "bilibili": "B"})

    files = backups(cm)
    assert len(files) == 1 and files[0].startswith("cookies-") and files[0].endswith(".json")
    # 备份里是「被覆盖掉的那一份」，线上文件已是新值
    assert read_json(os.path.join(cm.cookies_backup_dir, files[0])) == {"douyin": "sessionid=good; ttwid=1"}
    assert read_json(cm.cookies_config_path) == {"douyin": "sessionid=new", "bilibili": "B"}


def test_save_only_keeps_cookie_keys_intact(tmp_path):
    """备份是整份文件（其它平台键也一起留在备份里），不是只挑 douyin。"""
    cm = ConfigManager(str(tmp_path))
    save(cm, {"douyin": "sessionid=good", "bilibili": "B", "huya": ""})
    save(cm, {"douyin": "sessionid=new"})
    files = backups(cm)
    assert read_json(os.path.join(cm.cookies_backup_dir, files[0])) == {
        "douyin": "sessionid=good",
        "bilibili": "B",
        "huya": "",
    }


def test_empty_write_never_rotates_a_good_backup(tmp_path):
    """误写成空值/半残值不能把好备份轮换掉——否则备份等于没做。

    唯一会轮换备份的是「当前文件里还有个非空 Cookie」，所以一次写坏之后
    接着又写坏几次，那份好备份依旧躺在那儿。
    """
    cm = ConfigManager(str(tmp_path))
    save(cm, {"douyin": "sessionid=good"})
    assert backups(cm) == []  # 首写无备份（原文件是 {}）

    save(cm, {})  # 模拟一次写坏：备份拿到的正是被覆盖掉的好值
    save(cm, {"douyin": "   "})  # 再写坏一次：当前值已非有效，不再轮换
    save(cm, {"douyin": ""})

    files = backups(cm)
    assert len(files) == 1
    assert read_json(os.path.join(cm.cookies_backup_dir, files[0])) == {"douyin": "sessionid=good"}


def test_broken_json_is_skipped_and_save_still_works(tmp_path):
    cm = ConfigManager(str(tmp_path))
    with open(cm.cookies_config_path, "w", encoding="utf-8") as file:
        file.write("{ not json ")

    save(cm, {"douyin": "sessionid=new"})  # 不抛异常
    assert backups(cm) == []
    assert read_json(cm.cookies_config_path) == {"douyin": "sessionid=new"}


def test_backup_dir_keeps_only_the_latest_five(tmp_path, monkeypatch):
    """保留最近 5 份：备份只在真出问题时用得到，留太多反而没人看。"""
    cm = ConfigManager(str(tmp_path))
    stamp = iter(f"2026010{i}-000000" for i in range(1, 10))

    class FakeTime:
        @staticmethod
        def strftime(_fmt):
            return next(stamp)

        @staticmethod
        def time():
            return 0.0

    monkeypatch.setattr(cm_module, "time", FakeTime)

    save(cm, {"douyin": "v0"})
    for i in range(1, 9):
        save(cm, {"douyin": f"v{i}"})
        # 备份是「上一个值」：第 i 次写入备份的是 v(i-1)

    files = backups(cm)
    assert len(files) == ConfigManager.COOKIES_BACKUP_KEEP
    assert files == sorted(files)  # 按时间戳升序 = 文件名排序
    assert files[0].startswith("cookies-20260104")  # 最老的 3 份被删了
    assert read_json(os.path.join(cm.cookies_backup_dir, files[-1])) == {"douyin": "v7"}


def test_same_second_writes_do_not_collapse_into_one_file(tmp_path, monkeypatch):
    """时间戳只到秒的话，同一秒内的两次写入会落到同一文件名、把前一份盖掉。"""
    cm = ConfigManager(str(tmp_path))
    millis = iter([0, 1, 2])

    class FakeTime:
        @staticmethod
        def strftime(_fmt):
            return "20260101-000000"

        @staticmethod
        def time():
            return next(millis) / 1000.0

    monkeypatch.setattr(cm_module, "time", FakeTime)

    save(cm, {"douyin": "v0"})
    save(cm, {"douyin": "v1"})
    save(cm, {"douyin": "v2"})

    files = backups(cm)
    assert len(files) == 2  # 三份 cron：{} → v0 → v1 → v2，后两次写入各备一份
    assert [read_json(os.path.join(cm.cookies_backup_dir, name)) for name in files] == [
        {"douyin": "v0"},
        {"douyin": "v1"},
    ]
    assert read_json(cm.cookies_config_path) == {"douyin": "v2"}


def test_prune_leaves_foreign_files_alone(tmp_path):
    cm = ConfigManager(str(tmp_path))
    os.makedirs(cm.cookies_backup_dir, exist_ok=True)
    foreign = os.path.join(cm.cookies_backup_dir, "notes.txt")
    with open(foreign, "w", encoding="utf-8") as file:
        file.write("keep me")

    for i in range(1, 9):
        with open(
            os.path.join(cm.cookies_backup_dir, f"cookies-2026010{i}-000000.json"), "w", encoding="utf-8"
        ) as file:
            file.write("{}")

    cm._prune_cookies_backups()
    names = backups(cm)
    assert names.count("cookies-20260101-000000.json") == 0
    assert os.path.exists(foreign)


def test_no_temp_files_left_behind(tmp_path):
    cm = ConfigManager(str(tmp_path))
    save(cm, {"douyin": "sessionid=good"})
    save(cm, {"douyin": "sessionid=new"})
    assert [n for n in backups(cm) if n.startswith(".backup_")] == []