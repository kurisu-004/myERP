"""2026-09-24 PR-2 新增：零件标签 PDF 打印端点（双面 PDF：图纸正面 + 条码背面）。

两个端点：
- ``GET  /api/v1/parts/{part_id}/print``       — 单件
- ``POST /api/v1/parts/print-batch``          — 批量（``part_ids`` 按序逐件
  拼接，``assembly_ids`` 自动追加总装图页 + 全部子件；两者至少一个非空）

鉴权：本端点自身无鉴权（裸开，``api/deps.py::get_printing_service`` 只注入
DB session、不注入身份）。经 Rust 转发层（``/api/v2/parts/{part_id}/print-drawing`` /
``/api/v2/parts/print-drawing-batch``，JWT + RBAC）触达时鉴权由 Rust 承担；
部署层是否已收敛 nginx ``/api/`` 直连路径以本仓外配置为准，本仓
``CLAUDE.md`` §14 仍按「裸开 + nginx 隔离」记录。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field, model_validator

from api.deps import get_printing_service
from service._id_parse import parse_snowflake_id
from service.printing import PrintingServiceFacade

router = APIRouter(prefix="/parts", tags=["printing"])


class PrintBatchRequest(BaseModel):
    """批量打印请求 body。

    2026-10-03 新增：``part_ids`` 允许空列表（前端对「只勾选装配体」的批次发
    ``part_ids=[]`` + 非空 ``assembly_ids``，而服务层本就按 ``assembly_ids``
    拉总装图 + 全部子件），「至少一类目标非空」改由本模型的
    ``model_validator`` 收口。
    """

    part_ids: list[str] = Field(default_factory=list, max_length=200)
    assembly_ids: list[str] | None = Field(default=None, max_length=50)
    vector: bool = Field(
        default=False,
        description="true=光栅旁路（个别图纸光栅化不清晰时用）",
    )

    @model_validator(mode="after")
    def _require_one_non_empty_target(self) -> "PrintBatchRequest":
        """两类目标至少一类非空。

        pydantic 把 ``ValueError`` 包成校验错误，FastAPI 转
        ``RequestValidationError`` → 本仓统一 422 + ``VALIDATION_ERROR`` +
        errors 数组（与其它校验失败同形，不另造错误响应结构）。
        """
        if not self.part_ids and not self.assembly_ids:
            raise ValueError("part_ids 与 assembly_ids 至少提供一个非空列表")
        return self


@router.get(
    "/{part_id}/print",
    operation_id="print_part_pdf",
    summary="单件零件标签 PDF（图纸正面 + Code128 条码背面）",
    responses={
        200: {"content": {"application/pdf": {}}, "description": "PDF 二进制流"},
        404: {"description": "零件不存在"},
    },
)
async def print_part_pdf(
    part_id: Annotated[str, Path(description="雪花 ID 字符串")],
    vector: Annotated[
        bool,
        Query(description="光栅旁路（个别图纸光栅化不清晰时用）"),
    ] = False,
    svc: PrintingServiceFacade = Depends(get_printing_service),  # noqa: B008
) -> Response:
    """返回单件零件的双面 PDF（图纸 / 图片正面 + 条码背面）。"""
    pid = parse_snowflake_id(part_id, field_name="part_id")
    pdf_bytes = await svc.build_part_print_pdf(part_id=pid, vector=vector)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="part-{part_id}.pdf"',
            "Cache-Control": "private, max-age=600",
        },
    )


@router.post(
    "/print-batch",
    operation_id="print_parts_batch_pdf",
    summary="批量零件 + 装配体标签 PDF（合并多件为单 PDF）",
    responses={
        200: {"content": {"application/pdf": {}}, "description": "PDF 二进制流"},
    },
)
async def print_parts_batch_pdf(
    body: PrintBatchRequest,
    svc: PrintingServiceFacade = Depends(get_printing_service),  # noqa: B008
) -> Response:
    """返回多件零件 / 装配体的双面 PDF 合并流（顺序按 body.part_ids，其后
    追加 body.assembly_ids 的总装图页 + 全部子件）。"""
    pids = [parse_snowflake_id(p, field_name="part_ids[]") for p in body.part_ids]
    aids = (
        [parse_snowflake_id(a, field_name="assembly_ids[]") for a in body.assembly_ids]
        if body.assembly_ids
        else []
    )
    pdf_bytes = await svc.build_parts_print_pdf_batch(
        part_ids=pids,
        assembly_ids=aids or None,
        vector=body.vector,
    )
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": 'inline; filename="parts-batch.pdf"',
            "Cache-Control": "private, max-age=600",
        },
    )
