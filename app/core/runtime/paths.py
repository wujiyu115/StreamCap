from pathlib import Path

APP_NAME = "StreamCap"


def _project_root() -> Path:
    # <root>/app/core/runtime/paths.py -> <root>
    return Path(__file__).resolve().parents[3]


resource_dir = _project_root()
user_data_dir = resource_dir

default_recordings_dir = user_data_dir / "downloads"


def prepare_user_data_dir() -> None:
    user_data_dir.mkdir(parents=True, exist_ok=True)
