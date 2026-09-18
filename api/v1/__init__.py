"""2026-09-19 重构：python 端 IAM 域（auth/user/menu）整体迁出至 backend-rust v2。

本仓 v1 仅保留 3 个 STS 端口（与 IAM 无关，2026-09-17/18 新增）：

- `sts`  —— `POST /files/sts-tmp-keys`（前端直传 COS 临时凭证）+
  `POST /files/sts-prefix-credentials`（2026-09-18 内部端口，供 rust 后端
  按任意 `tmp/...` 前缀签凭证）+
  `GET /files/sts-health`（2026-09-18 自检探针，healthcheck 用）。三个 STS
  端点全部裸开鉴权，靠部署层 nginx / 安全组隔离。

历史 IAM 端点（`/auth/login` / `/auth/refresh` / `/auth/me` / `/auth/change-password`
+ `/users`）已下线，业务由 backend-rust v2 的 `/api/v2/iam/*` 承接。
"""

from fastapi import APIRouter

from . import sts

api_router = APIRouter(prefix="/v1")
api_router.include_router(sts.router)
