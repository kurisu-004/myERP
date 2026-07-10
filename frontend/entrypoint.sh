#!/bin/sh
# ----------------------------------------------------------------------------
# frontend 容器启动脚本：根据 SSL 证书是否存在决定是否启用 HTTPS server 块。
#
# nginx.conf 里 HTTPS server 块用 # >>> SSL_BLOCK_START <<< / <<< SSL_BLOCK_END <<<
# 标记包裹；证书缺失时 sed 删掉整段，nginx 启动不会因为找不到证书文件而失败。
#
# 启用 HTTPS 的条件：CVM 宿主机把 cert / key scp 到
#   /home/ubuntu/hsh-erp/ssl/hsh-erp.cloud_bundle.crt
#   /home/ubuntu/hsh-erp/ssl/hsh-erp.cloud.key
# 并在 docker-compose.yml 启用 bind mount（参见文件内注释）。
# ----------------------------------------------------------------------------
set -eu

NGINX_CONF="/etc/nginx/conf.d/default.conf"
SSL_CRT="/etc/nginx/ssl/hsh-erp.cloud_bundle.crt"
SSL_KEY="/etc/nginx/ssl/hsh-erp.cloud.key"

if [ -f "$SSL_CRT" ] && [ -f "$SSL_KEY" ]; then
    echo "[entrypoint] SSL cert found, HTTPS server block enabled"
else
    echo "[entrypoint] SSL cert missing ($SSL_CRT or $SSL_KEY)"
    echo "[entrypoint]   removing HTTPS server block from nginx.conf"
    # 删掉从 SSL_BLOCK_START 标记行（含）到文件末尾的所有行
    sed -i '/>>> SSL_BLOCK_START <<</,$d' "$NGINX_CONF"
    # 上面会保留 SSL_BLOCK_END 这行残留（如果它还在的话）；一并清掉
    sed -i '/<<< SSL_BLOCK_END <<</d' "$NGINX_CONF"
fi

# 注意：不要在这里 exec nginx。nginx:1.27-alpine 镜像默认 entrypoint
# /docker-entrypoint.sh 会先跑 /docker-entrypoint.d/*.sh，再 exec CMD
# ["nginx", "-g", "daemon off;"]。我们在前置阶段处理完配置文件即可退出。