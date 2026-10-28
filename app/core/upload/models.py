from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class UploadAuthError(RuntimeError):
    """渠道凭据缺失、过期或被平台拒绝；重试不会自行恢复。"""


class UploadRateLimited(RuntimeError):
    """平台风控/限流（B 站「上传视频过快」「投稿过于频繁」）。

    这是**账号级**状态，平台侧要人工过一次人机/短信验证才解除；逐任务重试只会把
    重试预算烧在限流窗口里（2026-10-28 生产：49 条任务的 5 次预算 40 分钟烧光）。
    所以这类错误由渠道级冷却处理，且不消耗 ``attempts``。
    """

    def __init__(self, message: str, code: int | None = None, needs_verify: bool = False):
        super().__init__(message)
        self.code = code
        #: 响应里带 v_voucher / 「需要验证」字样时置真——说明平台已经挂着待过的验证
        self.needs_verify = needs_verify


class UploadLineUnavailable(RuntimeError):
    """上传线路的节点证书校验失败（平台侧坏节点），换条线路就能过。

    biliup 探线按延迟挑最快的那条，选中坏节点时同轮 4 次重试都打在同一台，必挂
    （2026-10-28 生产：bldsa 三个节点里两个证书 7-11 就过期了）。换线是可恢复的，
    所以不消耗 ``attempts``，也不冷却渠道。
    """


@dataclass(frozen=True)
class UploadRequest:
    """与具体上传渠道无关的一次投稿请求。"""

    job_id: str
    files: list[str]
    title: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class UploadResult:
    provider: str
    remote_id: str | None = None
    message: str = ""
