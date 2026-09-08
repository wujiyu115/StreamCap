import asyncio
import json
import os
import shutil
import tempfile
import threading
from typing import TypeVar

from ...utils.logger import logger
from .layering import strip_defaults

T = TypeVar("T")


class ConfigManager:
    # 镜像内置的配置模板目录（打包在 /app/config_templates）。
    # `default_settings.json` 就地当默认层读，**不复制进挂载卷**；
    # language.json / version.json 仍在缺失时初始化一份到 config/。
    TEMPLATE_DIR_NAME = "config_templates"
    # 覆盖层稀疏化迁移的标记：放 sidecar，不污染 user_settings.json
    MIGRATIONS_FILE_NAME = ".config_migrations.json"
    SPARSE_MIGRATION_KEY = "sparse_user_settings"

    def __init__(self, run_path):
        self.config_path = os.path.join(run_path, "config")
        self.language_config_path = os.path.join(self.config_path, "language.json")
        self.user_config_path = os.path.join(self.config_path, "user_settings.json")
        self.cookies_config_path = os.path.join(self.config_path, "cookies.json")
        self.about_config_path = os.path.join(self.config_path, "version.json")
        self.recordings_config_path = os.path.join(self.config_path, "recordings.json")
        self.validity_cache_config_path = os.path.join(self.config_path, "room_validity.json")
        self.accounts_config_path = os.path.join(self.config_path, "accounts.json")
        self.web_auth_config_path = os.path.join(self.config_path, "web_auth.json")
        self.analytics_dir = os.path.join(self.config_path, "analytics")
        self.migrations_path = os.path.join(self.config_path, self.MIGRATIONS_FILE_NAME)

        os.makedirs(self.config_path, exist_ok=True)

        template_dir = os.path.join(run_path, self.TEMPLATE_DIR_NAME)
        self.template_dir = template_dir if os.path.isdir(template_dir) else None
        # 默认层路径：镜像模板优先，开发态回落到仓库 config/default_settings.json。
        # 两种情况都是只读来源——运行期从不写它，所以老键不会停留在首次部署时的值。
        self.default_config_path = os.path.join(self.config_path, "default_settings.json")
        if self.template_dir:
            template_default = os.path.join(self.template_dir, "default_settings.json")
            if os.path.isfile(template_default):
                self.default_config_path = template_default
                self._retire_legacy_default_copy()
            for name in ("language.json", "version.json"):
                src = os.path.join(self.template_dir, name)
                dst = os.path.join(self.config_path, name)
                if os.path.isfile(src) and (not os.path.exists(dst) or os.path.getsize(dst) == 0):
                    shutil.copy(src, dst)
                    logger.info(f"Initialized {dst} from image template")

        self.init()
        self._migrate_sparse_user_settings()

    def init(self):
        self.init_user_config()
        self.init_cookies_config()
        self.init_accounts_config()
        self.init_recordings_config()
        self.init_web_auth_config()

    def _retire_legacy_default_copy(self) -> None:
        """把历史版本复制进挂载卷的 default_settings.json 挪走。

        它已经不是默认层了（默认层在镜像模板里），留着只会让人误以为改它
        有用——而且因为老实现「只补缺键、不覆盖已有键」，那份副本对老键会
        永久停留在首次部署时的值。
        """
        stale = os.path.join(self.config_path, "default_settings.json")
        if not os.path.isfile(stale):
            return
        retired = stale + ".legacy"
        try:
            os.replace(stale, retired)
            logger.info(f"Retired stale default settings copy: {stale} -> {retired} (defaults now read from image)")
        except OSError as e:
            logger.warning(f"Failed to retire stale {stale}: {e}")

    def _load_migrations(self) -> dict:
        if not os.path.isfile(self.migrations_path):
            return {}
        try:
            with open(self.migrations_path, encoding="utf-8") as f:
                return json.load(f) or {}
        except Exception:
            return {}

    def _mark_migration_done(self, key: str) -> None:
        state = self._load_migrations()
        state[key] = True
        try:
            with open(self.migrations_path, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=4)
        except OSError as e:
            logger.warning(f"Failed to record migration {key}: {e}")

    def _migrate_sparse_user_settings(self) -> None:
        """一次性把全量物化的 user_settings.json 收敛成稀疏覆盖层。

        历史实现的 PUT 会把 `default ∪ user` 的合并视图整体写回用户层，
        默认层因此被物化、永久失效。这里按当前默认层剥离等值键，让用户层
        只留真正偏离默认的部分。

        **只跑一次**（sidecar 标记）：旧文件里已经无法区分「用户显式设成与
        默认相同」和「继承默认」，每次启动都剥离会反复吃掉用户显式的选择。
        """
        if self._load_migrations().get(self.SPARSE_MIGRATION_KEY):
            return
        defaults = self.load_default_config()
        user = self.load_user_config()
        if not defaults or not user:
            # 没有默认层可比，或用户层本来就是空的：没什么可迁移，直接落标记
            self._mark_migration_done(self.SPARSE_MIGRATION_KEY)
            return

        sparse, dropped = strip_defaults(user, defaults)
        if dropped:
            backup = self.user_config_path + ".pre-sparse.bak"
            try:
                shutil.copy(self.user_config_path, backup)
                with open(self.user_config_path, "w", encoding="utf-8") as f:
                    json.dump(sparse, f, ensure_ascii=False, indent=4)
            except OSError as e:
                logger.error(f"Sparse user settings migration failed, keeping file as-is: {e}")
                return
            logger.info(
                f"Sparse user settings migration: dropped {len(dropped)} key(s) equal to defaults "
                f"({', '.join(dropped[:12])}{' ...' if len(dropped) > 12 else ''}); "
                f"{len(sparse)} override(s) kept; backup at {backup}"
            )
        self._mark_migration_done(self.SPARSE_MIGRATION_KEY)

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

    def init_user_config(self):
        """用户覆盖层：缺失时建一份空的 `{}`。

        不再从默认层复制——复制出来的就是物化的合并视图，之后镜像里改默认值
        对这台机器永久失效。空文件 = 全部跟随默认层。
        """
        self._init_config(self.user_config_path, {})

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

    def load_validity_cache_config(self):
        return self._load_config(self.validity_cache_config_path, "An error occurred while loading room validity cache")

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

    async def save_validity_cache_config(self, config):
        await self._save_config(
            self.validity_cache_config_path,
            config,
            success_message="Room validity cache saved.",
            error_message="An error occurred while saving room validity cache",
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
