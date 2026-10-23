"""B站官方网页扫码接口；只保存用户明确扫码确认后的登录态。"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import threading
import time
import urllib.parse
import uuid
from pathlib import Path

import httpx
import qrcode

TV_BASE = "https://passport.bilibili.com/x/passport-tv-login/qrcode"
TV_APPKEY = "4409e2ce8ffd12b8"
TV_APPSEC = "59b43e04ad6965f34319062b478f83dd"


class BilibiliLoginSession:
    def __init__(self, cookie_path: str):
        self.cookie_path = Path(cookie_path)
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.state = {"state": "starting", "qrcode": None, "saved": False}

    def snapshot(self):
        with self.lock:
            return dict(self.state)

    def update(self, **values):
        with self.lock:
            self.state.update(values)

    def start(self):
        threading.Thread(target=self.run, name="BilibiliLogin", daemon=True).start()

    def cancel(self):
        # 取消与落盘共用锁，确保关闭会话后不会又写入凭据。
        with self.lock:
            self.stop.set()
            if self.state["state"] != "confirmed":
                self.state["state"] = "cancelled"

    @staticmethod
    def _signed(**values):
        values.update(appkey=TV_APPKEY, local_id="0", ts=int(time.time()))
        query = urllib.parse.urlencode(sorted(values.items()))
        values["sign"] = hashlib.md5((query + TV_APPSEC).encode()).hexdigest()
        return values

    def run(self):
        try:
            headers = {"User-Agent": "Mozilla/5.0 BiliDroid/7.0.0", "Referer": "https://www.bilibili.com/"}
            with httpx.Client(timeout=15, headers=headers) as client:
                payload = client.post(TV_BASE + "/auth_code", data=self._signed()).json()
                if payload.get("code") != 0:
                    raise RuntimeError("获取B站二维码失败")
                data = payload["data"]
                image = qrcode.make(data["url"])
                buffer = io.BytesIO()
                image.save(buffer, format="PNG")
                encoded = base64.b64encode(buffer.getvalue()).decode()
                self.update(state="waiting", qrcode="data:image/png;base64," + encoded)
                deadline = time.monotonic() + 180
                while not self.stop.wait(1.5):
                    result = client.post(
                        TV_BASE + "/poll", data=self._signed(auth_code=data["auth_code"])
                    ).json()
                    if result.get("code") == 0 and isinstance(result.get("data"), dict):
                        self.save(result["data"])
                        return
                    if result.get("code") in (86038, -3) or time.monotonic() >= deadline:
                        self.update(state="timeout")
                        return
                    # TV 登录待扫码/待确认的业务码可能随客户端版本变化；非零先继续等。
                    self.update(state="scanned" if result.get("code") == 86090 else "waiting")
        except Exception:
            if not self.stop.is_set():
                self.update(state="failed", error="B站扫码登录失败，请重试")

    def save(self, login_info):
        with self.lock:
            if self.stop.is_set():
                return
            self.cookie_path.parent.mkdir(parents=True, exist_ok=True)
            if self.cookie_path.is_file():
                backup = self.cookie_path.with_name(f"cookies-{time.time_ns()}.bak")
                backup.write_bytes(self.cookie_path.read_bytes())
                os.chmod(backup, 0o600)
                for old in sorted(self.cookie_path.parent.glob("cookies-*.bak"))[:-5]:
                    old.unlink()
            payload = dict(login_info)
            payload["platform"] = "BiliTV"
            if not isinstance(payload.get("cookie_info"), dict) or not isinstance(payload.get("token_info"), dict):
                raise RuntimeError("扫码成功但缺少投稿凭据")
            tmp = self.cookie_path.with_name(f".cookies-{uuid.uuid4().hex}.tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(payload, file)
            os.replace(tmp, self.cookie_path)
            self.state.update(state="confirmed", saved=True, qrcode=None)
