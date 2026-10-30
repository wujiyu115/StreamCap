from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ...utils.logger import logger
from .. import error_codes as errors
from ..deps import get_current_user, get_services
from ..schemas import AccountsUpdate, CookiesUpdate, SettingsUpdate

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
async def get_settings(user: str = Depends(get_current_user), services=Depends(get_services)):
    """下发两层配置。

    ``user_settings`` 是有效配置（default ⊕ overrides，`SettingsConfig` 已在内存里
    维护），``user_overrides`` 是稀疏覆盖层——前端靠它区分「已覆盖」和「跟随默认」。
    """
    sc = services.settings_config
    return {
        "user_settings": dict(sc.user_config),
        "default_settings": sc.default_config,
        "user_overrides": sc.user_overrides,
        "version": sc.config_version,
        "language_code": sc.language_code,
    }


@router.put("")
async def update_settings(
    body: SettingsUpdate,
    user: str = Depends(get_current_user),
    services=Depends(get_services),
):
    """把 ``patch`` 里的键深度合并进覆盖层。没出现的键继续跟随默认层。"""
    sc = services.settings_config
    if body.patch is None:
        # 旧前端（镜像更新前加载的页面）会整体 PUT 合并视图，写进覆盖层等于把
        # 默认层重新物化一遍——拒掉，让它刷新页面拿新 JS
        raise HTTPException(status_code=409, detail=errors.SETTINGS_STALE_CLIENT)
    if body.version is not None and body.version != sc.config_version:
        raise HTTPException(status_code=409, detail=errors.SETTINGS_VERSION_MISMATCH)

    old_language = sc.user_config.get("language")
    old_save_path = sc.user_config.get("live_save_path")

    changed = sc.apply_user_patch(body.patch)
    if changed:
        await services.config_manager.save_user_config(sc.user_overrides)
        _apply_side_effects(services, sc, old_language, old_save_path)

    # 回传新指纹与覆盖层，前端据此更新本地缓存，不必 refetch（refetch 会把
    # 在途编辑冲掉）
    return {
        "ok": True,
        "changed": changed,
        "version": sc.config_version,
        "user_overrides": sc.user_overrides,
    }


@router.delete("/keys/{key_path:path}")
async def reset_setting(
    key_path: str,
    version: str | None = None,
    user: str = Depends(get_current_user),
    services=Depends(get_services),
):
    """删掉一个覆盖项，让这个键恢复跟随默认层。``key_path`` 支持点号路径。"""
    sc = services.settings_config
    if version is not None and version != sc.config_version:
        raise HTTPException(status_code=409, detail=errors.SETTINGS_VERSION_MISMATCH)

    old_language = sc.user_config.get("language")
    old_save_path = sc.user_config.get("live_save_path")

    removed = sc.reset_user_key(key_path)
    if removed:
        await services.config_manager.save_user_config(sc.user_overrides)
        _apply_side_effects(services, sc, old_language, old_save_path)

    return {
        "ok": True,
        "removed": removed,
        "value": _read_path(sc.user_config, key_path),
        "version": sc.config_version,
        "user_overrides": sc.user_overrides,
    }


def _read_path(config: dict, path: str):
    node = config
    for segment in path.split("."):
        if not isinstance(node, dict) or segment not in node:
            return None
        node = node[segment]
    return node


def _apply_side_effects(services, sc, old_language, old_save_path) -> None:
    """语言 / 存储路径变更后的联动（按变更后的有效值判断）。"""
    if sc.user_config.get("language") != old_language:
        services.language_manager.load()
    if sc.user_config.get("live_save_path") != old_save_path:
        logger.info(f"Video save path changed to: {sc.user_config.get('live_save_path')}")


@router.get("/cookies")
async def get_cookies(user: str = Depends(get_current_user), services=Depends(get_services)):
    return {"cookies": services.settings_config.cookies_config}


@router.put("/cookies")
async def update_cookies(
    body: CookiesUpdate,
    user: str = Depends(get_current_user),
    services=Depends(get_services),
):
    old = dict(services.settings_config.cookies_config or {})
    services.settings_config.adopt_cookies_config(body.cookies)
    await services.config_manager.save_cookies_config(body.cookies)
    _resume_channels_with_new_cookies(services, old, body.cookies)
    return {"ok": True}


def _resume_channels_with_new_cookies(services, old: dict, new: dict) -> None:
    """Cookie 真的变了且新值非空 → 自动解除对应渠道的「登录失效」。

    自动上传的渠道名恰好等于 cookie 平台的 key（bilibili / douyin），所以直接按 key 对应；
    没变或改成空的都不动（清空 cookie 不该把渠道“恢复”）。
    """
    manager = getattr(services, "upload_manager", None)
    if manager is None:
        return
    for provider, value in (new or {}).items():
        if not str(value or "").strip():
            continue
        if str(value) == str((old or {}).get(provider) or ""):
            continue
        manager.on_credentials_updated(provider)


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
