"""抖音扫码登录：会话状态机 + 接口层（写 Cookie / 取消 / 错误码）。

浏览器全部换成 fake 驱动，测试不起真 playwright、不发任何请求。
"""
import asyncio
import time

import pytest

from app.core.platforms import douyin_following as df
from app.core.platforms import douyin_login as dl
from app.server import error_codes as errors
from app.server.routers import douyin as douyin_router

QR_A = "data:image/png;base64,AAAA"
QR_B = "data:image/png;base64,BBBB"
COOKIE = "sessionid=abc; ttwid=xyz"


class FakeDriver:
    """脚本化浏览器：qr()/cookie() 按顺序吐预设值（最后一个会一直重复返回）。"""

    def __init__(self, qrs=None, cookies=None, open_error=None):
        self.qrs = list(qrs or [])
        self.cookies = list(cookies or [])
        self.open_error = open_error
        self.reloads = 0
        self.closed = False
        self.opened = False

    def open(self):
        self.opened = True
        if self.open_error is not None:
            raise self.open_error

    def qr(self):
        if not self.qrs:
            return None
        return self.qrs[0] if len(self.qrs) == 1 else self.qrs.pop(0)

    def reload(self):
        self.reloads += 1

    def cookie(self):
        return self.cookies.pop(0) if self.cookies else None

    def close(self):
        self.closed = True


@pytest.fixture
def driver_box():
    """把 fake 驱动交给会话（会话内部按 ``box["factory"]()`` 取）。"""
    box = {}

    def factory():
        return box["driver"]

    box["factory"] = factory
    return box


def make_session(box, driver, timeout=5.0, poll=0.01):
    box["driver"] = driver
    return dl.DouyinLoginSession(timeout=timeout, driver_factory=box["factory"], poll_interval=poll)


def wait_state(session, states, timeout=5.0):
    """等会话落到某个状态；超时就报出来，避免测试挂死。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        snapshot = session.snapshot()
        if snapshot["state"] in states:
            return snapshot
        time.sleep(0.02)
    raise AssertionError(f"会话没有进入 {states}，当前 {session.snapshot()['state']}")


def wait_for(condition, timeout=5.0):
    """等线程收尾那一步（状态先落、close() 后跑，直接断言会晃）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return
        time.sleep(0.02)
    raise AssertionError("条件没成立")


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- 会话状态机


def test_publishes_qr_then_confirms(driver_box):
    driver = FakeDriver(qrs=[None, (QR_A, "请使用抖音扫码", False)], cookies=[COOKIE])
    session = make_session(driver_box, driver).start()
    snapshot = wait_state(session, {dl.STATE_CONFIRMED})

    wait_for(lambda: driver.closed)  # 结束后浏览器一定被关掉
    assert driver.opened
    assert snapshot["qrcode"] == QR_A
    assert snapshot["hint"] == "请使用抖音扫码"
    assert snapshot["verify"] is False
    assert snapshot["message"] == ""  # 成功不留错误
    # Cookie 只能被取走一次，且不进快照（避免顺手泄漏到日志/响应里）
    assert "cookie" not in snapshot
    assert session.take_cookie() == COOKIE
    assert session.take_cookie() is None
    assert session.is_finished() is True


def test_confirming_state_from_hint(driver_box):
    driver = FakeDriver(qrs=[(QR_A, "扫码成功 请在手机上确认登录", False)])
    session = make_session(driver_box, driver).start()
    snapshot = wait_state(session, {dl.STATE_CONFIRMING})
    assert snapshot["state"] == dl.STATE_CONFIRMING


def test_second_verify_state(driver_box):
    driver = FakeDriver(qrs=[(QR_A, "请完成安全验证", True)])
    session = make_session(driver_box, driver).start()
    snapshot = wait_state(session, {dl.STATE_VERIFY})
    assert snapshot["verify"] is True


def test_new_qr_is_republished(driver_box):
    driver = FakeDriver(qrs=[(QR_A, "扫码", False), (QR_B, "扫码", False)], cookies=[None, COOKIE])
    session = make_session(driver_box, driver).start()
    wait_state(session, {dl.STATE_CONFIRMED})
    assert session.snapshot()["qrcode"] == QR_B


def test_stale_qr_triggers_reload(driver_box, monkeypatch):
    monkeypatch.setattr(dl, "QR_RELOAD_AFTER", 0.05)
    # 先拖几轮不登录，才走到「码没变也重载」那条分支
    driver = FakeDriver(qrs=[(QR_A, "扫码", False)], cookies=[None] * 12 + [COOKIE])
    session = make_session(driver_box, driver).start()
    wait_state(session, {dl.STATE_CONFIRMED})
    assert driver.reloads >= 1


def test_timeout_when_never_scanned(driver_box):
    driver = FakeDriver(qrs=[(QR_A, "扫码", False)])
    session = make_session(driver_box, driver, timeout=0.2).start()
    snapshot = wait_state(session, {dl.STATE_TIMEOUT})
    assert snapshot["message"] == ""
    wait_for(lambda: driver.closed)


def test_cancel_sets_state_immediately(driver_box):
    driver = FakeDriver(qrs=[(QR_A, "扫码", False)])
    session = make_session(driver_box, driver, timeout=30).start()
    wait_state(session, {dl.STATE_WAITING})
    session.cancel()
    assert session.snapshot()["state"] == dl.STATE_CANCELLED  # 不等线程收尾
    wait_state(session, {dl.STATE_CANCELLED})
    session.cancel()  # 幂等
    wait_for(lambda: driver.closed)


def test_no_qr_marks_page_blocked_not_unavailable(driver_box, monkeypatch):
    """没拿到码是页面/风控问题，不是「没装浏览器」——文案别把用户支去装浏览器。"""
    monkeypatch.setattr(dl, "QR_START_TIMEOUT", 0.1)
    driver = FakeDriver()
    session = make_session(driver_box, driver).start()
    snapshot = wait_state(session, {dl.STATE_FAILED})
    assert snapshot["unavailable"] is False
    assert snapshot["page_blocked"] is True
    assert "二维码" in snapshot["message"]


def test_login_unavailable_from_driver(driver_box):
    driver = FakeDriver(open_error=dl.LoginUnavailable("没装 playwright"))
    session = make_session(driver_box, driver).start()
    snapshot = wait_state(session, {dl.STATE_FAILED})
    assert snapshot["unavailable"] is True
    assert "playwright" in snapshot["message"]
    assert driver.closed


def test_page_blocked_from_driver_is_not_unavailable(driver_box):
    """首页没给登录入口是页面问题，不能报成「本部署没装浏览器」——生产真踩过。"""
    driver = FakeDriver(open_error=dl.LoginPageBlocked("抖音首页没找到「登录」入口（标题 'x'）"))
    session = make_session(driver_box, driver).start()
    snapshot = wait_state(session, {dl.STATE_FAILED})
    assert snapshot["unavailable"] is False
    assert snapshot["page_blocked"] is True
    assert "登录" in snapshot["message"]
    assert driver.closed


def test_unexpected_driver_error_is_not_unavailable(driver_box):
    driver = FakeDriver(open_error=RuntimeError("boom"))
    session = make_session(driver_box, driver).start()
    snapshot = wait_state(session, {dl.STATE_FAILED})
    assert snapshot["unavailable"] is False
    assert snapshot["message"] == "RuntimeError: boom"


def test_take_cookie_before_confirmed_is_none(driver_box):
    driver = FakeDriver(qrs=[(QR_A, "扫码", False)])
    session = make_session(driver_box, driver, timeout=30).start()
    assert session.take_cookie() is None
    assert session.is_finished() is False
    session.cancel()


def test_is_finished_keeps_confirmed_until_cookie_taken(driver_box):
    driver = FakeDriver(qrs=[(QR_A, "扫码", False)], cookies=[COOKIE])
    session = make_session(driver_box, driver).start()
    wait_state(session, {dl.STATE_CONFIRMED})
    # Cookie 还没被接口层取走 → 不能算结束，否则这一份登录态会被静默丢掉
    assert session.is_finished() is False
    session.take_cookie()
    assert session.is_finished() is True


def test_snapshot_shape(driver_box):
    session = make_session(driver_box, FakeDriver(qrs=[(QR_A, "扫码", False)]), timeout=30)
    snapshot = session.snapshot()
    assert set(snapshot) == {
        "state", "qrcode", "hint", "message", "verify", "unavailable",
        "page_blocked", "saved", "started_at", "elapsed", "expires_in",
    }
    assert snapshot["state"] == dl.STATE_STARTING  # 还没 start() 时是待启动
    assert snapshot["expires_in"] <= 30


# ---------------------------------------------------------------- 接口层


class FakeSettingsConfig:
    def __init__(self, cookies=None):
        self.cookies_config = dict(cookies or {})

    def adopt_cookies_config(self, cookies):
        self.cookies_config = dict(cookies)


class FakeConfigManager:
    def __init__(self):
        self.saved = []

    async def save_cookies_config(self, cookies):
        self.saved.append(dict(cookies))


class LoginServices:
    def __init__(self, cookies=None):
        self.settings_config = FakeSettingsConfig(cookies)
        self.config_manager = FakeConfigManager()


@pytest.fixture(autouse=True)
def reset_login_singleton(monkeypatch):
    """会话单例是模块级全局：每个用例前后都清掉，别串扰也别留线程。"""
    monkeypatch.setattr(douyin_router, "_LOGIN_SESSION", None)
    yield
    session = douyin_router._any_login()
    if session is not None:
        session.cancel()
        wait_state(session, dl.TERMINAL_STATES)


@pytest.fixture
def fake_session_factory(monkeypatch, driver_box):
    """让路由自己 new 出来的会话也用假驱动（否则会真去拉一个 chromium）。"""
    def build(**kwargs):
        return dl.DouyinLoginSession(
            driver_factory=driver_box["factory"], poll_interval=0.01, **kwargs
        )

    monkeypatch.setattr(douyin_router, "_LOGIN_SESSION_FACTORY", build)
    return driver_box


@pytest.fixture(autouse=True)
def count_store_reset(monkeypatch):
    """换 Cookie 必须让关注列表缓存作废（游标链是按 Cookie 指纹隔离的）。"""
    calls = []
    monkeypatch.setattr(df._STORE, "reset", lambda: calls.append(1))
    return calls


def install_session(monkeypatch, box, driver, **kwargs):
    """把一个（已启动的）会话塞进路由的单例位置，好直接测端点。"""
    session = make_session(box, driver, **kwargs)
    monkeypatch.setattr(douyin_router, "_LOGIN_SESSION", session)
    return session.start()


def test_status_without_session_is_idle():
    assert run(douyin_router.login_status(user="admin", services=LoginServices())) == {"state": "idle"}


def test_cancel_without_session_is_idle():
    assert run(douyin_router.cancel_login(user="admin")) == {"state": "idle"}


def test_login_payload_maps_failure_codes():
    session = dl.DouyinLoginSession()
    session._update(state=dl.STATE_FAILED, unavailable=True, message="浏览器启动失败：no libs")
    payload = douyin_router._login_payload(session)
    assert payload["error"] == f"{errors.DOUYIN_LOGIN_UNAVAILABLE}|浏览器启动失败：no libs"
    assert payload["unavailable"] is True
    assert "message" not in payload  # 失败信息只在 error 里带一次

    session._update(state=dl.STATE_FAILED, unavailable=False, message="RuntimeError: boom")
    assert douyin_router._login_payload(session)["error"] == (
        f"{errors.DOUYIN_LOGIN_FAILED}|RuntimeError: boom"
    )


def test_login_payload_maps_page_blocked_code():
    session = dl.DouyinLoginSession()
    session._update(state=dl.STATE_FAILED, page_blocked=True, message="首页没找到登录入口")
    payload = douyin_router._login_payload(session)
    assert payload["error"] == f"{errors.DOUYIN_LOGIN_PAGE_BLOCKED}|首页没找到登录入口"
    assert payload["unavailable"] is False
    assert payload["page_blocked"] is True


def test_login_payload_drops_cookie_and_started_at(driver_box):
    driver = FakeDriver(qrs=[(QR_A, "扫码", False)], cookies=[COOKIE])
    session = make_session(driver_box, driver).start()
    wait_state(session, {dl.STATE_CONFIRMED})
    payload = douyin_router._login_payload(session)
    assert "cookie" not in payload and "started_at" not in payload
    assert payload["state"] == dl.STATE_CONFIRMED
    assert payload["qrcode"] == QR_A


def test_status_applies_cookie_once(monkeypatch, driver_box, count_store_reset):
    services = LoginServices(cookies={"bilibili": "B"})
    session = install_session(monkeypatch, driver_box, FakeDriver(qrs=[(QR_A, "扫码", False)], cookies=[COOKIE]))
    wait_state(session, {dl.STATE_CONFIRMED})

    payload = run(douyin_router.login_status(user="admin", services=services))
    assert payload["state"] == dl.STATE_CONFIRMED and payload["saved"] is True
    # 写盘走的是设置页同一条路径：内存生效值 + 落盘 + 作废关注列表缓存
    assert services.settings_config.cookies_config == {"bilibili": "B", "douyin": COOKIE}
    assert services.config_manager.saved == [{"bilibili": "B", "douyin": COOKIE}]
    assert len(count_store_reset) == 1

    again = run(douyin_router.login_status(user="admin", services=services))
    # 状态是「粘」的：前端再轮一次还能看到 confirmed（不会闪回「未开始」），但绝不再写一次盘
    assert again["state"] == dl.STATE_CONFIRMED and again["saved"] is True
    assert len(services.config_manager.saved) == 1
    assert len(count_store_reset) == 1


def test_status_does_not_write_while_waiting(monkeypatch, driver_box, count_store_reset):
    services = LoginServices(cookies={"douyin": "OLD"})
    session = install_session(monkeypatch, driver_box, FakeDriver(qrs=[(QR_A, "扫码", False)]), timeout=30)
    wait_state(session, {dl.STATE_WAITING})
    payload = run(douyin_router.login_status(user="admin", services=services))
    assert payload["state"] == dl.STATE_WAITING and payload["saved"] is False
    assert services.settings_config.cookies_config == {"douyin": "OLD"}
    assert services.config_manager.saved == []
    assert count_store_reset == []
    session.cancel()


def test_start_login_returns_qr(monkeypatch, driver_box):
    session = install_session(
        monkeypatch, driver_box, FakeDriver(qrs=[(QR_A, "请使用抖音扫码", False)], cookies=[COOKIE])
    )
    payload = run(douyin_router.start_login(user="admin", services=LoginServices()))
    assert payload["state"] in (dl.STATE_WAITING, dl.STATE_CONFIRMED)
    assert payload["qrcode"] == QR_A
    assert douyin_router._any_login() is session


def test_start_login_reuses_running_session(monkeypatch, driver_box):
    session = install_session(
        monkeypatch, driver_box, FakeDriver(qrs=[(QR_A, "扫码", False)]), timeout=30
    )
    first = run(douyin_router.start_login(user="admin", services=LoginServices()))
    second = run(douyin_router.start_login(user="admin", services=LoginServices()))
    # 同一个会话：二维码一致，且没有第二个浏览器被拉起来
    assert second["qrcode"] == first["qrcode"] == QR_A
    assert douyin_router._any_login() is session
    assert douyin_router._live_login() is session
    session.cancel()


def test_start_login_reports_unavailable_fast(fake_session_factory):
    """没装浏览器时 POST 直接报错误码，前端不用白轮询一轮。"""
    fake_session_factory["driver"] = FakeDriver(open_error=dl.LoginUnavailable("没装 playwright"))
    payload = run(douyin_router.start_login(user="admin", services=LoginServices()))
    assert payload["state"] == dl.STATE_FAILED
    assert payload["error"].startswith(errors.DOUYIN_LOGIN_UNAVAILABLE)


def test_start_login_replaces_finished_session(fake_session_factory, monkeypatch):
    box = fake_session_factory
    old = install_session(monkeypatch, box, FakeDriver(open_error=dl.LoginUnavailable("x")))
    wait_state(old, {dl.STATE_FAILED})

    box["driver"] = FakeDriver(qrs=[(QR_B, "扫码", False)], cookies=[COOKIE])
    payload = run(douyin_router.start_login(user="admin", services=LoginServices()))
    assert payload["qrcode"] == QR_B
    assert douyin_router._any_login() is not old
    douyin_router._any_login().cancel()


def test_cancel_login_returns_cancelled(monkeypatch, driver_box):
    session = install_session(monkeypatch, driver_box, FakeDriver(qrs=[(QR_A, "扫码", False)]), timeout=30)
    wait_state(session, {dl.STATE_WAITING})
    payload = run(douyin_router.cancel_login(user="admin"))
    assert payload["state"] == dl.STATE_CANCELLED

# --------------------------------------------------- 「登录入口」探测（真页面逻辑）


class _LocatorStub:
    """只够 _try_click_login 用：count/is_visible/click。"""

    def __init__(self, page):
        self._page = page

    @property
    def first(self):
        return self

    def count(self):
        self._page.probes += 1
        return 1 if self._page.ready else 0

    def is_visible(self):
        return self._page.ready

    def click(self, timeout=None):
        self._page.clicks += 1


class _PageStub:
    """模拟「首页第 N 次探测后才渲染出登录入口」。"""

    def __init__(self, ready_after=0, closed=False):
        self.ready_after = ready_after
        self._closed = closed
        self.probes = 0
        self.clicks = 0
        self.waits = 0

    @property
    def ready(self):
        return self.probes > self.ready_after

    def get_by_role(self, role, name=None, exact=None):
        return _LocatorStub(self)

    def evaluate(self, script, arg=None):
        self.probes += 1
        return False  # JS 兜底恒不命中，只验证它被尝试过

    def is_closed(self):
        return self._closed

    def wait_for_timeout(self, ms):
        self.waits += 1
        time.sleep(min(ms, 10) / 1000)


def _driver_with_page(page):
    driver = dl.PlaywrightDriver()
    driver._page = page
    return driver


def test_click_login_polls_until_entry_renders():
    """入口晚几轮才出现时，必须轮询等出来（旧实现单次检查会直接判失败）。"""
    page = _PageStub(ready_after=2)
    assert _driver_with_page(page)._click_login(timeout=5.0) is True
    assert page.clicks == 1
    assert page.waits >= 1  # 真的等过，而不是查一次就放弃


def test_click_login_gives_up_after_timeout_is_bounded():
    page = _PageStub(ready_after=10**6)
    started = time.monotonic()
    assert _driver_with_page(page)._click_login(timeout=0.3) is False
    assert time.monotonic() - started < 2.0
    assert page.probes >= 2  # 至少探测了两轮


def test_click_login_returns_false_when_page_closed():
    page = _PageStub(ready_after=10**6, closed=True)
    assert _driver_with_page(page)._click_login(timeout=5.0) is False
