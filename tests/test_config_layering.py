"""配置分层：默认层只读 + 稀疏覆盖层 + patch 写入 + 恢复默认 + 乐观并发。

覆盖的核心不变量：
1. 有效配置只在内存里派生，落盘的永远只有稀疏覆盖层（否则默认层被物化，
   镜像里改默认值对已部署实例永久失效——这就是配置被更新「顶掉」的根因）
2. patch 只动它带的键，兄弟键不受影响
3. 稀疏化迁移只跑一次，不会反复吃掉「用户显式设成与默认相同」的选择
"""
import asyncio
import json

import pytest
from fastapi import HTTPException

from app.core.config.config_manager import ConfigManager
from app.core.config.layering import (
    apply_patch,
    config_hash,
    deep_merge,
    rebuild_effective,
    strip_defaults,
    unset_path,
)
from app.core.config.settings_config import SettingsConfig
from app.server.routers.settings import get_settings, reset_setting, update_settings
from app.server.schemas import SettingsUpdate

DEFAULTS = {
    "video_format": "MP4",
    "delete_original": True,
    "auto_stop_monitor_days": 30,
    "monitor_platform_min_interval_seconds": 1,
    "pose_detection": {"enabled": True, "pose_filter": "standing", "imgsz": 416},
}


# --------------------------------------------------------------------------- 纯函数


def test_deep_merge_recurses_dicts_and_replaces_scalars():
    merged = deep_merge(DEFAULTS, {"video_format": "TS", "pose_detection": {"imgsz": 640}})
    assert merged["video_format"] == "TS"
    assert merged["pose_detection"] == {"enabled": True, "pose_filter": "standing", "imgsz": 640}
    # 不改原对象
    assert DEFAULTS["pose_detection"]["imgsz"] == 416


def test_rebuild_effective_updates_in_place():
    """就地更新：StreamManager 等在构造时捕获的 user_config 引用必须能读到新值。"""
    effective: dict = {}
    held = effective
    rebuild_effective(effective, DEFAULTS, {"video_format": "TS"})
    assert held is effective
    assert held["video_format"] == "TS"
    rebuild_effective(effective, DEFAULTS, {})
    assert held["video_format"] == "MP4"


def test_apply_patch_only_touches_patched_keys():
    overrides = {"video_format": "TS", "pose_detection": {"imgsz": 640}}
    assert apply_patch(overrides, {"pose_detection": {"pose_filter": "sitting"}}) is True
    assert overrides == {
        "video_format": "TS",
        "pose_detection": {"imgsz": 640, "pose_filter": "sitting"},
    }
    # 同值再 patch 一次不算变化
    assert apply_patch(overrides, {"pose_detection": {"pose_filter": "sitting"}}) is False


def test_unset_path_prunes_empty_sections():
    overrides = {"pose_detection": {"imgsz": 640}, "video_format": "TS"}
    assert unset_path(overrides, "pose_detection.imgsz") is True
    assert overrides == {"video_format": "TS"}  # 空掉的段一并剪掉
    assert unset_path(overrides, "pose_detection.imgsz") is False
    assert unset_path(overrides, "") is False


def test_strip_defaults_keeps_only_deviations():
    user = {
        "video_format": "MP4",  # == 默认
        "delete_original": True,  # == 默认
        "auto_stop_monitor_days": "30",  # 字符串 ≠ 数字 30，保留
        "pose_detection": {"enabled": True, "pose_filter": "none", "legacy_key": 1},
        "unknown_key": "x",  # 默认层没有，保留
    }
    sparse, dropped = strip_defaults(user, DEFAULTS)
    assert sparse == {
        "auto_stop_monitor_days": "30",
        "pose_detection": {"pose_filter": "none", "legacy_key": 1},
        "unknown_key": "x",
    }
    assert set(dropped) == {"video_format", "delete_original", "pose_detection.enabled"}


def test_config_hash_is_order_insensitive():
    assert config_hash({"a": 1, "b": 2}) == config_hash({"b": 2, "a": 1})
    assert config_hash({"a": 1}) != config_hash({"a": 2})


# --------------------------------------------------------------------------- 装配夹具


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=4), encoding="utf-8")


class FakeLanguageManager:
    def __init__(self):
        self.loads = 0

    def load(self):
        self.loads += 1


class FakeServices:
    def __init__(self, config_manager):
        self.config_manager = config_manager
        self.language_manager = FakeLanguageManager()
        self.settings_config = SettingsConfig(self)


@pytest.fixture
def deployment(tmp_path):
    """带镜像模板的部署：run_path/config_templates 是默认层，run_path/config 是挂载卷。"""

    def _build(user_settings=None, legacy_default=None, defaults=None):
        _write(tmp_path / "config_templates" / "default_settings.json", defaults or DEFAULTS)
        if user_settings is not None:
            _write(tmp_path / "config" / "user_settings.json", user_settings)
        if legacy_default is not None:
            _write(tmp_path / "config" / "default_settings.json", legacy_default)
        return ConfigManager(str(tmp_path))

    return _build


# --------------------------------------------------------------------------- ConfigManager


def test_fresh_install_leaves_defaults_in_image_and_user_layer_empty(deployment, tmp_path):
    cm = deployment()
    assert cm.default_config_path == str(tmp_path / "config_templates" / "default_settings.json")
    # 默认层不进挂载卷：老实现复制过去之后，改镜像默认值对这台机器就永久失效了
    assert not (tmp_path / "config" / "default_settings.json").exists()
    assert json.loads((tmp_path / "config" / "user_settings.json").read_text(encoding="utf-8")) == {}
    assert cm.load_default_config() == DEFAULTS


def test_upgrade_retires_stale_default_copy_and_sparsifies_user_layer(deployment, tmp_path):
    materialized = {**DEFAULTS, "pose_detection": dict(DEFAULTS["pose_detection"])}
    materialized["video_format"] = "TS"  # 用户真实偏离
    materialized["pose_detection"]["imgsz"] = 640  # 嵌套段里的真实偏离
    cm = deployment(user_settings=materialized, legacy_default={**DEFAULTS, "video_format": "TS"})

    config_dir = tmp_path / "config"
    assert not (config_dir / "default_settings.json").exists()
    assert (config_dir / "default_settings.json.legacy").exists()

    sparse = json.loads((config_dir / "user_settings.json").read_text(encoding="utf-8"))
    assert sparse == {"video_format": "TS", "pose_detection": {"imgsz": 640}}

    backup = json.loads((config_dir / "user_settings.json.pre-sparse.bak").read_text(encoding="utf-8"))
    assert backup == materialized
    assert cm._load_migrations().get(ConfigManager.SPARSE_MIGRATION_KEY) is True


def test_sparse_migration_runs_only_once(deployment, tmp_path):
    deployment(user_settings={**DEFAULTS, "video_format": "TS"})
    user_path = tmp_path / "config" / "user_settings.json"

    # 迁移后用户在 UI 上显式把某项设成了与默认相同的值
    _write(user_path, {"video_format": "TS", "delete_original": True})
    ConfigManager(str(tmp_path))

    # 再次启动不能把它当成「继承默认」剥掉
    assert json.loads(user_path.read_text(encoding="utf-8")) == {
        "video_format": "TS",
        "delete_original": True,
    }


def test_dev_mode_falls_back_to_repo_default_file(tmp_path):
    _write(tmp_path / "config" / "default_settings.json", DEFAULTS)
    cm = ConfigManager(str(tmp_path))
    assert cm.default_config_path == str(tmp_path / "config" / "default_settings.json")
    # 开发态那份就是默认层，不许被挪走
    assert (tmp_path / "config" / "default_settings.json").exists()
    assert cm.load_default_config() == DEFAULTS


# --------------------------------------------------------------------------- SettingsConfig


def test_effective_config_falls_back_to_defaults_per_field(deployment):
    cm = deployment(user_settings={"pose_detection": {"pose_filter": "none"}})
    sc = SettingsConfig(FakeServices(cm))
    assert sc.user_overrides == {"pose_detection": {"pose_filter": "none"}}
    # 读侧看到的是合并后的有效值（字段级回退），稀疏化不会漏键
    assert sc.user_config["video_format"] == "MP4"
    assert sc.user_config["pose_detection"] == {"enabled": True, "pose_filter": "none", "imgsz": 416}


def test_patch_then_reset_returns_to_default(deployment):
    sc = SettingsConfig(FakeServices(deployment()))
    version_before = sc.config_version

    assert sc.apply_user_patch({"pose_detection": {"imgsz": 640}}) is True
    assert sc.user_config["pose_detection"]["imgsz"] == 640
    assert sc.user_config["pose_detection"]["enabled"] is True  # 兄弟键没被 patch 波及
    assert sc.config_version != version_before

    assert sc.reset_user_key("pose_detection.imgsz") is True
    assert sc.user_overrides == {}
    assert sc.user_config["pose_detection"]["imgsz"] == 416
    assert sc.config_version == version_before


# --------------------------------------------------------------------------- 路由


def test_get_settings_exposes_both_layers(deployment):
    services = FakeServices(deployment(user_settings={"video_format": "TS"}))
    data = asyncio.run(get_settings(user="u", services=services))
    assert data["user_settings"]["video_format"] == "TS"
    assert data["default_settings"]["video_format"] == "MP4"
    assert data["user_overrides"] == {"video_format": "TS"}
    assert data["version"] == services.settings_config.config_version


def test_put_patch_persists_only_overrides(deployment, tmp_path):
    services = FakeServices(deployment())
    body = SettingsUpdate(patch={"video_format": "TS"}, version=services.settings_config.config_version)
    result = asyncio.run(update_settings(body, user="u", services=services))
    assert result["ok"] and result["changed"]
    assert json.loads((tmp_path / "config" / "user_settings.json").read_text(encoding="utf-8")) == {
        "video_format": "TS"
    }
    assert result["version"] == services.settings_config.config_version


def test_put_rejects_stale_version_and_legacy_full_document(deployment):
    services = FakeServices(deployment())
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(update_settings(SettingsUpdate(patch={"video_format": "TS"}, version="deadbeef"), "u", services))
    assert excinfo.value.status_code == 409

    # 旧前端整份合并视图：拒掉，否则默认层被重新物化
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(update_settings(SettingsUpdate(user_settings=dict(DEFAULTS)), "u", services))
    assert excinfo.value.status_code == 409
    assert services.settings_config.user_overrides == {}


def test_put_triggers_language_reload_only_on_change(deployment):
    services = FakeServices(deployment())
    asyncio.run(update_settings(SettingsUpdate(patch={"language": "English"}), "u", services))
    assert services.language_manager.loads == 1
    asyncio.run(update_settings(SettingsUpdate(patch={"language": "English"}), "u", services))
    assert services.language_manager.loads == 1  # 无变化不重载


def test_delete_key_resets_to_default(deployment, tmp_path):
    services = FakeServices(deployment(user_settings={"pose_detection": {"imgsz": 640}}))
    result = asyncio.run(reset_setting("pose_detection.imgsz", None, "u", services))
    assert result["removed"] is True
    assert result["value"] == 416
    assert json.loads((tmp_path / "config" / "user_settings.json").read_text(encoding="utf-8")) == {}

    # 幂等：已经跟随默认的键再删一次不报错
    assert asyncio.run(reset_setting("pose_detection.imgsz", None, "u", services))["removed"] is False


def test_delete_rejects_stale_version(deployment):
    services = FakeServices(deployment(user_settings={"video_format": "TS"}))
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(reset_setting("video_format", "deadbeef", "u", services))
    assert excinfo.value.status_code == 409
    assert services.settings_config.user_overrides == {"video_format": "TS"}
