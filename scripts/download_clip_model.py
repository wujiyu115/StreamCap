#!/usr/bin/env python
"""下载 CLIP 服装分类权重到本地模型目录（约 580MB，一次性）。

用法:
    python scripts/download_clip_model.py [目标目录]

默认下载到 <项目根>/models/clip（生产容器内为 /app/models/clip，即宿主机
挂载的模型 share）。权重不进镜像也不进 git——镜像重建/更新后无需重新下载。

国内网络默认走 hf-mirror.com；已下载则直接跳过（幂等）。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.pose.clip_filter import CLIP_CACHE_DIR  # noqa: E402

DEFAULT_MODEL = "ViT-B-32-quickgelu"
DEFAULT_TAG = "openai"


def main() -> int:
    cache_dir = sys.argv[1] if len(sys.argv) > 1 else CLIP_CACHE_DIR
    os.makedirs(cache_dir, exist_ok=True)

    from app.core.pose.clip_filter import weights_available

    if weights_available(cache_dir):
        print(f"权重已存在，跳过: {cache_dir}")
        return 0

    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    print(f"下载 CLIP 权重 {DEFAULT_MODEL}/{DEFAULT_TAG} -> {cache_dir}")
    print("（国内网络走 hf-mirror.com；海外可 export HF_ENDPOINT=https://huggingface.co）")
    import open_clip

    open_clip.create_model_and_transforms(
        DEFAULT_MODEL, pretrained=DEFAULT_TAG, cache_dir=cache_dir
    )
    print(f"完成: {cache_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
