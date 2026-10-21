"""抖音扫码登录：在 WebUI 里点一下，把失效的 Cookie 换成新的。

为什么必须在服务里起真浏览器，而不是直接打 passport 接口拿二维码（2026-10-21 实测）：

* ``sso.douyin.com/get_qrcode/`` → 200，但返回的是风控 JS 挑战页（要跑 JS 才给码）
* ``www.douyin.com/passport/web/get_qrcode/`` → 200 + ``error_code 4031``
  「您正在尝试访问的网站存在安全风险…已阻止此次访问」
* ``sso.douyin.com/passport/ticket/get_qrcode/`` → 403「路由已封禁」

真浏览器（headless 也行）能正常拿到码，但**必须做两件事**，否则抖音整页换成滑块拼图：

1. 伪装成真 Chrome 的 UA：headless 默认 UA 带 ``HeadlessChrome``；
2. 抹掉 ``navigator.webdriver``。

（对照实验：默认 UA + webdriver=true → 页面变成「请完成下列验证后继续」的拼图；
伪装后 → 正常弹出扫码登录框。见 AGENTS.md「抖音扫码登录」。）

线程模型：Playwright 的同步 API 不能在 asyncio 事件循环里跑，所以整个登录会话丢在一个
后台线程里，接口层只读它的「快照」。Cookie 拿到后不在这里写盘——由接口层在事件循环上写，
避免跨 loop 去动配置文件。
"""

from __future__ import annotations

import os
import signal
import threading
import time
from typing import Any, Callable, Protocol

from ...utils.logger import logger

HOMEPAGE = "https://www.douyin.com/"
# 抖音的登录框有几种形态：普通扫码、二次安全验证（#uc-second-verify）
QR_BOX_SELECTOR = "#douyin_login_comp_scan_code #animate_qrcode_container"
VERIFY_BOX_SELECTOR = "#uc-second-verify #uc_verification_animate_qrcode_container"
REAL_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)
LAUNCH_ARGS = [
    "--no-sandbox",  # 容器里以 root 跑 chromium，没有它起不来
    "--disable-dev-shm-usage",  # /dev/shm 默认 64MB，不够会崩
    "--disable-blink-features=AutomationControlled",
]
WEBDRIVER_PATCH = "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
# 给浏览器进程打标记（env 变量），关闭后按标记收尸，见 reap_tagged_browsers
BROWSER_TAG_ENV = "STREAMCAP_DY_LOGIN_TAG"

LOGIN_TIMEOUT = 300.0  # 等扫码的总时长（秒）
POLL_INTERVAL = 1.5  # 轮询间隔
QR_START_TIMEOUT = 120.0  # 从打开首页到拿到第一张码的时限
#   NAS 实测：空载一次性容器 27s；生产容器刚重启（模型加载、152 个任务抢 CPU）75s
#   才出码——留薄了必翻车，反正总时限 LOGIN_TIMEOUT=300s 兜着
QR_RELOAD_AFTER = 90.0  # 码没变也超过这么久就整页 reload 兜底（token 可能已换）
LOGIN_ENTRY_TIMEOUT = 20.0  # 等首页渲染出「登录」入口的时限（容器里首屏可能要十几秒）
LOGIN_ENTRY_POLL = 0.5
LOGIN_ENTRY_TEXTS = ("登录", "立即登录")
# 只要有其中之一就认为登录态已经落到浏览器里（与 DDr 的 LOGIN_NAMES 一致）
LOGIN_COOKIE_NAMES = ("sessionid", "sessionid_ss", "sid_tt", "sid_guard")
COOKIE_ORIGINS = (HOMEPAGE, "https://sso.douyin.com/", "https://www-hj.douyin.com/")

STATE_STARTING = "starting"
STATE_WAITING = "waiting"
STATE_CONFIRMING = "confirming"
STATE_VERIFY = "verify"
STATE_CONFIRMED = "confirmed"
STATE_FAILED = "failed"
STATE_CANCELLED = "cancelled"
STATE_TIMEOUT = "timeout"
TERMINAL_STATES = frozenset({STATE_CONFIRMED, STATE_FAILED, STATE_CANCELLED, STATE_TIMEOUT})

# 抖音自己的提示文案 → 我们的状态（页面里的字，不做翻译）
CONFIRMING_HINTS = ("扫码成功", "请在手机上确认", "请在手机端确认", "已扫码")


class LoginUnavailable(RuntimeError):
    """服务里没有可用的浏览器（没装 playwright 或浏览器启动失败）。

    单独一类是为了让接口层返回「本部署没装登录组件」这种可翻译的错误码，而不是把它
    混进「抖音那边出错」里。
    """


class LoginPageBlocked(RuntimeError):
    """首页没给出登录入口（首屏太慢 / 页面改版 / 被风控）——浏览器本身是好的。

    必须和 :class:`LoginUnavailable` 分开：后者才会被渲染成「本部署没装浏览器组件」，
    而这个场景重试或换时间多半能成，文案得如实说，别把用户支去装浏览器。
    """


class LoginDriver(Protocol):
    """浏览器操作的最小接口（单测注入假实现，不依赖 playwright）。"""

    def open(self) -> None: ...

    def qr(self) -> tuple[str, str, bool] | None:
        """返回 ``(data_url, 页面提示文案, 是否二次安全验证)``；没有就返回 None。"""

    def reload(self) -> None: ...

    def cookie(self) -> str | None:
        """已登录则返回 Cookie 串，否则 None。"""

    def close(self) -> None: ...


# ------------------------------------------------------------------ 会话状态机


class DouyinLoginSession:
    """一次扫码登录。后台线程跑浏览器，主线程（事件循环）只读快照。

    ``driver_factory`` 只为测试注入，生产走 :class:`PlaywrightDriver`。
    """

    def __init__(
        self,
        timeout: float = LOGIN_TIMEOUT,
        driver_factory: Callable[[], LoginDriver] | None = None,
        poll_interval: float = POLL_INTERVAL,
    ):
        self.timeout = timeout
        self.poll_interval = poll_interval
        self._driver_factory = driver_factory
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._started_at = time.time()
        self._state = STATE_STARTING
        self._qrcode = ""
        self._hint = ""
        self._message = ""
        self._cookie = ""
        self._verify = False
        self._unavailable = False
        self._page_blocked = False
        self._saved = False
        self._thread = threading.Thread(target=self._run, name="douyin-login", daemon=True)

    # -- 生命周期 ----------------------------------------------------------
    def start(self) -> "DouyinLoginSession":
        if self._thread.ident is None:  # 幂等：重复 start 不要炸线程
            self._thread.start()
        return self

    def cancel(self) -> None:
        self._cancel.set()
        with self._lock:
            if self._state not in TERMINAL_STATES:
                # 立即落状态：否则取消后这一轮响应还会说「等待扫码」，前端要等到下一轮
                # 才看到 cancelled（线程自己最多一个轮询周期后也会落同一个值）
                self._state = STATE_CANCELLED

    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def is_finished(self) -> bool:
        """会话结束、且该做的都做完了（确认后还没被接口层取走 Cookie 的不算结束）。"""
        with self._lock:
            if self._state == STATE_CONFIRMED:
                return self._saved
            return self._state in TERMINAL_STATES

    def take_cookie(self) -> str | None:
        """确认登录后取出 Cookie（只给接口层用，不进快照/响应）。"""
        with self._lock:
            if self._state != STATE_CONFIRMED or self._saved or not self._cookie:
                return None
            self._saved = True
            return self._cookie

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "state": self._state,
                "qrcode": self._qrcode,
                "hint": self._hint,
                "message": self._message if self._state == STATE_FAILED else "",
                "page_blocked": self._page_blocked,
                "verify": self._verify,
                "unavailable": self._unavailable,
                "saved": self._saved,
                "started_at": self._started_at,
                "elapsed": time.time() - self._started_at,
                "expires_in": max(0.0, self.timeout - (time.time() - self._started_at)),
            }

    # -- 内部 --------------------------------------------------------------
    def _update(self, **fields: Any) -> None:
        with self._lock:
            for key, value in fields.items():
                setattr(self, f"_{key}", value)

    def _run(self) -> None:
        driver: LoginDriver | None = None
        try:
            factory = self._driver_factory or PlaywrightDriver
            driver = factory()
            driver.open()
            deadline = time.time() + self.timeout
            qr = self._await_first_qr(driver, deadline)
            if qr is None:
                if self._cancel.is_set():
                    self._update(state=STATE_CANCELLED)
                else:
                    self._update(
                        state=STATE_FAILED,
                        page_blocked=True,
                        message="抖音登录框一直没出二维码（可能被风控限流，或页面加载太慢）",
                    )
                return
            self._publish(qr)
            self._poll(driver, deadline)
        except LoginUnavailable as exc:
            logger.warning(f"Douyin login unavailable: {exc}")
            self._update(state=STATE_FAILED, unavailable=True, message=str(exc))
        except LoginPageBlocked as exc:
            logger.warning(f"Douyin login page blocked: {exc}")
            self._update(state=STATE_FAILED, page_blocked=True, message=str(exc))
        except Exception as exc:  # 浏览器/页面异常别把请求线程带走
            logger.error(f"Douyin login failed: {type(exc).__name__}: {exc}")
            self._update(state=STATE_FAILED, message=f"{type(exc).__name__}: {exc}")
        finally:
            if driver is not None:
                try:
                    driver.close()
                except Exception as exc:
                    logger.debug(f"Douyin login close failed: {exc}")

    def _await_first_qr(self, driver: LoginDriver, deadline: float) -> tuple[str, str, bool] | None:
        limit = min(deadline, time.time() + QR_START_TIMEOUT)
        while time.time() < limit and not self._cancel.is_set():
            qr = driver.qr()
            if qr:
                return qr
            self._cancel.wait(1.0)
        return None

    def _publish(self, qr: tuple[str, str, bool]) -> None:
        data_url, hint, verify = qr
        self._update(
            state=STATE_VERIFY if verify else self._hint_state(hint),
            qrcode=data_url,
            hint=hint,
            verify=verify,
        )

    @staticmethod
    def _hint_state(hint: str) -> str:
        return STATE_CONFIRMING if any(word in hint for word in CONFIRMING_HINTS) else STATE_WAITING

    def _poll(self, driver: LoginDriver, deadline: float) -> None:
        signature = self._qrcode
        last_reload = time.time()
        while time.time() < deadline and not self._cancel.is_set():
            cookie = driver.cookie()
            if cookie:
                self._update(state=STATE_CONFIRMED, cookie=cookie, message="")
                logger.info("Douyin login confirmed: 浏览器里已出现登录态 Cookie")
                return

            fresh = driver.qr()
            if fresh and fresh[0] != signature:
                signature = fresh[0]
                last_reload = time.time()
                self._publish(fresh)
            elif time.time() - last_reload > QR_RELOAD_AFTER:
                # 码没变但拖太久：token 可能在服务端已经换了，整页重来一次拿最新的码
                last_reload = time.time()
                driver.reload()
            self._cancel.wait(self.poll_interval)

        if self._cancel.is_set():
            self._update(state=STATE_CANCELLED)
        else:
            self._update(state=STATE_TIMEOUT)


# ------------------------------------------------------------------ 真浏览器


def _iter_browser_procs(proc_root: str = "/proc"):
    """遍历「名字像浏览器」的进程，产出 ``(pid, comm, state, ppid)``。"""
    try:
        entries = os.listdir(proc_root)
    except OSError:
        return
    for entry in entries:
        if not entry.isdigit():
            continue
        base = os.path.join(proc_root, entry)
        try:
            with open(os.path.join(base, "comm"), "rb") as fp:
                comm = fp.read().decode("utf-8", "replace").strip()
            if "chrome" not in comm.lower() and "headless" not in comm.lower():
                continue
            with open(os.path.join(base, "stat"), "rb") as fp:
                # stat 格式：pid (comm) state ppid ...；comm 里可能带 ')'，从右侧切
                tail = fp.read().decode("utf-8", "replace").rsplit(")", 1)[1].split()
            state, ppid = tail[0], int(tail[1])
        except (OSError, IndexError, ValueError):
            continue
        yield int(entry), comm, state, ppid


def reap_tagged_browsers(tag: str, proc_root: str = "/proc") -> int:
    """杀掉带 ``tag`` 标记的 chromium 进程，返回杀掉的个数。

    为什么需要：``browser.close()`` + ``playwright.stop()`` 都正常返回、也不抛异常，
    但容器里实测**仍会剩 4 个 chrome-headless 进程活着**（2026-10 生产与一次性容器都复现）。
    每试一次扫码就漏一组进程，攒着白吃内存，所以在优雅关闭之后按 env 标记收尸。

    只杀「进程名带 chrome/headless」且「environ 里带本会话唯一标记」的进程，
    因此不会误伤别的程序（标记是每次会话现生成的）。
    """
    if not tag:
        return 0
    needle = f"{BROWSER_TAG_ENV}={tag}".encode()
    killed = 0
    for pid, _comm, _state, _ppid in _iter_browser_procs(proc_root):
        try:
            with open(os.path.join(proc_root, str(pid), "environ"), "rb") as fp:
                if needle not in fp.read():
                    continue
        except OSError:
            continue
        try:
            os.kill(pid, signal.SIGKILL)
            killed += 1
        except OSError:
            continue
    return killed


def reap_zombie_children(proc_root: str = "/proc") -> int:
    """收掉过继给本进程的 chromium 僵尸，返回收掉的个数。

    ``playwright.stop()`` 把 node driver 干掉后，被杀掉的 chromium 会被过继给
    PID 1（容器里就是 main.py 自己），而 Python 不会自动 wait → ``ps`` 里永久
    留一排 ``chrome-headless`` 的 Z（RSS 0，但卡 PID 表、看着膈应）。

    **只**对「名字像浏览器 + 状态 Z + ppid 就是本进程」的做 ``waitpid``，
    绝不 ``waitpid(-1)``：录制用的 ffmpeg 子进程挂在同一进程下，一把捞会把它们的
    退出码吃掉，:class:`~app.core.recording.record_manager.RecordManager` 会跟着坏。
    """
    me = os.getpid()
    reaped = 0
    for pid, _comm, state, ppid in _iter_browser_procs(proc_root):
        if state != "Z" or ppid != me:
            continue
        try:
            os.waitpid(pid, os.WNOHANG)
            reaped += 1
        except OSError:
            continue
    return reaped


class PlaywrightDriver:
    """headless chromium-headless-shell 实现（pyproject/镜像里带 playwright 才有）。"""

    def __init__(self) -> None:
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._tag = ""

    def open(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # 镜像没带登录组件
            raise LoginUnavailable(
                "当前部署没有安装 playwright（登录组件），请在设置页手动粘贴 Cookie"
            ) from exc
        self._playwright = sync_playwright().start()
        self._tag = os.urandom(8).hex()
        try:
            # env 里带上标记，关闭后好按标记收尸（传全量 os.environ，不赌合并语义）
            self._browser = self._playwright.chromium.launch(
                headless=True,
                args=LAUNCH_ARGS,
                env=dict(os.environ, **{BROWSER_TAG_ENV: self._tag}),
            )
        except Exception as exc:
            self._playwright.stop()
            self._playwright = None
            raise LoginUnavailable(f"浏览器启动失败：{exc}") from exc
        self._context = self._browser.new_context(
            user_agent=REAL_UA,  # 必须伪装，否则整页变滑块拼图
            viewport={"width": 1440, "height": 900},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )
        self._context.add_init_script(WEBDRIVER_PATCH)
        self._page = self._context.new_page()
        self._page.goto(HOMEPAGE, wait_until="domcontentloaded", timeout=45_000)
        self._page.wait_for_timeout(3_000)
        if self._click_login():
            return
        # 首屏慢/偶发渲染失败：整页重来一次再等，比直接判死刑合理（生产上真踩过）
        logger.info("Douyin login entry not found, reloading homepage once")
        self._reload_homepage()
        if self._click_login():
            return
        title = url = ""
        try:
            title, url = self._page.title()[:80], self._page.url[:120]
        except Exception:
            pass
        raise LoginPageBlocked(f"抖音首页没找到「登录」入口（标题 {title!r}，地址 {url}）")

    def _reload_homepage(self) -> None:
        assert self._page is not None
        try:
            self._page.reload(wait_until="domcontentloaded", timeout=45_000)
            self._page.wait_for_timeout(3_000)
        except Exception as exc:
            logger.debug(f"Douyin homepage reload failed: {exc}")

    def _click_login(self, timeout: float = LOGIN_ENTRY_TIMEOUT) -> bool:
        """等首页把登录入口渲染出来再点（**必须轮询**）。

        以前是「固定等 3s + count() 查一次」，容器刚重启/首屏 JS 慢十几秒时必然
        查不到，然后被当成「没装浏览器组件」——2026-10 生产实测踩到。
        """
        assert self._page is not None
        deadline = time.monotonic() + timeout
        while True:
            if self._try_click_login():
                return True
            if time.monotonic() >= deadline or self._page.is_closed():
                return False
            self._page.wait_for_timeout(int(LOGIN_ENTRY_POLL * 1000))

    def _try_click_login(self) -> bool:
        assert self._page is not None
        for role in ("button", "link"):
            try:
                locator = self._page.get_by_role(role, name="登录", exact=True).first
                if locator.count() and locator.is_visible():
                    locator.click(timeout=8_000)
                    return True
            except Exception:
                continue
        # 兜底：有的 A/B 版本把入口做成普通 div/span，不挂 role
        try:
            return bool(
                self._page.evaluate(
                    """(texts) => {
                        const els = [...document.querySelectorAll('button,a,span,div,p')]
                            .filter(el => {
                                const t = (el.innerText || '').trim();
                                if (!texts.includes(t)) return false;
                                const r = el.getBoundingClientRect();
                                return r.width > 0 && r.height > 0 && r.top < window.innerHeight;
                            });
                        const el = els[els.length - 1];
                        if (!el) return false;
                        el.click();
                        return true;
                    }""",
                    list(LOGIN_ENTRY_TEXTS),
                )
            )
        except Exception:
            return False

    def qr(self) -> tuple[str, str, bool] | None:
        if self._page is None or self._page.is_closed():
            return None
        try:
            result = self._page.evaluate(
                """([verifyBoxSel, boxSel]) => {
                    const verify = document.querySelector(verifyBoxSel);
                    const container = verify
                        ? verify.querySelector('#uc_verification_animate_qrcode_container')
                        : document.querySelector(boxSel);
                    if (!container) return null;
                    let img = container.querySelector("img[aria-label='二维码']");
                    if (!img || !(img.getAttribute('src') || '').startsWith('data:image')) {
                        img = document.querySelector("img[aria-label='二维码']");
                    }
                    const src = img && (img.getAttribute('src') || '');
                    if (!src || !src.startsWith('data:image')) return null;
                    const hint = container.innerText.split(/\\n+/).map(s => s.trim())
                        .filter(s => s && s !== '如何扫码').join(' ');
                    if (/失效|过期|刷新/.test(hint)) {
                        // 过期时顺手点它自己的「刷新」，省得整页 reload
                        [...container.querySelectorAll('*')].reverse()
                            .find(el => /刷新/.test(el.textContent || ''))?.click();
                    }
                    return { image: src, hint: hint.slice(0, 60), verify: Boolean(verify) };
                }""",
                [VERIFY_BOX_SELECTOR, QR_BOX_SELECTOR],
            )
        except Exception:
            return None
        if not result:
            return None
        return str(result["image"]), str(result["hint"]), bool(result["verify"])

    def reload(self) -> None:
        if self._page is None or self._page.is_closed():
            return
        try:
            self._reload_homepage()
            self._click_login()
        except Exception as exc:
            logger.debug(f"Douyin login reload failed: {exc}")

    def cookie(self) -> str | None:
        if self._context is None:
            return None
        try:
            items = self._context.cookies(list(COOKIE_ORIGINS))
        except Exception:
            return None
        jar: dict[str, str] = {}
        for item in items:
            name, value = item.get("name"), item.get("value")
            domain = str(item.get("domain", "")).lstrip(".").lower()
            if not name or not value:
                continue
            if domain == "douyin.com" or domain.endswith(".douyin.com"):
                jar[name] = value
        if not any(name in jar for name in LOGIN_COOKIE_NAMES):
            return None
        return "; ".join(f"{key}={value}" for key, value in jar.items())

    def close(self) -> None:
        for closer in (self._context, self._browser):
            try:
                if closer is not None:
                    closer.close()
            except Exception:
                pass
        # 优雅关闭偶尔留残兵（实测 4 个活着），趁 driver 还没停按标记补一刀，让它来收
        try:
            killed = reap_tagged_browsers(self._tag)
            if killed:
                logger.info(f"Douyin login reaped {killed} leftover browser process(es)")
        except Exception as exc:
            logger.debug(f"Douyin login reap failed: {exc}")
        try:
            if self._playwright is not None:
                self._playwright.stop()
        except Exception:
            pass
        # driver 死后被杀的 chromium 过继到本进程，会变僵尸没人 wait，顺手收掉
        try:
            zombies = reap_zombie_children()
            if zombies:
                logger.info(f"Douyin login reaped {zombies} zombie browser process(es)")
        except Exception as exc:
            logger.debug(f"Douyin login zombie reap failed: {exc}")
        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None
        self._tag = ""


__all__ = [
    "DouyinLoginSession",
    "LoginDriver",
    "LoginUnavailable",
    "LoginPageBlocked",
    "PlaywrightDriver",
    "reap_tagged_browsers",
    "reap_zombie_children",
    "STATE_CANCELLED",
    "STATE_CONFIRMED",
    "STATE_CONFIRMING",
    "STATE_FAILED",
    "STATE_STARTING",
    "STATE_TIMEOUT",
    "STATE_VERIFY",
    "STATE_WAITING",
]