"""抖音投稿渠道（浏览器自动化）的纯逻辑单测：不真开浏览器，`page_runner` 注入桩。"""

from __future__ import annotations

import pytest

from app.core.upload.manager import UploadManager
from app.core.upload.models import UploadAuthError, UploadRequest
from app.core.upload.providers.douyin import (
    DouyinProvider,
    build_caption,
    detect_auth,
    detect_failure,
    detect_rate_limit,
    extract_aweme_id,
    has_login_cookie,
    parse_cookie_string,
)

def _atom(kind: bytes, payload: bytes = b"") -> bytes:
    """构造一个顶层 mp4 atom（size + type + payload）。"""
    return (len(payload) + 8).to_bytes(4, "big") + kind + payload


#: 标准 mp4：完整 moov 在 mdat 前面（`-movflags +faststart` 重封装后的样子）
_STANDARD_MP4 = _atom(b"ftyp", b"isomisom") + _atom(b"moov", b"\x00" * 32) + _atom(b"mdat", b"\x00" * 64)
#: 直录的分段式 mp4：`+empty_moov` 写的是空壳 moov，轨道信息在 moof 分片里
_FRAGMENTED_MP4 = (
    _atom(b"ftyp", b"isomisom")
    + _atom(b"moov", b"")
    + _atom(b"moof", b"\x00" * 16)
    + _atom(b"mdat", b"\x00" * 64)
)


def _write(tmp_path, name, payload):
    path = tmp_path / name
    path.write_bytes(payload)
    return str(path)


def _config(**overrides):
    config = {"enabled": True, "title_prefix": "", "description": "", "tags": "", "only_self": True}
    config.update(overrides)
    return config


def test_parse_cookie_keeps_url_encoded_sid_guard():
    raw = "sessionid=abc123; ttwid=t0; sid_guard=abc123%7C1750000000%7C5184000%7CFri%2C+10+Oct+2026+00%3A00%3A00+GMT"
    cookies = parse_cookie_string(raw)
    names = [item["name"] for item in cookies]
    assert names == ["sessionid", "ttwid", "sid_guard"]
    # 值保持原样：解码后反而可能和平台记录的字段对不上
    assert cookies[2]["value"].startswith("abc123%7C1750000000")
    assert cookies[0]["domain"] == ".douyin.com"
    assert all(item["path"] == "/" for item in cookies)


def test_has_login_cookie_needs_session_id():
    assert has_login_cookie(parse_cookie_string("sessionid=abc"))
    assert has_login_cookie(parse_cookie_string("sid_tt=abc"))
    # 只有公共 ttwid：关注列表和创作者中心都会判未登录
    assert not has_login_cookie(parse_cookie_string("ttwid=t0; odin_tt=x"))


def test_build_caption_truncates_title_and_appends_hashtags():
    request = UploadRequest("job", ["/tmp/某主播_2026-10-30_20-00-00_000_merged.mp4"], "标题" * 20)
    title, description = build_caption(request, _config(title_prefix="【录播】", tags="直播录像, #录播", description="简介A"))
    assert title.startswith("【录播】")
    assert len(title) <= 30
    assert description == "简介A #直播录像 #录播"

    # 没有标题时回落到文件名
    empty = UploadRequest("job", ["/tmp/fallback_name.mp4"], "")
    assert build_caption(empty, _config())[0] == "fallback_name"


def test_detect_helpers():
    assert detect_auth("请先登录后再进行操作") is True
    assert detect_auth("作品发布成功") is False
    assert detect_rate_limit("操作过于频繁，请稍后再试") == (True, False)
    assert detect_rate_limit("请完成滑块验证") == (True, True)
    assert detect_rate_limit("作品发布成功") is None
    assert detect_failure("视频上传失败，请重试") == "视频上传失败"
    assert detect_failure("作品发布成功") is None
    assert extract_aweme_id("https://www.douyin.com/video/7412345678901234567") == "7412345678901234567"
    assert extract_aweme_id("aweme_id=7412345678901234567") == "7412345678901234567"
    assert extract_aweme_id("https://creator.douyin.com/creator-micro/content/manage") is None


def test_moov_check_rejects_fragmented_mp4(tmp_path):
    assert DouyinProvider._looks_like_standard_mp4(_write(tmp_path, "ok.mp4", _STANDARD_MP4)) is True
    # 直录的分段式 mp4（头部空壳 moov + moof 分片）投不了：光看「有没有 moov」会误判
    assert DouyinProvider._looks_like_standard_mp4(_write(tmp_path, "frag.mp4", _FRAGMENTED_MP4)) is False
    # moov 落在 1MB 之后（非 faststart）也不合格：抖音需要可 seek 的标准封装
    late_moov = _atom(b"ftyp", b"isomisom") + _atom(b"mdat", b"\x00" * (1024 * 1024)) + _atom(b"moov", b"")
    assert DouyinProvider._looks_like_standard_mp4(_write(tmp_path, "late.mp4", late_moov)) is False
    assert DouyinProvider._looks_like_standard_mp4(_write(tmp_path, "junk.mp4", b"not-a-video")) is False
    assert DouyinProvider._looks_like_standard_mp4(str(tmp_path / "nope.mp4")) is False


def test_upload_requires_single_file(tmp_path):
    provider = DouyinProvider(str(tmp_path))
    path = _write(tmp_path, "ok.mp4", _STANDARD_MP4)
    with pytest.raises(RuntimeError, match="只能一个视频"):
        provider.upload(UploadRequest("job", [path, path], "标题"), _config())


def test_upload_raises_auth_error_without_login_cookie(tmp_path):
    path = _write(tmp_path, "ok.mp4", _STANDARD_MP4)
    request = UploadRequest("job", [path], "标题")

    with pytest.raises(UploadAuthError, match="未配置"):
        DouyinProvider(str(tmp_path), cookie_getter=lambda: "").upload(request, _config())

    # 只有 ttwid 的公共 cookie 打不开创作者中心
    with pytest.raises(UploadAuthError, match="缺少登录态"):
        DouyinProvider(str(tmp_path), cookie_getter=lambda: "ttwid=t0").upload(request, _config())


def test_upload_delegates_to_page_runner_and_returns_aweme_id(tmp_path):
    path = _write(tmp_path, "ok.mp4", _STANDARD_MP4)
    seen = {}

    def runner(cookies, file_path, title, description, config):
        seen.update(cookies=len(cookies), path=file_path, title=title, description=description)
        return "7412345678901234567"

    provider = DouyinProvider(
        str(tmp_path),
        cookie_getter=lambda: "sessionid=abc; ttwid=t0",
        page_runner=runner,
    )
    result = provider.upload(
        UploadRequest("job", [path], "某主播 录播"),
        _config(tags="录播"),
    )
    assert result.provider == "douyin"
    assert result.remote_id == "7412345678901234567"
    assert seen["path"] == path and seen["cookies"] == 2
    assert seen["title"] == "某主播 录播" and seen["description"] == "#录播"


def test_manager_splits_douyin_jobs_one_video_each(tmp_path):
    """渠道配额来自 ``supports_multi_parts``：抖音恒为 1，B 站跟随 max_pages_per_job。"""
    config = {
        "uploads": {
            "enabled": True,
            "auto_after_pose": True,
            "max_attempts": 2,
            "min_interval_seconds": 1800,
            "max_pages_per_job": 5,
            "providers": {
                "bilibili": {"enabled": True, "tid": 138},
                "douyin": {"enabled": True, "only_self": True},
            },
        }
    }
    manager = UploadManager(str(tmp_path), lambda: config)
    manager._stop.set()
    manager._worker.join(timeout=3)
    manager._providers["douyin"] = DouyinProvider(str(tmp_path), cookie_getter=lambda: "")

    files = [_write(tmp_path, f"part{i}.mp4", _STANDARD_MP4) for i in range(1, 4)]
    state = {"status": "completed", "trigger": "auto", "summary": {"output_files": files}}
    assert manager.enqueue_pose_outputs("task-x", state) == 4
    douyin = [job for job in manager.list_jobs() if job["provider"] == "douyin"]
    assert len(douyin) == 3
    assert all(len(job["files"]) == 1 for job in douyin)