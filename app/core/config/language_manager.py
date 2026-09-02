"""加载后端运行所需的 i18n 文案（录制状态标题、推送内容模板等）。

headless 版本：无 UI 观察者机制，仅按 settings.language_code 从 locales/
读取语言包。前端 i18n 与此无关（前端自带语言包）。
"""

from __future__ import annotations

import json
import os


class LanguageManager:
    def __init__(self, services):
        self.services = services
        self.run_path = services.run_path
        self.language: dict = {}
        self.load()

    @classmethod
    def create_headless(cls, services) -> LanguageManager:
        return cls(services)

    def _resolve_language_code(self) -> str:
        sc = getattr(self.services, "settings_config", None)
        if sc is not None and hasattr(sc, "language_code"):
            return sc.language_code
        return "zh_CN"

    def load(self) -> dict:
        language_code = self._resolve_language_code() or "zh_CN"
        i18n_path = os.path.join(self.run_path, "locales", f"{language_code}.json")
        try:
            with open(i18n_path, encoding="utf-8") as f:
                self.language = json.load(f)
        except (OSError, ValueError):
            self.language = {}
        return self.language

    # ── 兼容旧接口（无操作） ────────────────────────────────

    def add_observer(self, observer) -> None:
        pass

    def remove_observer(self, observer) -> None:
        pass

    def notify_observers(self) -> None:
        pass
