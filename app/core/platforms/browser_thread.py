"""Playwright 同步 API 的独占工作线程。

`sync_playwright` 建出来的 greenlet 绑在**创建它的线程**上：同一个 `CreatorSession`
先后落到 `asyncio.to_thread` 的两个不同线程池线程上，就会炸

    greenlet.error: Cannot switch to a different thread

（2026-11-27 生产取关 500 的真因：第一次取关在池线程 A 上建了浏览器，第二次落到线程 B。）

所以凡是要碰同步浏览器对象的调用——取关、关注、页面回读、`reset_session`——都必须排队
到这里的**唯一**线程上跑。`asyncio.to_thread` 用的是默认线程池（`min(32, cpu+4)` 个线程），
每次派发到哪个线程不确定，不能用来跑这类对象。
"""

from __future__ import annotations

import asyncio
import functools
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="douyin-browser")
_THREAD_ID: int | None = None
_THREAD_LOCK = threading.Lock()


def _mark_owner() -> None:
    global _THREAD_ID
    with _THREAD_LOCK:
        _THREAD_ID = threading.get_ident()


def owner_thread_id() -> int | None:
    """当前独占线程的 ident（还没跑过任何任务时为 None）。"""
    return _THREAD_ID


def run_sync(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """在独占线程上同步执行并等结果（供非事件循环的调用方用）。"""
    future = _EXECUTOR.submit(fn, *args, **kwargs)
    _mark_owner()
    return future.result()


async def run_async(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """把同步的浏览器调用丢到独占线程上执行，不阻塞事件循环。"""
    loop = asyncio.get_running_loop()
    call = functools.partial(fn, *args, **kwargs)
    result = await loop.run_in_executor(_EXECUTOR, call)
    _mark_owner()
    return result


def shutdown() -> None:
    """退出/测试用：关掉执行器（不强制杀正在跑的任务）。"""
    _EXECUTOR.shutdown(wait=False)