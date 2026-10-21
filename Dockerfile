# ── Stage 1: build frontend ──────────────────────────────
FROM --platform=$BUILDPLATFORM oven/bun:1-alpine AS frontend-build

WORKDIR /build
COPY frontend/package.json frontend/bun.lock* ./
RUN --mount=type=cache,target=/root/.bun/install/cache bun install
COPY frontend/ ./
RUN bun run build

# ── Stage 2: install python dependencies into a venv ─────
# 基础镜像钉在 trixie：运行阶段要装 chromium 依赖，包名带 t64 后缀（libatk1.0-0t64 等），
# 写成滚动 tag 的话上游换发行版会让那串包名静默失效。
FROM python:3.12-slim-trixie AS builder

WORKDIR /app

# 独立 venv：整体拷贝到运行阶段，且 pip 能正确识别已安装的包
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt requirements-pose.txt requirements-login.txt ./

# 先从 CPU 专用源装 torch/torchvision，避免 ultralytics 拉取数 GB 的 CUDA 版本；
# venv 在 PATH 上，后续安装 ultralytics 时能识别已装的 CPU torch，不会回退到 CUDA 版。
# CLIP 权重不进镜像：外置挂载到 /app/models/clip（宿主机模型 share），
# 用 scripts/download_clip_model.py 下载；未下载时 CLIP 闸门自动跳过并提示。
RUN HF_ENDPOINT=https://hf-mirror.com pip install --no-cache-dir --root-user-action=ignore \
        --index-url https://download.pytorch.org/whl/cpu \
        torch torchvision \
    && HF_ENDPOINT=https://hf-mirror.com pip install --no-cache-dir --root-user-action=ignore -r requirements.txt \
    && HF_ENDPOINT=https://hf-mirror.com pip install --no-cache-dir --root-user-action=ignore -r requirements-pose.txt \
    && pip install --no-cache-dir --root-user-action=ignore -r requirements-login.txt \
    # ultralytics 会拉入非 headless 的 opencv-python（依赖 libGL 且与 headless 重复），
    # 统一只保留 headless 版：省约 230MB，运行时也不需要 libGL。
    # 卸载目标不存在时 pip 报错，忽略后只装 headless 版即可。
    && (pip uninstall -y opencv-python opencv-python-headless || true) \
    && pip install --no-cache-dir --root-user-action=ignore opencv-python-headless

# 扫码登录用的浏览器：只装 chromium headless shell。
# - 不用完整 chromium：省约 127MB（headless shell 262MB vs chromium 389MB），我们只驱动页面取二维码
# - 不用 `playwright install --with-deps`：它会给 slim 装一整排 fonts/完整 X 栈；实测真正缺的只有
#   9 个包，改由运行阶段显式补（见下），净增 5.6MB
# - locales 只留中英文：220 个 .pak 里 218 个用不上，实测 262MB → 209MB
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
RUN playwright install --no-progress --only-shell chromium \
    && find /opt/ms-playwright -type f -name '*.pak' ! -name 'en-US.pak' ! -name 'zh-CN.pak' -delete

COPY . .
COPY --from=frontend-build /build/dist ./frontend/dist

# ── Stage 3: runtime ─────────────────────────────────────
FROM python:3.12-slim-trixie

WORKDIR /app

# 运行时系统依赖：headless OpenCV 不需要 libGL；curl 供 healthcheck；
# psmisc 提供 fuser——文件就绪判定的快路径，没它就退化成 sleep 采样 mtime。
# 最后 9 个是 chromium-headless-shell 直接依赖里基础镜像缺的那部分（其余 X11/wayland/gbm/drm/asound
# 已被上面 ffmpeg→libsdl2 带进来，所以这里只补这几只小的）；t64 后缀是 trixie 的 time64 改名。
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    tzdata \
    curl \
    libglib2.0-0 \
    psmisc \
    libnspr4 \
    libnss3 \
    libatk1.0-0t64 \
    libatk-bridge2.0-0t64 \
    libatspi2.0-0t64 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libxfixes3 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv
# 扫码登录用的 chromium headless shell（209MB，多余语言包已在 builder 里删掉）
COPY --from=builder /opt/ms-playwright /opt/ms-playwright
ENV PATH="/opt/venv/bin:$PATH"
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright

# 后端 i18n 语言包（config 挂载卷不覆盖它）
COPY --from=builder /app/locales ./locales

# 配置默认层 + 模板：default_settings.json 就地被当默认层读取（不复制进挂载卷，
# 否则老键会永久停留在首次部署时的值）；language/version 仅在缺失时初始化一份
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
