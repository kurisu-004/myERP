# myERP — Release 分支

> **本分支只包含 CVM 部署所需文件**。源码、Dockerfile、测试都在 `master` 分支。
> CVM 只需要从这个分支拉取 compose / deploy 脚本，镜像从 TCR 拉。

## 文件清单

| 文件 | 用途 |
|------|------|
| `docker-compose.yml` | 服务编排：postgres + backend + frontend |
| `scripts/deploy.sh` | 一键部署：login → pull → up → 健康检查 |
| `.env.production.example` | 环境变量模板（**不**含真实密钥） |
| `.gitignore` | 忽略本地 `.env.production` |

## CVM 首次部署

```bash
# 1. 克隆本分支
cd /opt
git clone -b release https://github.com/kurisu-004/myERP.git
cd myERP

# 2. 准备生产环境变量
cp .env.production.example .env.production
vim .env.production
#   必填:
#     POSTGRES_PASSWORD    — 强密码
#     JWT_SECRET           — 32+ 字节随机串
#     COS_BUCKET / COS_SECRET_ID / COS_SECRET_KEY
#   可选:
#     FRONTEND_PORT        — 默认 80
#     IMAGE_TAG            — 默认 latest

# 3. 登录 TCR（与本地 push 脚本用同一份固定密码）
docker login ccr.ccs.tencentyun.com

# 4. 启动
./scripts/deploy.sh
```

预期看到：

```
✓ 部署完成
  浏览器访问: http://<CVM-IP>:80/
```

## 日常更新流程

1. **本地** 修改代码、提交到 master
2. **本地** 跑 `./scripts/push-images.sh` 把新镜像推到 TCR
3. **CVM** 跑 `./scripts/deploy.sh` 拉取新镜像并重启

```bash
# CVM
cd /opt/myerp
./scripts/deploy.sh
```

## 镜像来源

`docker-compose.yml` 里：

```yaml
image: ${DOCKER_REPO}/prod-backend:${IMAGE_TAG:-latest}
```

- `DOCKER_REPO` 默认 `ccr.ccs.tencentyun.com/hsh-erp`
- `IMAGE_TAG` 默认 `latest`，可改为具体 SHA（`1c69f1b`）做版本回滚

## 服务端口

- frontend: 宿主机 80 → 容器 80（nginx 静态资源 + `/api/` 反代）
- backend: 容器 8000，**不暴露**给宿主机（仅通过 frontend 内部访问）
- postgres: 容器 5432，**不暴露**给宿主机

## 故障排查

```bash
# 容器状态
docker compose --env-file .env.production ps

# 实时日志
docker logs -f myerp-backend
docker logs -f myerp-frontend
docker logs -f myerp-postgres

# 重置数据库（**会丢数据**）
docker compose --env-file .env.production down
docker volume rm myerp-pgdata
./scripts/deploy.sh
```

## 安全注意

- **`.env.production` 不要 commit**（已在 .gitignore）
- TCR 固定密码不要泄露（与本地 push 脚本用同一份）
- CVM 安全组只开放 22 (SSH) + 80 (HTTP)，443 走 TLS 时再加
