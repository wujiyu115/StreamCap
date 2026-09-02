from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from ...auth.auth_manager import AuthManager
from ..deps import SESSION_COOKIE, get_auth_manager, get_current_user, get_services
from ..schemas import LoginRequest, PasswordChangeRequest

router = APIRouter(prefix="/api/auth", tags=["auth"])

COOKIE_MAX_AGE = 7 * 24 * 3600


@router.get("/session")
async def get_session(request: Request, services=Depends(get_services), auth_manager=Depends(get_auth_manager)):
    login_required = services.settings_config.get_config_value("login_required", False)
    username = None
    if login_required:
        session = auth_manager.get_session(request.cookies.get(SESSION_COOKIE))
        username = session["username"] if session else None
    else:
        username = "admin"
    return {"login_required": login_required, "authenticated": username is not None, "username": username}


@router.post("/login")
async def login(
    body: LoginRequest,
    response: Response,
    auth_manager: AuthManager = Depends(get_auth_manager),
):
    ok, token = await auth_manager.authenticate(body.username, body.password)
    if not ok:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return {"ok": True, "username": body.username}


@router.post("/logout")
async def logout(
    response: Response,
    request: Request,
    auth_manager: AuthManager = Depends(get_auth_manager),
):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        auth_manager.logout(token)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.post("/password")
async def change_password(
    body: PasswordChangeRequest,
    username: str = Depends(get_current_user),
    auth_manager: AuthManager = Depends(get_auth_manager),
):
    if await auth_manager.change_password(username, body.old_password, body.new_password):
        return {"ok": True}
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="wrong old password")
