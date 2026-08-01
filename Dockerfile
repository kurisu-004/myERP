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
# font-dejavu：service/printing.py 渲染序列号（ASCII 字符如 F1004）需要支持 size
# 的 TTF 字体；alpine 默认无任何字体，_load_cn_font 会 fallback 到 PIL 内置
# ~10px bitmap（完全忽略 size 参数），导致序列号在部署后变成蚂蚁大小。
# font-noto-cjk（2026-08-01 新增）：信息卡中文 / PDFium 源字体替代所需的 CJK 字体；
# alpine community 包，含 NotoSans/Serif CJK Regular/Bold TTC，~71 MB 安装大小；
# 装在 /usr/share/fonts/noto/NotoSansCJK-Regular.ttc，与 _load_cn_font 候选路径
# 第 5 项一致，覆盖 SC/TC/HK/JP/KR。可由 .env 的 PRINT_CN_FONT_PATH 覆盖到企业
# 自有字体；删除此包会让中文字符回退到 tofu。
# 注：Alpine 主仓库没有 wqy-microhei 包（只有 font-noto-cjk / font-wqy-zenhei
# 名字，且 font-wqy-zenhei 也已不在 community repo），故选用 font-noto-cjk。
RUN apk add --no-cache tzdata tini font-dejavu font-noto-cjk \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone

RUN addgroup -g 1000 -S myerp \
    && adduser -u 1000 -S -G myerp -h /app -s /bin/bash myerp

WORKDIR /app

# 2026-07-31：打印正面页两级缓存目录（L1 本地磁盘 LRU；docker-compose
# 把 printcache 命名卷挂到这里，跨容器重建保留缓存）
RUN mkdir -p /app/.cache/print && chown myerp:myerp /app/.cache/print

COPY --from=builder --chown=myerp:myerp /app /app
USER myerp

EXPOSE 8000

# 不装 curl：用 stdlib urllib 打 /api/v1/health
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; r=urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=2); sys.exit(0 if r.status==200 else 1)"

# 启动时跑迁移；exec 让 uvicorn 接管 PID 1，tini 负责信号转发
ENTRYPOINT ["/sbin/tini", "--"]
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn main:app --host 0.0.0.0 --port 8000"]
