"""2026-09-24 PR-2 新增：送货单 / 标签 Excel 打印端点。

按 L1 客户的 ``serial_prefix`` 从 ``core.config.settings.delivery_note_template_by_prefix``
选模板（"F"=法拉 / "L"=路达），渲染 XLSX 字节流返回。状态不限
（DRAFT/SUBMITTED/PICKED_UP/ARCHIVED 都可打；``t_part.delivery_note_id`` 已
在 pickup 时保留指向归档单）。

两个端点：
- ``POST /api/v1/delivery-notes/{note_id}/print``         — 送货单 Excel 模板填表
- ``POST /api/v1/delivery-notes/{note_id}/print-labels``  — 标签 Excel（无模板）

鉴权：本端点自身无鉴权（裸开，``api/deps.py::get_delivery_note_print_service``
只注入 DB session、不注入身份）。经 Rust 转发层
（``/api/v2/delivery-notes/{id}/print`` / ``/api/v2/delivery-notes/{id}/print-labels``，
JWT + RBAC）触达时鉴权由 Rust 承担；部署层是否已收敛 nginx ``/api/`` 直连路径
以本仓外配置为准，本仓 ``CLAUDE.md`` §14 仍按「裸开 + nginx 隔离」记录。

2026-10-04：``assembly_ids`` / ``merge_quantities`` 两个键由转发层注入
（此前 handler 把 ``assembly_ids`` 硬编码成 None，装配件合并从未生效）。
本仓只做「str 雪花 id → int」与「JSON 字符串键归一化」的适配，
可出货套数的计算（每装配件子件能否凑齐整套）在 backend-rust 侧。
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
    # 2026-10-04 新增：此前 handler 硬编码 assembly_ids=None ⇒ assembly_map 恒空 ⇒
    # 装配件合并从未生效（前端预览显示「套」，xlsx 里一直是「件」）。现由
    # backend-rust 转发层注入本单涉及的装配件 id（只含能成功解析的）。
    # 2026-10-04：merge_quantities 的值语义 = 该装配件的**可出货套数**（可为 0 ⇒
    # 该装配件整体不出行），同样由 Rust 侧算好后注入；键恒为字符串（JSON 约束）。
    assembly_ids: list[str] | None = Field(default=None, max_length=500)


class PrintLabelsRequest(PrintDeliveryNoteRequest):
    """标签 Excel 请求 body（render_labels 专用，含 line_item_ids 子集）。"""

    line_item_ids: list[str] | None = Field(default=None, max_length=500)


def _parse_assembly_ids(raw: list[str] | None) -> list[int] | None:
    """body 的 ``assembly_ids``（str 列表）→ service 层的 ``list[int]``。

    雪花 ID 入参用 str 承载（CLAUDE.md §3），解析统一走 ``parse_snowflake_id``
    ——非法 id 抛 400 BIZ_INVALID_VALUE，而不是静默丢弃（静默丢弃会退化成
    「不合并」，打印出一张与预览不一致的送货单）。空 / None → None（不合并）。

    ``parse_snowflake_id`` 对空串返回 None，这里顺带剔掉（避免 ``IN (NULL)``
    变成静默全不命中）；剔完为空同样按「不合并」处理。
    """
    if not raw:
        return None
    parsed = [
        i
        for i in (parse_snowflake_id(v, field_name="assembly_ids") for v in raw)
        if i is not None
    ]
    return parsed or None


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
        # 2026-10-04：透传 body.assembly_ids（原先硬编码 None ⇒ 装配件合并死代码）
        assembly_ids=_parse_assembly_ids(body.assembly_ids),
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
        # 2026-10-04：同 /print，透传 body.assembly_ids
        assembly_ids=_parse_assembly_ids(body.assembly_ids),
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
