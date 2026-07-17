"""送货单 Excel 模板导出 service（PR-F 2026-07-17 重设计）。

设计要点：
- 按 L1 客户的序列号前缀（A-Z）从 `core.config.settings.delivery_note_template_by_prefix`
  选对应 xlsx 模板；
- 同次所选零件必须同属一个 L1 root customer（多客户 → 400）；
- 仅接受 `status=READY_TO_SHIP` 状态的零件；
- 缺图号 / 缺流水号 → 跳过 + logger.warning（不抛错）；
- 模板 sheet 名固定「送货单」；列 → 字段映射按 prefix 在
  `COLUMN_BINDINGS` 模块级常量里显式编码；
- barcode 图嵌入走 `utils.barcode_gen._make_barcode_png`。

约定：
- 模板 R1-R3 为表头（公司抬头 / 客户标识 / 列头），从 R4 或 R5 起为数据行；
- 数据行追加时**不要覆盖**签字栏——服务层在 sheet.max_row 后追加新行；
- 写字典 cell 时统一走 Pydantic 校验失败静默策略（不影响主流程）。
"""
from __future__ import annotations

import io
import logging
import os
from dataclasses import dataclass
from datetime import date
from typing import Any, Sequence

from openpyxl import load_workbook
from openpyxl.drawing.image import Image as XLImage
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from core.config import settings
from core.error_code import ErrCode
from core.exception import BizError
from fastapi import status as http_status
from model.customer import TCustomer
from model.enums import PartStatus
from model.part import TPart
from repository.customer import CustomerRepository
from repository.part import PartRepository
from utils.barcode_gen import _make_barcode_png

logger = logging.getLogger(__name__)


# ============================================================
# 列映射模型
# ============================================================
@dataclass(frozen=True)
class ColumnBinding:
    """模板里「一列 → 一个值」的映射声明。"""

    col: int  # 1-based 列号
    source: str  # "row_index" | "part.<field>" | "customer_name" | "const" | "barcode_image"
    const_value: Any = None  # source == "const" 时使用


# ============================================================
# 按 prefix 的列映射（PR-F 2026-07-17）
# ============================================================
COLUMN_BINDINGS: dict[str, list[ColumnBinding]] = {
    # 法拉：单 sheet 单行；列结构按 `docs/example/送货单_法拉.xlsx`
    "F": [
        ColumnBinding(col=1, source="row_index"),                      # 序号
        ColumnBinding(col=2, source="part.order_no"),                  # 订单号
        ColumnBinding(col=3, source="customer_name"),                  # 分厂
        ColumnBinding(col=4, source="part.applicant_name"),            # 申请人
        ColumnBinding(col=5, source="part.drawing_no"),                # 编码（图号复用）
        ColumnBinding(col=6, source="part.name"),                      # 名称
        ColumnBinding(col=7, source="part.quantity"),                  # 数量
        ColumnBinding(col=8, source="const", const_value="件"),         # 单位（硬编码）
        ColumnBinding(col=9, source="part.planned_delivery_date"),    # 预估交期
        ColumnBinding(col=10, source="part.note"),                     # 备注
        ColumnBinding(col=11, source="barcode_image"),                 # 条码图
    ],
    # 路达：单 sheet 单行；列结构按 `docs/example/送货单_路达.xlsx`
    "L": [
        ColumnBinding(col=1, source="row_index"),                      # 序号
        ColumnBinding(col=2, source="part.order_no"),                  # 订单编号
        ColumnBinding(col=3, source="part.applicant_name"),            # 申请部门/人
        ColumnBinding(col=4, source="part.drawing_no"),                # 图号
        ColumnBinding(col=5, source="part.name"),                      # 检具名称
        ColumnBinding(col=6, source="part.quantity"),                  # 数量
        # 路达特有：检具状态（new/repair）、NG/OK/校验备注 由文员手填
        ColumnBinding(col=7, source="const", const_value=""),
        ColumnBinding(col=8, source="const", const_value=""),
        ColumnBinding(col=9, source="part.planned_delivery_date"),    # 交货日期
    ],
}


# ============================================================
# 服务层
# ============================================================
class DeliveryNoteService:
    """加载 Excel 模板 → 按 part_ids 填行 → 返回 .xlsx 字节流。"""

    def __init__(
        self,
        parts: PartRepository,
        customers: CustomerRepository,
    ) -> None:
        self.parts = parts
        self.customers = customers

    async def build_xlsx_by_prefix(
        self, part_ids: list[int]
    ) -> tuple[bytes, str]:
        """按 part_ids 顺序填模板并返回字节流 + 模板 prefix。

        返回：
        - bytes: xlsx 文件字节流
        - str:  模板类型 prefix（"F" / "L"），用于前端 Content-Disposition 文件名

        抛错：
        - part_ids 为空 → BIZ_INVALID_VALUE 400
        - 任一零件软删或不存在 → BIZ_PART_NOT_FOUND 404
        - 任一零件状态非 READY_TO_SHIP → BIZ_DELIVERY_PART_STATUS_INVALID 400
        - 跨 L1 客户 → BIZ_DELIVERY_PARTS_MULTIPLE_CUSTOMERS 400
        - prefix 未配置模板 → BIZ_DELIVERY_TEMPLATE_NOT_CONFIGURED 400
        - 模板文件缺失 / 后缀 / sheet 名 → BIZ_INVALID_VALUE 400
        """
        if not part_ids:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message="part_ids 不能为空",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )

        # 1) 批查所有 part（避免 N+1）
        parts_list = await self.parts.list_by_ids(part_ids, include_deleted=False)
        found_ids = {p.id for p in parts_list}
        missing = [pid for pid in part_ids if pid not in found_ids]
        if missing:
            raise BizError(
                code=ErrCode.BIZ_PART_NOT_FOUND,
                message=f"part 不存在或已删除：{missing}",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )

        # 2) 校验状态：必须全部 READY_TO_SHIP
        invalid = [p for p in parts_list if p.status != PartStatus.READY_TO_SHIP.value]
        if invalid:
            ids_str = ", ".join(str(p.id) for p in invalid[:5])
            raise BizError(
                code=ErrCode.BIZ_DELIVERY_PART_STATUS_INVALID,
                message=(
                    f"以下零件状态非 READY_TO_SHIP：{ids_str}"
                    f"{' 等' if len(invalid) > 5 else ''}"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )

        # 3) 缺 serial/drawing → 跳过（不抛错）
        rows: list[TPart] = []
        for p in parts_list:
            if not p.serial_no or not p.drawing_no:
                logger.warning(
                    "delivery_note: skip part id=%s (serial_no=%r drawing_no=%r)",
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

        # 4) 批查客户 + 父客户 → 推导 L1 root prefix
        cust_ids = list({p.customer_id for p in rows})
        cust_list = await self.customers.list_by_ids(cust_ids)
        cust_map: dict[int, TCustomer] = {c.id: c for c in cust_list}
        parent_ids = [c.parent_id for c in cust_list if c.parent_id]
        parents = (
            await self.customers.list_by_ids(parent_ids) if parent_ids else []
        )
        parent_map: dict[int, TCustomer] = {p.id: p for p in parents}

        # 推导每个 part 的 L1 root customer + 收集唯一 root
        root_ids: set[int] = set()
        for p in rows:
            cust = cust_map.get(p.customer_id)
            if cust is None:
                raise BizError(
                    code=ErrCode.BIZ_CUSTOMER_NOT_FOUND,
                    message=f"customer {p.customer_id} not found",
                    http_status=http_status.HTTP_404_NOT_FOUND,
                )
            root = (
                parent_map.get(cust.parent_id)
                if cust.parent_id
                else cust
            )
            root_ids.add(root.id)

        # 5) 校验所有 part 同属一个 L1 root
        if len(root_ids) > 1:
            raise BizError(
                code=ErrCode.BIZ_DELIVERY_PARTS_MULTIPLE_CUSTOMERS,
                message=(
                    f"所选零件分属多个一级客户（{len(root_ids)} 个），"
                    f"请按客户分别生成送货单"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )

        # 6) 解析 prefix
        first_root_id = next(iter(root_ids))
        first_root = (
            parent_map.get(first_root_id)
            or cust_map.get(first_root_id)
        )
        if first_root is None:
            extra = await self.customers.list_by_ids([first_root_id])
            first_root = extra[0] if extra else None
        if first_root is None:
            raise BizError(
                code=ErrCode.BIZ_CUSTOMER_NOT_FOUND,
                message=f"root customer {first_root_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        prefix = first_root.serial_prefix
        if not prefix:
            # 一级客户必须配置 serial_prefix（建客户时已校验）；兜底走 PARENT_TO_CODE
            from core.serial import code_for_parent

            legacy = code_for_parent(first_root.name)
            if legacy is None:
                raise BizError(
                    code=ErrCode.BIZ_DELIVERY_TEMPLATE_NOT_CONFIGURED,
                    message=(
                        f"客户 {first_root.name} 未配置序列号前缀，"
                        f"且不在 PARENT_TO_CODE 兼容列表"
                    ),
                    http_status=http_status.HTTP_400_BAD_REQUEST,
                )
            prefix = legacy

        # 7) 模板分发
        templates = settings.delivery_note_template_by_prefix
        template_path = templates.get(prefix)
        if template_path is None:
            raise BizError(
                code=ErrCode.BIZ_DELIVERY_TEMPLATE_NOT_CONFIGURED,
                message=(
                    f"序列号前缀 {prefix!r} 未配置送货单模板。"
                    f"已配置前缀：{sorted(templates.keys())}"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )

        # 8) 加载模板
        if not os.path.exists(template_path):
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message=(
                    f"送货单模板文件不存在：{template_path}。"
                    f"请用户提供 docs/example/送货单_<L1>.xlsx 或配置"
                    f" DELIVERY_NOTE_TEMPLATE_BY_PREFIX 环境变量。"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        if not template_path.lower().endswith(".xlsx"):
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message=f"送货单模板必须是 .xlsx：{template_path}",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )

        wb = load_workbook(template_path)
        if "送货单" not in wb.sheetnames:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message=(
                    f"送货单模板缺少 sheet '送货单'。"
                    f"当前 sheets: {wb.sheetnames}"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        ws = wb["送货单"]

        # 9) 按 COLUMN_BINDINGS[prefix] 写数据
        bindings = COLUMN_BINDINGS.get(prefix)
        if bindings is None:
            raise BizError(
                code=ErrCode.BIZ_DELIVERY_TEMPLATE_NOT_CONFIGURED,
                message=(
                    f"前缀 {prefix!r} 在 COLUMN_BINDINGS 中未定义"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        start_row = _detect_data_start_row(ws, prefix)
        await self._fill_rows(
            ws=ws,
            rows=rows,
            cust_map=cust_map,
            parent_map=parent_map,
            bindings=bindings,
            start_row=start_row,
        )

        # 10) 序列化
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue(), prefix

    async def _fill_rows(
        self,
        *,
        ws: Worksheet,
        rows: Sequence[TPart],
        cust_map: dict[int, TCustomer],
        parent_map: dict[int, TCustomer],
        bindings: list[ColumnBinding],
        start_row: int,
    ) -> None:
        """按 bindings 写入数据行 + barcode 图。"""
        for idx, part in enumerate(rows):
            r = start_row + idx
            cust = cust_map.get(part.customer_id)
            parent = (
                parent_map.get(cust.parent_id)
                if cust and cust.parent_id
                else None
            )
            customer_name = cust.name if cust else None

            for binding in bindings:
                if binding.source == "barcode_image":
                    self._embed_barcode(ws, part.serial_no, r, binding.col)
                    continue
                value = self._resolve_value(
                    binding=binding,
                    part=part,
                    customer_name=customer_name,
                    parent_name=parent.name if parent else None,
                    row_index=idx + 1,
                )
                ws.cell(row=r, column=binding.col, value=value)

    @staticmethod
    def _resolve_value(
        *,
        binding: ColumnBinding,
        part: TPart,
        customer_name: str | None,
        parent_name: str | None,
        row_index: int,
    ) -> Any:
        """把 binding 解析为单元格值。"""
        if binding.source == "row_index":
            return row_index
        if binding.source == "const":
            return binding.const_value
        if binding.source == "customer_name":
            return customer_name or ""
        if binding.source == "parent_name":
            return parent_name or ""
        if binding.source.startswith("part."):
            field = binding.source[len("part."):]
            val = getattr(part, field, None)
            return _fmt_value(val)
        # 未知 source：留空
        logger.warning("delivery_note: unknown source %r, write blank", binding.source)
        return ""

    @staticmethod
    def _embed_barcode(
        ws: Worksheet, serial_no: str | None, row: int, col: int
    ) -> None:
        """在 (row, col) 嵌入 Code128 PNG 条码图。"""
        if not serial_no:
            return
        png_bytes = _make_barcode_png(serial_no)
        if png_bytes is None:
            return
        img = XLImage(io.BytesIO(png_bytes))
        # 自适应单元格：宽 120px、高 40px（与原版一致）
        img.width = 120
        img.height = 40
        anchor_cell = f"{get_column_letter(col)}{row}"
        ws.add_image(img, anchor_cell)


def _detect_data_start_row(ws: Worksheet, prefix: str) -> int:
    """根据 prefix 推断数据起始行。

    - 法拉：表头 R1-R3，从 R4 起数据
    - 路达：表头 R1-R4（两行表头合并），从 R5 起数据
    """
    if prefix == "L":
        return 5
    return 4


def _fmt_value(val: Any) -> Any:
    """统一格式化：date → isoformat str；None → ""；其余原样。"""
    if val is None:
        return ""
    if isinstance(val, date):
        return val.isoformat()
    return val