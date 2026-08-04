"""送货单 Excel 模板打印 service（2026-07-23 复活 PR-F 的 build_xlsx_by_prefix）。

设计要点（沿用 PR-F 历史决策）：
- 按 L1 客户的序列号前缀（A-Z）从 `core.config.settings.delivery_note_template_by_prefix`
  选对应 xlsx 模板；
- 数据行超过 `cfg.max_rows` → 自动分页：复制模板 sheet，续到下一份/下一页
  （2026-07-24 起；此前法拉超过 14 行会抛 BIZ_DELIVERY_TEMPLATE_TOO_MANY_PARTS）；
- 任一零件缺 serial_no / drawing_no → 跳过 + logger.warning（不抛错）；
- prefix 未配置 / sheet 名不匹配 → 400 BIZ_DELIVERY_TEMPLATE_NOT_CONFIGURED /
  BIZ_INVALID_VALUE；
- 阻塞 openpyxl IO 用 `asyncio.to_thread` 包裹（避免事件循环阻塞）；
- 状态不限（DRAFT/SUBMITTED/PICKED_UP/ARCHIVED 都可打印；t_part.delivery_note_id
  已在 pickup 时保留指向）。

2026-08-04 装配件合并打印：
- ``render(merge_assemblies=True)`` 时，把同一装配体下的子件在送货单上合并为一行
  （数量 1，单位套，显示总装图号/装配体序列号/装配体名称）；散件行保持不变。
- 重构行解析为统一 ``PrintRow`` DTO（覆盖两套模板 bindings 全部列），散件与装配体
  行用同一套 `_fill_row` / 分页逻辑；法拉模板 col 8 改按行取 ``PrintRow.unit``
  （散件「件」/装配体「套」）。

模板字段含义（service 层不读，但供维护参考）：
- 法拉（`template/delivery_note_fala.xlsx`，Sheet 'Sheet1'）：
  2026-07-24 换新模板（洪升宏 26.7.24），单份最多 10 行。
  R1 公司抬头（merged A1:J1）；R2 列头；R3-R12 数据（10 行）；
  R13-R17 收货/送货签字栏（merged cells）；R17 合并 A17:J17 是「送货日期：YYYY年M月D日」
  文案，由 `_write_footer` 覆盖。超 10 行按每页 10 行复制 sheet 续打。
- 路达（`template/delivery_note_luda.xlsx`，Sheet '杏南'）：
  R1 标题（merged A1:I1）；R2 送货日期（H2='送货日期' + I2=日期）；R3-R4 双行表头
  （含 G3:H3 / G4 / H4 合并）；R5-R29 数据（25 行）；R30-R31 填写说明。
  R2 由 `_write_footer` 写日期（2026-07-23）。
"""
from __future__ import annotations

import asyncio
import io
import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

from fastapi import status as http_status
from openpyxl import load_workbook

from core.config import settings
from core.error_code import ErrCode
from core.exception import BizError
from core.time import now_naive
from model.assembly import TAssembly
from model.customer import TCustomer
from model.delivery_note import TDeliveryNote
from model.part import TPart
from model.part_batch import TPartBatch
from repository.customer import CustomerRepository
from repository.delivery_note import DeliveryNoteRepository
from repository.part import PartRepository
from repository.part_batch import PartBatchRepository

logger = logging.getLogger(__name__)


# ============================================================
# 行 DTO（散件 / 装配体共用；2026-08-04 重构引入）
# ============================================================
@dataclass(frozen=True)
class PrintRow:
    """送货单上「一行」的统一数据载体（散件行 + 装配体合并行复用）。

    字段对齐 ``CellBinding`` 的 ``row.<field>`` / ``row_unit`` / ``customer_name``
    source；散件与装配体行共用同一套模板列。
    """

    order_no: str = ""
    applicant_name: str = ""
    drawing_no: str = ""
    name: str = ""
    quantity: int = 0
    unit: str = "件"  # 散件="件"；装配体合并行="套"
    planned_delivery_date: Any = None
    note: str = ""
    customer_name: str = ""  # 法拉 col 3（L2 客户名）


# ============================================================
# 列映射 + 模板元信息（按 prefix 编码，与 PR-F 一致）
# ============================================================
@dataclass(frozen=True)
class CellBinding:
    """模板里「一列 → 一个值」的映射声明。"""

    col: int  # 1-based 列号
    source: str  # "row_index" | "row.<field>" | "customer_name" | "parent_name" | "row_unit" | "const"
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
    # 法拉：Sheet 'Sheet1'，数据 R3-R12（10 行），R13-R17 为签字栏；超 10 行自动分页
    "F": TemplateConfig(
        sheet_name="Sheet1",
        start_row=3,
        max_rows=10,
        barcode_col=None,
        bindings=(
            CellBinding(1, "row_index"),
            CellBinding(2, "row.order_no"),
            CellBinding(3, "customer_name"),
            CellBinding(4, "row.applicant_name"),
            CellBinding(5, "row.drawing_no"),
            CellBinding(6, "row.name"),
            CellBinding(7, "row.quantity"),
            # 2026-08-04：col 8 从 const "件" 改为按行取 unit（散件「件」/装配体「套」）
            CellBinding(8, "row_unit"),
            CellBinding(9, "row.planned_delivery_date"),
            CellBinding(10, "row.note"),
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
            CellBinding(2, "row.order_no"),
            CellBinding(3, "row.applicant_name"),
            CellBinding(4, "row.drawing_no"),
            CellBinding(5, "row.name"),
            CellBinding(6, "row.quantity"),
            CellBinding(7, "const", const_value=""),
            CellBinding(8, "const", const_value=""),
            CellBinding(9, "row.planned_delivery_date"),
        ),
    ),
}


# ============================================================
# 服务层
# ============================================================
class DeliveryNotePrintService:
    """加载 Excel 模板 → 按 note 当前关联的批次行构造 ``PrintRow`` → 填模板 → 返回字节流。"""

    def __init__(
        self,
        notes: DeliveryNoteRepository,
        parts: PartRepository,
        customers: CustomerRepository,
        part_batches: PartBatchRepository | None = None,
    ) -> None:
        self.notes = notes
        self.parts = parts
        self.customers = customers
        self.part_batches = part_batches

    async def render(
        self,
        note: TDeliveryNote,
        custom_order: list[str] | None = None,
        merge_assemblies: bool = False,  # 2026-08-04 新增
        assembly_map: dict[int, TAssembly] | None = None,  # 2026-08-04：service 层预查
    ) -> tuple[bytes, str]:
        """填模板并返回字节流 + 模板 prefix。

        - ``custom_order`` 为 None / 空 → 按 ``TPartBatch.id ASC``（旧行为）
        - ``custom_order`` 提供 → 按其列表顺序投影；含非法 batch id 或漏行 → 422
        - ``merge_assemblies`` 为 True → 同装配体的子件合并为一行（数量 1，单位套）；
          散件逐行不变；组位置 = 组内最早出现的 batch 在 ``custom_order`` 中的位次
        """
        custom_order_list: list[str] = list(custom_order) if custom_order else []
        assembly_map = assembly_map or {}

        # 1) 拉 L1 客户的 serial_prefix（决定模板）
        cust = await self.customers.get_by_id(note.customer_id)
        if cust is None:
            raise BizError(
                code=ErrCode.BIZ_CUSTOMER_NOT_FOUND,
                message=f"customer {note.customer_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        if cust.parent_id is not None:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message="delivery note customer must be L1 root",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
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

        # 2) 拉 note 关联的批次行
        if self.part_batches is None:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message="server missing part batch repository",
                http_status=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        linked = await self.part_batches.list_by_delivery_note(note.id)
        if custom_order_list:
            pairs_by_id: dict[str, tuple[Any, TPart]] = {
                str(b.id): (b, p) for b, p in linked
            }
            ordered: list[tuple[Any, TPart]] = []
            seen: set[str] = set()
            for bid in custom_order_list:
                if bid not in pairs_by_id:
                    raise BizError(
                        code=ErrCode.BIZ_DELIVERY_PRINT_BAD_ORDER,
                        message=(
                            f"custom_order 含不属于本单的 batch id: {bid}"
                        ),
                        http_status=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                    )
                ordered.append(pairs_by_id[bid])
                seen.add(bid)
            missing = set(pairs_by_id) - seen
            if missing:
                raise BizError(
                    code=ErrCode.BIZ_DELIVERY_PRINT_BAD_ORDER,
                    message=(
                        f"custom_order 漏掉 {len(missing)} 行；"
                        "不允许静默丢弃（请确保预览包含全部行）"
                    ),
                    http_status=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                )
            linked = ordered
        # 过滤缺 serial/drawing 的行（warning + 跳过）
        rows: list[tuple[Any, TPart]] = []  # (batch, part) 保留的
        for b, p in linked:
            if not p.serial_no or not p.drawing_no:
                logger.warning(
                    "delivery_note_print: skip part id=%s (serial_no=%r drawing_no=%r)",
                    p.id, p.serial_no, p.drawing_no,
                )
                continue
            rows.append((b, p))
        if not rows:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message="所选零件均不可用（缺流水号或图号）",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )

        # 3) 一次性查 leaf_map（散件 / 装配体行的 customer_name 都用）
        leaf_ids: set[int] = set()
        for _b, p in rows:
            leaf_ids.add(p.customer_id)
        for a in assembly_map.values():
            if a.customer_id:
                leaf_ids.add(a.customer_id)
        leaf_list = await self.customers.list_by_ids(list(leaf_ids)) if leaf_ids else []
        leaf_map: dict[int, TCustomer] = {c.id: c for c in leaf_list}
        parent_ids = [c.parent_id for c in leaf_list if c.parent_id]
        parent_list = (
            await self.customers.list_by_ids(list(set(parent_ids)))
            if parent_ids else []
        )
        parent_map: dict[int, TCustomer] = {c.id: c for c in parent_list}

        # 4) 构造 PrintRow 列表（散件 + 装配体合并）
        print_rows = self._build_print_rows(
            rows=rows,
            leaf_map=leaf_map,
            parent_map=parent_map,
            assembly_map=assembly_map,
            merge_assemblies=merge_assemblies,
        )

        # 5) 分页填表
        def _fill_row(sheet, idx_in_page: int, row: PrintRow, customer_name: str) -> None:
            target_row = cfg.start_row + idx_in_page - 1
            ctx: dict[str, Any] = {
                "row_index": idx_in_page,
                "row": row,
                "customer_name": customer_name,
                "unit": row.unit,
            }
            for binding in cfg.bindings:
                val = _resolve_cell(binding, ctx)
                if val is not None:
                    sheet.cell(row=target_row, column=binding.col, value=val)

        def _fill() -> bytes:
            pages = [
                print_rows[i : i + cfg.max_rows]
                for i in range(0, len(print_rows), cfg.max_rows)
            ]
            base_pa = ws.print_area if isinstance(ws.print_area, str) else None
            local_pa = base_pa.split("!")[-1] if base_pa else None
            sheets = [ws]
            for n in range(1, len(pages)):
                cp = wb.copy_worksheet(ws)
                cp.title = f"{cfg.sheet_name} ({n + 1})"
                if local_pa:
                    cp.print_area = local_pa
                sheets.append(cp)

            for page_rows, sheet in zip(pages, sheets):
                for idx, pr in enumerate(page_rows, start=1):
                    _fill_row(sheet, idx, pr, pr.customer_name)
                _write_footer(sheet, note=note, prefix=prefix)

            buf = io.BytesIO()
            wb.save(buf)
            return buf.getvalue()

        xlsx_bytes = await asyncio.to_thread(_fill)
        return xlsx_bytes, prefix

    def _build_print_rows(
        self,
        rows: list[tuple[Any, TPart]],
        leaf_map: dict[int, TCustomer],
        parent_map: dict[int, TCustomer],
        assembly_map: dict[int, TAssembly],
        merge_assemblies: bool,
    ) -> list[PrintRow]:
        """把 (batch, part) 列表转成 ``PrintRow``；merge_assemblies 时同装配体子件合并一行。

        返回的列表保留 ``rows`` 的原始顺序（custom_order 已应用）；合并行位置 = 组内
        最早出现的 batch 位次；散件行照常。
        """
        # 先逐 (batch, part) 生成 PrintRow（散件逻辑）
        leaf_name_of_part: dict[int, str] = {}
        for _b, p in rows:
            leaf = leaf_map.get(p.customer_id)
            leaf_name_of_part[p.id] = leaf.name if leaf else ""

        per_row_print_rows: list[tuple[int, PrintRow]] = []
        for idx, (b, p) in enumerate(rows):
            per_row_print_rows.append((
                idx,
                PrintRow(
                    order_no=p.order_no or "",
                    applicant_name=p.applicant_name or "",
                    drawing_no=p.drawing_no or "",
                    name=p.name or "",
                    quantity=b.quantity,
                    unit="件",
                    planned_delivery_date=p.planned_delivery_date,
                    note=p.note or "",
                    customer_name=leaf_name_of_part.get(p.id, ""),
                ),
            ))

        if not merge_assemblies:
            return [pr for _idx, pr in per_row_print_rows]

        # 合并模式：按 part.assembly_id 分组（仅 assembly_map 命中的真装配体）
        groups: dict[int, list[int]] = {}  # asm.id → [原 rows 中的 idx 列表]
        for idx, (_b, p) in enumerate(rows):
            if p.assembly_id and p.assembly_id in assembly_map:
                groups.setdefault(p.assembly_id, []).append(idx)

        if not groups:
            return [pr for _idx, pr in per_row_print_rows]

        merged_batch_indices: set[int] = set()
        merged_items: list[tuple[int, PrintRow]] = []
        for asm_id, group_indices in groups.items():
            asm = assembly_map[asm_id]
            group_min_idx = min(group_indices)
            # 装配体行的 customer_name 取组内第一个子件的 L2 客户
            first_part = rows[group_indices[0]][1]
            cust_name = leaf_name_of_part.get(first_part.id, "")
            merged_items.append((
                group_min_idx,
                PrintRow(
                    order_no="",
                    applicant_name=asm.applicant_name or "",
                    drawing_no=asm.drawing_no or "",
                    name=asm.name or "",
                    quantity=1,
                    unit="套",
                    planned_delivery_date=asm.planned_delivery_date,
                    note="",
                    customer_name=cust_name,
                ),
            ))
            merged_batch_indices.update(group_indices)

        # 合并散件行（非装配体子件）
        result: list[tuple[int, PrintRow]] = []
        for idx, pr in per_row_print_rows:
            if idx in merged_batch_indices:
                continue
            result.append((idx, pr))
        result.extend(merged_items)
        # 按位置排序（保持视觉顺序一致）
        result.sort(key=lambda x: x[0])
        return [pr for _idx, pr in result]


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
    if src == "row_unit":
        return ctx.get("unit", "件")
    if src.startswith("row.") or src.startswith("part."):
        field_name = src.split(".", 1)[1]
        row = ctx.get("row")
        if row is None:
            return None
        # 兼容旧 part.<field> 写法（如 part.quantity 已被 _build_print_rows 用
        # PrintRow.quantity 替代，但保留兼容）
        val = getattr(row, field_name, None)
        # 日期对象保持原状由 openpyxl 写为 Excel 日期；空值写 None 跳过
        return val
    return None


# ============================================================
# 内部：模板级 footer / header 日期同步（2026-07-23 新增）
# ============================================================
def _format_fala_date(d: date) -> str:
    """法拉模板右下角日期文案：「送货日期：YYYY年M月D日》。

    与原模板字面量格式对齐（不去前导零；原模板写的是「2026年7月14日」）。
    """
    return f"送货日期：{d.year}年{d.month}月{d.day}日"


def _resolve_footer_date(note: TDeliveryNote) -> date:
    """送货日期：优先取 `note.delivery_date`；NULL（旧库 010 之前）回退当天。

    与 `service/delivery_note.create_draft` 的「默认 = 创建当天」语义保持一致；
    旧记录 NULL 时不能凭空塞一个旧日期（会引入另一类「陈旧日期」bug），也不能报错
    （会让已部署的旧单据无法打印），所以选「打印当天」兜底。
    """
    return note.delivery_date or now_naive().date()


def _write_footer(ws, *, note: TDeliveryNote, prefix: str) -> None:
    """把 `note.delivery_date` 写到模板的 footer / header 日期单元格。

    - ``prefix == "F"``：覆盖法拉模板合并区 A17 整段为「送货日期：YYYY年M月D日」
      （保留合并 + 模板原有样式：右对齐 + 缩字 + 边框 + General 格式）。
    - ``prefix == "L"``：写路达模板 H2='送货日期' 旁的 I2 为 Python ``date`` 对象
      （openpyxl 自动应用模板内 numFmtId=31 内置日期格式，Excel 显示为
      「2026/7/15」之类本地化形式；H2 标签不动）。

    其他 prefix 抛 ``BIZ_INVALID_VALUE``（应当走 ``BIZ_DELIVERY_TEMPLATE_NOT_CONFIGURED``
    在更早的入口被拦下；此处是兜底）。
    """
    effective = _resolve_footer_date(note)
    if prefix == "F":
        ws["A17"] = _format_fala_date(effective)
    elif prefix == "L":
        ws["I2"] = effective
    else:
        raise BizError(
            code=ErrCode.BIZ_INVALID_VALUE,
            message=f"delivery note template prefix {prefix!r} 无 footer 写入策略",
            http_status=http_status.HTTP_400_BAD_REQUEST,
        )