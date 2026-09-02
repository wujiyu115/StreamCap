from __future__ import annotations

from fastapi import HTTPException, Request

from ..core.runtime.backend_services import BackendServices

SESSION_COOKIE = "sc_session"


def get_services(request: Request) -> BackendServices:
    return request.app.state.services


def get_auth_manager(request: Request):
    return request.app.state.auth_manager


def get_current_user(request: Request) -> str:
    """Resolve the session user; raises 401 when login is required and the
    request carries no valid session cookie."""
    services = request.app.state.services
    auth_manager = request.app.state.auth_manager

    login_required = services.settings_config.get_config_value("login_required", False)
    if not login_required:
        return "admin"

    session = auth_manager.get_session(request.cookies.get(SESSION_COOKIE))
    if session is not None:
        return session["username"]

    raise HTTPException(status_code=401, detail="authentication required")
