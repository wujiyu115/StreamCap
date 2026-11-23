"""带优先级的信号量：并发上限不变，交互请求能插到监控前面；取消不泄漏名额。"""
import asyncio
import time

from app.core.recording.priority_semaphore import PrioritySemaphore


def run(coro):
    return asyncio.run(coro)


def test_caps_concurrency():
    sem = PrioritySemaphore(2)
    state = {"cur": 0, "peak": 0}

    async def worker():
        async with sem.hold():
            state["cur"] += 1
            state["peak"] = max(state["peak"], state["cur"])
            await asyncio.sleep(0.05)
            state["cur"] -= 1

    async def scenario():
        await asyncio.gather(*(worker() for _ in range(6)))

    run(scenario())
    assert state["peak"] == 2


def test_priority_jumps_normal_backlog():
    """normal 档排满后，priority 档只等「一个名额释放」，不排在整条队尾。"""
    sem = PrioritySemaphore(1)
    order = []

    async def normal_worker(name):
        async with sem.hold():
            order.append(name)
            await asyncio.sleep(0.05)

    async def priority_worker():
        async with sem.hold(priority=True):
            order.append("P")

    async def scenario():
        tasks = [asyncio.create_task(normal_worker(f"n{i}")) for i in range(20)]
        await asyncio.sleep(0.02)  # 20 个 normal 全部入队
        t0 = time.monotonic()
        await priority_worker()
        waited = time.monotonic() - t0
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        return waited

    waited = run(scenario())
    assert order[1] == "P", f"priority 应抢到第二个名额，实际顺序 {order[:3]}"
    assert waited < 0.3, f"插队只应等一个在跑的任务，实际 {waited:.2f}s"


def test_same_tier_is_fifo():
    sem = PrioritySemaphore(1)
    order = []

    async def worker(name):
        async with sem.hold():
            order.append(name)
            await asyncio.sleep(0.01)

    async def scenario():
        tasks = [asyncio.create_task(worker(f"n{i}")) for i in range(5)]
        await asyncio.gather(*tasks)

    run(scenario())
    assert order == [f"n{i}" for i in range(5)]


def test_cancelled_waiter_does_not_leak_permit():
    sem = PrioritySemaphore(1)
    reached = []

    async def holder():
        async with sem.hold():
            reached.append("hold")
            await asyncio.sleep(0.1)
            reached.append("release")

    async def waiter():
        async with sem.hold():
            reached.append("waiter")

    async def scenario():
        tasks = [asyncio.create_task(holder()), asyncio.create_task(waiter())]
        await asyncio.sleep(0.02)
        tasks[1].cancel()  # 排队中的等待者被取消（前端关掉播放器/连接断开）
        await asyncio.gather(*tasks, return_exceptions=True)
        # 名额没被吞掉：下一个请求还能立刻拿到
        t0 = time.monotonic()
        async with sem.hold():
            pass
        return time.monotonic() - t0

    waited = run(scenario())
    assert "release" in reached
    assert waited < 0.05, f"取消的等待者不该吞掉名额，实际等了 {waited:.2f}s"


def test_cancel_after_grant_returns_permit():
    """极端时序：名额刚交到手上（set_result）就被取消，必须还回去，别把池子漏干。"""
    sem = PrioritySemaphore(1)

    async def scenario():
        await sem.acquire()  # 占住唯一名额
        task = asyncio.ensure_future(sem.acquire())
        await asyncio.sleep(0.01)  # 让等待者登进队列
        sem.release()  # 释放 → 名额立刻转给等待者（其 fut 已 set_result）
        task.cancel()  # 还没被调度起来就取消
        await asyncio.gather(task, return_exceptions=True)
        # 还回来的名额必须可用
        assert sem.value == 1, f"名额应已归还，实际 value={sem.value}"
        await sem.acquire()
        sem.release()

    run(scenario())
