"""主播名清洗后为空（纯 emoji/纯标点）时录制路径构造的回归测试。

生产事故：主播名只有一个 emoji（如 "🐾"），clean_name 不带 default 时返回
None，_get_output_dir 里 os.path.join(dir, None) 抛 TypeError，
start_recording 在 ffmpeg 启动前崩溃且异常被 run_coro fire-and-forget 吞掉
（analytics 表现为 starts=1、seconds/files/bytes 全 0）。
"""

from types import SimpleNamespace

from app.core.recording.stream_manager import LiveStreamRecorder

LANG = {"recording_manager": {}, "stream_manager": {"live_room": "直播间"}}
FOLDER_DEFAULTS = {
    "folder_name_platform": True,
    "folder_name_author": True,
    "folder_name_time": False,
    "folder_name_title": False,
}


def make_recorder(streamer_name, anchor_name="某主播", tmp_path=None):
    settings = SimpleNamespace(
        user_config=dict(FOLDER_DEFAULTS),
        accounts_config={},
        cookies_config={},
    )
    services = SimpleNamespace(
        settings_config=settings,
        subprocess_start_up_info=None,
        language_manager=SimpleNamespace(language=LANG),
        recording_manager=SimpleNamespace(persist_recordings=lambda: None),
        run_coro=lambda coro: None,
    )
    info = {
        "platform_key": "douyin",
        "platform": "抖音直播",
        "live_url": "https://live.douyin.com/x",
        "output_dir": str(tmp_path or "/tmp/streamcap_test_output"),
        "streamer_name": streamer_name,
    }
    return LiveStreamRecorder(
        services,
        SimpleNamespace(streamer_name=streamer_name, recording_dir=None),
        info,
    )


def make_stream_info(anchor_name):
    return SimpleNamespace(anchor_name=anchor_name, title="标题", platform="抖音")


def test_pure_emoji_name_falls_back_to_placeholder(tmp_path):
    """开关开 + 纯 emoji 主播名：清洗后为空，回落到占位名，不再抛 TypeError。"""
    rec = make_recorder("🐾", tmp_path=tmp_path)
    rec.user_config["remove_emojis"] = True
    si = make_stream_info("🐾")
    filename = rec._get_filename(si)
    output_dir = rec._get_output_dir(si)
    save_path = rec._get_save_path(filename)

    assert si.anchor_name == "直播间"
    assert "直播间" in output_dir
    assert isinstance(output_dir, str) and output_dir  # os.path.join 不再收到 None
    assert "直播间" in save_path or "2026-" in save_path
    assert filename  # 文件名非空（至少含时间戳）


def test_pure_punctuation_name_falls_back(tmp_path):
    """纯标点主播名（clean_name 的正则字符集）不受开关影响，清洗为空回落占位名。"""
    rec = make_recorder("？？？", tmp_path=tmp_path)
    si = make_stream_info("？？？")
    rec._get_filename(si)
    output_dir = rec._get_output_dir(si)

    assert si.anchor_name == "直播间"
    assert "直播间" in output_dir


def test_normal_name_unchanged(tmp_path):
    """普通名字不受影响：清洗后原样用于目录与文件名（clean_name 会折叠连续下划线）。"""
    rec = make_recorder("燕儿__健身教练", tmp_path=tmp_path)
    si = make_stream_info("别的名字")
    filename = rec._get_filename(si)

    assert si.anchor_name == "燕儿_健身教练"
    assert "燕儿_健身教练" in rec._get_output_dir(si)
    assert filename.startswith("燕儿_健身教练_2026-")


def test_api_anchor_pure_emoji_falls_back_when_setting_on(tmp_path):
    """任务名为空 + API 返回纯 emoji 名 + 开关开：else 分支同样回落占位名。"""
    rec = make_recorder("", tmp_path=tmp_path)
    rec.user_config["remove_emojis"] = True
    si = make_stream_info("🐾")
    rec._get_filename(si)
    output_dir = rec._get_output_dir(si)

    assert si.anchor_name == "直播间"
    assert "直播间" in output_dir


def test_emoji_kept_in_filename_when_setting_off(tmp_path):
    """remove_emojis 关闭（默认）：文件/目录名保留 emoji。"""
    rec = make_recorder("桃桃🍎满枝", tmp_path=tmp_path)
    rec.user_config["remove_emojis"] = False
    si = make_stream_info("桃桃🍎满枝")
    filename = rec._get_filename(si)

    assert si.anchor_name == "桃桃🍎满枝"
    assert "桃桃🍎满枝" in rec._get_output_dir(si)
    assert filename.startswith("桃桃🍎满枝_2026-")


def test_emoji_removed_in_filename_when_setting_on(tmp_path):
    """remove_emojis 开启：文件/目录名剥掉 emoji（emoji 位替换为 _，旧行为）。"""
    rec = make_recorder("桃桃🍎满枝", tmp_path=tmp_path)
    rec.user_config["remove_emojis"] = True
    si = make_stream_info("桃桃🍎满枝")
    filename = rec._get_filename(si)

    assert si.anchor_name == "桃桃_满枝"
    assert "桃桃_满枝" in rec._get_output_dir(si)
    assert "🍎" not in filename


def test_pure_emoji_name_falls_back_even_when_setting_off(tmp_path):
    """remove_emojis 关闭时纯 emoji 名就是合法文件名，不再回落占位名。"""
    rec = make_recorder("🐾", tmp_path=tmp_path)
    rec.user_config["remove_emojis"] = False
    si = make_stream_info("🐾")
    filename = rec._get_filename(si)

    assert si.anchor_name == "🐾"
    assert "🐾" in rec._get_output_dir(si)
    assert filename.startswith("🐾_2026-")


def test_title_emoji_follows_same_setting(tmp_path):
    """标题（filename_includes_title 开时）的 emoji 同样受开关控制。"""
    rec = make_recorder("某主播", tmp_path=tmp_path)
    rec.user_config["filename_includes_title"] = True
    rec.user_config["remove_emojis"] = False
    si = make_stream_info("某主播")
    si.title = "健身💪训练"
    filename = rec._get_filename(si)

    assert "💪" in filename

    rec.user_config["remove_emojis"] = True
    si2 = make_stream_info("某主播")
    si2.title = "健身💪训练"
    filename2 = rec._get_filename(si2)

    assert "💪" not in filename2
