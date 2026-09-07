from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from ..auth.auth_manager import AuthManager
from ..core.pose.pose_task_manager import PoseTaskManager
from ..core.runtime.backend_services import BackendServices
from ..utils.logger import logger
from .deps import SESSION_COOKIE
from .routers import analytics, auth, media, pose, recordings, settings as settings_router, system

PROJECT_ROOT = Path(__file__).resolve().parents[2]

__all__ = ["create_app", "SESSION_COOKIE", "PROJECT_ROOT"]


def create_app(run_path: str | None = None) -> FastAPI:
    """Build the FastAPI application hosting the SPA and all API routers.

    ``run_path`` defaults to the directory holding ``main.py`` so the config/
    downloads/ layout stays identical to the previous single-process setup.
    """

    if run_path is None:
        run_path = str(PROJECT_ROOT)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        services = BackendServices.bootstrap(run_path)
        app.state.services = services
        app.state.auth_manager = AuthManager(services.config_manager)
        await app.state.auth_manager.initialize()

        pose_task_manager = PoseTaskManager(run_path)
        app.state.pose_task_manager = pose_task_manager
        services.pose_task_manager = pose_task_manager
        _bind_pose_analytics(services, pose_task_manager)

        services.start_background_loop()
        logger.info(f"StreamCap server started (run_path={run_path})")
        yield

        pose_task_manager.shutdown()
        services.stop_background_loop()
        logger.info("StreamCap server stopped")

    app = FastAPI(title="StreamCap", lifespan=lifespan)

    app.include_router(auth.router)
    app.include_router(recordings.router)
    app.include_router(settings_router.router)
    app.include_router(media.router)
    app.include_router(pose.router)
    app.include_router(system.router)
    app.include_router(analytics.router)

    _mount_spa(app)

    return app


def _bind_pose_analytics(services, pose_task_manager: PoseTaskManager) -> None:
    """让识别任务管理器能把产出字节写进录制分析。

    识别在独立子进程里跑，AnalyticsStore 只活在主进程，所以由管理器在任务落终态
    后回调这里补记。管理器构造得比 recording_manager 早，因此走晚绑定。
    """
    rm = getattr(services, "recording_manager", None)
    analytics = getattr(rm, "analytics", None)
    if analytics is None:
        logger.warning("Recording manager unavailable, pose analytics accounting disabled")
        return

    def sink(rec_id: str, ts: float, output_bytes: int, deleted_bytes: int) -> None:
        analytics.record_pose(rec_id, ts, out_bytes=output_bytes, del_bytes=deleted_bytes)
        analytics.maybe_flush()

    pose_task_manager.set_analytics_sink(sink)


def _mount_spa(app: FastAPI) -> None:
    dist_dir = PROJECT_ROOT / "frontend" / "dist"
    if not dist_dir.is_dir():
        logger.info(f"Frontend dist not found at {dist_dir}, SPA routes disabled")
        return

    # 已注册的 API 路由优先匹配；此 catch-all 服务静态资源并对未知路径
    # 回退到 index.html，让 React Router 接管前端路由。
    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        file = (dist_dir / full_path).resolve()
        if full_path and file.is_file() and file.is_relative_to(dist_dir):
            return FileResponse(file)
        return FileResponse(dist_dir / "index.html")

    logger.info(f"Serving frontend from {dist_dir}")
