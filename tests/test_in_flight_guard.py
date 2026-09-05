"""in-flight 守卫：排队/执行中的任务跳过重复派发，异常后标志复位"""
import asyncio

from helpers import make_manager, make_recording


def test_concurrent_dispatch_runs_once():
    """5 次并发派发只执行 1 次——防止排队期间重复派发导致请求队列雪崩"""
    mgr = make_manager()
    rec = make_recording()
    runs = []

    async def slow_impl(r):
        runs.append(1)
        await asyncio.sleep(0.05)

    mgr._check_if_live_impl = slow_impl

    async def run():
        await asyncio.gather(*(mgr.check_if_live(rec) for _ in range(5)))

    asyncio.run(run())

    assert len(runs) == 1, f"应只执行 1 次，实际 {len(runs)} 次"
    assert rec.check_in_flight is False, "完成后标志复位"


def test_exception_resets_in_flight_flag():
    """协程异常退出也必须复位标志，否则任务永久卡死不再被检测"""
    mgr = make_manager()
    rec = make_recording()

    async def boom(r):
        raise RuntimeError("boom")

    async def ok(r):
        runs.append(1)

    runs = []
    mgr._check_if_live_impl = boom

    async def run():
        try:
            await mgr.check_if_live(rec)
        except RuntimeError:
            pass
        assert rec.check_in_flight is False, "异常后标志必须复位"
        # 复位后可再次派发（换回正常实现验证）
        mgr._check_if_live_impl = ok
        await mgr.check_if_live(rec)

    asyncio.run(run())
    assert len(runs) == 1
