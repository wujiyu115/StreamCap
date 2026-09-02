from __future__ import annotations

from fastapi import APIRouter, Depends

from ...utils.logger import logger
from ..deps import get_current_user, get_services
from ..schemas import AccountsUpdate, CookiesUpdate, SettingsUpdate

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
async def get_settings(user: str = Depends(get_current_user), services=Depends(get_services)):
    sc = services.settings_config
    # default → user 两级合并视图：嵌套段（如 pose_detection）做字段级回退，
    # 顶层标量按 user 优先。保存时仍整体写回 user_settings。
    merged = dict(sc.default_config)
    for key, value in sc.user_config.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    return {
        "user_settings": merged,
        "default_settings": sc.default_config,
        "language_code": sc.language_code,
    }


@router.put("")
async def update_settings(
    body: SettingsUpdate,
    user: str = Depends(get_current_user),
    services=Depends(get_services),
):
    sc = services.settings_config
    old_language = sc.user_config.get("language")
    old_save_path = sc.user_config.get("live_save_path")

    sc.adopt_user_config(body.user_settings)
    await services.config_manager.save_user_config(body.user_settings)

    if body.user_settings.get("language") != old_language:
        services.language_manager.load()
    if body.user_settings.get("live_save_path") != old_save_path:
        logger.info(f"Video save path changed to: {body.user_settings.get('live_save_path')}")

    return {"ok": True}


@router.get("/cookies")
async def get_cookies(user: str = Depends(get_current_user), services=Depends(get_services)):
    return {"cookies": services.settings_config.cookies_config}


@router.put("/cookies")
async def update_cookies(
    body: CookiesUpdate,
    user: str = Depends(get_current_user),
    services=Depends(get_services),
):
    services.settings_config.adopt_cookies_config(body.cookies)
    await services.config_manager.save_cookies_config(body.cookies)
    return {"ok": True}


@router.get("/accounts")
async def get_accounts(user: str = Depends(get_current_user), services=Depends(get_services)):
    return {"accounts": services.settings_config.accounts_config}


@router.put("/accounts")
async def update_accounts(
    body: AccountsUpdate,
    user: str = Depends(get_current_user),
    services=Depends(get_services),
):
    services.settings_config.adopt_accounts_config(body.accounts)
    await services.config_manager.save_accounts_config(body.accounts)
    return {"ok": True}
