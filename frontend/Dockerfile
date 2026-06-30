# syntax=docker/dockerfile:1.7
# ---- 阶段1：builder，用 node 编译前端 ----
FROM node:22-alpine AS builder

WORKDIR /app

# 先拷 lock 文件以利用 docker 缓存
COPY package*.json ./
RUN npm ci --no-audit --no-fund

COPY . .
RUN npm run build   # vue-tsc --noEmit && vite build

# ---- 阶段2：runtime，nginx 服务静态 + 反代 ----
FROM nginx:1.27-alpine AS runtime

# 删掉镜像自带的 default.conf（避免 server_name localhost 干扰）
RUN rm -f /etc/nginx/conf.d/default.conf

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]