"""平台请求节奏门：相邻请求发起时刻 ≥ 最小间隔；优先队列插队；interval=0 保持旧行为"""
import asyncio
import time

from helpers import make_manager


def test_spacing_enforced_between_request_starts():
    interval = 0.1
    mgr = make_manager({"monitor_platform_min_interval_seconds": interval})
    starts = []

    async def worker():
        async with mgr._platform_slot("douyin"):
            starts.append(time.monotonic())
            await asyncio.sleep(0.02)  # 模拟请求耗时

    async def run():
        await asyncio.gather(*(worker() for _ in range(5)))

    asyncio.run(run())

    gaps = [starts[i + 1] - starts[i] for i in range(len(starts) - 1)]
    assert len(starts) == 5
    assert all(g >= interval * 0.9 for g in gaps), f"发起间隔应 ≥ {interval}s，实际 {gaps}"


def test_zero_interval_keeps_legacy_behavior():
    """间隔=0：只有并发限制，无节奏排队"""
    mgr = make_manager({"monitor_platform_min_interval_seconds": 0})

    async def worker():
        async with mgr._platform_slot("douyin"):
            await asyncio.sleep(0.05)

    async def run():
        t0 = time.monotonic()
        await asyncio.gather(*(worker() for _ in range(6)))
        return time.monotonic() - t0

    elapsed = asyncio.run(run())
    assert elapsed < 0.3, f"无间隔时 6 个请求应即刻并发完成，实际 {elapsed:.2f}s"


def test_spacing_shared_per_platform():
    """不同平台各自计时，互不拖慢"""
    mgr = make_manager({"monitor_platform_min_interval_seconds": 0.2})

    async def run():
        mgr._next_slot_at["douyin"] = time.monotonic() + 0.2   # douyin 排队
        t0 = time.monotonic()
        async with mgr._platform_slot("tiktok"):  # 另一平台不应等待
            pass
        return time.monotonic() - t0

    elapsed = asyncio.run(run())
    assert elapsed < 0.1, f"其他平台不应被 douyin 的排期阻塞，实际 {elapsed:.2f}s"


def test_priority_jumps_queue():
    """优先请求插队：晚到的 priority 应先于晚到的 normal 放行，全局节奏不变"""
    mgr = make_manager({"monitor_platform_min_interval_seconds": 0.1})
    order = []
    starts = []

    async def worker(priority, name):
        async with mgr._platform_slot("douyin", priority):
            starts.append(time.monotonic())
            order.append(name)

    async def run():
        # 先派 4 个 normal，等它们全部入队
        tasks = [asyncio.create_task(worker(False, f"n{i}")) for i in range(4)]
        await asyncio.sleep(0.03)
        # 此时再投一个 priority 和一个 normal：P 应插到 n4 前面
        tasks.append(asyncio.create_task(worker(True, "P")))
        tasks.append(asyncio.create_task(worker(False, "n4")))
        await asyncio.gather(*tasks)

    asyncio.run(run())

    assert order.index("P") < order.index("n4"), f"优先应插队，实际顺序 {order}"
    gaps = [starts[i + 1] - starts[i] for i in range(len(starts) - 1)]
    assert all(g >= 0.09 for g in gaps), f"插队不得破坏全局节奏，实际间隔 {gaps}"

