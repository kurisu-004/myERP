# syntax=docker/dockerfile:1.7

# ---------- 阶段 1：用 uv 装依赖（带 BuildKit 缓存） ----------
FROM ghcr.io/astral-sh/uv:0.5.11-python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# 先只同步依赖（无项目代码），能命中 BuildKit 缓存
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev

# 再复制项目本体并完成同步
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


# ---------- 阶段 2：精简运行时 ----------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    TZ=Asia/Shanghai

# tzdata 给 logging 用；不装 libpq5（asyncpg 的 manylinux 轮子自带）
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# 创建非 root 用户（python:3.12-slim 不自带 python 用户）
RUN groupadd --system --gid 1000 myerp \
    && useradd  --system --uid 1000 --gid myerp --home /app --shell /bin/bash myerp

WORKDIR /app

COPY --from=builder --chown=myerp:myerp /app /app
USER myerp

EXPOSE 8000

# 不装 curl：用 stdlib urllib 打 /api/v1/health
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; r=urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=2); sys.exit(0 if r.status==200 else 1)"

# 启动时跑迁移；exec 让 uvicorn 接管 PID 1，正确接收信号
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn main:app --host 0.0.0.0 --port 8000"]
