"""抖音自动投稿（浏览器自动化）。

抖音没有个人可用的开放上传 API（开放平台要 client_key + 企业主体 + 应用审核），
所以走创作者中心的网页流程：复用「设置 → Cookie」里那份 douyin Cookie（与关注列表
共用），用 Playwright 打开发布页 → 选文件 → 填标题/简介/话题 → 选可见性 → 发布 → 读回作品 ID。

实测要点（2026-10-29 生产容器 headless Chromium，见 AGENTS.md）：
- 浏览器启动只要 0.2–0.4s，慢的 13–16s 全在抖音前端风控 JS，冷启动/持久 profile 无差别，
  所以这里「每次新建、用完即关」，不做常驻浏览器。
- 发布页是 SPA：home 只是入口，选完文件才跳到 ``/content/post/video`` 编辑页。
- 「谁可以看」默认是**公开**，所以 ``only_self`` 默认 True，连选项也校验一次。
- 抖音一作品一视频，不支持多 P（``supports_multi_parts = False``）。
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

from ....utils.logger import logger
from ..models import (
    UploadAuthError,
    UploadRateLimited,
    UploadRequest,
    UploadResult,
)

UPLOAD_URL = "https://creator.douyin.com/creator-micro/content/upload"
MANAGE_URL = "https://creator.douyin.com/creator-micro/content/manage"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_BROWSER_ARGS = (
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
    "--lang=zh-CN",
    "--no-first-run",
)
# 展开/收起文案里的隐写字段：5x 版本常见，先把 webdriver 抹掉再谈别的
_INIT_SCRIPT = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"

# 编辑页各控件（抖音改版频繁，每处都给 2–3 个候选，按顺序试）
_FILE_INPUT = "input[type=file]"
_TITLE_SELECTORS = (
    "div[contenteditable='true'][data-placeholder*='标题']",
    "input[placeholder*='作品标题']",
    "div[contenteditable='true'][data-placeholder*='作品描述']",
)
_DESC_SELECTORS = (
    "div[contenteditable='true'][data-placeholder*='简介']",
    "div.zone-container[contenteditable='true']",
    "div[contenteditable='true']",
)
_SELF_ONLY_TEXT = "仅自己可见"

_TITLE_LIMIT = 30
#: 重封装输出后缀（与源文件同目录，投递成功后删除）
_REMUX_SUFFIX = ".faststart.mp4"
#: 重封装超时：几分钟的 4K 录制也就几秒，这里只是防 ffmpeg 挂死
_REMUX_TIMEOUT = 1800
_AUTH_MARKERS = (
    "扫码登录",
    "验证码登录",
    "密码登录",
    "登录后发布",
    "请先登录",
    "登录已过期",
    "登录状态已失效",
    "登录失效",
)
_RATE_LIMIT_MARKERS = (
    "操作频繁",
    "操作过于频繁",
    "上传过于频繁",
    "发布过于频繁",
    "投稿过于频繁",
    "投稿频繁",
    "请稍后再试",
    "请稍后重试",
    "稍后重试",
    "今日发布次数已达上限",
    "发布次数已达上限",
    "请求过于频繁",
)
_VERIFY_MARKERS = (
    "滑块",
    "拖动",
    "完成验证",
    "安全验证",
    "身份验证",
    "短信验证",
    "验证码",
)
# 抖音后台的失败提示常在 toast/弹窗里，抓这几类就算「这次没成」。长词在前：
# 「视频上传失败」要比「上传失败」先命中，报出去的提示才具体。
# 注意别放「重新上传」：编辑页的音乐区块常年挂着一个「重新上传」链接，会天天误报。
_FAILURE_MARKERS = (
    "视频上传失败",
    "发布未成功",
    "发布失败",
    "上传失败",
)

#: toast/浮层容器（抖音的 class 名带哈希，只能按前缀模糊匹配）。真机实测：
#: 上传完成 → 「上传成功」，点发布 → 「正在发布」→ 约 3s 后「发布成功」并跳到内容管理。
_TOAST_SELECTORS = (
    "[class*=toast]",
    "[class*=Toast]",
    "[role=alert]",
    "[class*=message]",
    "[class*=notice]",
)
_UPLOAD_DONE_MARKERS = ("上传成功",)
_PUBLISH_OK_MARKERS = ("发布成功",)
_PUBLISH_PENDING_MARKERS = ("正在发布", "发布中")
# 点早了抖音会提示「请等待上传完成」之类：等一会儿再点一次，不算失败
_PUBLISH_RETRY_MARKERS = ("请等待", "还未上传完成", "不允许发布", "视频上传中")
# 抖音自己的服务端/网络抖动（不是限流）：出现这些要「过会儿重试」，不能把渠道冻 6 小时。
# 这个提示里自带「请稍后再试」，所以必须先于 _RATE_LIMIT_MARKERS 判。
_TRANSIENT_MARKERS = (
    "服务器/网络开小差了",
    "网络开小差",
    "开小差了",
    "服务器繁忙",
    "网络异常",
    "请检查网络",
)
_AWEME_RE = re.compile(r"(?:douyin\.com/video/|/video/|aweme_id=)(\d{8,})")


def parse_cookie_string(raw: str) -> list[dict]:
    """把 ``a=1; b=2`` 解析成 Playwright 的 cookie 列表（值保持原样，不解码）。

    抖音浏览器导出的串里 ``sid_guard`` 是 URL 编码过的（``%7C`` 当分隔符），实测原样
    发出去就能过鉴权，所以不做 unquote —— 解码反而可能和平台记录的字段对不上。
    """
    cookies = []
    for segment in (raw or "").split(";"):
        if "=" not in segment:
            continue
        name, value = segment.split("=", 1)
        name, value = name.strip(), value.strip()
        if not name:
            continue
        cookies.append({"name": name, "value": value, "domain": ".douyin.com", "path": "/"})
    return cookies


def has_login_cookie(cookies: list[dict]) -> bool:
    """只有 sessionid 之类的登录态才算可用；只有 ttwid 的公共 Cookie 打不开创作者中心。"""
    names = {item["name"] for item in cookies}
    return bool(names & {"sessionid", "sessionid_ss", "sid_tt"})


def detect_auth(text: str) -> bool:
    return any(marker in text for marker in _AUTH_MARKERS)


def detect_rate_limit(text: str) -> tuple[bool, bool] | None:
    """识别风控/限流：返回 ``(hit, needs_verify)``，没命中返回 None。

    只要出现滑块/短信这类人机校验就算风控——哪怕没有「操作过于频繁」的措辞，这单也
    过不去，该走渠道冷却而不是逐任务重试把预算烧光。调用方要先判 ``detect_auth``，
    否则登录框里的「验证码登录」会被误判成风控。
    """
    needs_verify = any(marker in text for marker in _VERIFY_MARKERS)
    if needs_verify or any(marker in text for marker in _RATE_LIMIT_MARKERS):
        return True, needs_verify
    return None


def detect_failure(text: str) -> str | None:
    for marker in _FAILURE_MARKERS:
        if marker in text:
            return marker
    return None


def detect_transient(text: str) -> str | None:
    """抖音侧的服务端/网络抖动 —— 可重试，不是风控。"""
    for marker in _TRANSIENT_MARKERS:
        if marker in text:
            return marker
    return None


def extract_aweme_id(text: str) -> str | None:
    match = _AWEME_RE.search(text or "")
    return match.group(1) if match else None


def build_caption(request: UploadRequest, config: dict) -> tuple[str, str]:
    """生成（标题, 简介）文本：标题裁到抖音的 30 字上限，话题拼进简介。"""
    prefix = str(config.get("title_prefix") or "").strip()
    title = f"{prefix}{request.title or ''}".strip() or Path(request.files[0]).stem
    title = title[:_TITLE_LIMIT]

    parts = [str(config.get("description") or "").strip()]
    tags = [tag.strip().lstrip("#") for tag in str(config.get("tags") or "").split(",")]
    tags = [tag for tag in tags if tag]
    if tags:
        parts.append(" ".join(f"#{tag}" for tag in tags))
    description = " ".join(part for part in parts if part).strip()
    return title, description


def looks_like_standard_mp4(path: str) -> bool:
    """前 1MB 里要有完整 moov，且不能出现 moof 分片。

    直录的 mp4 用 ``+frag_keyframe+empty_moov`` 写的是**空壳 moov**，真正的轨道
    信息在 moof/mdat 分片里：光看「有没有 moov 字样」会把分段文件误判成标准 mp4，
    传上去抖音转码才报 moov 缺失。所以按顶层 atom 走一遍，见到 moof 就直接拒。
    """
    try:
        with open(path, "rb") as handle:
            head = handle.read(1024 * 1024)
    except OSError:
        return False
    if head[4:8] not in (b"ftyp", b"moov", b"free", b"skip", b"mdat"):
        return False
    has_moov = False
    offset = 0
    while offset + 8 <= len(head):
        size = int.from_bytes(head[offset : offset + 4], "big")
        kind = head[offset + 4 : offset + 8]
        if kind == b"moof":
            return False  # 分段式（fragmented）容器，抖音解不了
        if kind == b"moov":
            has_moov = True
        if size == 1:  # 64 位 largesize
            if offset + 16 > len(head):
                break
            size = int.from_bytes(head[offset + 8 : offset + 16], "big")
        if size < 8:
            break
        offset += size
    return has_moov


def remux_to_standard_mp4(path: str, timeout: int = _REMUX_TIMEOUT) -> str:
    """把直录的分片式 mp4 无损重封装成标准 mp4，返回新文件路径。

    ``-c copy`` 只重写容器、不重编码：41MB/102s 的录制实测 0.7s，体积/时长/编码全不变。
    输出放在源文件同目录（同一个卷，几百 MB 也不怕，别塞 /tmp 把容器写爆），投递完就删。
    上一轮重封装完没来得及删（投递失败/进程被杀）时直接复用，不重复拷几 GB。
    """
    source = Path(path)
    target = source.with_name(f"{source.stem}{_REMUX_SUFFIX}")
    if (
        target.exists()
        and target.stat().st_mtime >= source.stat().st_mtime
        and looks_like_standard_mp4(str(target))
    ):
        return str(target)
    started = time.time()
    logger.info(f"抖音：{source.name} 不是标准 mp4（moov 不在头部），先无损重封装")
    try:
        proc = subprocess.run(
            [
                "ffmpeg", "-y", "-v", "error", "-i", str(source),
                "-c", "copy", "-movflags", "+faststart", str(target),
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        target.unlink(missing_ok=True)
        raise RuntimeError(f"重封装失败（ffmpeg 无法执行或超时）：{exc}") from exc
    if proc.returncode != 0 or not target.exists():
        target.unlink(missing_ok=True)
        detail = (proc.stderr or "").strip().splitlines()[-1:] or [""]
        raise RuntimeError(f"重封装失败（ffmpeg {proc.returncode}）：{detail[0][:200]}")
    if not looks_like_standard_mp4(str(target)):
        target.unlink(missing_ok=True)
        raise RuntimeError(f"重封装后仍不是标准 mp4（{source.name}）：moov 头缺失")
    logger.info(
        f"抖音：重封装完成 {target.name}（{time.time() - started:.1f}s，{target.stat().st_size / 1e6:.1f}MB）"
    )
    return str(target)


class DouyinProvider:
    """抖音创作者中心投稿：一视频一作品，只接受标准 mp4（分段录制的 moov 缺失投不了）。"""

    name = "douyin"
    display_name = "抖音"
    #: 抖音不支持多 P：一单只能一个文件（manager 按这个把识别产物拆成一文件一单）
    supports_multi_parts = False

    def __init__(self, run_path: str, cookie_getter=None, page_runner=None):
        self.run_path = os.path.abspath(run_path)
        self._cookie_getter = cookie_getter or (lambda: "")
        #: 测试注入点：``page_runner(cookies, path, title, description, config) -> str|None``
        self._page_runner = page_runner

    def build_caption(self, request: UploadRequest, config: dict) -> tuple[str, str]:
        return build_caption(request, config)

    def upload(self, request: UploadRequest, config: dict) -> UploadResult:
        if len(request.files) != 1:
            raise RuntimeError(
                f"抖音一作品只能一个视频，收到 {len(request.files)} 个文件（渠道配额配错了）"
            )
        path = request.files[0]
        if not os.path.isfile(path):
            raise RuntimeError(f"Upload file not found: {path}")

        cookies = parse_cookie_string(str(self._cookie_getter() or ""))
        if not cookies:
            raise UploadAuthError("抖音 Cookie 未配置：请先在「设置 → Cookie」里粘贴抖音登录态")
        if not has_login_cookie(cookies):
            raise UploadAuthError("抖音 Cookie 缺少登录态（sessionid），无法打开创作者中心")

        # 直录的分段式 mp4（moof 分片）抖音解不了：先 ``-c copy`` 无损重封装，投完删掉临时文件。
        temp_path = ""
        if not self._looks_like_standard_mp4(path):
            path = remux_to_standard_mp4(path)
            temp_path = path

        title, description = self.build_caption(request, config)
        runner = self._page_runner or self._run_browser
        try:
            remote_id = runner(cookies, path, title, description, config)
        finally:
            if temp_path:
                try:
                    os.unlink(temp_path)
                except OSError as exc:  # pragma: no cover - 删不掉不影响投稿结果
                    logger.warning(f"抖音：重封装临时文件清理失败 {temp_path}: {exc}")
        return UploadResult(
            provider=self.name,
            remote_id=remote_id,
            message=f"抖音投稿完成: {title}" + (f" ({remote_id})" if remote_id else ""),
        )

    @staticmethod
    def _looks_like_standard_mp4(path: str) -> bool:
        """保留旧调用点；判据在模块级（重封装也要用同一套）。"""
        return looks_like_standard_mp4(path)

    # ------------------------------------------------------------------ 浏览器流程

    def _run_browser(self, cookies, path, title, description, config) -> str | None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - 生产镜像里一定有
            raise RuntimeError(f"抖音上传需要 playwright：{exc}") from exc

        timeout = max(120, int(config.get("timeout_seconds") or 900))
        headless = bool(config.get("headless", True))
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=headless, args=list(_BROWSER_ARGS))
            try:
                context = browser.new_context(
                    viewport={"width": 1440, "height": 900},
                    user_agent=_USER_AGENT,
                    locale="zh-CN",
                )
                context.add_init_script(_INIT_SCRIPT)
                context.add_cookies(cookies)
                page = context.new_page()
                page.set_default_timeout(30_000)
                try:
                    return self._publish(page, path, title, description, config, timeout)
                except Exception:
                    self._dump(page, "douyin-failed")
                    raise
            finally:
                browser.close()

    def _publish(self, page, path, title, description, config, timeout: int) -> str | None:
        deadline = time.time() + timeout
        page.goto(UPLOAD_URL, wait_until="domcontentloaded", timeout=120_000)
        self._wait_file_input(page, deadline)
        page.locator(_FILE_INPUT).first.set_input_files(path)
        logger.info(f"抖音：已提交文件 {Path(path).name}，等待编辑页")

        self._wait_editor(page, deadline)
        self._dismiss_popups(page)
        self._wait_upload_done(page, deadline)

        self._fill(page, _TITLE_SELECTORS, title)
        if description:
            self._fill(page, _DESC_SELECTORS, description, required=False)
        logger.info(f"抖音：标题「{title}」已填，准备设置可见性")
        if bool(config.get("only_self", True)):
            self._select_self_only(page)

        self._publish_click(page, deadline)
        remote_id = self._read_back(page, deadline, title)
        logger.success(f"抖音：发布完成 {remote_id or '(未取到作品 ID)'}")
        return remote_id

    # ---- 步骤实现

    def _wait_file_input(self, page, deadline: float) -> None:
        """等发布页渲染出文件输入；期间如果看到登录入口就判 Cookie 失效。"""
        while time.time() < deadline:
            if page.locator(_FILE_INPUT).count():
                return
            text = self._body_text(page)
            if detect_auth(text):
                raise UploadAuthError("抖音 Cookie 已失效（发布页要求重新登录）")
            page.wait_for_timeout(500)
        raise RuntimeError("抖音发布页 30s 内没出现上传入口（页面结构可能改版）")

    def _wait_editor(self, page, deadline: float) -> None:
        while time.time() < deadline:
            if self._first(page, _DESC_SELECTORS) is not None:
                return
            text = self._body_text(page)
            if detect_auth(text):
                raise UploadAuthError("抖音 Cookie 已失效（选完文件后被要求登录）")
            transient = detect_transient(text)
            if transient is not None:
                raise RuntimeError(f"抖音暂时失败（{transient}），稍后重试")
            rate = detect_rate_limit(text)
            if rate is not None:
                raise UploadRateLimited(f"抖音风控：{text[-300:]}", needs_verify=rate[1])
            page.wait_for_timeout(500)
        raise RuntimeError("抖音编辑页没打开（上传超时或页面改版）")

    def _wait_upload_done(self, page, deadline: float) -> None:
        """等素材真的传到抖音侧再点发布，否则点下去只会提示「请等待上传完成」。

        判据只认两个硬信号：
        1. toast 出现「上传成功」（真机 8.6MB 片约 10s 出现，是抖音自己给的完成回执）；
        2. 兜底：本地预览 ``<video>`` 拿到 duration 且「快速检测」不再显示「检测中」。
        不能拿整页文案当判据：编辑页常驻「点击发布后，如作品还在上传中…」的提示语，
        任何「上传中」字样都会永远命中。
        """
        while time.time() < deadline:
            toast = self._toast_text(page)
            if any(marker in toast for marker in _UPLOAD_DONE_MARKERS):
                return
            failure = detect_failure(toast)
            if failure:
                raise RuntimeError(f"抖音上传失败：{failure}")
            text = self._body_text(page)
            if detect_auth(text):
                raise UploadAuthError("抖音 Cookie 已失效（上传中被要求登录）")
            transient = detect_transient(text)
            if transient is not None:
                raise RuntimeError(f"抖音暂时失败（{transient}），稍后重试")
            rate = detect_rate_limit(text)
            if rate is not None:
                raise UploadRateLimited(f"抖音风控：{text[-300:]}", needs_verify=rate[1])
            if self._preview_ready(page, text):
                return
            page.wait_for_timeout(500)
        raise RuntimeError("抖音素材上传/转码超时（没等到「上传成功」）")

    @staticmethod
    def _preview_ready(page, text: str) -> bool:
        """兜底判据：预览视频已能读出时长，且「快速检测」已结束。"""
        if "检测中" in text:
            return False
        try:
            duration = page.evaluate(
                "() => { const v = document.querySelector('video');"
                " return v && isFinite(v.duration) ? v.duration : 0; }"
            )
        except Exception:
            return False
        return bool(duration and duration > 0)

    def _select_self_only(self, page) -> None:
        """默认「公开」，所以必须点一次「仅自己可见」，并尽量回读校验。"""
        label = page.get_by_text(_SELF_ONLY_TEXT, exact=True).first
        self._click(page, label)
        page.wait_for_timeout(500)
        try:
            checked = label.evaluate(
                "e => { const box = e.closest('label') || e.parentElement;"
                " const input = box && box.querySelector('input');"
                " return input ? input.checked : null; }"
            )
        except Exception:
            checked = None
        if checked is False:
            raise RuntimeError("抖音「仅自己可见」没选中（页面结构改版？）")
        logger.info(f"抖音：可见性已设为「仅自己可见」（回读={checked}）")

    def _publish_click(self, page, deadline: float) -> None:
        """点发布并等抖音的回执：成功是 toast「发布成功」或跳转内容管理。

        失败/成功判定都只看 toast：整页文案里「视频发布成功后…」这类条款文案会把
        「发布成功」变成永远为真的假信号（旧实现就这么误判过）。
        """
        button = self._publish_button(page)
        if button is None:
            raise RuntimeError("找不到抖音发布按钮")
        self._click(page, button)
        clicked_at = time.time()
        while time.time() < deadline:
            if "/content/manage" in page.url:
                return
            toast = self._toast_text(page)
            if any(marker in toast for marker in _PUBLISH_OK_MARKERS):
                logger.success("抖音：toast 回执「发布成功」")
                return
            failure = detect_failure(toast)
            if failure:
                raise RuntimeError(f"抖音发布失败：{failure}")
            text = self._body_text(page)
            # 先判登录失效：「验证码登录」这类字样也会命中风控的关键词
            if detect_auth(text):
                raise UploadAuthError("抖音发布时要求重新登录")
            transient = detect_transient(text)
            if transient is not None:
                raise RuntimeError(f"抖音暂时失败（{transient}），稍后重试")
            rate = detect_rate_limit(text)
            if rate is not None:
                raise UploadRateLimited(f"抖音发布被风控：{text[-300:]}", needs_verify=rate[1])
            pending = any(marker in toast for marker in _PUBLISH_PENDING_MARKERS)
            retry = any(marker in toast for marker in _PUBLISH_RETRY_MARKERS)
            if (retry or not pending) and time.time() - clicked_at > 8:
                # 点早了（素材还没传完）或 toast 已消失但没跳转：再点一次
                logger.warning(f"抖音：发布没回执（toast={toast[:80]!r}），重试点击")
                button = self._publish_button(page)
                if button is None:
                    raise RuntimeError("抖音发布按钮消失（页面可能已跳转失败）")
                self._click(page, button)
                clicked_at = time.time()
            page.wait_for_timeout(1000)
        raise RuntimeError("抖音发布超时（没等到「发布成功」或跳转内容管理）")

    def _read_back(self, page, deadline: float, title: str) -> str | None:
        remote_id = extract_aweme_id(page.url) or extract_aweme_id(self._body_text(page))
        if remote_id:
            return remote_id
        try:
            page.goto(MANAGE_URL, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(3000)
            # 先按标题找自己刚发的那条，找不到再退化成「列表第一项」
            links = page.locator("a[href*='/video/']")
            fallback = None
            for index in range(min(links.count(), 30)):
                link = links.nth(index)
                href = link.get_attribute("href") or ""
                candidate = extract_aweme_id(href)
                if not candidate:
                    continue
                if fallback is None:
                    fallback = candidate
                try:
                    card_text = link.inner_text()
                except Exception:
                    card_text = ""
                if title and title[:12] in card_text:
                    return candidate
            return fallback
        except Exception as exc:  # 读回失败不影响「已发布」的结论
            logger.warning(f"抖音作品 ID 读回失败（作品已发布）: {exc}")
            return None

    # ---- 小工具

    @staticmethod
    def _body_text(page) -> str:
        """页面可见文案，先剥掉 script/style。

        抖音把整包 JS/CSS 以 ``<script>``/``<style>`` 塞在 body 里，实测 ``body.innerText``
        有 1.5MB（全是源码），而真实文案只有 ~900 字 —— 不剥掉的话源码里的弹窗字样、
        登录字样会随机命中，把登录失效/风控/失败判定全带偏。
        """
        try:
            return page.evaluate(
                """() => {
                    const clone = document.body.cloneNode(true);
                    clone.querySelectorAll('script,style,noscript,template').forEach(n => n.remove());
                    return clone.innerText || '';
                }"""
            ) or ""
        except Exception:
            return ""

    @staticmethod
    def _toast_text(page) -> str:
        """把当前可见的 toast/浮层文案拼起来（失败/成功判定只看这块）。"""
        try:
            return page.evaluate(
                """(sels) => {
                    const out = [];
                    for (const sel of sels) {
                        for (const el of document.querySelectorAll(sel)) {
                            if (!el.getClientRects().length) continue;
                            const t = (el.textContent || '').trim();
                            if (t) out.push(t.slice(0, 120));
                        }
                    }
                    return out.join(' | ');
                }""",
                list(_TOAST_SELECTORS),
            ) or ""
        except Exception:
            return ""

    @staticmethod
    def _first(page, selectors):
        for selector in selectors:
            locator = page.locator(selector)
            if locator.count():
                return locator.first
        return None

    def _fill(self, page, selectors, text: str, required: bool = True) -> None:
        target = self._first(page, selectors)
        if target is None:
            if required:
                raise RuntimeError(f"找不到抖音输入框（候选 {selectors}）")
            logger.warning("抖音：没找到简介输入框，跳过简介")
            return
        tag = target.evaluate("e => e.tagName.toLowerCase()")
        if tag in ("input", "textarea"):
            target.fill(text)
        else:
            self._click(page, target)
            page.keyboard.press("Control+A")
            page.keyboard.press("Delete")
            page.keyboard.type(text, delay=15)

    @staticmethod
    def _publish_button(page):
        for locate in (
            lambda: page.get_by_role("button", name="发布", exact=True),
            lambda: page.locator("button:has-text('发布')"),
        ):
            try:
                locator = locate()
                if locator.count():
                    return locator.last
            except Exception:
                continue
        return None

    @staticmethod
    def _click(page, locator) -> None:
        """正常点；被浮层挡住时退化成 JS 点击（抖音的「我知道了」弹窗常盖住发布按钮）。"""
        try:
            locator.click(timeout=8_000)
        except Exception:
            locator.evaluate("e => e.click()")

    def _dismiss_popups(self, page) -> None:
        for text in ("我知道了", "知道了"):
            try:
                button = page.get_by_role("button", name=text, exact=True)
                while button.count():
                    self._click(page, button.first)
                    page.wait_for_timeout(300)
            except Exception:
                continue

    def _dump(self, page, reason: str) -> None:
        """失败时留一张现场图和页面文字，免得「页面改版」类问题只能靠猜。"""
        try:
            folder = Path(self.run_path) / "config" / "upload_debug"
            folder.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            shot = folder / f"{reason}-{stamp}.png"
            page.screenshot(path=str(shot), full_page=False)
            logger.warning(f"抖音上传失败现场已保存: {shot}")
        except Exception as exc:
            logger.warning(f"抖音失败现场保存失败: {exc}")