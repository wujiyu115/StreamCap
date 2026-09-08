from __future__ import annotations

from typing import Any

from ..runtime.paths import default_recordings_dir
from .layering import apply_patch, config_hash, rebuild_effective, unset_path


class SettingsConfig:
    """配置的两层视图。

    - ``default_config``：默认层，来自镜像模板（只读）
    - ``user_overrides``：稀疏覆盖层，只含用户显式改过的键，**只有它落盘**
    - ``user_config``：派生的有效配置（default ⊕ overrides），只活在内存里

    读侧一律用 ``user_config``（沿用旧名字，全站几十个调用点无需改动）：它
    现在是合并后的有效值，所以覆盖层稀疏化不会让任何 ``get(key, 硬编码兜底)``
    悄悄读到兜底值。
    """

    def __init__(self, services):
        self.services = services
        cm = services.config_manager
        self.user_overrides: dict = cm.load_user_config() or {}
        self.default_config: dict = cm.load_default_config() or {}
        self.user_config: dict = {}
        self._refresh_effective()
        self.cookies_config: dict = cm.load_cookies_config() or {}
        self.accounts_config: dict = cm.load_accounts_config() or {}
        self.language_option: dict = cm.load_language_config() or {}

        select_language = self.user_config.get("language")
        if select_language and select_language in self.language_option:
            self.language_code: str = self.language_option[select_language]
        elif self.language_option:
            self.language_code = next(iter(self.language_option.values()))
        else:
            self.language_code = "zh_CN"

    def get_config_value(self, key: str, default: Any = None) -> Any:
        return self.user_config.get(key, self.default_config.get(key, default))

    def get_cookies_value(self, key: str, default: str = "") -> str:
        return self.cookies_config.get(key, default)

    def get_accounts_value(self, key: str, default: Any = None) -> Any:
        try:
            k1, k2 = key.split("_", maxsplit=1)
        except ValueError:
            return default
        return self.accounts_config.get(k1, {}).get(k2, default)

    def get_video_save_path(self) -> str:
        live_save_path = self.get_config_value("live_save_path")
        if not live_save_path:
            live_save_path = str(default_recordings_dir)
        return live_save_path

    def _refresh_effective(self) -> None:
        """就地刷新有效配置。

        就地而非换引用：``StreamManager`` 等在构造时就捕获了
        ``settings_config.user_config`` 的引用，换新对象会让它们读到旧值。
        """
        rebuild_effective(self.user_config, self.default_config, self.user_overrides)

    @property
    def config_version(self) -> str:
        """覆盖层指纹，用于设置写入的乐观并发控制。"""
        return config_hash(self.user_overrides)

    def apply_user_patch(self, patch: dict) -> bool:
        """把「用户改过的键」深度合并进覆盖层，返回是否有实际变化。

        没出现在 patch 里的键继续跟随默认层——这是不再把默认值物化进用户层
        的关键，也是改默认值能作用到已部署实例的前提。
        """
        changed = apply_patch(self.user_overrides, patch)
        if changed:
            self._refresh_effective()
        return changed

    def reset_user_key(self, path: str) -> bool:
        """按点号路径删掉一个覆盖项（恢复默认），返回是否删掉了东西。"""
        removed = unset_path(self.user_overrides, path)
        if removed:
            self._refresh_effective()
        return removed

    def adopt_cookies_config(self, cookies_config: dict) -> None:
        self.cookies_config = cookies_config

    def adopt_accounts_config(self, accounts_config: dict) -> None:
        self.accounts_config = accounts_config
