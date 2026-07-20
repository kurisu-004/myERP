"""送货单 Excel 模板导出 service（PR-F 2026-07-17 重设计；2026-07-20 切到 template/ 新模板）。

设计要点：
- 模板按 L1 客户的序列号前缀（A-Z）从 `core.config.settings.delivery_note_template_by_prefix`
  选对应 xlsx 模板；同次所选零件必须同属一个 L1 root customer（多客户 → 400）；
- API 调用方也可通过 `template: "F" | "L"` 显式指定模板（覆盖自动分发）；
- 仅接受 `status=READY_TO_SHIP` 状态的零件；
- 缺图号 / 缺流水号 → 跳过 + logger.warning（不抛错）；
- 模板布局元信息（sheet 名 / 数据起始行 / 最大行数 / 列映射）合并在
  `TEMPLATE_CONFIGS: dict[str, TemplateConfig]` 中按 prefix 显式编码；
- 数据行数超出 `cfg.max_rows` → 400 BIZ_DELIVERY_TEMPLATE_TOO_MANY_PARTS
  （保护签字栏不被覆盖；新法模板 R3-R16=14 行，新路 R5-R29=25 行）；
- 新模板均无 barcode_col；如未来要重新打开条码，把 cfg `barcode_col` 改回 int 即可。

模板字段含义（service 层不读，但供维护参考）：
- 法拉（`template/delivery_note_fala.xlsx`，Sheet 'Sheet1'）：
  R1 公司抬头（merged A1:J1）；R2 列头；R3-R16 数据（14 行）；
  R17-R21 收货/送货签字栏（merged cells）。
- 路达（`template/delivery_note_luda.xlsx`，Sheet '杏南'）：
  R1 标题（merged A1:I1）；R2 送货日期；R3-R4 双行表头（含 G3:H3 / G4 / H4 合并）；
  R5-R29 数据（25 行）；R30-R31 填写说明。I2=46218 是用户上次编辑模板时
  留下的日期序列号，V1 不主动刷新。
"""
from __future__ import annotations

import io
import logging
import os
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, Sequence

from openpyxl import load_workbook
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

logger = logging.getLogger(__name__)


# ============================================================
# 列映射 + 模板元信息（按 prefix 编码）
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

    sheet_name: str  # 模板里目标 sheet 的名字
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
        barcode_col=None,  # 新法模板只到 J 列，没有条码槽
        bindings=(
            CellBinding(1, "row_index"),                  # 序号
            CellBinding(2, "part.order_no"),              # 订单号
            CellBinding(3, "customer_name"),              # 分厂
            CellBinding(4, "part.applicant_name"),        # 申请人
            CellBinding(5, "part.drawing_no"),            # 编码（图号）
            CellBinding(6, "part.name"),                  # 名称
            CellBinding(7, "part.quantity"),              # 数量
            CellBinding(8, "const", const_value="件"),    # 单位
            CellBinding(9, "part.planned_delivery_date"), # 预估交期
            CellBinding(10, "part.note"),                 # 备注
        ),
    ),
    # 路达：Sheet '杏南'，数据 R5-R29（25 行），R3-R4 双行表头（含合并）
    "L": TemplateConfig(
        sheet_name="杏南",
        start_row=5,
        max_rows=25,
        barcode_col=None,
        bindings=(
            CellBinding(1, "row_index"),
            CellBinding(2, "part.order_no"),              # 订单编号
            CellBinding(3, "part.applicant_name"),        # 申请部门/人
            CellBinding(4, "part.drawing_no"),            # 图号
            CellBinding(5, "part.name"),                  # 检具名称
            CellBinding(6, "part.quantity"),
            # 检具状态 / NG-OK-校验备注 由文员手填，留空
            CellBinding(7, "const", const_value=""),
            CellBinding(8, "const", const_value=""),
            CellBinding(9, "part.planned_delivery_date"), # 交货日期
        ),
    ),
}


# ============================================================
# 服务层
# ============================================================
class DeliveryNoteService:
    """加载 Excel 模板 → 按 part_ids 填行 → 返回 .xlsx 字节流 + prefix。"""

    def __init__(
        self,
        parts: PartRepository,
        customers: CustomerRepository,
    ) -> None:
        self.parts = parts
        self.customers = customers

    async def build_xlsx(
        self,
        part_ids: list[int],
        *,
        template: Literal["F", "L"] | None = None,
    ) -> tuple[bytes, str]:
        """按 part_ids 顺序填模板并返回字节流 + 模板 prefix。

        Args:
            part_ids: 雪花 ID int 列表（API 层已 parse_snowflake_id 转换）。
            template: 显式指定模板 prefix（F=法拉 / L=路达）；None=按客户前缀自动分发。

        Returns:
            (bytes: xlsx 文件字节流, str: 模板类型 prefix "F" / "L")

        Raises:
            BizError:
              - BIZ_INVALID_VALUE 400：part_ids 空 / 模板文件缺失 / 后缀 / sheet 不匹配
              - BIZ_PART_NOT_FOUND 404：任一零件软删或不存在
              - BIZ_DELIVERY_PART_STATUS_INVALID 400：任一零件状态非 READY_TO_SHIP
              - BIZ_DELIVERY_PARTS_MULTIPLE_CUSTOMERS 400：跨 L1 客户
              - BIZ_DELIVERY_TEMPLATE_NOT_CONFIGURED 400：prefix 未配置 / 显式
                template 与零件所属 L1 root 不匹配
              - BIZ_DELIVERY_TEMPLATE_TOO_MANY_PARTS 400：零件数超过模板数据行容量
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

        # 6) 解析 prefix（root 必为 cust_map 或 parent_map 中的一员，否则再查一次）
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

        # 7) 显式 template 与自动分发结果必须一致（防呆）
        if template is not None and template != prefix:
            raise BizError(
                code=ErrCode.BIZ_DELIVERY_TEMPLATE_NOT_CONFIGURED,
                message=(
                    f"显式指定的模板「{template}」与所选零件所属一级客户"
                    f"（{first_root.name}，前缀 {prefix!r}）不匹配。"
                    f"已配置模板前缀："
                    f"{sorted(settings.delivery_note_template_by_prefix.keys())}"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )

        # 8) 查 cfg：TEMPLATE_CONFIGS 是模板分发的唯一来源
        cfg = TEMPLATE_CONFIGS.get(prefix)
        if cfg is None:
            raise BizError(
                code=ErrCode.BIZ_DELIVERY_TEMPLATE_NOT_CONFIGURED,
                message=(
                    f"序列号前缀 {prefix!r} 未配置送货单模板。"
                    f"已配置前缀：{sorted(TEMPLATE_CONFIGS.keys())}"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )

        # 9) 模板文件存在 / 后缀
        templates = settings.delivery_note_template_by_prefix
        template_path = templates.get(prefix)
        if template_path is None:
            raise BizError(
                code=ErrCode.BIZ_DELIVERY_TEMPLATE_NOT_CONFIGURED,
                message=(
                    f"序列号前缀 {prefix!r} 在"
                    f" settings.delivery_note_template_by_prefix 中未配置路径。"
                    f"已配置前缀：{sorted(templates.keys())}"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        if not os.path.exists(template_path):
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message=(
                    f"送货单模板文件不存在：{template_path}。"
                    f"请提供 template/delivery_note_<L1>.xlsx 或配置"
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

        # 10) 装载 + sheet 名校验（按 cfg.sheet_name，不再硬编码 "送货单"）
        wb = load_workbook(template_path)
        if cfg.sheet_name not in wb.sheetnames:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message=(
                    f"送货单模板 {template_path!r} 缺少 sheet"
                    f" {cfg.sheet_name!r}。当前 sheets: {wb.sheetnames}"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        ws = wb[cfg.sheet_name]

        # 11) 行数溢出保护：超 max_rows → 400，不动签字栏
        if len(rows) > cfg.max_rows:
            raise BizError(
                code=ErrCode.BIZ_DELIVERY_TEMPLATE_TOO_MANY_PARTS,
                message=(
                    f"模板「{prefix}」单页最多容纳 {cfg.max_rows} 件零件，"
                    f"当前选了 {len(rows)} 件，请分批生成。"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )

        # 12) 填行
        await self._fill_rows(
            ws=ws,
            rows=rows,
            cust_map=cust_map,
            parent_map=parent_map,
            bindings=cfg.bindings,
            start_row=cfg.start_row,
        )

        # 13) 序列化
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue(), prefix

    # --------------------------------------------------------------
    # 旧 API 别名（保留 build_xlsx_by_prefix 入口 → 转发到 build_xlsx）
    # --------------------------------------------------------------
    async def build_xlsx_by_prefix(
        self, part_ids: list[int]
    ) -> tuple[bytes, str]:
        """旧 API 别名；等同于 `build_xlsx(part_ids)`（按客户前缀自动分发）。"""
        return await self.build_xlsx(part_ids, template=None)

    async def _fill_rows(
        self,
        *,
        ws: Worksheet,
        rows: Sequence[TPart],
        cust_map: dict[int, TCustomer],
        parent_map: dict[int, TCustomer],
        bindings: Sequence[CellBinding],
        start_row: int,
    ) -> None:
        """按 bindings 写入数据行。"""
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
        binding: CellBinding,
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


def _fmt_value(val: Any) -> Any:
    """统一格式化：date → isoformat str；None → ""；其余原样。"""
    if val is None:
        return ""
    if isinstance(val, date):
        return val.isoformat()
    return val