from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from ..models import UploadAuthError, UploadRequest, UploadResult

_REMOTE_ID = re.compile(r"\b(BV[0-9A-Za-z]{10})\b")
_AUTH_ERROR_PATTERNS = (
    "未登录",
    "登录失效",
    "登录过期",
    "cookie失效",
    "cookie已过期",
    "not logged in",
    "unauthorized",
    "-101",
    "-111",
)


class BilibiliProvider:
    """通过活跃维护的开源 biliup CLI 投稿，隔离平台私有协议变化。"""

    name = "bilibili"

    def __init__(self, run_path: str, executable: str = "biliup"):
        self.run_path = os.path.abspath(run_path)
        self.executable = executable

    def _resolve_cookie_file(self, configured: str) -> str:
        path = Path(configured or "config/bilibili/cookies.json")
        if not path.is_absolute():
            path = Path(self.run_path) / path
        return str(path.resolve())

    def build_command(self, request: UploadRequest, config: dict) -> list[str]:
        executable = shutil.which(self.executable) or self.executable
        cookie_file = self._resolve_cookie_file(str(config.get("cookie_file") or ""))
        command = [
            executable,
            "--user-cookie",
            cookie_file,
            "upload",
            "--submit",
            str(config.get("submit") or "web"),
            "--title",
            request.title[:80],
            "--tid",
            str(int(config.get("tid") or 138)),
            "--tag",
            str(config.get("tags") or "直播录像,录播"),
            "--copyright",
            str(int(config.get("copyright") or 1)),
            "--limit",
            str(max(1, int(config.get("upload_threads") or 3))),
        ]
        description = str(config.get("description") or "").strip()
        if description:
            command.extend(["--desc", description])
        if bool(config.get("only_self")):
            command.extend(["--is-only-self", "1"])
        if bool(config.get("no_reprint")):
            command.extend(["--no-reprint", "1"])
        # 刻意不传 --source：本渠道只投稿识别产物，不附带直播间地址。
        command.extend(request.files)
        return command

    def upload(self, request: UploadRequest, config: dict) -> UploadResult:
        cookie_file = self._resolve_cookie_file(str(config.get("cookie_file") or ""))
        if not os.path.isfile(cookie_file):
            raise UploadAuthError(f"Bilibili cookie file not found: {cookie_file}")
        for path in request.files:
            if not os.path.isfile(path):
                raise RuntimeError(f"Upload file not found: {path}")

        completed = subprocess.run(
            self.build_command(request, config),
            cwd=self.run_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            timeout=max(60, int(config.get("timeout_seconds") or 21600)),
            check=False,
        )
        output = (completed.stdout or "").strip()
        if completed.returncode != 0:
            tail = output[-4000:] if output else f"exit code {completed.returncode}"
            normalized = tail.lower().replace(" ", "")
            if any(pattern.replace(" ", "") in normalized for pattern in _AUTH_ERROR_PATTERNS):
                raise UploadAuthError(f"Bilibili login expired: {tail}")
            raise RuntimeError(f"biliup upload failed: {tail}")
        match = _REMOTE_ID.search(output)
        return UploadResult(
            provider=self.name,
            remote_id=match.group(1) if match else None,
            message=output[-4000:],
        )
