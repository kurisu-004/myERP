# syntax=docker/dockerfile:1.7
# ---- 阶段1：builder，用 uv 装依赖 ----
FROM ghcr.io/astral-sh/uv:0.5.11-python3.12-bookworm-slim AS builder

WORKDIR /app

# 先只拷 lock + pyproject，最大限度利用 docker 缓存
COPY pyproject.toml uv.lock ./

# 装运行时依赖到 /app/.venv（排除 dev 依赖）
RUN uv sync --frozen --no-dev --no-install-project

# 再拷源码（依赖装完才拷，改源码不重装依赖）
COPY . .

# 把项目本身也装进 venv
RUN uv sync --frozen --no-dev

# ---- 阶段2：runtime ----
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH" \
    TZ=Asia/Shanghai

# Debian 需要 tzdata 才能识别 TZ
RUN apt-get update && apt-get install -y --no-install-recommends \
        tzdata curl \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 从 builder 拷整个 /app（含 .venv + 源码）
COPY --from=builder /app /app

EXPOSE 8000

# 启动：先跑 alembic 迁移，再起 uvicorn
# lifespan 里有 SELECT 1 心跳，DB 不可达会让容器退出
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn main:app --host 0.0.0.0 --port 8000"]