import asyncio
import json
import os
import shutil
import tempfile
import threading
from typing import TypeVar

from ...utils.logger import logger

T = TypeVar("T")


class ConfigManager:
    # 镜像内置的配置模板目录（打包在 /app/config_templates）。
    # 容器把 config/ 挂载为空卷时，首启从模板初始化。
    TEMPLATE_DIR_NAME = "config_templates"

    def __init__(self, run_path):
        self.config_path = os.path.join(run_path, "config")
        self.language_config_path = os.path.join(self.config_path, "language.json")
        self.default_config_path = os.path.join(self.config_path, "default_settings.json")
        self.user_config_path = os.path.join(self.config_path, "user_settings.json")
        self.cookies_config_path = os.path.join(self.config_path, "cookies.json")
        self.about_config_path = os.path.join(self.config_path, "version.json")
        self.recordings_config_path = os.path.join(self.config_path, "recordings.json")
        self.accounts_config_path = os.path.join(self.config_path, "accounts.json")
        self.web_auth_config_path = os.path.join(self.config_path, "web_auth.json")

        template_dir = os.path.join(run_path, self.TEMPLATE_DIR_NAME)
        if os.path.isdir(template_dir):
            for name in ("default_settings.json", "language.json", "version.json"):
                src = os.path.join(template_dir, name)
                dst = os.path.join(self.config_path, name)
                if os.path.isfile(src) and (not os.path.exists(dst) or os.path.getsize(dst) == 0):
                    shutil.copy(src, dst)
                    logger.info(f"Initialized {dst} from image template")
                elif os.path.isfile(src) and name == "default_settings.json":
                    # 升级部署：已存在的副本补齐镜像模板里的新增键（不覆盖用户已有值）
                    try:
                        self._merge_template_defaults(src, dst)
                    except Exception as e:
                        logger.warning(f"Failed to merge template defaults into {dst}: {e}")

        os.makedirs(os.path.dirname(self.default_config_path), exist_ok=True)
        self.init()

    def init(self):
        self.init_default_config()
        self.init_user_config()
        self.init_cookies_config()
        self.init_accounts_config()
        self.init_recordings_config()
        self.init_web_auth_config()

    @staticmethod
    def _merge_template_defaults(src: str, dst: str) -> None:
        """把镜像模板的默认设置合并进已部署的 config 副本。

        深度合并：仅补齐副本中缺失的键，不覆盖已有值——升级镜像新增
        默认键（如 pose_detection.inference_threads）才能生效，同时
        用户对副本的任何修改都保留。
        """
        with open(src, encoding="utf-8") as f:
            template = json.load(f)
        with open(dst, encoding="utf-8") as f:
            deployed = json.load(f)

        def merge(base: dict, override: dict) -> bool:
            changed = False
            for key, value in override.items():
                if key not in base:
                    base[key] = value
                    changed = True
                elif isinstance(value, dict) and isinstance(base.get(key), dict):
                    changed = merge(base[key], value) or changed
            return changed

        if merge(deployed, template):
            with open(dst, "w", encoding="utf-8") as f:
                json.dump(deployed, f, ensure_ascii=False, indent=4)
            logger.info(f"Merged new template defaults into {dst}")

    @staticmethod
    def _init_config(config_path, default_config=None):
        """Initialize a configuration file with default values if it does not exist."""
        if not os.path.exists(config_path):
            if default_config is None:
                default_config = {}
            try:
                with open(config_path, "w", encoding="utf-8") as file:
                    json.dump(default_config, file, ensure_ascii=False, indent=4)
                logger.info(f"Initialized configuration file: {config_path}")
            except Exception as e:
                logger.error(f"Failed to initialize configuration file {config_path}: {e}")

    def init_default_config(self):
        default_config = {}
        self._init_config(self.default_config_path, default_config)

    def init_user_config(self):
        if os.path.exists(self.user_config_path) and self.load_user_config():
            return
        shutil.copy(self.default_config_path, self.user_config_path)

    def init_cookies_config(self):
        cookies_config = {}
        self._init_config(self.cookies_config_path, cookies_config)

    def init_accounts_config(self):
        cookies_config = {}
        self._init_config(self.accounts_config_path, cookies_config)

    def init_recordings_config(self):
        cookies_config = {}
        self._init_config(self.recordings_config_path, cookies_config)

    def init_web_auth_config(self):
        cookies_config = {}
        self._init_config(self.web_auth_config_path, cookies_config)

    @staticmethod
    def _load_config(config_path, error_message):
        """Load configuration from a JSON file."""
        try:
            with open(config_path, encoding="utf-8") as file:
                return json.load(file)
        except json.JSONDecodeError:
            logger.error(f"Invalid JSON format in file: {config_path}")
            return {}
        except FileNotFoundError:
            logger.error(f"Configuration file not found: {config_path}")
            return {}
        except Exception as e:
            logger.error(f"{error_message}: {e}")
            return {}

    def load_default_config(self):
        return self._load_config(self.default_config_path, "An error occurred while loading default config")

    def load_user_config(self):
        return self._load_config(self.user_config_path, "An error occurred while loading user config")

    def load_recordings_config(self):
        return self._load_config(self.recordings_config_path, "An error occurred while loading recordings config")

    def load_accounts_config(self):
        return self._load_config(self.accounts_config_path, "An error occurred while loading accounts config")

    def load_cookies_config(self):
        return self._load_config(self.cookies_config_path, "An error occurred while loading cookies config")

    def load_about_config(self):
        return self._load_config(self.about_config_path, "An error occurred while loading about config")

    def load_language_config(self):
        return self._load_config(self.language_config_path, "An error occurred while loading language config")

    def load_i18n_config(self, path):
        """Load i18n configuration from a JSON file."""
        return self._load_config(path, "An error occurred while loading i18n config")

    def load_web_auth_config(self):
        return self._load_config(self.web_auth_config_path, "An error occurred while loading web auth config")

    _write_lock = threading.Lock()

    @staticmethod
    async def _save_config(config_path, config, success_message, error_message):
        """Save configuration to a JSON file (thread-safe, atomic write)."""
        try:
            content = json.dumps(config, ensure_ascii=False, indent=4)

            def _write_sync():
                with ConfigManager._write_lock:
                    dir_name = os.path.dirname(config_path)
                    fd, tmp_path = tempfile.mkstemp(suffix=".tmp", prefix=".cfg_", dir=str(dir_name))
                    try:
                        with os.fdopen(fd, "w", encoding="utf-8") as f:
                            f.write(content)
                        os.replace(tmp_path, config_path)
                    except BaseException:
                        # Clean up temp file on failure.
                        try:
                            os.unlink(tmp_path)
                        except OSError:
                            pass
                        raise

            await asyncio.to_thread(_write_sync)
            logger.info(success_message)
        except Exception as e:
            logger.error(f"{error_message}: {e}")

    async def save_recordings_config(self, config):
        await self._save_config(
            self.recordings_config_path,
            config,
            success_message="Recordings configuration saved.",
            error_message="An error occurred while saving recordings config",
        )

    async def save_accounts_config(self, config):
        await self._save_config(
            self.accounts_config_path,
            config,
            success_message="Accounts configuration saved.",
            error_message="An error occurred while saving accounts config",
        )

    async def save_web_auth_config(self, config):
        await self._save_config(
            self.web_auth_config_path,
            config,
            success_message="Web auth configuration saved.",
            error_message="An error occurred while saving web auth config",
        )

    async def save_user_config(self, config):
        await self._save_config(
            self.user_config_path,
            config,
            success_message="User configuration saved.",
            error_message="An error occurred while saving user config",
        )

    async def save_cookies_config(self, config):
        await self._save_config(
            self.cookies_config_path,
            config,
            success_message="Cookies configuration saved.",
            error_message="An error occurred while saving cookies config",
        )

    def get_config_value(self, key: str, default: T = None) -> T:
        user_config = self.load_user_config()
        default_config = self.load_default_config()
        return user_config.get(key, default_config.get(key, default))
