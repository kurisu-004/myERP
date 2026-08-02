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
from typing import Any, Literal

from fastapi import status as http_status
from openpyxl import load_workbook

from core.config import settings
from core.error_code import ErrCode
from core.exception import BizError
from core.time import now_naive
from model.customer import TCustomer
from model.delivery_note import TDeliveryNote
from model.enums import PartStatus
from model.part import TPart
from repository.customer import CustomerRepository
from repository.delivery_note import DeliveryNoteRepository
from repository.part import PartRepository
from repository.part_batch import PartBatchRepository

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
    # 法拉：Sheet 'Sheet1'，数据 R3-R12（10 行），R13-R17 为签字栏；超 10 行自动分页
    "F": TemplateConfig(
        sheet_name="Sheet1",
        start_row=3,
        max_rows=10,
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
    """加载 Excel 模板 → 按 note 当前关联的批次行填行 → 返回 .xlsx 字节流 + prefix。"""

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
        custom_order: list[str] | None = None,  # 2026-08-02 新增：预览组件拖动后的 batch id 顺序
    ) -> tuple[bytes, str]:
        """填模板并返回字节流 + 模板 prefix。

        - ``custom_order`` 为 None / 空 → 按 ``TPartBatch.id ASC``（旧行为）
        - ``custom_order`` 提供 → 按其列表顺序投影；含非法 batch id 或漏行 → 422
        """
        custom_order_list: list[str] = list(custom_order) if custom_order else []
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

        # 2) 拉 note 关联的批次行（2026-07-29 批次级：行=批次，数量=批次量；
        #    picked_up 后 delivery_note_id 仍指本单，因此也能拉到）
        if self.part_batches is None:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message="server missing part batch repository",
                http_status=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        linked = await self.part_batches.list_by_delivery_note(note.id)
        # 2026-08-02：先按 custom_order 重排，再过滤缺 serial/drawing 的行
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
        rows: list[tuple[Any, TPart]] = []  # (batch, part)
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

        # 3) 一次性查每个 part 的 L2 叶子客户 + L1 父（customer_name 用 L2、parent_name 留作备用）
        leaf_ids = list({p.customer_id for _b, p in rows})
        leaf_list = await self.customers.list_by_ids(leaf_ids) if leaf_ids else []
        leaf_map: dict[int, TCustomer] = {c.id: c for c in leaf_list}
        parent_ids = [c.parent_id for c in leaf_list if c.parent_id]
        parent_list = (
            await self.customers.list_by_ids(list(set(parent_ids)))
            if parent_ids else []
        )
        parent_map: dict[int, TCustomer] = {c.id: c for c in parent_list}

        # 4) 分页填表：每页最多 cfg.max_rows 行；超出自动续到下一份（复制模板 sheet）。
        #    openpyxl CPU-bound + 无重 IO，用 asyncio.to_thread 包裹整段同步逻辑。
        def _fill_row(sheet, idx_in_page: int, row: tuple[Any, TPart]) -> None:
            b, p = row
            target_row = cfg.start_row + idx_in_page - 1
            leaf = leaf_map.get(p.customer_id)
            ctx: dict[str, Any] = {
                "row_index": idx_in_page,
                "part": p,
                # 2026-07-29 批次级：数量列取批次量（part.quantity 不脏写）
                "quantity_override": b.quantity,
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
                    sheet.cell(row=target_row, column=binding.col, value=val)

        def _fill() -> bytes:
            pages = [
                rows[i : i + cfg.max_rows]
                for i in range(0, len(rows), cfg.max_rows)
            ]
            # 复制发生在 base sheet 仍空白时，确保每份都是干净模板（不带上页数据）。
            base_pa = ws.print_area if isinstance(ws.print_area, str) else None
            local_pa = base_pa.split("!")[-1] if base_pa else None
            sheets = [ws]
            for n in range(1, len(pages)):
                cp = wb.copy_worksheet(ws)
                cp.title = f"{cfg.sheet_name} ({n + 1})"
                if local_pa:
                    cp.print_area = local_pa
                sheets.append(cp)

            # 5) 逐页填数据 + 写 footer 日期（footer 见 `_write_footer`）：
            #    - 法拉 A17 合并区整段覆盖成「送货日期：YYYY年M月D日」（保留合并）
            #    - 路达 I2 写入 date 对象（保留模板 numFmtId=31 内置日期格式）
            #    - delivery_date 为 NULL（旧库 010 之前的数据）回退到当天
            for page_rows, sheet in zip(pages, sheets):
                for idx, p in enumerate(page_rows, start=1):
                    _fill_row(sheet, idx, p)
                _write_footer(sheet, note=note, prefix=prefix)

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
        # 2026-07-29 批次级：数量列优先取批次量（行=批次）
        if field_name == "quantity" and ctx.get("quantity_override") is not None:
            return ctx["quantity_override"]
        val = getattr(part, field_name, None)
        # 日期对象保持原状由 openpyxl 写为 Excel 日期；空值写 None 跳过
        return val
    return None


# ============================================================
# 内部：模板级 footer / header 日期同步（2026-07-23 新增）
# ============================================================
def _format_fala_date(d: date) -> str:
    """法拉模板右下角日期文案：「送货日期：YYYY年M月D日」。

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
