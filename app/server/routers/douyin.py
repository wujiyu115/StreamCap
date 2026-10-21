"""抖音关注列表接口：读「我关注的人」与「我关注的人谁在直播」。

出网请求全部调度到后台监控线程的 loop（``services.run_coro`` + 跨 loop 等待），
这样关注列表的请求与周期监控、有效性检测共享同一份平台节奏预算，不会绕过
抖音风控阈值；每个具体请求再自己套一层 ``platform_slot``。

还有一组扫码登录接口（``/api/douyin/login``）：Cookie 失效时前端弹二维码，用户用
抖音 App 扫一下，登录态就从浏览器里取回来写进设置。浏览器在单独的线程里跑（Playwright
同步 API 不能进事件循环），接口层只读快照；写配置仍在事件循环上做。
"""

from __future__ import annotations

import asyncio
import threading

from fastapi import APIRouter, Depends, HTTPException, Query

from ...core.platforms import douyin_following as following
from ...core.platforms import douyin_login as login
from ...core.platforms.room_validity import resolve_platform_proxy
from ...utils.logger import logger
from .. import error_codes as errors
from ..deps import get_current_user, get_services

router = APIRouter(prefix="/api/douyin", tags=["douyin"])

_PLATFORM_KEY = "douyin"
# web_rid 会直接拼进 https://live.douyin.com/{web_rid}，只允许房间号/短号那种字符集
_WEB_RID_PATTERN = r"^[A-Za-z0-9_.-]+$"

_ERROR_CODES = {
    "login_required": (400, errors.DOUYIN_LOGIN_REQUIRED),
    "bad_response": (502, errors.DOUYIN_UPSTREAM_ERROR),
    "upstream_error": (502, errors.DOUYIN_UPSTREAM_ERROR),
}

# 扫码登录：同一时刻只允许一个会话（内存里握着它在跑的浏览器线程）
_LOGIN_LOCK = threading.Lock()
_LOGIN_SESSION: login.DouyinLoginSession | None = None
_LOGIN_SESSION_FACTORY = login.DouyinLoginSession  # 抽成变量方便测试注入假驱动
_LOGIN_START_GRACE = 2.0  # POST 里等这么久，好把「没装浏览器」这类秒失败直接报出来


def _http_error(exc: following.DouyinFollowingError) -> HTTPException:
    status, code = _ERROR_CODES.get(exc.code, (502, errors.DOUYIN_UPSTREAM_ERROR))
    detail = f"{code}|{exc.message}" if exc.message else code
    return HTTPException(status_code=status, detail=detail)


def _douyin_context(services, require_cookie: bool = True) -> tuple[str, str | None]:
    cookie = (services.settings_config.get_cookies_value(_PLATFORM_KEY) or "").strip()
    if not cookie and require_cookie:
        raise HTTPException(status_code=400, detail=errors.DOUYIN_COOKIE_MISSING)
    proxy = resolve_platform_proxy(services.settings_config.user_config, _PLATFORM_KEY)
    return cookie, proxy


async def _dispatch(services, coro):
    """提交到后台 loop 跑（含节奏门等待），再把结果搬回来。"""
    future = services.run_coro(coro)
    if future is None:
        raise HTTPException(status_code=500, detail=errors.DOUYIN_FOLLOWING_UNAVAILABLE)
    try:
        return await asyncio.wrap_future(future)
    except following.DouyinFollowingError as exc:
        raise _http_error(exc) from exc
    except Exception as exc:  # 后台 loop 上的意外异常，别吐成不可翻译的 500
        logger.error(f"Douyin following dispatch failed: {type(exc).__name__}: {exc}")
        raise HTTPException(
            status_code=502, detail=f"{errors.DOUYIN_UPSTREAM_ERROR}|{type(exc).__name__}: {exc}"
        ) from exc


def _slot(services):
    manager = services.recording_manager
    return lambda: manager.platform_slot(_PLATFORM_KEY)


@router.get("/following/self")
async def get_self_profile(
    refresh: bool = Query(False),
    user: str = Depends(get_current_user),
    services=Depends(get_services),
):
    """当前 Cookie 对应的抖音账号信息（也用来判定登录态是否还有效）。"""
    cookie, proxy = _douyin_context(services)
    store = following.get_store()
    return await _dispatch(services, store.self_profile(cookie, proxy, _slot(services), refresh=refresh))


@router.get("/following")
async def list_following(
    page: int = Query(1, ge=1, le=2000),
    page_size: int = Query(20, ge=1, le=following.FOLLOWING_PAGE_MAX),
    refresh: bool = Query(False),
    user: str = Depends(get_current_user),
    services=Depends(get_services),
):
    """关注的用户，一页一页取；已取过的页命中内存缓存，不再打接口。"""
    cookie, proxy = _douyin_context(services)
    store = following.get_store()
    return await _dispatch(
        services,
        store.following_page(cookie, proxy, _slot(services), page=page, page_size=page_size, refresh=refresh),
    )


@router.get("/following/live")
async def list_following_live(
    refresh: bool = Query(False),
    user: str = Depends(get_current_user),
    services=Depends(get_services),
):
    """我关注的人里正在直播的（默认 60s 缓存）。"""
    cookie, proxy = _douyin_context(services)
    store = following.get_store()
    return await _dispatch(services, store.live(cookie, proxy, _slot(services), refresh=refresh))


@router.get("/live/stream")
async def get_live_stream(
    web_rid: str = Query(..., min_length=1, max_length=64, pattern=_WEB_RID_PATTERN),
    quality: str | None = Query(None, max_length=16),
    refresh: bool = Query(False),
    user: str = Depends(get_current_user),
    services=Depends(get_services),
):
    """某个直播间的播放直链（WebUI 内嵌播放器用；与录制取流同一条 streamget 路径）。

    Cookie 不是必须的：room enter 是游客接口，录制本身就靠公共 ttwid 取流。"""
    cookie, proxy = _douyin_context(services, require_cookie=False)
    store = following.get_store()
    return await _dispatch(
        services,
        store.stream(
            cookie,
            proxy,
            _slot(services),
            web_rid=web_rid,
            quality=(quality or "").strip() or None,
            refresh=refresh,
        ),
    )


@router.get("/cookie")
async def get_cookie_status(user: str = Depends(get_current_user), services=Depends(get_services)):
    """抖音 Cookie 的登录态寿命（有没有配、什么时候到期）。

    只解析本地 Cookie 串里的 sid_guard，不打抖音接口：设置页/关注页要在没有登录态
    （也就是最该提醒的时候）也能读到状态。默认层不写库，纯读。"""
    cookie, _ = _douyin_context(services, require_cookie=False)
    return following.parse_cookie_expiry(cookie)


# ---------------------------------------------------------------- 扫码登录


def _any_login() -> login.DouyinLoginSession | None:
    """最后一个会话（含已结束的）：GET 靠它把结果稳定地回给前端。"""
    with _LOGIN_LOCK:
        return _LOGIN_SESSION


def _live_login() -> login.DouyinLoginSession | None:
    """还在跑的会话；结束了的（Cookie 已被取走）当没有，下次重新起一个。"""
    session = _any_login()
    if session is None or session.is_finished():
        return None
    return session


def _start_login() -> login.DouyinLoginSession:
    global _LOGIN_SESSION
    with _LOGIN_LOCK:
        session = _LOGIN_SESSION
        if session is None or session.is_finished():
            session = _LOGIN_SESSION_FACTORY()
            _LOGIN_SESSION = session
            session.start()
            logger.info("Douyin login: 已启动扫码登录会话")
    return session


def _login_payload(session: login.DouyinLoginSession | None) -> dict:
    """会话快照 → 响应体。

    失败原因沿用项目统一的错误码字符串（``err.xxx|附加信息``），前端直接走
    ``translateError`` 就能翻；这里不做 HTTP 错误是为了让「启动中/等扫码/失败」
    都走同一条轮询回路。
    """
    if session is None:
        return {"state": "idle"}
    data = session.snapshot()
    data.pop("started_at", None)
    if data["state"] == login.STATE_FAILED:
        code = errors.DOUYIN_LOGIN_UNAVAILABLE if data["unavailable"] else errors.DOUYIN_LOGIN_FAILED
        message = str(data.pop("message", "") or "")
        data["error"] = f"{code}|{message}" if message else code
    else:
        data.pop("message", None)
    return data


async def _apply_login_cookie(services, cookie: str) -> None:
    """把扫出来的 Cookie 写进设置（沿用设置页那条写入路径：内存 + 落盘）。"""
    cookies = dict(services.settings_config.cookies_config or {})
    cookies[_PLATFORM_KEY] = cookie
    services.settings_config.adopt_cookies_config(cookies)
    await services.config_manager.save_cookies_config(cookies)
    # 换了 Cookie，关注列表的游标链/缓存的账号资料都作废（缓存本身按 Cookie 指纹隔离）
    following.get_store().reset()
    logger.info(f"Douyin login: 新 Cookie 已写入配置（{len(cookie)} 字符）")


@router.post("/login")
async def start_login(user: str = Depends(get_current_user), services=Depends(get_services)):
    """开始一次扫码登录（已在跑就直接返回当前状态，不会开第二个浏览器）。"""
    session = _live_login() or _start_login()
    deadline = asyncio.get_running_loop().time() + _LOGIN_START_GRACE
    # 等一小会儿：没装 playwright 之类会秒失败，直接报出来比让前端白轮询一轮强
    while session.snapshot()["state"] == login.STATE_STARTING:
        if asyncio.get_running_loop().time() >= deadline:
            break
        await asyncio.sleep(0.1)
    return _login_payload(session)


@router.get("/login")
async def login_status(user: str = Depends(get_current_user), services=Depends(get_services)):
    """轮询扫码进度；一旦确认，这一轮就把 Cookie 落进设置。"""
    session = _any_login()
    if session is None:
        return {"state": "idle"}
    cookie = session.take_cookie()
    if cookie:
        await _apply_login_cookie(services, cookie)
    return _login_payload(session)


@router.delete("/login")
async def cancel_login(user: str = Depends(get_current_user)):
    """关掉登录框（浏览器线程会在一个轮询周期内退出并自行关闭）。"""
    session = _live_login() or _any_login()
    if session is not None and not session.is_finished():
        session.cancel()
    return _login_payload(session)