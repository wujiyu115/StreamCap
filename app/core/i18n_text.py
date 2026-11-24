"""后端 i18n 文案的兜底取值。

`RecordingManager._` / `LiveStreamRecorder._` 都是 `<run_path>/locales/<lang>.json`
按块 merge 出来的扁平字典。取文案时**不能下标取值**：隔离配置目录、精简部署或语言包
损坏都会让字典是空的，而下标会直接抛 KeyError —— 2026-11-24 踩过：

- `stop_monitor_recording` 崩在 `self._["monitor_stopped"]`，而 `batch-monitor` 走
  `services.run_coro` 把异常吞在 future 里，接口照样返回 `{"ok": true}`；
- 直播检测写标题（`recording.quality` / `is_live`）时崩，表现为「检测到开播了但永远
  不开始录」。
"""

from __future__ import annotations


def lang_text(dictionary: dict, key: str, fallbacks: dict | None = None) -> str:
    """先取语言包，再取内置兜底，最后原样返回 key（永不抛异常）。"""
    value = dictionary.get(key)
    if isinstance(value, str) and value:
        return value
    if fallbacks:
        fallback = fallbacks.get(key)
        if isinstance(fallback, str) and fallback:
            return fallback
    return key
