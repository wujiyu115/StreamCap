"""带优先级的信号量：交互请求先于周期监控拿到平台并发名额。

``asyncio.Semaphore`` 的等待队列是 FIFO 的：一轮监控（生产 ~170 个任务）把名额排满
后，用户点「播放」这类交互请求只能排在 167 个任务后面——2026-11 实测首帧直链要等
**170s**（同样的请求在空闲时 0.3~2s）。这里把等待者分两档（``priority`` /
``normal``），释放名额时优先放行 priority 档，全局并发上限不变。
"""
from __future__ import annotations

import asyncio
from collections import deque
from contextlib import asynccontextmanager

PRIORITY = "priority"
NORMAL = "normal"


class PrioritySemaphore:
    """并发上限 + 两档等待队列；同档内保持 FIFO（不饿死监控，只是交互能插队）。"""

    def __init__(self, value: int = 1):
        self._value = max(1, int(value))
        self._waiters: dict[str, deque[asyncio.Future]] = {
            PRIORITY: deque(),
            NORMAL: deque(),
        }

    @property
    def value(self) -> int:
        return self._value

    def _idle(self) -> bool:
        return not self._waiters[PRIORITY] and not self._waiters[NORMAL]

    async def acquire(self, priority: bool = False) -> None:
        # 没有等待者时才直接拿名额，保证「先来先得」不被后来的插队（同档）
        if self._value > 0 and self._idle():
            self._value -= 1
            return
        tier = PRIORITY if priority else NORMAL
        fut = asyncio.get_running_loop().create_future()
        self._waiters[tier].append(fut)
        try:
            await fut
        except asyncio.CancelledError:
            if fut.done() and not fut.cancelled():
                # 名额已经交到手上（_wake 先 set_result），调用方却取消了：还回去
                self.release()
            else:
                fut.cancel()
                try:
                    self._waiters[tier].remove(fut)
                except ValueError:
                    pass
            raise

    def release(self) -> None:
        self._value += 1
        self._wake()

    def _wake(self) -> None:
        while self._value > 0:
            if self._waiters[PRIORITY]:
                tier = PRIORITY
            elif self._waiters[NORMAL]:
                tier = NORMAL
            else:
                return
            fut = self._waiters[tier].popleft()
            if fut.done():
                continue  # 已取消的等待者不占名额
            self._value -= 1
            fut.set_result(True)

    @asynccontextmanager
    async def hold(self, priority: bool = False):
        await self.acquire(priority)
        try:
            yield
        finally:
            self.release()
