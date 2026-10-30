"""抖音投稿渠道（浏览器自动化）的纯逻辑单测：不真开浏览器，`page_runner` 注入桩。"""

from __future__ import annotations

import os

import pytest

from app.core.upload.manager import UploadManager
from app.core.upload.models import UploadAuthError, UploadRequest
from app.core.upload.providers import douyin
from app.core.upload.providers.douyin import (
    DouyinProvider,
    build_caption,
    detect_auth,
    detect_failure,
    detect_rate_limit,
    detect_transient,
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
    # 编辑页音乐区块常年挂着「重新上传」链接，误当失败会让每一单都判「上传失败」
    assert detect_failure("添加音乐重新上传") is None
    assert detect_failure("上传失败，请重新上传") == "上传失败"
    # 抖音的服务端/网络抖动自带「请稍后再试」，不能被当成限流把渠道冻 6 小时
    assert detect_transient("服务器/网络开小差了，请稍后再试～") == "服务器/网络开小差了"
    assert detect_transient("操作过于频繁，请稍后再试") is None
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


def test_upload_remuxes_fragmented_mp4_then_deletes_temp(tmp_path, monkeypatch):
    """直录的分片式 mp4 不该直接拒投：先无损重封装，投完把临时文件删掉。"""
    source = _write(tmp_path, "frag.mp4", _FRAGMENTED_MP4)
    remuxed = tmp_path / "frag.faststart.mp4"
    calls = []

    def fake_remux(path, timeout=0):
        calls.append(path)
        remuxed.write_bytes(_STANDARD_MP4)
        return str(remuxed)

    monkeypatch.setattr(douyin, "remux_to_standard_mp4", fake_remux)
    seen = {}

    def runner(cookies, file_path, title, description, config):
        seen["path"] = file_path
        return None

    provider = DouyinProvider(
        str(tmp_path), cookie_getter=lambda: "sessionid=abc", page_runner=runner
    )
    result = provider.upload(UploadRequest("job", [source], "标题"), _config())

    assert calls == [source]
    assert seen["path"] == str(remuxed)  # 投的是重封装后的文件
    assert not remuxed.exists()  # 投完删掉，不在下载盘里堆重复文件
    assert "抖音投稿完成" in result.message
    # 源文件原样保留
    assert (tmp_path / "frag.mp4").read_bytes() == _FRAGMENTED_MP4


def test_upload_skips_remux_for_standard_mp4(tmp_path, monkeypatch):
    """标准 mp4 不该多跑一次 ffmpeg（快速招几 GB 的拷贝不必要）。"""
    monkeypatch.setattr(
        douyin, "remux_to_standard_mp4", lambda *a, **k: pytest.fail("标准 mp4 不该重封装")
    )
    source = _write(tmp_path, "ok.mp4", _STANDARD_MP4)
    seen = {}
    provider = DouyinProvider(
        str(tmp_path),
        cookie_getter=lambda: "sessionid=abc",
        page_runner=lambda c, p, t, d, cfg: seen.update(path=p),
    )
    provider.upload(UploadRequest("job", [source], "标题"), _config())
    assert seen["path"] == source


def test_remux_reuses_previous_output_and_verifies(tmp_path, monkeypatch):
    """上一轮重封装完没来得及删（投递失败/进程被杀）时要直接复用，不重复拷几 GB。"""
    source = tmp_path / "frag.mp4"
    source.write_bytes(_FRAGMENTED_MP4)
    target = tmp_path / "frag.faststart.mp4"
    target.write_bytes(_STANDARD_MP4)
    os.utime(target, (source.stat().st_atime + 10, source.stat().st_mtime + 10))

    monkeypatch.setattr(
        douyin.subprocess, "run", lambda *a, **k: pytest.fail("已有可用产物不该重跑 ffmpeg")
    )
    assert douyin.remux_to_standard_mp4(str(source)) == str(target)


def test_remux_raises_and_cleans_up_on_ffmpeg_failure(tmp_path, monkeypatch):
    source = tmp_path / "frag.mp4"
    source.write_bytes(_FRAGMENTED_MP4)

    class _Proc:
        returncode = 1
        stderr = "ffmpeg: moov atom not found"

    monkeypatch.setattr(douyin.subprocess, "run", lambda *a, **k: _Proc())
    with pytest.raises(RuntimeError, match="重封装失败"):
        douyin.remux_to_standard_mp4(str(source))
    assert not (tmp_path / "frag.faststart.mp4").exists()


def test_remux_rejects_output_still_fragmented(tmp_path, monkeypatch):
    source = tmp_path / "frag.mp4"
    source.write_bytes(_FRAGMENTED_MP4)

    class _Proc:
        returncode = 0
        stderr = ""

    def fake_run(*args, **kwargs):
        (tmp_path / "frag.faststart.mp4").write_bytes(_FRAGMENTED_MP4)  # 拷了但没修好
        return _Proc()

    monkeypatch.setattr(douyin.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="仍不是标准 mp4"):
        douyin.remux_to_standard_mp4(str(source))
    assert not (tmp_path / "frag.faststart.mp4").exists()


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

class _StubPage:
    """够 ``_wait_upload_done`` 用的假页面：分别喂 整页文案 / toast / 预览时长。"""

    def __init__(self, body="", toast="", duration=0.0):
        self.body = body
        self.toast = toast
        self.duration = duration
        self.waits = 0

    def evaluate(self, script, arg=None):
        if arg is not None:  # _toast_text 传了选择器列表
            return self.toast
        if "cloneNode" in script:  # _body_text
            return self.body
        return self.duration  # _preview_ready 的 video.duration

    def wait_for_timeout(self, _ms):
        self.waits += 1


def test_wait_upload_done_ignores_static_uploading_hint():
    """编辑页常驻「如作品还在上传中…」提示语，不能因此一直等下去。"""
    page = _StubPage(
        body="基础信息 作品描述 谁可以看 仅自己可见 点击发布后，如作品还在上传中，请勿关闭页面，等待上传发布完成。",
        toast="",
        duration=20.1,
    )
    DouyinProvider("/tmp")._wait_upload_done(page, __import__("time").time() + 5)
    assert page.waits <= 2


def test_wait_upload_done_returns_on_success_toast():
    page = _StubPage(body="点击发布后，如作品还在上传中，请勿关闭页面", toast="上传成功", duration=0)
    DouyinProvider("/tmp")._wait_upload_done(page, __import__("time").time() + 5)
    assert page.waits == 0


def test_wait_upload_done_raises_on_failure_toast():
    page = _StubPage(body="", toast="视频上传失败，请重新上传", duration=20.0)
    with pytest.raises(RuntimeError) as excinfo:
        DouyinProvider("/tmp")._wait_upload_done(page, __import__("time").time() + 5)
    assert "视频上传失败" in str(excinfo.value)


def test_wait_upload_done_keeps_waiting_while_detecting():
    page = _StubPage(body="发文助手 快速检测 检测中 21%", toast="", duration=20.0)
    with pytest.raises(RuntimeError) as excinfo:
        DouyinProvider("/tmp")._wait_upload_done(page, __import__("time").time() + 0.8)
    assert "上传/转码超时" in str(excinfo.value)


class _Crash(Exception):
    """仿 Playwright 渲染进程崩溃的报错（真机日志原文）。"""


def test_upload_retries_once_when_renderer_crashes_before_publish(tmp_path):
    """renderer 崩了但还没点发布 → 重建浏览器重试一次（不烧队列的重试预算）。"""
    path = _write(tmp_path, "ok.mp4", _STANDARD_MP4)
    calls = []

    def runner(cookies, file_path, title, description, config):
        calls.append(file_path)
        if len(calls) == 1:
            raise _Crash("Page.wait_for_timeout: Page crashed")
        return "7412345678901234567"

    provider = DouyinProvider(str(tmp_path), cookie_getter=lambda: "sessionid=abc", page_runner=runner)
    result = provider.upload(UploadRequest("job", [path], "标题"), _config())
    assert len(calls) == 2
    assert result.remote_id == "7412345678901234567"


def test_upload_does_not_retry_after_publish_click(tmp_path):
    """已经点过发布再崩 → 绝不重试，否则账号里会多一条重复作品。"""
    path = _write(tmp_path, "ok.mp4", _STANDARD_MP4)
    calls = []

    def runner(cookies, file_path, title, description, config):
        calls.append(file_path)
        provider._publish_started = True  # 真流程里 ``_publish`` 点发布前就是这么置位的
        raise _Crash("Target crashed")

    provider = DouyinProvider(str(tmp_path), cookie_getter=lambda: "sessionid=abc", page_runner=runner)
    with pytest.raises(_Crash):
        provider.upload(UploadRequest("job", [path], "标题"), _config())
    assert len(calls) == 1


def test_upload_gives_up_after_second_crash(tmp_path):
    """连续两次崩就不再重试，把错误抛给 manager 走正常退避。"""
    path = _write(tmp_path, "ok.mp4", _STANDARD_MP4)
    calls = []

    def runner(cookies, file_path, title, description, config):
        calls.append(file_path)
        raise _Crash("Page crashed")

    provider = DouyinProvider(str(tmp_path), cookie_getter=lambda: "sessionid=abc", page_runner=runner)
    with pytest.raises(_Crash):
        provider.upload(UploadRequest("job", [path], "标题"), _config())
    assert len(calls) == 2


def test_is_page_crash_markers():
    assert douyin._is_page_crash(Exception("Page.wait_for_timeout: Page crashed"))
    assert douyin._is_page_crash(Exception("Target crashed"))
    assert not douyin._is_page_crash(RuntimeError("抖音素材上传/转码超时（没等到「上传成功」）"))


def test_publish_crash_verified_as_success(tmp_path):
    """点完发布崩了，但内容管理页能核到这条作品 → 算发布成功，不重投。"""
    provider = DouyinProvider(str(tmp_path), cookie_getter=lambda: "sessionid=abc")
    seen = {}

    def boom(page, deadline):
        raise _Crash("Page.wait_for_timeout: Page crashed")

    provider._publish_click = boom  # type: ignore[assignment]
    provider._verify_published = lambda title: seen.setdefault("title", title) or True  # type: ignore[assignment]
    provider._publish_click_safe(page=None, deadline=0, title="阿蛮想变强_2026-10-30_13-39-08_000 ")
    assert provider._publish_started is True
    assert seen["title"] == "阿蛮想变强_2026-10-30_13-39-08_000 "


def test_publish_crash_without_verification_raises_result_unknown(tmp_path):
    """崩了又核不到作品 → 结果未知（manager 会一次判 failed，不再自动重试）。"""
    from app.core.upload.models import UploadResultUnknown

    provider = DouyinProvider(str(tmp_path), cookie_getter=lambda: "sessionid=abc")

    def boom(page, deadline):
        raise _Crash("Target crashed")

    provider._publish_click = boom  # type: ignore[assignment]
    provider._verify_published = lambda title: False  # type: ignore[assignment]
    with pytest.raises(UploadResultUnknown) as excinfo:
        provider._publish_click_safe(page=None, deadline=0, title="标题")
    assert "没核到该作品" in str(excinfo.value)


def test_publish_non_crash_error_is_not_swallowed(tmp_path):
    """不是崩溃的错误（风控/登录失效）不能被「核验」吞掉。"""
    from app.core.upload.models import UploadAuthError

    provider = DouyinProvider(str(tmp_path), cookie_getter=lambda: "sessionid=abc")
    provider._verify_published = lambda title: True  # type: ignore[assignment]

    def auth_fail(page, deadline):
        raise UploadAuthError("抖音发布时要求重新登录")

    provider._publish_click = auth_fail  # type: ignore[assignment]
    with pytest.raises(UploadAuthError):
        provider._publish_click_safe(page=None, deadline=0, title="标题")


def test_title_in_manage_text_matching():
    page_text = "作品 (1)\n03:51\n 私密\n阿蛮想变强_2026-10-30_13-39-08_000\n删除作品\n没有更多作品"
    # 上传时标题被 30 字上限截断在空格处，要能容忍尾部空格
    assert douyin._title_in_manage_text(page_text, "阿蛮想变强_2026-10-30_13-39-08_000 ")
    # 卡片文字换行也要能匹配
    assert douyin._title_in_manage_text("阿蛮想变强\n_2026-10-30_13-39-08_000", "阿蛮想变强_2026-10-30_13-39-08_000")
    assert not douyin._title_in_manage_text("作品 (1)\n没有更多作品", "别的标题_2026-10-30")
    assert not douyin._title_in_manage_text(page_text, "   ")


def test_publish_timeout_but_verified_counts_as_success(tmp_path):
    """「发布超时」也可能是已经发出去了：核到就按成功处理，不重投。"""
    provider = DouyinProvider(str(tmp_path), cookie_getter=lambda: "sessionid=abc")
    provider._verify_published = lambda title: True  # type: ignore[assignment]

    def timeout(page, deadline):
        raise RuntimeError("抖音发布超时（没等到「发布成功」或跳转内容管理）")

    provider._publish_click = timeout  # type: ignore[assignment]
    provider._publish_click_safe(page=None, deadline=0, title="标题")


def test_publish_timeout_without_verification_keeps_original_error(tmp_path):
    """核不到时超时仍是原来的可重试错误（不是「结果未知」）。"""
    provider = DouyinProvider(str(tmp_path), cookie_getter=lambda: "sessionid=abc")
    provider._verify_published = lambda title: False  # type: ignore[assignment]

    def timeout(page, deadline):
        raise RuntimeError("抖音发布超时（没等到「发布成功」或跳转内容管理）")

    provider._publish_click = timeout  # type: ignore[assignment]
    with pytest.raises(RuntimeError) as excinfo:
        provider._publish_click_safe(page=None, deadline=0, title="标题")
    assert "发布超时" in str(excinfo.value)
