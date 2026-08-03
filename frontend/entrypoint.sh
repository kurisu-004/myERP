#!/bin/sh
# ----------------------------------------------------------------------------
# frontend 容器启动脚本：
#   1. 根据 SSL 证书是否存在选择 nginx 模板（nginx.conf 带 HTTPS / nginx.http-only.conf 纯 HTTP）。
#   2. 用环境变量渲染模板里的占位符，写到 /etc/nginx/conf.d/default.conf。
#
# 模板里的占位符（见 frontend/nginx.conf / frontend/nginx.http-only.conf）：
#   ${NGINX_SERVER_NAME}     - HTTP/HTTPS 块的 server_name（生产 = hsh-erp.cloud）
#   ${NGINX_REDIRECT_TARGET} - HTTP 301 跳转目标（生产 = https://hsh-erp.cloud）
#   ${SSL_CRT_FILENAME}      - 证书文件名（生产 = hsh-erp.cloud_bundle.crt）
#   ${SSL_KEY_FILENAME}      - 私钥文件名（生产 = hsh-erp.cloud.key）
#
# 默认值由 docker-compose.yml 通过 environment 注入；测试服务器（无域名 / 无证书）
# 只需覆盖 NGINX_SERVER_NAME=_ + NGINX_REDIRECT_TARGET= + 不挂证书，entrypoint 会
# 自动走 HTTP-only 分支，渲染成 server_name _ / return 301 $request_uri; 。
#
# 启用 HTTPS 的条件：宿主机把 cert / key 放到 SSL_CRT_FILENAME / SSL_KEY_FILENAME
# 指定的文件名，并在 docker-compose.yml 启用 bind mount。
# ----------------------------------------------------------------------------
set -eu

NGINX_CONF="/etc/nginx/conf.d/default.conf"
SSL_CRT="/etc/nginx/ssl/${SSL_CRT_FILENAME:-hsh-erp.cloud_bundle.crt}"
SSL_KEY="/etc/nginx/ssl/${SSL_KEY_FILENAME:-hsh-erp.cloud.key}"

if [ -f "$SSL_CRT" ] && [ -f "$SSL_KEY" ]; then
    echo "[entrypoint] SSL cert found, HTTPS server block enabled (use shipped default.conf)"
else
    echo "[entrypoint] SSL cert missing ($SSL_CRT or $SSL_KEY)"
    echo "[entrypoint]   switching to HTTP-only config (nginx.http-only.conf)"
    # nginx.conf（HTTPS 版）的 :80 server 只 301 → https，
    # 证书缺失时整份替换成纯 HTTP 配置（location 与 HTTPS 块一致，不带 SSL/HSTS）。
    cp /etc/nginx/templates/http-only.conf "$NGINX_CONF"
fi

# 用环境变量渲染 4 个白名单占位符。
# envsubst 第一个参数只列需要替换的变量名（nginx 内置变量如 $request_uri 保持原样）。
envsubst '${NGINX_SERVER_NAME} ${NGINX_REDIRECT_TARGET} ${SSL_CRT_FILENAME} ${SSL_KEY_FILENAME}' \
    < "$NGINX_CONF" > "$NGINX_CONF.rendered"
mv "$NGINX_CONF.rendered" "$NGINX_CONF"

# 注意：不要在这里 exec nginx。nginx:1.27-alpine 镜像默认 entrypoint
# /docker-entrypoint.sh 会先跑 /docker-entrypoint.d/*.sh，再 exec CMD
# ["nginx", "-g", "daemon off;"]。我们在前置阶段处理完配置文件即可退出。