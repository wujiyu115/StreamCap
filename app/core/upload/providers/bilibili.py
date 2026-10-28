from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from ....utils.logger import logger
from ..models import (
    UploadAuthError,
    UploadLineUnavailable,
    UploadRateLimited,
    UploadRequest,
    UploadResult,
)

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

# 平台风控/限流：同一根因在不同版本、不同接口下给的 code 不一样——2026-10-28 生产
# pre_upload 回的是 406，biliup issue #1565 里是 601，issue #1362 里 submit 回 21566
# （还有 21564「上传过于频繁请等待24小时」）。所以既认 code，也认文案。
_RATE_LIMIT_CODES = frozenset({406, 601, 21070, 21564, 21566})
_RATE_LIMIT_PATTERNS = (
    "上传视频过快",
    "上传过快",
    "上传过于频繁",
    "投稿过于频繁",
    "投稿频繁",
    "稍作休息",
)
# JSON（"code":406）与 Rust Debug（ResponseData { code: 21566, ... }）两种写法都要认
_CODE_RE = re.compile(r'"?code"?\s*[:=]\s*(-?\d+)')
# 风控已经挂出待过的验证（B 站 601 响应会带 detail.v_voucher），能区分出「要人工过验证」
_VERIFY_RE = re.compile(r"v_voucher|需要验证|请完成验证|短信验证|验证码")

# 上传线路节点证书失效：2026-10-28 生产撞到 bldsa 线路三个节点里两个的 TLS 证书
# 2026-07-11 就过期了（biliup 报 invalid peer certificate ... not valid after 1783746060），
# biliup 用 rustls 严格校验、也没有 skip-verify 选项，同轮 4 次重试都打在同一台 → 必挂。
# 探线是按延迟挑最快的，坏节点很容易被反复选中，所以撞上就临时钉一条备用线路，
# 过一阵放回自动探线（否则那条快线修好了也永远用不到）。
_CERT_ERROR_MARKERS = (
    "invalid peer certificate",
    "certificate expired",
    "certificate is not valid after",
)
_LINE_FALLBACKS = ("tx", "bda2")
_LINE_FORCE_SECONDS = 1800


def detect_cert_error(output: str) -> bool:
    """判断 biliup 输出里是否有上传线路的证书校验失败（平台侧坏节点）。"""
    lowered = output.lower()
    return any(marker in lowered for marker in _CERT_ERROR_MARKERS)


def detect_rate_limit(output: str) -> tuple[int | None, bool] | None:
    """识别 biliup 输出里的风控/限流；不是这类错误时返回 None。

    返回 ``(code, needs_verify)``，code 在只有文案匹配时为 None。
    """
    if not any(pattern in output for pattern in _RATE_LIMIT_PATTERNS):
        codes = [int(match) for match in _CODE_RE.findall(output)]
        hit = next((item for item in codes if item in _RATE_LIMIT_CODES), None)
        if hit is None:
            return None
        return hit, bool(_VERIFY_RE.search(output))
    codes = [int(match) for match in _CODE_RE.findall(output)]
    code = next((item for item in codes if item in _RATE_LIMIT_CODES), codes[0] if codes else None)
    return code, bool(_VERIFY_RE.search(output))


class BilibiliProvider:
    """通过活跃维护的开源 biliup CLI 投稿，隔离平台私有协议变化。"""

    name = "bilibili"

    def __init__(self, run_path: str, executable: str = "biliup"):
        self.run_path = os.path.abspath(run_path)
        self.executable = executable
        #: 临时钉住的线路（None = 交给 biliup 自动探线）；worker 是单线程，无需加锁
        self._line: str | None = None
        self._line_until = 0.0

    def active_line(self) -> str | None:
        """当前是否钉住了某条线路；过了冷却期就放回自动探线。"""
        if self._line is None:
            return None
        if time.time() < self._line_until:
            return self._line
        logger.info(f"B站上传线路 {self._line} 观察期满，恢复自动探线")
        self._line = None
        self._line_until = 0.0
        return None

    def switch_line(self, reason: str) -> str:
        """换一条备用线路并临时钉住，返回新线路。"""
        candidates = [line for line in _LINE_FALLBACKS if line != self._line] or list(_LINE_FALLBACKS)
        self._line = candidates[0]
        self._line_until = time.time() + _LINE_FORCE_SECONDS
        logger.warning(
            f"B站上传线路 {reason} 不可用，临时改用 {self._line}（{_LINE_FORCE_SECONDS}s 后恢复自动探线）"
        )
        return self._line

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
        line = self.active_line()
        if line:
            # 透传 biliup 的线路选择，绕开探线会选中的坏节点
            command.extend(["--line", line])
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
            rate_limit = detect_rate_limit(tail)
            if rate_limit is not None:
                code, needs_verify = rate_limit
                raise UploadRateLimited(
                    f"Bilibili rate limited (code={code}): {tail}",
                    code=code,
                    needs_verify=needs_verify,
                )
            if detect_cert_error(tail):
                line = self.switch_line("biliup 探线选中的节点证书过期")
                raise UploadLineUnavailable(
                    f"上传线路节点 TLS 证书校验失败（平台侧问题，不是发布失败），"
                    f"已改走线路 {line} 重试：{tail}"
                )
            raise RuntimeError(f"biliup upload failed: {tail}")
        match = _REMOTE_ID.search(output)
        return UploadResult(
            provider=self.name,
            remote_id=match.group(1) if match else None,
            message=output[-4000:],
        )
