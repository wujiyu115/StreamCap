"""本地 Cookie 到期巡检与平台登录失效通知；复用所有启用的推送渠道。"""
from __future__ import annotations

import hashlib
import json
import os
import threading

from ..core.platforms.douyin_following import parse_cookie_expiry
from ..utils.logger import logger
from .message_pusher import MessagePusher


class CookieHealthMonitor:
    def __init__(self, run_path, settings):
        self.settings = settings
        self.path = os.path.join(run_path, "config", "cookie_health.json")
        self.pusher = MessagePusher(settings)
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = None
        try:
            with open(self.path, encoding="utf-8") as file:
                self.notified = json.load(file)
        except (OSError, ValueError):
            self.notified = {}

    def check(self):
        cookie = (self.settings.get_cookies_value("douyin") or "").strip()
        if parse_cookie_expiry(cookie)["state"] == "expired":
            self.report_expired("douyin", cookie)

    def report_expired(self, platform, cookie):
        if not cookie or not self.pusher.is_any_push_channel_enabled():
            return
        fingerprint = hashlib.sha256(cookie.encode()).hexdigest()
        with self.lock:
            if self.notified.get(platform) == fingerprint:
                return
            # 通知失败不落去重标记，下轮可重试；不把 Cookie 内容写入磁盘或消息。
            try:
                self.pusher.push_messages_sync(
                    "StreamCap 抖音登录失效",
                    "抖音 Cookie 登录态已失效，请在设置页重新扫码登录或更新 Cookie。",
                )
                self.notified[platform] = fingerprint
                tmp = self.path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as file:
                    json.dump(self.notified, file)
                os.replace(tmp, self.path)
            except Exception as exc:
                logger.warning(f"Cookie 失效通知失败: {type(exc).__name__}")

    def start(self):
        def run():
            while not self.stop_event.is_set():
                try:
                    self.check()
                except Exception as exc:
                    logger.warning(f"Cookie 巡检失败: {type(exc).__name__}")
                self.stop_event.wait(60)
        self.thread = threading.Thread(target=run, name="CookieHealth", daemon=True)
        self.thread.start()

    def shutdown(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2)
