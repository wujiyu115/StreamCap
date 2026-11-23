"""平台请求节奏门：相邻请求发起时刻 ≥ 最小间隔；优先队列插队；interval=0 保持旧行为"""
import asyncio
import time
from collections import defaultdict

from helpers import make_manager

from app.core.recording.priority_semaphore import PrioritySemaphore


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



def test_interactive_does_not_queue_behind_monitor_backlog():
    """并发名额也要分档：一轮监控把名额排满后，点播放不能排在几十个任务后面。

    回归依据：生产一轮 ~170 个任务，``asyncio.Semaphore`` 是 FIFO，晚到的交互请求
    要等前面全部拿完名额，实测首帧直链等了 170s（同样请求空闲时 0.3~2s）。
    """
    interval = 0.05
    mgr = make_manager({"monitor_platform_min_interval_seconds": interval})
    mgr.platform_semaphores = defaultdict(lambda: PrioritySemaphore(3))

    async def monitor_task():
        async with mgr._platform_slot("douyin"):  # 监控走 normal 档
            await asyncio.sleep(0.3)  # 一次取流耗时

    async def interactive_wait():
        t0 = time.monotonic()
        async with mgr._platform_slot("douyin", True):  # 交互走 priority 档
            pass
        return time.monotonic() - t0

    async def run():
        monitors = [asyncio.create_task(monitor_task()) for _ in range(40)]
        await asyncio.sleep(0.05)  # 让监控先把名额与队列占满
        try:
            return await interactive_wait()
        finally:
            for task in monitors:
                task.cancel()
            await asyncio.gather(*monitors, return_exceptions=True)

    waited = asyncio.run(run())
    # 40 个任务 FIFO 排队要 40/3*0.3 ≈ 4s；插队后只需「一个在跑的请求结束 + 一次间隔」
    assert waited < 1.0, f"交互请求应插队拿到并发名额，实际等了 {waited:.2f}s"


def test_priority_inserted_during_slot_sleep_gets_that_slot():
    """门正睡等下一格时插进来的交互请求，抢的是这一格，而不是再等一整个间隔。

    回归依据：原来「先取排队者、再睡到下一格」，睡的那 1s 里到达的 priority 请求
    只能等下一轮（实测晚到 0.1s 就多等一个完整间隔）。
    """
    interval = 0.3
    mgr = make_manager({"monitor_platform_min_interval_seconds": interval})

    async def worker(priority, name):
        async with mgr._platform_slot("douyin", priority):
            await asyncio.sleep(0.01)

    async def run():
        backlog = [asyncio.create_task(worker(False, f"n{i}")) for i in range(6)]
        await asyncio.sleep(interval / 3)  # 此刻门正在 sleep 等下一格
        t0 = time.monotonic()
        await worker(True, "P")
        waited = time.monotonic() - t0
        await asyncio.gather(*backlog)
        return waited

    waited = asyncio.run(run())
    assert waited < interval * 1.6, f"插队应抢到下一格（≤{interval}s 量级），实际等了 {waited:.2f}s"


def test_dispenser_exits_when_all_waiters_cancelled():
    """等待者全被取消时门要自行退出，不留在 _dispenser_tasks 里空转。"""
    interval = 0.2
    mgr = make_manager({"monitor_platform_min_interval_seconds": interval})

    async def hold():
        async with mgr._platform_slot("douyin"):
            await asyncio.sleep(interval * 3)  # 占住名额，让后来的排队

    async def waiting():
        async with mgr._platform_slot("douyin"):
            pass

    async def run():
        holder = asyncio.create_task(hold())
        waiters = [asyncio.create_task(waiting()) for _ in range(3)]
        await asyncio.sleep(0.05)
        for task in waiters:
            task.cancel()
        await asyncio.gather(*waiters, return_exceptions=True)
        await holder
        await asyncio.sleep(interval * 1.2)  # 给门一个周期退出
        return dict(mgr._dispenser_tasks)

    left = asyncio.run(run())
    assert left == {}, f"门应当自行退出，实际还挂着 {left}"


def test_interactive_has_its_own_concurrency_budget():
    """监控占满并发名额时交互请求不等它。

    回归依据：并发名额是在**排队等节奏门期间**持着的，共享名额会让交互白等一个在跑的
    监控请求（生产实测 2~4s，bogus 房间的 validity 检测更慢）。交互档有自己的名额。
    """
    mgr = make_manager({"monitor_platform_min_interval_seconds": 0})  # 只测名额，不测节奏

    async def monitor():
        async with mgr._platform_slot("douyin"):
            await asyncio.sleep(0.5)

    async def run():
        monitors = [asyncio.create_task(monitor()) for _ in range(6)]
        await asyncio.sleep(0.05)  # 监控把 platform_semaphores 占满
        t0 = time.monotonic()
        async with mgr._platform_slot("douyin", True):
            pass
        waited = time.monotonic() - t0
        for task in monitors:
            task.cancel()
        await asyncio.gather(*monitors, return_exceptions=True)
        return waited

    waited = asyncio.run(run())
    assert waited < 0.2, f"交互不该等监控名额，实际等了 {waited:.2f}s"
