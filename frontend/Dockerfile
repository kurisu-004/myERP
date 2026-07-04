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
    && apk add --no-cache wget \
    && mkdir -p /etc/nginx/ssl

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80 443

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget --no-check-certificate -q --spider https://127.0.0.1/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
