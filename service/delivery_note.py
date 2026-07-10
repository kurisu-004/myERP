"""送货单 Excel 模板导出 service。

PR-B（2026-07-10）：文员在 `/delivery-notes/new` 勾选 READY_TO_SHIP 零件，
后端用 `openpyxl.load_workbook(template)` 加载用户提供的模板，按行填字段
后返回 .xlsx 字节流。

约定（见 plan 文件）：
- 模板 sheet 名固定 `送货单`；列结构由用户模板决定（A 列起始固定序号）。
- A1 = 公司抬头（代码不修改）；第 3 行 = 列头；从第 4 行起 = 数据行；
  最后一行 = 签字栏。
- 数据行字段：序号 | 流水号 | 图号 | 名称 | 申请人 | 客户 | 数量 | 计划交期
  | 条码图（Code128 PNG 嵌入）。
- 缺图号 / 缺流水的零件跳过 + 写日志，**不抛错**（部分缺失正常）。
- 模板文件路径由 `core.config.settings.delivery_note_template_path` 控制，
  仓库默认 `docs/example/送货单模板.xlsx`（用户现场提供）。
"""
from __future__ import annotations

import io
import logging
import os
from datetime import date

from openpyxl import load_workbook
from openpyxl.drawing.image import Image as XLImage
from openpyxl.utils import get_column_letter
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.error_code import ErrCode
from core.exception import BizError
from fastapi import status as http_status
from repository.customer import CustomerRepository
from repository.part import PartRepository
from utils.barcode_gen import _make_barcode_png  # 见下文辅助

logger = logging.getLogger(__name__)


class DeliveryNoteService:
    """加载 Excel 模板 → 按 part_ids 填行 → 返回 .xlsx 字节流。"""

    def __init__(self, parts: PartRepository, customers: CustomerRepository) -> None:
        self.parts = parts
        self.customers = customers

    async def build_delivery_note_xlsx(self, part_ids: list[int]) -> bytes:
        """按 part_ids 顺序填模板并返回字节流。

        抛错：
        - 模板文件不存在：BIZ_INVALID_VALUE 400
        - 模板文件后缀不是 .xlsx：BIZ_INVALID_VALUE 400
        - 模板没有 `送货单` sheet：BIZ_INVALID_VALUE 400
        - part_ids 为空：BIZ_INVALID_VALUE 400

        缺图号 / 缺流水号的行跳过（不抛错）+ logger.warning。
        """
        if not part_ids:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message="part_ids 不能为空",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )

        template_path = settings.delivery_note_template_path
        if not os.path.exists(template_path):
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message=(
                    f"送货单模板文件不存在：{template_path}。"
                    f"请用户提供 docs/example/送货单模板.xlsx 或配置"
                    f" DELIVERY_NOTE_TEMPLATE_PATH 环境变量。"
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

        # 1) 批查零件 + 客户路径
        rows = []
        for pid in part_ids:
            part = await self.parts.get_by_id(pid)
            if part is None or part.deleted_at is not None:
                logger.warning("delivery_note: skip missing/deleted part id=%s", pid)
                continue
            if not part.serial_no or not part.drawing_no:
                logger.warning(
                    "delivery_note: skip part id=%s (serial_no=%r drawing_no=%r)",
                    pid, part.serial_no, part.drawing_no,
                )
                continue
            rows.append(part)

        if not rows:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message="所选零件均不可用（缺流水号或图号）",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )

        cust_ids = list({p.customer_id for p in rows})
        cust_list = await self.customers.list_by_ids(cust_ids)
        cust_map = {c.id: c for c in cust_list}
        parent_ids = [c.parent_id for c in cust_list if c.parent_id]
        parents = (
            await self.customers.list_by_ids(parent_ids) if parent_ids else []
        )
        parent_map = {p.id: p for p in parents}

        # 2) 填行（第 4 行起）
        start_row = 4
        for idx, part in enumerate(rows):
            r = start_row + idx
            ws.cell(row=r, column=1, value=idx + 1)  # 序号
            ws.cell(row=r, column=2, value=part.serial_no)  # 流水号
            ws.cell(row=r, column=3, value=part.drawing_no)  # 图号
            ws.cell(row=r, column=4, value=part.name)  # 名称
            ws.cell(row=r, column=5, value=part.applicant_name or "")  # 申请人

            cust = cust_map.get(part.customer_id)
            parent = parent_map.get(cust.parent_id) if cust and cust.parent_id else None
            customer_path = (
                f"{parent.name} / {cust.name}" if parent and cust else
                (cust.name if cust else "")
            )
            ws.cell(row=r, column=6, value=customer_path)  # 客户

            ws.cell(row=r, column=7, value=int(part.quantity))  # 数量
            pdate = part.planned_delivery_date
            ws.cell(row=r, column=8, value=_fmt_date(pdate))  # 计划交期

            # 列 9 = 条码图（嵌入 PNG）
            png_bytes = _make_barcode_png(part.serial_no)
            if png_bytes is not None:
                img = XLImage(io.BytesIO(png_bytes))
                # 自适应单元格宽度（约 80 px 高）
                img.height = 40
                img.width = 120
                anchor_cell = f"{get_column_letter(9)}{r}"
                ws.add_image(img, anchor_cell)

        # 3) 序列化到内存
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()


def _fmt_date(d: date | None) -> str:
    if d is None:
        return ""
    return d.isoformat()