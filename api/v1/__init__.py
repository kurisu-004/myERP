"""2026-09-24 PR-2 重构 + 2026-09-28 删 STS 内部端口：python 端 v1 仅保留
2 个 STS 凭证端口（前端直传 + healthcheck 自检）+ 2 个打印端口（与 IAM 无关）。

2026-10-08：送货单 / 标签 Excel 端口随 backend-rust 送货单打印域重构整体下线
（前端改用 ``hucre`` 读用户上传的 xlsx 模板在浏览器内渲染），本包只剩 STS +
零件打印两组路由。

保留端点（全部裸开鉴权，靠部署层 nginx / 安全组隔离；打印端口另见
``api/v1/printing.py`` 模块 docstring 的「鉴权」段——经 Rust 转发层触达时由
Rust 承担鉴权）：

- ``sts``       — ``POST /files/sts-tmp-keys``（2026-09-17 前端直传 COS 临时
                   凭证；2026-09-28 扩 Union 入参：单文件 / 批量 schema 自由
                   切换）+ ``GET /files/sts-health``（2026-09-18 自检探针）。
- ``printing``  — 2026-09-24 PR-2 新增：零件标签 PDF
                   （``GET /parts/{id}/print`` + ``POST /parts/print-batch``）。

历史 IAM 端点（``/auth/login`` / ``/auth/refresh`` / ``/auth/me`` /
``/auth/change-password`` + ``/users``）已下线，业务由 backend-rust v2 的
``/api/v2/iam/*`` 承接。其它 v1 业务路由（parts / customers / assemblies / ...
共 18 个）整体移至 ``_archive/api_v1/``，业务由 ``/api/v2/*`` 承接。
"""

from fastapi import APIRouter

from . import printing, sts

api_router = APIRouter(prefix="/v1")
api_router.include_router(sts.router)
api_router.include_router(printing.router)
