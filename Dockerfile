# ── Stage 1: build frontend ──────────────────────────────
FROM --platform=$BUILDPLATFORM oven/bun:1-alpine AS frontend-build

WORKDIR /build
COPY frontend/package.json frontend/bun.lock* ./
RUN --mount=type=cache,target=/root/.bun/install/cache bun install
COPY frontend/ ./
RUN bun run build

# ── Stage 2: install python dependencies into a venv ─────
FROM python:3.12-slim AS builder

WORKDIR /app

# 独立 venv：整体拷贝到运行阶段，且 pip 能正确识别已安装的包
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt requirements-pose.txt ./

# 先从 CPU 专用源装 torch/torchvision，避免 ultralytics 拉取数 GB 的 CUDA 版本；
# venv 在 PATH 上，后续安装 ultralytics 时能识别已装的 CPU torch，不会回退到 CUDA 版。
RUN pip install --no-cache-dir --root-user-action=ignore \
        --index-url https://download.pytorch.org/whl/cpu \
        torch torchvision \
    && pip install --no-cache-dir --root-user-action=ignore -r requirements.txt \
    && pip install --no-cache-dir --root-user-action=ignore -r requirements-pose.txt \
    # ultralytics 会拉入非 headless 的 opencv-python（依赖 libGL 且与 headless 重复），
    # 统一只保留 headless 版：省约 230MB，运行时也不需要 libGL。
    # 卸载目标不存在时 pip 报错，忽略后只装 headless 版即可。
    && (pip uninstall -y opencv-python opencv-python-headless || true) \
    && pip install --no-cache-dir --root-user-action=ignore opencv-python-headless

COPY . .
COPY --from=frontend-build /build/dist ./frontend/dist

# ── Stage 3: runtime ─────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

# 运行时系统依赖：headless OpenCV 不需要 libGL；curl 供 healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    tzdata \
    curl \
    libglib2.0-0 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# 后端 i18n 语言包（config 挂载卷不覆盖它）
COPY --from=builder /app/locales ./locales

# 配置模板：config 挂载为空卷时首启自动初始化（default_settings/language/version）
COPY --from=builder /app/config/ ./config_templates/

COPY --from=builder /app/ ./

# 禁用 NNPACK，避免部分硬件上刷 "Could not initialize NNPACK" 警告
ENV PYTORCH_DISABLE_NNPACK=1
# ultralytics 的 settings.json 落点：slim 镜像无 ~/.config，指定 /tmp 避免误导性警告
ENV YOLO_CONFIG_DIR=/tmp

ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo "$TZ" > /etc/timezone \
    && mkdir -p /app/logs /app/downloads

EXPOSE 6006

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD curl -sf http://localhost:6006/api/system/info || exit 1

CMD ["python", "main.py", "--host", "0.0.0.0", "--port", "6006"]
