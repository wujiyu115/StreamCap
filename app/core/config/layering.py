"""配置分层的纯函数工具。

分层模型：

- **默认层**：镜像内的只读模板（`/app/config_templates/default_settings.json`），
  开发态回落到仓库 `config/default_settings.json`。不进挂载卷。
- **用户覆盖层**：挂载卷里的 `user_settings.json`，**稀疏**——只含用户显式改过的键。
- **有效配置**：`deep_merge(默认层, 覆盖层)`，只在内存里派生，**永不落盘**。

把合并视图写回覆盖层（旧实现的整文档 PUT）会把默认层「物化」进用户层，
之后镜像里改默认值对已部署实例永久失效，且新增默认键会在用户下一次
保存任意设置时被固化——这是配置被更新「顶掉」的根因。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

_MISSING = object()


def deep_merge(base: dict, override: dict) -> dict:
    """深度合并两层配置，返回新 dict。dict 递归合并，其余类型（含 list）整体覆盖。"""
    merged = dict(base)
    for key, value in override.items():
        current = merged.get(key)
        if isinstance(value, dict) and isinstance(current, dict):
            merged[key] = deep_merge(current, value)
        else:
            merged[key] = value
    return merged


def rebuild_effective(target: dict, defaults: dict, overrides: dict) -> dict:
    """就地把 ``target`` 刷成最新的有效配置。

    就地更新而不是换引用：`StreamManager` 等处在构造时就捕获了
    `settings_config.user_config` 的引用，换新对象会让它们读到旧值。
    """
    merged = deep_merge(defaults, overrides)
    target.clear()
    target.update(merged)
    return target


def apply_patch(overrides: dict, patch: dict) -> bool:
    """把 ``patch`` 深度合并进覆盖层（就地），返回是否有实际变化。

    只动 patch 里出现的键——没出现的键继续跟随默认层，这是「构造式稀疏」：
    稀疏性来自写入路径（前端只发用户真正编辑过的键），而不是保存时剥离
    等值键。后者会把「用户显式设成与默认相同」误判成「继承默认」。
    """
    changed = False
    for key, value in patch.items():
        current = overrides.get(key, _MISSING)
        if isinstance(value, dict) and isinstance(current, dict):
            changed = apply_patch(current, value) or changed
        elif current is _MISSING or current != value:
            overrides[key] = value
            changed = True
    return changed


def unset_path(overrides: dict, path: str) -> bool:
    """按点号路径删除一个覆盖项（恢复默认），返回是否删掉了东西。空的父段一并剪掉。"""
    segments = [s for s in path.split(".") if s]
    if not segments:
        return False

    chain: list[tuple[dict, str]] = []
    node: Any = overrides
    for segment in segments[:-1]:
        if not isinstance(node, dict) or segment not in node:
            return False
        chain.append((node, segment))
        node = node[segment]
    if not isinstance(node, dict) or segments[-1] not in node:
        return False

    del node[segments[-1]]
    for parent, segment in reversed(chain):
        if isinstance(parent[segment], dict) and not parent[segment]:
            del parent[segment]
        else:
            break
    return True


def strip_defaults(user: dict, defaults: dict, prefix: str = "") -> tuple[dict, list[str]]:
    """剥离与默认层相等的键，返回（稀疏覆盖层, 被剥离的路径列表）。

    只用于一次性迁移历史的全量 `user_settings.json`：旧文件里已经分不清
    「显式设置」和「继承默认」，所以这个判断天然有损，绝不能在每次保存或
    每次启动时跑——否则用户显式选的、恰好等于默认值的项会被反复吃掉。
    """
    kept: dict[str, Any] = {}
    dropped: list[str] = []
    for key, value in user.items():
        default_value = defaults.get(key, _MISSING)
        if isinstance(value, dict) and isinstance(default_value, dict):
            sub_kept, sub_dropped = strip_defaults(value, default_value, f"{prefix}{key}.")
            dropped.extend(sub_dropped)
            if sub_kept:
                kept[key] = sub_kept
        elif default_value is not _MISSING and value == default_value:
            dropped.append(f"{prefix}{key}")
        else:
            kept[key] = value
    return kept, dropped


def config_hash(overrides: dict) -> str:
    """覆盖层内容指纹，用于设置写入的乐观并发（GET 下发、PUT 带回，不匹配 409）。"""
    canonical = json.dumps(overrides, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
