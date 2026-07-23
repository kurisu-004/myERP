"""送货单 Excel 模板打印 service（2026-07-23 复活 PR-F 的 build_xlsx_by_prefix）。

设计要点（沿用 PR-F 历史决策）：
- 按 L1 客户的序列号前缀（A-Z）从 `core.config.settings.delivery_note_template_by_prefix`
  选对应 xlsx 模板；
- 数据行超过 `cfg.max_rows` → 400 BIZ_DELIVERY_TEMPLATE_TOO_MANY_PARTS
  （保护签字栏不被覆盖；F=R3-R16=14 行 / L=R5-R29=25 行）；
- 任一零件缺 serial_no / drawing_no → 跳过 + logger.warning（不抛错）；
- prefix 未配置 / sheet 名不匹配 → 400 BIZ_DELIVERY_TEMPLATE_NOT_CONFIGURED /
  BIZ_INVALID_VALUE；
- 阻塞 openpyxl IO 用 `asyncio.to_thread` 包裹（避免事件循环阻塞）；
- 状态不限（DRAFT/SUBMITTED/PICKED_UP/ARCHIVED 都可打印；t_part.delivery_note_id
  已在 pickup 时保留指向）。

模板字段含义（service 层不读，但供维护参考）：
- 法拉（`template/delivery_note_fala.xlsx`，Sheet 'Sheet1'）：
  R1 公司抬头（merged A1:J1）；R2 列头；R3-R16 数据（14 行）；
  R17-R21 收货/送货签字栏（merged cells）。
- 路达（`template/delivery_note_luda.xlsx`，Sheet '杏南'）：
  R1 标题（merged A1:I1）；R2 送货日期；R3-R4 双行表头（含 G3:H3 / G4 / H4 合并）；
  R5-R29 数据（25 行）；R30-R31 填写说明。
"""
from __future__ import annotations

import asyncio
import io
import logging
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from fastapi import status as http_status
from openpyxl import load_workbook

from core.config import settings
from core.error_code import ErrCode
from core.exception import BizError
from model.customer import TCustomer
from model.delivery_note import TDeliveryNote
from model.enums import PartStatus
from model.part import TPart
from repository.customer import CustomerRepository
from repository.delivery_note import DeliveryNoteRepository
from repository.part import PartRepository

logger = logging.getLogger(__name__)


# ============================================================
# 列映射 + 模板元信息（按 prefix 编码，与 PR-F 一致）
# ============================================================
@dataclass(frozen=True)
class CellBinding:
    """模板里「一列 → 一个值」的映射声明。"""

    col: int  # 1-based 列号
    source: str  # "row_index" | "part.<field>" | "customer_name" | "parent_name" | "const"
    const_value: Any = None  # source == "const" 时使用


@dataclass(frozen=True)
class TemplateConfig:
    """一份送货单模板的完整布局元信息（per prefix）。"""

    sheet_name: str
    start_row: int  # 数据起始行（1-based）
    max_rows: int  # 数据区最大行数；超出 → BIZ_DELIVERY_TEMPLATE_TOO_MANY_PARTS
    barcode_col: int | None  # 条码列号；None 表示该模板不带条码
    bindings: tuple[CellBinding, ...]


TEMPLATE_CONFIGS: dict[str, TemplateConfig] = {
    # 法拉：Sheet 'Sheet1'，数据 R3-R16（14 行），R17-R21 为签字栏
    "F": TemplateConfig(
        sheet_name="Sheet1",
        start_row=3,
        max_rows=14,
        barcode_col=None,
        bindings=(
            CellBinding(1, "row_index"),
            CellBinding(2, "part.order_no"),
            CellBinding(3, "customer_name"),
            CellBinding(4, "part.applicant_name"),
            CellBinding(5, "part.drawing_no"),
            CellBinding(6, "part.name"),
            CellBinding(7, "part.quantity"),
            CellBinding(8, "const", const_value="件"),
            CellBinding(9, "part.planned_delivery_date"),
            CellBinding(10, "part.note"),
        ),
    ),
    # 路达：Sheet '杏南'，数据 R5-R29（25 行）
    "L": TemplateConfig(
        sheet_name="杏南",
        start_row=5,
        max_rows=25,
        barcode_col=None,
        bindings=(
            CellBinding(1, "row_index"),
            CellBinding(2, "part.order_no"),
            CellBinding(3, "part.applicant_name"),
            CellBinding(4, "part.drawing_no"),
            CellBinding(5, "part.name"),
            CellBinding(6, "part.quantity"),
            CellBinding(7, "const", const_value=""),
            CellBinding(8, "const", const_value=""),
            CellBinding(9, "part.planned_delivery_date"),
        ),
    ),
}


# ============================================================
# 服务层
# ============================================================
class DeliveryNotePrintService:
    """加载 Excel 模板 → 按 note 当前关联的 part 填行 → 返回 .xlsx 字节流 + prefix。"""

    def __init__(
        self,
        notes: DeliveryNoteRepository,
        parts: PartRepository,
        customers: CustomerRepository,
    ) -> None:
        self.notes = notes
        self.parts = parts
        self.customers = customers

    async def render(self, note: TDeliveryNote) -> tuple[bytes, str]:
        """填模板并返回字节流 + 模板 prefix。"""
        # 1) 拉 L1 客户的 serial_prefix（决定模板）
        cust = await self.customers.get_by_id(note.customer_id)
        if cust is None:
            raise BizError(
                code=ErrCode.BIZ_CUSTOMER_NOT_FOUND,
                message=f"customer {note.customer_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        if cust.parent_id is not None:
            # 防御性：理论上 create_draft 已拦；print 时再保险
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message="delivery note customer must be L1 root",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        # 2026-07-23：note.customer_id 是 L1 root；如果 L1 本身没 serial_prefix，
        # 按「前缀即客户」语义无法分发。无 prefix 走 fallback：抛 BIZ_DELIVERY_TEMPLATE_NOT_CONFIGURED。
        prefix: str | None = cust.serial_prefix
        if not prefix:
            raise BizError(
                code=ErrCode.BIZ_DELIVERY_TEMPLATE_NOT_CONFIGURED,
                message=(
                    f"customer {cust.id} ({cust.name}) 缺序列号前缀；"
                    "请先在客户编辑里填 L1 serial_prefix (A-Z)"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        if prefix not in settings.delivery_note_template_by_prefix:
            raise BizError(
                code=ErrCode.BIZ_DELIVERY_TEMPLATE_NOT_CONFIGURED,
                message=(
                    f"未配置前缀 {prefix!r} 的送货单模板；"
                    f"已知前缀: {sorted(settings.delivery_note_template_by_prefix.keys())}"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )

        cfg = TEMPLATE_CONFIGS[prefix]
        template_path = settings.delivery_note_template_by_prefix[prefix]
        try:
            wb = await asyncio.to_thread(load_workbook, template_path)
        except FileNotFoundError as e:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message=f"delivery note template not found: {template_path}",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            ) from e
        try:
            ws = wb[cfg.sheet_name]
        except KeyError as e:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message=(
                    f"sheet {cfg.sheet_name!r} not in template "
                    f"{template_path}"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            ) from e

        # 2) 拉 note 关联的零件（picked_up 后 delivery_note_id 仍指本单，因此也能拉到）
        linked = await self.notes.list_parts(note.id)
        rows: list[TPart] = []
        for p in linked:
            if not p.serial_no or not p.drawing_no:
                logger.warning(
                    "delivery_note_print: skip part id=%s (serial_no=%r drawing_no=%r)",
                    p.id, p.serial_no, p.drawing_no,
                )
                continue
            rows.append(p)
        if not rows:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message="所选零件均不可用（缺流水号或图号）",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        if len(rows) > cfg.max_rows:
            raise BizError(
                code=ErrCode.BIZ_DELIVERY_TEMPLATE_TOO_MANY_PARTS,
                message=(
                    f"本单零件 {len(rows)} 件超过模板 {prefix!r} 的最大行数 "
                    f"{cfg.max_rows}；请分单打印"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )

        # 3) 一次性查每个 part 的 L2 叶子客户 + L1 父（customer_name 用 L2、parent_name 留作备用）
        leaf_ids = list({p.customer_id for p in rows})
        leaf_list = await self.customers.list_by_ids(leaf_ids) if leaf_ids else []
        leaf_map: dict[int, TCustomer] = {c.id: c for c in leaf_list}
        parent_ids = [c.parent_id for c in leaf_list if c.parent_id]
        parent_list = (
            await self.customers.list_by_ids(list(set(parent_ids)))
            if parent_ids else []
        )
        parent_map: dict[int, TCustomer] = {c.id: c for c in parent_list}

        # 4) 填表（开放为同步 IO；openpyxl CPU-bound + 不重 IO，run_in_executor 即可）
        def _fill() -> bytes:
            for idx, p in enumerate(rows, start=1):
                target_row = cfg.start_row + idx - 1
                leaf = leaf_map.get(p.customer_id)
                ctx: dict[str, Any] = {
                    "row_index": idx,
                    "part": p,
                    "customer_name": leaf.name if leaf else "",
                    "parent_name": (
                        parent_map[leaf.parent_id].name
                        if leaf and leaf.parent_id and leaf.parent_id in parent_map
                        else ""
                    ),
                }
                for binding in cfg.bindings:
                    val = _resolve_cell(binding, ctx)
                    if val is not None:
                        ws.cell(row=target_row, column=binding.col, value=val)

            buf = io.BytesIO()
            wb.save(buf)
            return buf.getvalue()

        xlsx_bytes = await asyncio.to_thread(_fill)
        return xlsx_bytes, prefix


# ============================================================
# 内部：CellBinding.source → cell value
# ============================================================
def _resolve_cell(binding: CellBinding, ctx: dict[str, Any]) -> Any:
    """按 binding.source 把 ctx 翻译成 cell value。"""
    src = binding.source
    if src == "row_index":
        return ctx["row_index"]
    if src == "const":
        return binding.const_value
    if src == "customer_name":
        return ctx.get("customer_name")
    if src == "parent_name":
        return ctx.get("parent_name")
    if src.startswith("part."):
        field_name = src.split(".", 1)[1]
        part = ctx.get("part")
        if part is None:
            return None
        val = getattr(part, field_name, None)
        # 日期对象保持原状由 openpyxl 写为 Excel 日期；空值写 None 跳过
        return val
    return None
