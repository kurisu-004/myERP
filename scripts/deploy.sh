#!/usr/bin/env bash
# ----------------------------------------------------------------------------
# 在 CVM 上拉取并启动 myERP
# 用法:
#   ./scripts/deploy.sh                  # 用 .env.production 启动
#   ./scripts/deploy.sh --env-file .env  # 用 dev .env 启动
#   IMAGE_TAG=v1.2.3 ./scripts/deploy.sh # 拉指定 tag
#
# 前置:
#   1. 已经 docker login ccr.ccs.tencentyun.com
#   2. 当前目录有 docker-compose.yml 和 .env.production
#   3. 镜像已由本地 ./scripts/push-images.sh 推到 TCR
# ----------------------------------------------------------------------------
set -euo pipefail

cd "$(dirname "$0")/.."

ENV_FILE="${ENV_FILE:-.env.production}"
COMPOSE="docker compose --env-file $ENV_FILE"

# 兜底：把 $ENV_FILE 里的关键变量导到当前 shell
# （docker compose 自己会读 $ENV_FILE 一次；但脚本自己 shell 也要用这些值）
if [ -f "$ENV_FILE" ]; then
  while IFS='=' read -r key val; do
    case "$key" in
      DOCKER_USERNAME|DOCKER_PASSWORD|DOCKER_REPO|FRONTEND_PORT|IMAGE_TAG)
        if [ -z "${!key:-}" ]; then
          export "$key=$val"
        fi
        ;;
    esac
  done < <(grep -E '^(DOCKER_USERNAME|DOCKER_PASSWORD|DOCKER_REPO|FRONTEND_PORT|IMAGE_TAG)=' "$ENV_FILE" || true)
fi

echo "==> 当前部署目录: $(pwd)"
echo "==> 环境文件:     $ENV_FILE"
echo "==> IMAGE_TAG:    ${IMAGE_TAG:-latest}"
echo

# 确认 TCR 登录
if ! docker info 2>/dev/null | grep -q "Username"; then
  echo "==> 未登录 TCR，先登录"
  : "${DOCKER_USERNAME:?DOCKER_USERNAME 未设置（在 .env.production 里加 DOCKER_USERNAME=xxx 和 DOCKER_PASSWORD=xxx，或先 docker login）}"
  : "${DOCKER_PASSWORD:?DOCKER_PASSWORD 未设置（在 .env.production 里加 DOCKER_USERNAME=xxx 和 DOCKER_PASSWORD=xxx，或先 docker login）}"
  REGISTRY_HOST=$(echo "${DOCKER_REPO:-ccr.ccs.tencentyun.com/hsh-erp}" | cut -d/ -f1)
  echo "$DOCKER_PASSWORD" | docker login "$REGISTRY_HOST" -u "$DOCKER_USERNAME" --password-stdin
fi

echo
echo "==> 1/5 拉取镜像"
$COMPOSE pull

echo
echo "==> 2/5 启动（迁移在 backend 容器启动时自动跑）"
$COMPOSE up -d --remove-orphans

echo
echo "==> 3/5 等待 backend 健康"
for i in $(seq 1 30); do
  STATUS=$(docker inspect --format='{{.State.Health.Status}}' myerp-backend 2>/dev/null || echo "missing")
  if [ "$STATUS" = "healthy" ]; then
    echo "  ✓ backend healthy (after ${i}s)"
    break
  fi
  if [ "$i" = "30" ]; then
    echo "  ✗ backend 30s 内未 healthy，当前状态: $STATUS"
    echo "  日志:"
    docker logs myerp-backend --tail 30
    exit 1
  fi
  sleep 1
done

echo
echo "==> 4/5 验证 HTTP /api/v1/health"
HOST_PORT="${FRONTEND_PORT:-80}"
for i in $(seq 1 10); do
  if curl -fsS "http://127.0.0.1:${HOST_PORT}/api/v1/health" >/dev/null 2>&1; then
    echo "  ✓ http://127.0.0.1:${HOST_PORT}/api/v1/health 200"
    break
  fi
  if [ "$i" = "10" ]; then
    echo "  ✗ /api/v1/health 10 次都没通"
    echo "  frontend 日志:"
    docker logs myerp-frontend --tail 20
    exit 1
  fi
  sleep 1
done

echo
echo "==> 5/5 验证 HTTPS (容器内 self-test)"
# 容器内 wget 用 --no-check-certificate 跳过证书校验，仅验证 nginx HTTPS 块是否启用 + upstream 通；
# 真正的证书链 / 浏览器兼容性留给外部 curl / 浏览器去验。
for i in $(seq 1 10); do
  if docker exec myerp-frontend wget --no-check-certificate -q --spider https://127.0.0.1/api/v1/health 2>/dev/null; then
    echo "  ✓ frontend container https://127.0.0.1/api/v1/health 200"
    break
  fi
  if [ "$i" = "10" ]; then
    # HTTPS 没通不直接 exit 1：可能是证书未挂载（旧 compose），HTTP 模式仍可用
    echo "  ⚠ HTTPS 自检 10 次都没通（可能证书未挂载或 HTTPS 块未启用）"
    echo "  frontend 日志最后 5 行:"
    docker logs myerp-frontend --tail 5
  fi
  sleep 1
done

echo
echo "==> 当前容器状态"
$COMPOSE ps

echo
echo "✓ 部署完成"
echo "  浏览器访问: https://hsh-erp.cloud/"
echo "  （HTTP 请求会自动 301 重定向到 HTTPS）"
