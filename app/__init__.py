from .core.runtime.paths import prepare_user_data_dir, resource_dir, user_data_dir

prepare_user_data_dir()

execute_dir = str(user_data_dir)
resource_dir = str(resource_dir)

__all__ = ["execute_dir", "resource_dir"]
