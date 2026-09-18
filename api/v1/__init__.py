"""2026-09-17 重构：v1 业务路由下线 + JWT bypass 后，api/v1 只保留 4 + 3 共 7 个端点：

- `auth`  —— `POST /auth/login` + `POST /auth/refresh`（双 token 签发 / 轮转），
  `GET /auth/me`（当前账号，bypass 后固定 default user），
  `POST /auth/change-password`（`UserService.change_own_password`）；
  `AuthService` 仍走 `core.security.decode_*_token`，登录 / 刷新端点本身
  不调用 `get_current_user`，不受 bypass 影响。
- `sts`   —— `POST /files/sts-tmp-keys`（前端直传 COS 临时凭证）+
  `POST /files/sts-prefix-credentials`（2026-09-18 内部端口，供 rust 后端
  按任意 `tmp/...` 前缀签凭证）+
  `GET /files/sts-health`（2026-09-18 自检探针，healthcheck 用）。三个 STS
  端点全部裸开鉴权，靠部署层 nginx / 安全组隔离。

其它 18 个 v1 业务 router（applicant/assembly/cnc_program/customer/
delivery_note/drawing/outsource_company/outsource_quote/outsource_shipment/
part/process/shelf/statistics/user/work_type/worker/ws）已整体移至
`_archive/api_v1/`，业务由 backend-rust v2 承接；待 reviewer 验证后再决定
是否 git rm。
"""

from fastapi import APIRouter

from . import auth, sts

api_router = APIRouter(prefix="/v1")
api_router.include_router(auth.router)
api_router.include_router(sts.router)
