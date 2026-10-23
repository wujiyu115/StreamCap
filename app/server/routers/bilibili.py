from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, Request

from ...core.platforms.bilibili_login import BilibiliLoginSession
from ..deps import get_current_user, get_services

router = APIRouter(prefix="/api/bilibili", tags=["bilibili"])
TERMINAL = {"confirmed", "failed", "cancelled", "timeout"}


def _snapshot(request):
    session = getattr(request.app.state, "bilibili_login", None)
    return session.snapshot() if session else {"state": "idle", "qrcode": None, "saved": False}


@router.post("/login")
async def start_login(
    request: Request,
    user: str = Depends(get_current_user),
    services=Depends(get_services),  # noqa: B008
):
    session = getattr(request.app.state, "bilibili_login", None)
    if session is None or session.snapshot()["state"] in TERMINAL:
        config = (services.settings_config.user_config.get("uploads") or {}).get("providers") or {}
        path = Path((config.get("bilibili") or {}).get("cookie_file") or "config/bilibili/cookies.json")
        if not path.is_absolute():
            path = Path(services.run_path) / path
        session = BilibiliLoginSession(str(path))
        request.app.state.bilibili_login = session
        session.start()
    return session.snapshot()


@router.get("/login")
async def login_status(request: Request, user: str = Depends(get_current_user)):
    result = _snapshot(request)
    session = getattr(request.app.state, "bilibili_login", None)
    if result.get("saved") and not getattr(session, "resumed", False):
        # 只有成功落盘才恢复被登录错误暂停的渠道。
        manager = request.app.state.upload_manager
        await asyncio.to_thread(manager.resume_channel, "bilibili")
        session.resumed = True
    return result


@router.delete("/login")
async def cancel_login(request: Request, user: str = Depends(get_current_user)):
    session = getattr(request.app.state, "bilibili_login", None)
    if session:
        session.cancel()
    return _snapshot(request)
