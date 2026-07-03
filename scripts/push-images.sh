#!/usr/bin/env bash
# ----------------------------------------------------------------------------
# 本地构建并推送 myERP 前后端镜像到腾讯云 TCR
# 用法:
#   ./scripts/push-images.sh                  # 推 prod- 前缀（默认）
#   TAG_PREFIX=staging ./scripts/push-images.sh
#   DOCKER_REPO=ccr.ccs.tencentyun.com/foo ./scripts/push-images.sh
#
# 凭据: 自动从 .env 读取 DOCKER_USERNAME / DOCKER_PASSWORD / DOCKER_REPO
#       也可以在 shell 里 export 覆盖
# ----------------------------------------------------------------------------
set -euo pipefail

cd "$(dirname "$0")/.."

# 载入 .env（如果存在）—— 把所有 K=V 导出到当前 shell
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . .env
  set +a
fi

: "${DOCKER_USERNAME:?DOCKER_USERNAME 未设置（在 .env 里配或 export）}"
: "${DOCKER_PASSWORD:?DOCKER_PASSWORD 未设置（在 .env 里配或 export）}"
: "${DOCKER_REPO:?DOCKER_REPO 未设置（默认 ccr.ccs.tencentyun.com/hsh-erp）}"

TAG_PREFIX="${TAG_PREFIX:-prod}"
SHA="$(git rev-parse --short HEAD 2>/dev/null || echo 'local')"
REGISTRY_HOST="$(echo "$DOCKER_REPO" | cut -d/ -f1)"

echo "==> 仓库: $DOCKER_REPO"
echo "==> 标签前缀: $TAG_PREFIX  (镜像: $TAG_PREFIX-backend / $TAG_PREFIX-frontend)"
echo "==> Git SHA: $SHA"
echo

echo "==> 1/4 登录 TCR ($REGISTRY_HOST)"
echo "$DOCKER_PASSWORD" | docker login "$REGISTRY_HOST" -u "$DOCKER_USERNAME" --password-stdin

echo
echo "==> 2/4 构建 backend  ($TAG_PREFIX-backend:$SHA)"
# --platform linux/amd64：Mac Apple Silicon 本地 build 默认出 arm64，
# CVM 是 amd64，必须显式指定。push 之后 CVM 才能正常拉取。
docker build \
  --platform linux/amd64 \
  -t "$DOCKER_REPO/$TAG_PREFIX-backend:$SHA" \
  -t "$DOCKER_REPO/$TAG_PREFIX-backend:latest" \
  --label "org.opencontainers.image.revision=$SHA" \
  --label "org.opencontainers.image.source=$(git config --get remote.origin.url 2>/dev/null || echo 'local')" \
  .

echo
echo "==> 3/4 构建 frontend ($TAG_PREFIX-frontend:$SHA)"
docker build \
  --platform linux/amd64 \
  -t "$DOCKER_REPO/$TAG_PREFIX-frontend:$SHA" \
  -t "$DOCKER_REPO/$TAG_PREFIX-frontend:latest" \
  --label "org.opencontainers.image.revision=$SHA" \
  --label "org.opencontainers.image.source=$(git config --get remote.origin.url 2>/dev/null || echo 'local')" \
  ./frontend

echo
echo "==> 4/4 推送 4 个 tag 到 TCR"
docker push "$DOCKER_REPO/$TAG_PREFIX-backend:$SHA"
docker push "$DOCKER_REPO/$TAG_PREFIX-backend:latest"
docker push "$DOCKER_REPO/$TAG_PREFIX-frontend:$SHA"
docker push "$DOCKER_REPO/$TAG_PREFIX-frontend:latest"

echo
echo "==> 登出（清理凭据）"
docker logout "$REGISTRY_HOST" >/dev/null

echo
echo "✓ 推送完成"
echo "  - $DOCKER_REPO/$TAG_PREFIX-backend:$SHA"
echo "  - $DOCKER_REPO/$TAG_PREFIX-backend:latest"
echo "  - $DOCKER_REPO/$TAG_PREFIX-frontend:$SHA"
echo "  - $DOCKER_REPO/$TAG_PREFIX-frontend:latest"
