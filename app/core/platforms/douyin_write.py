"""抖音「取消关注」写操作：走创作者后台 `creator.douyin.com`，在真浏览器页面里 fetch。

2026-11-24 在生产号上实测出来的三条硬约束（详见 ``docs/douyin-unfollow.md`` §8）：

1. **必须真浏览器**：`httpx` 直连写接口会被边缘拦成 `403 HTML`（按 TLS 指纹），
   与参数无关；页面上下文里的 `fetch` 才能过。
2. **必须带全套 device query**（`aid` / `app_name` / `device_platform` / `msToken` /
   `a_bogus` …，直接从页面自己发的那条 list 请求上抓）。缺 query 时接口返回
   `status_code: 0` 但**什么都不发生**——静默 no-op，是最容易误判成成功的地方。
3. **`status_code: 0` 不等于成功**：写完必须回读校验，这里用对方主页的
   `[data-e2e="user-info-follow-btn"]` 文案（「已关注」→「关注」）。

登录态复用扫码登录那份持久 profile（`douyin_login._profile_dir()`）：
www 登录态在 creator.douyin.com 直接生效，不需要单独登录创作者中心。

Playwright 同步 API 不能进事件循环，调用方（路由层）负责 `asyncio.to_thread`。
"""

from __future__ import annotations

import threading
import time
from typing import Any

from ...utils.logger import logger

CREATOR_BASE = "https://creator.douyin.com"
FOLLOWING_PAGE = f"{CREATOR_BASE}/creator-micro/data/following/following"
LIST_PATH = "/aweme/v1/creator/relation/following/list/"
CANCEL_PATH = "/aweme/v1/creator/relation/following/cancel/"
PROFILE_URL = "https://www.douyin.com/user/{sec_uid}"

LIST_PAGE_SIZE = 20  # creator 后端实测每页最多 20
MAX_SCAN_PAGES = 30  # 关注 400+ 时够用（20×30=600）
SCAN_CACHE_TTL = 300.0  # 句柄映射缓存，连续取关时不用反复翻页
WRITE_MIN_INTERVAL = 1.5  # 两次写操作最短间隔（GitHub 上同类经验值；阈值未实测）
IDLE_CLOSE_SECONDS = 900.0  # 浏览器闲置这么久就关掉，别长期占内存
PAGE_READY_TIMEOUT = 40.0  # 打开关注页并抓到 list 请求的超时
VERIFY_TIMEOUT = 15.0  # 主页按钮回读超时
GOTO_TIMEOUT = 60_000  # 单次页面导航超时（毫秒，Playwright 用的是 ms）

# 抖音「操作频率过快」类业务码
RATE_LIMIT_CODES = {15371}


class DouyinWriteError(Exception):
    """写操作失败。``code`` 是稳定标识，路由层映射成 HTTP 状态 + i18n 错误码。"""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code
        self.message = message


def avatar_key(src: str | None) -> str:
    """头像识别键：www 侧是完整 URL（`.../aweme-avatar/tos-cn-avt-0015_abc.jpeg?from=…`），
    creator 侧是相对路径（`aweme-avatar/tos-cn-avt-0015_abc`），取共同的 `aweme-avatar/<id>`
    部分做匹配——比昵称可靠（同名/改昵称）。"""
    if not src:
        return ""
    text = str(src)
    marker = "aweme-avatar/"
    idx = text.find(marker)
    if idx < 0:
        return ""
    tail = text[idx + len(marker):]
    for stop in (".jpeg", ".jpg", ".webp", ".png", "?", "~"):
        cut = tail.find(stop)
        if cut > 0:
            tail = tail[:cut]
    return tail.strip("/")


def pick_target(rows: list[dict] | None, nickname: str = "", avatar: str = "") -> dict | None:
    """在 creator 关注列表里挑出目标行：先按头像键精确匹配，再退回昵称。

    两条都匹配不上就返回 None（调用方报 target_not_found，不猜）。
    """
    rows = rows or []
    key = avatar_key(avatar)
    if key:
        for row in rows:
            if avatar_key(row.get("avatar")) == key:
                return row
    nick = (nickname or "").strip()
    if nick:
        for row in rows:
            if (row.get("nickname") or "").strip() == nick:
                return row
    return None


def _list_query(raw_url: str) -> str:
    """从页面自己发的 list 请求上取 device query，去掉翻页参数。"""
    if "?" not in raw_url:
        return ""
    query = raw_url.split("?", 1)[1]
    parts = [
        p for p in query.split("&")
        if p and not p.startswith(("cursor=", "count=", "pull_type="))
    ]
    return "&".join(parts)


class CreatorSession:
    """常驻的创作者中心页面。单例由 :func:`get_session` 管，内部串行。"""

    def __init__(self, profile_dir: str | None = None) -> None:
        self._profile_dir = profile_dir
        self._playwright: Any = None
        self._context: Any = None
        self._page: Any = None
        self._query = ""
        self._handle_cache: dict[str, Any] = {}  # avatar_key/nickname -> (user_id, ts)
        self._rows_ts = 0.0
        self._last_write = 0.0
        self._last_used = 0.0

    # ------------------------------------------------------------ 生命周期

    def close(self) -> None:
        for attr in ("_context", "_playwright"):
            obj = getattr(self, attr, None)
            if obj is None:
                continue
            try:
                obj.close() if attr == "_context" else obj.stop()
            except Exception as exc:  # 关不掉也不该影响主流程
                logger.warning(f"Douyin write browser close failed: {exc}")
            setattr(self, attr, None)
        self._page = None
        self._query = ""
        self._handle_cache.clear()
        self._rows_ts = 0.0

    def _launch(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # 精简部署没装浏览器
            raise DouyinWriteError("browser_unavailable", str(exc)) from exc

        from .douyin_login import LAUNCH_ARGS, REAL_UA, _profile_dir

        self._playwright = sync_playwright().start()
        profile = self._profile_dir or _profile_dir()
        last_exc: Exception | None = None
        # 扫码登录那个会话会独占同一个 profile 目录（Chrome 会锁），撞上了等一下重试一次
        for attempt in range(2):
            try:
                self._context = self._playwright.chromium.launch_persistent_context(
                    profile,
                    headless=True,
                    args=LAUNCH_ARGS,
                    user_agent=REAL_UA,
                    viewport={"width": 1440, "height": 900},
                    locale="zh-CN",
                    timezone_id="Asia/Shanghai",
                )
                break
            except Exception as exc:
                last_exc = exc
                if attempt == 0:
                    time.sleep(2.0)
        if self._context is None:
            self.close()
            raise DouyinWriteError("browser_unavailable", f"launch failed: {last_exc}")

        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()

    def _ensure_page(self) -> None:
        """确保已经打开创作者关注页，并且抓到了那条带 device query 的 list 请求。"""
        now = time.time()
        if self._page is not None and self._query and now - self._last_used < IDLE_CLOSE_SECONDS:
            self._last_used = now
            return
        if self._page is not None and now - self._last_used >= IDLE_CLOSE_SECONDS:
            self.close()
        if self._page is None:
            self._launch()

        captured: dict[str, str] = {}

        def on_request(req) -> None:
            if LIST_PATH in req.url and "query" not in captured:
                captured["query"] = _list_query(req.url)

        self._page.on("request", on_request)
        try:
            self._page.goto(FOLLOWING_PAGE, wait_until="domcontentloaded", timeout=GOTO_TIMEOUT)
        except Exception as exc:
            self.close()
            raise DouyinWriteError("upstream_error", f"goto creator failed: {exc}") from exc

        deadline = time.time() + PAGE_READY_TIMEOUT
        ticks = 0
        while time.time() < deadline and "query" not in captured:
            self._page.wait_for_timeout(500)
            ticks += 1
            # 登录态失效时 URL 不变（还是 following 页），只是渲染出扫码登录框——只能看正文，
            # 认出登录墙就立刻收工，别把 40s 等满
            if ticks % 2 == 0 and self._has_login_wall():
                break
        try:
            self._page.remove_listener("request", on_request)
        except Exception:
            pass

        if "query" not in captured:
            # 没有任何 list 请求：最常见是登录态失效（页面停在扫码登录）
            self.close()
            raise DouyinWriteError("login_required", "creator following page did not load")
        self._query = captured["query"]
        self._last_used = time.time()
        logger.info(f"Douyin write session ready (query len={len(self._query)})")

    def _has_login_wall(self) -> bool:
        try:
            return bool(self._page.evaluate(
                """() => {
                    const t = document.body ? document.body.innerText : '';
                    return t.includes('扫码登录') || t.includes('密码登录');
                }"""
            ))
        except Exception:
            return False

    # ------------------------------------------------------------ 读

    def _list_page(self, cursor: Any) -> dict:
        payload = self._page.evaluate(
            """async (cursor) => {
                const r = await fetch(`/aweme/v1/creator/relation/following/list/?cursor=${cursor}&count=20`,
                                      {credentials: 'include'});
                return await r.json();
            }""",
            cursor,
        )
        if not isinstance(payload, dict):
            raise DouyinWriteError("upstream_error", "unexpected list payload")
        code = payload.get("status_code")
        if code in RATE_LIMIT_CODES:
            raise DouyinWriteError("rate_limited", str(payload.get("status_msg") or ""))
        if code not in (0, None):
            raise DouyinWriteError("upstream_error", str(payload.get("status_msg") or code))
        return payload

    def _refresh_handles(self) -> None:
        rows: list[dict] = []
        cursor: Any = 0
        for _ in range(MAX_SCAN_PAGES):
            payload = self._list_page(cursor)
            batch = payload.get("user_info_list") or []
            rows.extend(batch)
            if not batch or not payload.get("has_more"):
                break
            cursor = payload.get("cursor")
        cache = {}
        for row in rows:
            uid = row.get("user_id")
            if not uid:
                continue
            key = avatar_key(row.get("avatar"))
            if key:
                cache[f"a:{key}"] = uid
            nick = (row.get("nickname") or "").strip()
            if nick:
                cache.setdefault(f"n:{nick}", uid)
        self._handle_cache = cache
        self._rows_ts = time.time()
        logger.info(f"Douyin write handle map refreshed: {len(rows)} rows")

    def resolve_user_id(self, nickname: str = "", avatar: str = "") -> str:
        """把 www 侧的用户信息映射成 creator 侧的 `user_id` 句柄。"""
        if time.time() - self._rows_ts > SCAN_CACHE_TTL:
            self._refresh_handles()
        key = avatar_key(avatar)
        if key and f"a:{key}" in self._handle_cache:
            return self._handle_cache[f"a:{key}"]
        nick = (nickname or "").strip()
        if nick and f"n:{nick}" in self._handle_cache:
            return self._handle_cache[f"n:{nick}"]
        # 可能是缓存的映射过期了（刚关注的人），强制刷一次再找
        self._refresh_handles()
        if key and f"a:{key}" in self._handle_cache:
            return self._handle_cache[f"a:{key}"]
        if nick and f"n:{nick}" in self._handle_cache:
            return self._handle_cache[f"n:{nick}"]
        raise DouyinWriteError("target_not_found", nickname or key)

    def is_following(self, sec_uid: str) -> bool | None:
        """回读对方主页按钮文案：True=还关注着，False=已取关，None=读不到。"""
        page = None
        try:
            page = self._context.new_page()
            page.goto(PROFILE_URL.format(sec_uid=sec_uid), wait_until="domcontentloaded", timeout=GOTO_TIMEOUT)
            deadline = time.time() + VERIFY_TIMEOUT
            while time.time() < deadline:
                label = page.evaluate(
                    """() => {
                        const b = document.querySelector('[data-e2e="user-info-follow-btn"]');
                        return b ? b.innerText.trim() : '';
                    }"""
                )
                if label:
                    return "已关注" in label or "互相关注" in label or "相互关注" in label
                page.wait_for_timeout(500)
            return None
        except Exception as exc:
            logger.warning(f"Douyin follow state readback failed: {type(exc).__name__}: {exc}")
            return None
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:
                    pass

    # ------------------------------------------------------------ 写

    def cancel_follow(self, user_id: str) -> dict:
        wait = WRITE_MIN_INTERVAL - (time.time() - self._last_write)
        if wait > 0:
            time.sleep(wait)
        url = f"{CANCEL_PATH}?{self._query}"
        payload = self._page.evaluate(
            """async ([url, uid]) => {
                const r = await fetch(url, {
                    method: 'POST',
                    credentials: 'include',
                    headers: {'Content-Type': 'application/json',
                              'accept': 'application/json, text/plain, */*'},
                    body: JSON.stringify({user_id: uid}),
                });
                const text = await r.text();
                let data = null;
                try { data = JSON.parse(text); } catch (e) {}
                return {status: r.status, data, text: text.slice(0, 200)};
            }""",
            [url, user_id],
        )
        self._last_write = time.time()
        data = (payload or {}).get("data") or {}
        code = data.get("status_code")
        if code in RATE_LIMIT_CODES:
            raise DouyinWriteError("rate_limited", str(data.get("status_msg") or ""))
        if code != 0:
            raise DouyinWriteError(
                "upstream_error",
                f"http={payload.get('status')} code={code} msg={data.get('status_msg') or ''}",
            )
        return data


_SESSION: CreatorSession | None = None
_SESSION_LOCK = threading.RLock()
_SESSION_FACTORY = CreatorSession  # 抽成变量方便测试注入假实现


def get_session() -> CreatorSession:
    global _SESSION
    with _SESSION_LOCK:
        if _SESSION is None:
            _SESSION = _SESSION_FACTORY()
        return _SESSION


def reset_session() -> None:
    """关掉常驻浏览器（测试/退出时用）。"""
    global _SESSION
    with _SESSION_LOCK:
        if _SESSION is not None:
            _SESSION.close()
            _SESSION = None


def unfollow(sec_uid: str, nickname: str = "", avatar: str = "") -> dict:
    """取消关注一个账号，并回读校验。返回 ``{"user_id", "verified", "nickname"}``。

    失败抛 :class:`DouyinWriteError`（`login_required` / `target_not_found` /
    `rate_limited` / `upstream_error` / `verify_failed` / `browser_unavailable`）。
    """
    sec_uid = (sec_uid or "").strip()
    if not sec_uid:
        raise DouyinWriteError("target_not_found", "empty sec_uid")

    with _SESSION_LOCK:
        session = get_session()
        session._ensure_page()
        user_id = session.resolve_user_id(nickname, avatar)
        session.cancel_follow(user_id)
        # status_code 0 不代表成功（缺 query 时就是静默 no-op），必须回读
        following = session.is_following(sec_uid)
        if following is True:
            raise DouyinWriteError("verify_failed", "still following after cancel")
        if following is None:
            # 读不到就按「已删掉缓存映射」重试一次判定：至少把映射失效，下次重新扫描
            session._rows_ts = 0.0
            logger.warning("Douyin unfollow verify unreadable, treat as unverified")
        else:
            session._handle_cache.pop(f"n:{(nickname or '').strip()}", None)
            key = avatar_key(avatar)
            if key:
                session._handle_cache.pop(f"a:{key}", None)
        return {"user_id": user_id, "verified": following is False, "nickname": nickname}
