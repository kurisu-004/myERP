# syntax=docker/dockerfile:1.7

# ---------- 阶段 1：用 uv 装依赖（alpine 基础，musllinux 轮子） ----------
FROM ghcr.io/astral-sh/uv:0.5.11-python3.12-alpine AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev

COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


# ---------- 阶段 2：alpine 运行时（镜像体积减半） ----------
FROM python:3.12-alpine AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    TZ=Asia/Shanghai

# tzdata 给 logging 用；tini 正确转发信号给 uvicorn（alpine 没自带）
# 不装 libpq5：asyncpg 的 musllinux 轮子自带
# wqy-microhei：service/printing.py 渲染图纸反面的序列号 + 信息卡需要中文字体；
# alpine 默认无任何字体，_load_cn_font 会 fallback 到 PIL 内置 ~10px bitmap，
# 导致序列号在部署后变成蚂蚁大小
RUN apk add --no-cache tzdata tini wqy-microhei \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone

RUN addgroup -g 1000 -S myerp \
    && adduser -u 1000 -S -G myerp -h /app -s /bin/bash myerp

WORKDIR /app

COPY --from=builder --chown=myerp:myerp /app /app
USER myerp

EXPOSE 8000

# 不装 curl：用 stdlib urllib 打 /api/v1/health
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; r=urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=2); sys.exit(0 if r.status==200 else 1)"

# 启动时跑迁移；exec 让 uvicorn 接管 PID 1，tini 负责信号转发
ENTRYPOINT ["/sbin/tini", "--"]
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn main:app --host 0.0.0.0 --port 8000"]
