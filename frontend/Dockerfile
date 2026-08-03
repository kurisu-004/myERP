# syntax=docker/dockerfile:1.7

# ---------- 阶段 1：用 node 构建前端 ----------
FROM node:22-alpine AS builder

WORKDIR /app

COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci --no-audit --no-fund

COPY . .
# 只跑 vite build（不打类型检查）。类型检查应在独立的 CI 任务里跑（lint 流程）
# 而不是阻塞镜像构建——vite 用 esbuild 转译时会自动剥除类型注解。
RUN npx vite build


# ---------- 阶段 2：nginx 托管静态产物 ----------
FROM nginx:1.27-alpine AS runtime

# 移除默认 default.conf 避免与我们的 server 块冲突
RUN rm -f /etc/nginx/conf.d/default.conf \
    && apk add --no-cache wget bash gettext \
    && mkdir -p /etc/nginx/ssl

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY nginx.http-only.conf /etc/nginx/templates/http-only.conf
COPY entrypoint.sh /docker-entrypoint.d/40-nginx-ssl-gate.sh
COPY --from=builder /app/dist /usr/share/nginx/html
RUN chmod +x /docker-entrypoint.d/40-nginx-ssl-gate.sh

EXPOSE 80 443

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget --no-check-certificate -q --spider http://127.0.0.1/ || exit 1

# nginx:1.27-alpine 镜像默认 ENTRYPOINT 是 /docker-entrypoint.sh，
# 它会跑 /docker-entrypoint.d/*.sh 再 exec CMD ["nginx", "-g", "daemon off;"]。
# 我们在 40-nginx-ssl-gate.sh 里根据证书存在与否 sed 删 HTTPS server 块，
# 然后让 nginx 启动。如果证书不存在，nginx -t 不会因缺失证书报错。
CMD ["nginx", "-g", "daemon off;"]
