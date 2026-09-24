"""2026-09-24 PR-2 新增：零件标签 PDF 打印端点（双面 PDF：图纸正面 + 条码背面）。

两个端点：
- ``GET  /api/v1/parts/{part_id}/print``       — 单件
- ``POST /api/v1/parts/print-batch``          — 批量（按 body.part_ids 顺序拼接，
  可选 ``assembly_ids`` 自动追加总装图页 + 全部子件）

鉴权：裸开（参考 ``/api/v1/files/sts-*`` / ``/api/mcp/*`` 时代的裸开约定），
安全性靠部署层 nginx ``/api/v1/*`` 不暴露 / 安全组隔离保证。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from api.deps import get_printing_service
from service._id_parse import parse_snowflake_id
from service.printing import PrintingServiceFacade

router = APIRouter(prefix="/parts", tags=["printing"])


class PrintBatchRequest(BaseModel):
    """批量打印请求 body。"""

    part_ids: list[str] = Field(..., min_length=1, max_length=200)
    assembly_ids: list[str] | None = Field(default=None, max_length=50)
    vector: bool = Field(
        default=False,
        description="true=光栅旁路（个别图纸光栅化不清晰时用）",
    )


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
    summary="批量零件标签 PDF（合并多件为单 PDF）",
    responses={
        200: {"content": {"application/pdf": {}}, "description": "PDF 二进制流"},
    },
)
async def print_parts_batch_pdf(
    body: PrintBatchRequest,
    svc: PrintingServiceFacade = Depends(get_printing_service),  # noqa: B008
) -> Response:
    """返回多件零件的双面 PDF 合并流（顺序按 body.part_ids）。"""
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
