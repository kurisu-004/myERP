"""2026-09-24 PR-2 新增：送货单 / 标签 Excel 打印端点。

按 L1 客户的 ``serial_prefix`` 从 ``core.config.settings.delivery_note_template_by_prefix``
选模板（"F"=法拉 / "L"=路达），渲染 XLSX 字节流返回。状态不限
（DRAFT/SUBMITTED/PICKED_UP/ARCHIVED 都可打；``t_part.delivery_note_id`` 已
在 pickup 时保留指向归档单）。

两个端点：
- ``POST /api/v1/delivery-notes/{note_id}/print``         — 送货单 Excel 模板填表
- ``POST /api/v1/delivery-notes/{note_id}/print-labels``  — 标签 Excel（无模板）

鉴权：裸开（参考 ``/api/v1/files/sts-*`` / ``/api/v1/parts/print``）。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path
from fastapi.responses import Response
from pydantic import BaseModel, Field

from api.deps import get_delivery_note_print_service
from core.error_code import ErrCode
from core.exception import BizError
from service._id_parse import parse_snowflake_id
from service.delivery_note_print import DeliveryNotePrintService

router = APIRouter(prefix="/delivery-notes", tags=["delivery-note-print"])


class PrintDeliveryNoteRequest(BaseModel):
    """送货单打印请求 body（除 note_id 外全部可选，default 走 v1 行为）。"""

    custom_order: list[str] | None = Field(default=None, max_length=500)
    merge_assemblies: bool = Field(default=False)
    merge_quantities: dict[str, int] | None = Field(default=None)
    # 2026-09-24 PR-2：原 service assembly_map: dict[int, TAssembly] 改为
    # assembly_ids: list[str]，由 service 内部用
    # ``AssemblyRepository.list_by_ids`` 组装（API 层不应传 ORM 对象）。


class PrintLabelsRequest(PrintDeliveryNoteRequest):
    """标签 Excel 请求 body（render_labels 专用，含 line_item_ids 子集）。"""

    line_item_ids: list[str] | None = Field(default=None, max_length=500)


async def _load_note_or_404(
    svc: DeliveryNotePrintService,
    note_id: int,
):
    """2026-09-24 PR-2 新增：note 不存在 → 404 + ``BIZ_DELIVERY_NOTE_NOT_FOUND``。"""
    note = await svc.notes.get_by_id(note_id)
    if note is None:
        raise BizError(
            code=ErrCode.BIZ_DELIVERY_NOTE_NOT_FOUND,
            message=f"delivery note {note_id} not found",
            http_status=404,
        )
    return note


@router.post(
    "/{note_id}/print",
    operation_id="print_delivery_note_xlsx",
    summary="送货单 Excel 模板填表（F/L 模板按客户前缀分发）",
    responses={
        200: {
            "content": {
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {}
            },
        },
        404: {"description": "送货单不存在"},
    },
)
async def print_delivery_note(
    note_id: Annotated[str, Path(description="雪花 ID 字符串")],
    body: PrintDeliveryNoteRequest = PrintDeliveryNoteRequest(),  # noqa: B008
    svc: DeliveryNotePrintService = Depends(get_delivery_note_print_service),  # noqa: B008
) -> Response:
    """按 L1 客户前缀选模板，填表后返回 XLSX 字节流。"""
    nid = parse_snowflake_id(note_id, field_name="note_id")
    note = await _load_note_or_404(svc, nid)
    xlsx_bytes, prefix = await svc.render(
        note=note,
        custom_order=body.custom_order,
        merge_assemblies=body.merge_assemblies,
        assembly_ids=None,  # 2026-09-24 PR-2：API 层不传 ORM，由 service 内部组装
        merge_quantities=body.merge_quantities,
    )
    fname = f"delivery-note-{prefix}{note.delivery_note_no}.xlsx"
    return Response(
        content=xlsx_bytes,
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={"Content-Disposition": f'inline; filename="{fname}"'},
    )


@router.post(
    "/{note_id}/print-labels",
    operation_id="print_delivery_note_labels_xlsx",
    summary="标签 Excel（无模板，自建表头）",
    responses={
        200: {
            "content": {
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {}
            },
        },
        404: {"description": "送货单不存在"},
    },
)
async def print_delivery_note_labels(
    note_id: Annotated[str, Path(description="雪花 ID 字符串")],
    body: PrintLabelsRequest = PrintLabelsRequest(),  # noqa: B008
    svc: DeliveryNotePrintService = Depends(get_delivery_note_print_service),  # noqa: B008
) -> Response:
    """无模板的标签 Excel（表头：客户 / 申请人 / 名称 / 图号 / 数量 / 单位）。"""
    nid = parse_snowflake_id(note_id, field_name="note_id")
    note = await _load_note_or_404(svc, nid)
    xlsx_bytes, prefix = await svc.render_labels(
        note=note,
        custom_order=body.custom_order,
        merge_assemblies=body.merge_assemblies,
        assembly_ids=None,  # 2026-09-24 PR-2：API 层不传 ORM
        merge_quantities=body.merge_quantities,
        line_item_ids=body.line_item_ids,
    )
    fname = f"labels-{prefix}{note.delivery_note_no}.xlsx"
    return Response(
        content=xlsx_bytes,
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={"Content-Disposition": f'inline; filename="{fname}"'},
    )
