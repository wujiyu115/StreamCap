# ── Stage 1: build frontend ──────────────────────────────
FROM oven/bun:1 AS frontend

ENV BUN_CONFIG_REGISTRY=https://registry.npmmirror.com

WORKDIR /build
COPY frontend/package.json frontend/bun.lock* ./
RUN bun install

COPY frontend/ ./
RUN bun run build

# ── Stage 2: install python dependencies ─────────────────
FROM python:3.12-slim AS builder

WORKDIR /app

COPY requirements.txt requirements-pose.txt ./
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple \
    && pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements-pose.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

COPY . .
COPY --from=frontend /build/dist ./frontend/dist

# ── Stage 3: runtime ─────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

RUN sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null \
    || sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list \
    ; apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    tzdata \
    curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo "$TZ" > /etc/timezone

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /app/ ./

RUN mkdir -p /app/logs /app/downloads

EXPOSE 6006

CMD ["python", "main.py", "--host", "0.0.0.0", "--port", "6006"]
