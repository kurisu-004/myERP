# syntax=docker/dockerfile:1.7

# ---------- 阶段 1：用 uv 装依赖（bookworm 基础，glibc 轮子；与 runtime 同 libc） ----------
FROM ghcr.io/astral-sh/uv:0.5.11-python3.12-bookworm AS builder

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


# ---------- 阶段 2：Debian slim 运行时（带 CJK / MS 字体） ----------
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    TZ=Asia/Shanghai \
    DEBIAN_FRONTEND=noninteractive

# 切 apt 源到 HTTPS：Docker Desktop (macOS) 的 userland-proxy 会拦截容器内
# HTTP 出站，导致 deb.debian.org 的 InRelease 拉到一半 500 EOF。HTTPS 路径
# 走不同的代理逻辑，绕开该 bug。Debian 官方镜像（Fastly CDN）同时提供 HTTP
# 和 HTTPS，生产 / CI / 本地 build 都用同一套源；GPG keyring 不变。
# 同时开启 contrib 组件：ttf-mscorefonts-installer 属于 contrib（非自由，
# 因含 MS 字体）。
# python:3.12-slim-bookworm 已自带 debconf / adduser / ca-certificates，无需再装。
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g; s|http://security.debian.org|https://security.debian.org|g; s|^Components: main$|Components: main contrib|g' /etc/apt/sources.list.d/debian.sources

# 系统依赖 + 字体（一个 RUN 块原子完成）：
#   tzdata              — timezone 数据（Asia/Shanghai）
#   tini                — PID 1 信号转发（slim 镜像不带）
#   fontconfig          — fc-cache 命令（slim 不自带 fc-cache 二进制）
#   fonts-dejavu        — Latin 字体（service/_print_back_page.py 走 ASCII）
#   ttf-mscorefonts-installer — MS 字体（Arial/Times/...，备用，未走候选）
#   fonts-wqy-microhei  — CJK 字体（service/printing.py 信息卡中文渲染）
# fonts-wqy-microhei 提供 /usr/share/fonts/truetype/wqy/wqy-microhei.ttc，
# 候选路径已在 service/printing.py::_load_cn_font 列表里，零代码改动。
# ReportLab 背面页 ASCII 序列号走 fonts-dejavu（service/_print_back_page.py）。
# 不装 libpq5：asyncpg manylinux 轮子自带 libpq；pikepdf / pypdfium2 / pillow
# 轮子也都自带二进制依赖。
# ttf-mscorefonts-installer 的 postinst 询问 EULA——必须先 preseed 再 install，
# 否则 apt 在非交互式 build 里会卡死。
RUN apt-get update \
    && echo "ttf-mscorefonts-installer msttcorefonts/accepted-mscorefonts-eula select true" \
        | debconf-set-selections \
    && apt-get install -y --no-install-recommends \
        tzdata \
        tini \
        fontconfig \
        fonts-dejavu \
        ttf-mscorefonts-installer \
        fonts-wqy-microhei \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && fc-cache -fv \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

RUN addgroup --system --gid 1000 myerp \
    && adduser --system --uid 1000 --ingroup myerp --home /app --shell /bin/bash myerp

WORKDIR /app

COPY --from=builder --chown=myerp:myerp /app /app
USER myerp

EXPOSE 8000

# 不装 curl：用 stdlib urllib 打 /api/v1/health
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; r=urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=2); sys.exit(0 if r.status==200 else 1)"

# 启动时跑迁移；exec 让 uvicorn 接管 PID 1，tini 负责信号转发
# Debian 的 tini 包装在 /usr/bin/tini（alpine 在 /sbin/tini）
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn main:app --host 0.0.0.0 --port 8000"]
