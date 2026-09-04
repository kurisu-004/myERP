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

2026-08-06「打开即打印」：
- 页面设置（纸张 / 方向 / 缩放适配 / 页边距 / 打印区域）在渲染期由 ``_apply_page_setup``
  写进 xlsx，**模板文件本身不改**。法拉 = A5 横向且宽高都锁一页；路达维持 A4 横向、
  只锁横向。用户打开 Excel 直接 Ctrl+P 即可，无需手工调页面布局。
- 列宽改由 ``_apply_budgeted_widths`` 以「模板原始列宽为下限 + 预算内按需加宽」的方式
  计算，取代原先每页调用 ``_autosize_columns`` 覆盖模板列宽的做法（后者会把 min_width=8
  强加到模板刻意做窄的列上、把页脚长文本算进估算、并允许备注列涨到 60 字符，从而撑爆
  纸张宽度分成两页）。``_autosize_columns`` 现仅供 ``render_labels`` 使用。
- 路达模板 ``print_area`` 原本是空字符串（Excel 会连 J-Q 的悬空空列一起打印），现显式
  钉死到 A1:I31；其数据行高也从被强制的 25 磅改回模板原生 18 磅（25 行满页 254mm 会
  超出 A4 横向可打印高度 210mm）。

2026-08-08 法拉版式微调：
- 数据行 col 2（订单号）/ 5（图号）/ 6（名称）左对齐（统一覆盖模板里 R3-R8 col 5
  仍为中心、R9-R12 才是 left 的不一致）；表头 R2 全居中保持模板原值。详见
  ``_apply_data_alignments`` 与 ``TemplateConfig.data_alignments``。
- col 9 预估交期 ``grow_cap`` 从 10.0 收到 1.5（commit ``afc089a`` 改写
  ``M月D日`` 字符串而非 date 对象后，``grow_cap=10.0`` 已经过保）。最终列宽
  ≈ 8.1 单位，覆盖实测最长 ``12月31日`` 8 单位内容；列宽 <8 单位时走 Excel
  默认截断（col 9 未列入 ``shrink_fit_cols``）。

2026-09-02 交期列改取订单方系统交期：
- 两模板 col 9「预估交期」字段值从 ``planned_delivery_date``（我方计划交期）
  切到 ``system_delivery_date``（订单方系统内部交期）。``PrintRow`` 字段同步
  改名；散件 / 装配体合并行的取值切换。``system_delivery_date`` 为空则
  留空（不回退到 ``planned_delivery_date``——这是刻意行为，避免两份交期混淆）。

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
import math
from copy import copy
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Mapping

from fastapi import status as http_status
import openpyxl
from openpyxl import load_workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.page import PageMargins
from openpyxl.worksheet.properties import PageSetupProperties

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
    system_delivery_date: Any = None  # 2026-09-02：交期列改取订单方系统交期，NULL 留空
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
class PageSetupSpec:
    """一张纸的页面设置（2026-08-06 新增）。

    这些值在渲染期写进 xlsx，用户打开 Excel 直接 Ctrl+P 即可，无需手工调页面布局。
    模板文件本身不改动——页面设置全部由代码接管，便于统一维护 + 覆盖模板遗漏
    （如路达模板的 ``print_area`` 原本是空的）。
    """

    paper_size: int  # openpyxl PaperSize：9=A4, 11=A5
    orientation: str  # "landscape" | "portrait"
    # ⚠️ Excel 仅当 sheet_properties.pageSetUpPr.fitToPage 为 True 时才采纳
    # fit_to_width / fit_to_height；否则这两个值被静默忽略。
    fit_to_page: bool
    fit_to_width: int  # 横向页数上限；1 = 永不横向断页
    fit_to_height: int  # 纵向页数上限；0 = 不限（纵向可自然翻页）
    margins: tuple[float, float, float, float, float, float]  # L,R,T,B,header,footer（英寸）
    print_area: str  # 纯范围如 "A1:J17"；openpyxl 存盘时自动补 sheet 前缀


@dataclass(frozen=True)
class TemplateConfig:
    """一份送货单模板的完整布局元信息（per prefix）。"""

    sheet_name: str
    start_row: int  # 数据起始行（1-based）
    max_rows: int  # 数据区最大行数；超出 → BIZ_DELIVERY_TEMPLATE_TOO_MANY_PARTS
    barcode_col: int | None  # 条码列号；None 表示该模板不带条码
    bindings: tuple[CellBinding, ...]
    # ---- 2026-08-06「打开即打印」新增 ----
    page_setup: PageSetupSpec
    width_cols: tuple[int, ...]  # 参与列宽预算的列（= print_area 覆盖的列）
    header_rows: tuple[int, ...]  # 列宽测量纳入的表头行（**不含**页脚/签字/说明行）
    growable_cols: frozenset[int]  # 允许在预算内加宽的列；其余列锁死在模板基线
    shrink_fit_cols: frozenset[int]  # 超长文本靠 shrinkToFit 缩字号显示完整的列
    grow_cap: Mapping[int, float] = field(default_factory=dict)  # 单列加宽上限覆盖
    width_budget_ratio: float = 1.15  # 预算 = Σ模板基线列宽 × 本比例
    data_row_height: float = 25.0  # 数据行统一行高（磅）
    # ---- 2026-08-08 列对齐 ----
    # 数据行 horizontal 对齐覆盖：列号 → "left" / "center" / "right"。
    # 仅作用于 R[start_row] ~ R[start_row+page_row_count-1]，表头 R2 保持模板自带。
    # 实现走 copy(cell.alignment) + 改 horizontal，**不要**整对象替换否则会清掉
    # vertical / wrapText / shrinkToFit 等其他字段。
    data_alignments: Mapping[int, str] = field(default_factory=dict)


DEFAULT_GROW_CAP = 12.0  # 单列默认最多比模板基线宽 12 个字符单位


TEMPLATE_CONFIGS: dict[str, TemplateConfig] = {
    # 法拉：Sheet 'Sheet1'，数据 R3-R12（10 行），R13-R17 为签字栏；超 10 行自动分页
    # 模板原生就是 A5 横向，且天然放得下（内容高 128.0mm + 上下边距 12.0mm = 139.9mm
    # ≤ 148mm；列宽合计 94.25 单位 ≈ 175-200mm ≤ 210mm，左右边距为 0）。
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
            CellBinding(9, "row.system_delivery_date"),
            CellBinding(10, "row.note"),
        ),
        page_setup=PageSetupSpec(
            paper_size=11,  # A5
            orientation="landscape",
            fit_to_page=True,
            fit_to_width=1,
            fit_to_height=1,  # 宽高都锁一页
            margins=(0.0, 0.0, 0.1965, 0.275, 0.0785, 0.1181),  # 沿用模板实测值
            print_area="A1:J17",
        ),
        width_cols=(1, 2, 3, 4, 5, 6, 7, 8, 9, 10),
        header_rows=(2,),  # 单行表头
        # 序号 / 数量 / 单位 是模板刻意做窄的定宽列；预估交期实际是 "M月D日"
        # 字符串（commit afc089a 后），最长 "12月31日" ≈ 8 单位，模板基线 6.625
        # 偏窄但加宽额度已收紧
        growable_cols=frozenset({2, 3, 4, 5, 6, 9, 10}),
        shrink_fit_cols=frozenset({5, 6, 10}),  # 编码 / 名称 / 备注
        # 2026-08-08：预估交期 grow_cap 从 10.0 收到 1.5 → 最终列宽 ≈ 8.1 单位，
        # 覆盖 "12月31日" 8 单位内容；超出走 shrinkToFit（虽然 col 9 不在
        # shrink_fit_cols，但 col 8 走的是 _apply_budgeted_widths 后的预算约束，
        # Excel 在列宽不够时默认也会截断，不会爆）
        grow_cap={9: 1.5},
        width_budget_ratio=1.15,  # 预算 ≈ 108.4（基线 94.25）；最差 Excel 缩到 ~87%
        data_row_height=25.0,
        # 2026-08-08：数据行 col 2/5/6（订单号 / 图号 / 名称）左对齐；表头 R2
        # 保持模板居中；其余列（序号 / 分厂 / 申请人 / 数量 / 单位 / 交期 / 备注）
        # 仍走模板原值（一般居中）
        data_alignments={2: "left", 5: "left", 6: "left"},
    ),
    # 路达：Sheet '杏南'，数据 R5-R29（25 行），R30-R31 为填写说明
    # 维持模板原生的 A4 横向；只锁横向不断页，纵向允许自然翻页。
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
            CellBinding(9, "row.system_delivery_date"),
        ),
        page_setup=PageSetupSpec(
            paper_size=9,  # A4（保持模板原纸张）
            orientation="landscape",
            fit_to_page=True,
            fit_to_width=1,
            fit_to_height=0,  # 只锁横向；纵向可翻页
            margins=(0.2715, 0.2715, 0.0, 0.0, 0.0, 0.0),  # 沿用模板实测值
            # 模板 print_area 原本是空字符串 → Excel 会把 J-Q 的悬空空列一起打出来。
            # 这里显式钉死到 A-I，是本次的关键修复之一。
            print_area="A1:I31",
        ),
        width_cols=(1, 2, 3, 4, 5, 6, 7, 8, 9),
        header_rows=(3, 4),  # 双行表头
        growable_cols=frozenset({2, 3, 4, 5}),
        shrink_fit_cols=frozenset({3, 5}),  # 申请部门/人、检具名称
        width_budget_ratio=1.0,  # 总宽 147.2 已接近 A4 横向极限，不给加宽额度
        # 模板原生 18 磅；此前被强制成 25 磅，25 行满页后整页 254mm 超出 A4
        # 横向可打印高度 210mm，被迫翻到第 2 页。
        data_row_height=18.0,
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
        merge_quantities: dict[int, int] | None = None,  # 2026-08-04 扩展：每套 override
    ) -> tuple[bytes, str]:
        """填模板并返回字节流 + 模板 prefix。

        - ``custom_order`` 为 None / 空 → 按 ``TPartBatch.id ASC``（旧行为）
        - ``custom_order`` 提供 → 按其列表顺序投影；含非法 batch id 或漏行 → 422
        - ``merge_assemblies`` 为 True → 同装配体的子件合并为一行（数量 = merge_quantities
          或默认 1，单位套）；散件逐行不变；组位置 = 组内最早出现的 batch 在
          ``custom_order`` 中的位次
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

        # 2-4) 准备 PrintRow 列表（与 render_labels 共享：拉批次 → 过滤 → 客户
        # map → 构造 PrintRow）。prefix / 模板加载留在本方法内；labels 不需要。
        print_rows = await self._prepare_print_rows(
            note=note,
            custom_order=custom_order_list,
            merge_assemblies=merge_assemblies,
            assembly_map=assembly_map,
            merge_quantities=merge_quantities,
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
            # 2026-08-06：模板基线列宽必须在任何写入 / copy_worksheet 之前快照
            baseline = _snapshot_baseline_widths(ws, cfg.width_cols)
            # 页面设置也要赶在 copy_worksheet 之前——副本会自动继承
            # page_setup / pageSetUpPr / page_margins（print_area 除外，见下）
            _apply_page_setup(ws, cfg)

            base_pa = ws.print_area if isinstance(ws.print_area, str) else None
            local_pa = base_pa.split("!")[-1] if base_pa else None
            sheets = [ws]
            for n in range(1, len(pages)):
                cp = wb.copy_worksheet(ws)
                cp.title = f"{cfg.sheet_name} ({n + 1})"
                # WorksheetCopy 不复制 print_area，必须手工补
                if local_pa:
                    cp.print_area = local_pa
                sheets.append(cp)

            for page_rows, sheet in zip(pages, sheets):
                for idx, pr in enumerate(page_rows, start=1):
                    _fill_row(sheet, idx, pr, pr.customer_name)
                _write_footer(sheet, note=note, prefix=prefix)
                # 2026-08-06：行高按模板配置 + 列宽以模板为基线在 A5/A4 预算内微调
                _set_data_row_heights(
                    sheet, cfg.start_row, len(page_rows), cfg.data_row_height
                )
                # 2026-08-08：数据行列对齐（订单号/图号/名称 left；表头保持模板原值）
                _apply_data_alignments(sheet, cfg, len(page_rows))
                _apply_budgeted_widths(sheet, baseline, cfg, len(page_rows))
                _apply_shrink_to_fit(sheet, cfg, len(page_rows))

            buf = io.BytesIO()
            wb.save(buf)
            return buf.getvalue()

        xlsx_bytes = await asyncio.to_thread(_fill)
        return xlsx_bytes, prefix

    async def _prepare_print_rows(
        self,
        note: TDeliveryNote,
        custom_order: list[str],
        merge_assemblies: bool,
        assembly_map: dict[int, TAssembly],
        merge_quantities: dict[int, int] | None,
        line_item_ids: list[str] | None = None,  # 2026-08-07：标签勾选子集
    ) -> list[PrintRow]:
        """render 与 render_labels 共享的行构建：拉批次 → 过滤 → 客户 map → 构造 PrintRow。

        - ``custom_order`` 为空走 ``TPartBatch.id ASC``（与旧行为一致）；
        - 非法 batch id 或漏行 → 422 BIZ_DELIVERY_PRINT_BAD_ORDER；
        - ``line_item_ids``（2026-08-07 标签专用，None = 不过滤）→ 成员裁剪。
          放在 custom_order 排序之后 → 天然保留用户拖动的顺序；
          放在 serial/drawing 过滤之前 → 复用既有「全部不可用 → 400」兜底。
        - 缺 ``serial_no`` / ``drawing_no`` 的零件 warning + 跳过；
        - 全部行都不可用 → 400 BIZ_INVALID_VALUE。
        """
        # 2) 拉 note 关联的批次行
        if self.part_batches is None:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message="server missing part batch repository",
                http_status=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        linked = await self.part_batches.list_by_delivery_note(note.id)
        # 2026-08-07：同 part 多批次折叠 — 每个 part 派一个代表 batch id（最小 b.id）。
        # 折叠后 _build_print_rows 会按 part_id 求和；custom_order 校验改为"每个 part
        # 仅承认其代表 id"。
        rep_by_part: dict[int, int] = {}
        for _b, p in linked:
            cur = rep_by_part.get(p.id)
            if cur is None or _b.id < cur:
                rep_by_part[p.id] = _b.id
        if custom_order:
            pairs_by_id: dict[str, tuple[Any, TPart]] = {
                str(b.id): (b, p) for b, p in linked
            }
            note_batch_ids = set(pairs_by_id)
            custom_set = set(custom_order)
            # 1) 完全不在本单的 batch id
            unknown = custom_set - note_batch_ids
            if unknown:
                raise BizError(
                    code=ErrCode.BIZ_DELIVERY_PRINT_BAD_ORDER,
                    message=f"custom_order 含不属于本单的 batch id: {sorted(unknown)}",
                    http_status=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                )
            # 2) 非代表 id（同 part 但不是该组的最小 b.id）
            expected_reps = {str(rep) for rep in rep_by_part.values()}
            non_rep = custom_set - expected_reps
            if non_rep:
                sample = sorted(non_rep)[0]
                # 用 list 推导式避免 generator-next 在 async 上下文抛 StopIteration
                sample_match = [
                    p.id for b, p in linked if str(b.id) == sample
                ]
                sample_part_id = sample_match[0]
                rep = rep_by_part[sample_part_id]
                raise BizError(
                    code=ErrCode.BIZ_DELIVERY_PRINT_BAD_ORDER,
                    message=(
                        f"custom_order 含已合并的批次 id（{sample}）；"
                        f"请发代表批次 id {rep}（part {sample_part_id} 的代表）"
                    ),
                    http_status=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                )
            # 3) 漏掉某些 part 的代表 id
            missing = expected_reps - custom_set
            if missing:
                sample_missing = sorted(missing)[0]
                pid_match = [
                    p.id for b, p in linked if str(b.id) == sample_missing
                ]
                sample_pid = pid_match[0]
                raise BizError(
                    code=ErrCode.BIZ_DELIVERY_PRINT_BAD_ORDER,
                    message=(
                        f"custom_order 漏掉 {len(missing)} 个代表批次 id，"
                        f"例如 {sample_missing}（part {sample_pid} 的代表）；"
                        "请确保预览包含全部 part"
                    ),
                    http_status=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                )
            ordered: list[tuple[Any, TPart]] = [
                pairs_by_id[bid] for bid in custom_order
            ]
            linked = ordered
        # 2026-08-07：line_item_ids 子集过滤（仅标签导出用）
        if line_item_ids is not None:
            if not line_item_ids:
                raise BizError(
                    code=ErrCode.BIZ_INVALID_VALUE,
                    message="line_item_ids 为空：未勾选任何行",
                    http_status=http_status.HTTP_400_BAD_REQUEST,
                )
            wanted = set(line_item_ids)
            unknown = wanted - {str(b.id) for b, _p in linked}
            if unknown:
                raise BizError(
                    code=ErrCode.BIZ_DELIVERY_PRINT_BAD_ORDER,
                    message=f"line_item_ids 含不属于本单的 batch id: {sorted(unknown)}",
                    http_status=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                )
            linked = [(b, p) for b, p in linked if str(b.id) in wanted]
        # 过滤缺 serial/drawing 的行（warning + 跳过）
        rows: list[tuple[Any, TPart]] = []
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
        return self._build_print_rows(
            rows=rows,
            leaf_map=leaf_map,
            parent_map=parent_map,
            assembly_map=assembly_map,
            merge_assemblies=merge_assemblies,
            merge_quantities=merge_quantities,
        )

    async def render_labels(
        self,
        note: TDeliveryNote,
        custom_order: list[str] | None = None,
        merge_assemblies: bool = True,  # 2026-08-07 改默认：与送货单保持一致
        assembly_map: dict[int, TAssembly] | None = None,
        merge_quantities: dict[int, int] | None = None,
        line_item_ids: list[str] | None = None,  # 2026-08-07：标签勾选子集
    ) -> tuple[bytes, str]:
        """打印标签用的 Excel（无模板，沿用 PrintRow 口径）。

        表头：客户 | 申请人 | 名称 | 图号 | 数量 | 单位
        数据行：与送货单完全一致——``merge_assemblies`` 默认 True（与 ``render``
        对齐），自动反映（合并行 ``unit`` =「套」，散件行 =「件」），
        行顺序与 ``custom_order`` 一致。

        ``line_item_ids``（2026-08-07 新增）：只打勾选行；None = 全打。
        与 ``custom_order`` 正交——``custom_order`` 仍须包含本单全部行（漏行 422），
        ``line_item_ids`` 在排序后裁剪（顺序 × 成员，两个独立维度）。

        合并模式下若用户只勾装配体*部分*子件（API 直调可能），合并行数量仍取
        ``merge_quantities.get(asm_id, 1)``，不按存活子件缩放——以防合并行凭空
        缩小。``prefix`` 仅用于文件名前缀兜底（缺省 "X"）。
        """
        custom_order_list: list[str] = list(custom_order) if custom_order else []
        assembly_map = assembly_map or {}
        print_rows = await self._prepare_print_rows(
            note=note,
            custom_order=custom_order_list,
            merge_assemblies=merge_assemblies,
            assembly_map=assembly_map,
            merge_quantities=merge_quantities,
            line_item_ids=line_item_ids,
        )
        # prefix 走 L1 客户（service 层已保证 note.customer_id 是 L1 root）
        cust = await self.customers.get_by_id(note.customer_id)
        prefix = cust.serial_prefix if cust and cust.serial_prefix else "X"

        def _build() -> bytes:
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "标签"
            # 2026-09-04：新增「订单号」列（col 2，紧贴客户列），与送货单 F/L 模板
            # col 2 = 订单号的视觉位置对齐；PrintRow.order_no 已在 _build_print_rows 填好
            # （散件取 p.order_no，装配件合并行取 asm.order_no）。
            headers = ["客户", "订单号", "申请人", "名称", "图号", "数量", "单位"]
            for c, h in enumerate(headers, start=1):
                ws.cell(row=1, column=c, value=h).font = Font(bold=True)
            for r, pr in enumerate(print_rows, start=2):
                ws.cell(row=r, column=1, value=pr.customer_name)
                ws.cell(row=r, column=2, value=pr.order_no)
                ws.cell(row=r, column=3, value=pr.applicant_name)
                ws.cell(row=r, column=4, value=pr.name)
                ws.cell(row=r, column=5, value=pr.drawing_no)
                ws.cell(row=r, column=6, value=pr.quantity)
                ws.cell(row=r, column=7, value=pr.unit)
            # 列宽：客户/订单号/申请人/名称/图号留宽（订单号列长可至 30 字符，wide_max=60 够用）；
            # 数量/单位固定窄列（现为 col 6/7）。
            _autosize_columns(
                ws,
                max_col=7,
                fixed_cols={6: 8, 7: 8},
                wide_cols={1, 2, 3, 4, 5},
                wide_max=60,
            )
            buf = io.BytesIO()
            wb.save(buf)
            return buf.getvalue()

        xlsx_bytes = await asyncio.to_thread(_build)
        return xlsx_bytes, prefix

    def _build_print_rows(
        self,
        rows: list[tuple[Any, TPart]],
        leaf_map: dict[int, TCustomer],
        parent_map: dict[int, TCustomer],
        assembly_map: dict[int, TAssembly],
        merge_assemblies: bool,
        merge_quantities: dict[int, int] | None = None,
    ) -> list[PrintRow]:
        """把 (batch, part) 列表转成 ``PrintRow``；merge_assemblies 时同装配体子件合并一行。

        返回的列表保留 ``rows`` 的原始顺序（custom_order 已应用）；合并行位置 = 组内
        最早出现的 batch 位次；散件行照常。``merge_quantities`` 按 assembly_id override
        装配体合并行的数量（默认 1）。

        2026-08-07：同 part 多批次折叠（_split 产生）— 每 part 仅产出一行，
        ``quantity = Σ(批次.quantity)``。其余字段取该 part 首次出现的 batch。
        分组键用 ``p.id`` 而非 ``serial_no``：后者可空、可重号（COMPLETED 后回池），
        ``p.id`` 是雪花主键永不回收。同序列号多批次只能从 ``_split`` 产生，必然同 part。
        """
        # 1) 同 part 折叠：每 part 仅产出一行；quantity 求和
        qty_by_part: dict[int, int] = {}
        rep_idx_by_part: dict[int, int] = {}
        order_part_ids: list[int] = []
        for idx, (b, p) in enumerate(rows):
            if p.id in qty_by_part:
                qty_by_part[p.id] += b.quantity
            else:
                qty_by_part[p.id] = b.quantity
                rep_idx_by_part[p.id] = idx
                order_part_ids.append(p.id)
        if len(qty_by_part) < len(rows):
            # 至少一个 part 被折叠 → 重排 rows 让每个 part 仅出现一次
            rows = [rows[rep_idx_by_part[pid]] for pid in order_part_ids]

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
                    quantity=qty_by_part[p.id],  # 折叠后即求和；未折叠时等于 b.quantity
                    unit="件",
                    system_delivery_date=_format_print_date(p.system_delivery_date),
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
                    order_no=asm.order_no or "",
                    applicant_name=asm.applicant_name or "",
                    drawing_no=asm.drawing_no or "",
                    name=asm.name or "",
                    # 2026-08-04 扩展：merge_quantities 按 assembly_id override 套数
                    quantity=(merge_quantities or {}).get(asm_id, 1),
                    unit="套",
                    system_delivery_date=_format_print_date(asm.system_delivery_date),
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
def _estimate_cell_width(s: Any) -> int:
    """估算字符串显示宽度（Excel 字符宽单位）。

    ASCII 字符宽 1，中文 / 全角宽 2。用于 openpyxl 列宽自适配。
    """
    if s is None:
        return 0
    text = str(s)
    cn = sum(1 for c in text if ord(c) > 127)
    return cn * 2 + (len(text) - cn)


def _autosize_columns(
    ws,
    max_col: int,
    *,
    min_width: int = 8,
    max_width: int = 40,
    fixed_cols: dict[int, int] | None = None,
    wide_cols: set[int] | None = None,
    wide_max: int = 60,
) -> None:
    """按当前 sheet 已写内容估算每列宽度并写入 column_dimensions。

    ``fixed_cols`` 中的列直接写固定宽度（跳过内容估算）；
    ``wide_cols`` 中的列上限用 ``wide_max`` 而非 ``max_width``，
    兼顾备注等长文本列。

    ⚠️ 2026-08-06 起**仅供 ``render_labels`` 使用**（标签是 openpyxl 新建的空白
    工作簿，没有模板列宽要保护，扫全表 + min_width 下限的行为在那边是合适的）。
    送货单走 ``_apply_budgeted_widths``——本函数会覆盖模板手工调好的列宽，且把
    页脚/签字栏长文本算进估算，用在模板上会撑爆纸张宽度。
    """
    fixed_cols = fixed_cols or {}
    wide_cols = wide_cols or set()
    for col_idx in range(1, max_col + 1):
        letter = get_column_letter(col_idx)
        if col_idx in fixed_cols:
            ws.column_dimensions[letter].width = fixed_cols[col_idx]
            continue
        max_len = 0
        for row in ws.iter_rows(min_col=col_idx, max_col=col_idx, values_only=False):
            for cell in row:
                w = _estimate_cell_width(cell.value)
                if w > max_len:
                    max_len = w
        if max_len > 0:
            upper = wide_max if col_idx in wide_cols else max_width
            ws.column_dimensions[letter].width = max(min_width, min(max_len + 2, upper))


def _set_data_row_heights(ws, start_row: int, row_count: int, height: float = 25) -> None:
    """数据行统一行高（Excel 高度单位=磅）。

    默认 25 磅（法拉）；路达传 18 磅——模板原生值，强制 25 会让满页 25 行溢出
    A4 横向可打印高度。
    """
    for i in range(row_count):
        ws.row_dimensions[start_row + i].height = height


# ============================================================
# 内部：A5/A4「打开即打印」页面设置 + 列宽预算（2026-08-06 新增）
# ============================================================
def _apply_page_setup(ws, cfg: TemplateConfig) -> None:
    """把纸张 / 方向 / 缩放适配 / 页边距 / 打印区域写进 sheet。

    调用点必须在 ``wb.copy_worksheet`` **之前**：openpyxl 的 ``WorksheetCopy``
    会连同 ``page_setup`` / ``sheet_properties`` / ``page_margins`` 一起复制给副本，
    因此第 2、3 页无需重复设置（``print_area`` 是唯一的例外，见 ``render``）。

    两个易踩的坑：
    - ``fitToWidth`` / ``fitToHeight`` 只有在 ``pageSetUpPr.fitToPage`` 为 True 时
      才被 Excel 采纳，否则静默失效；
    - fit 模式下 ``page_setup.scale`` 必须清成 None，否则 Excel 按 scale 出图。
    """
    spec = cfg.page_setup
    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=spec.fit_to_page)
    ws.page_setup.paperSize = spec.paper_size
    ws.page_setup.orientation = spec.orientation
    if spec.fit_to_page:
        ws.page_setup.fitToWidth = spec.fit_to_width
        ws.page_setup.fitToHeight = spec.fit_to_height
        ws.page_setup.scale = None
    left, right, top, bottom, header, footer = spec.margins
    ws.page_margins = PageMargins(
        left=left, right=right, top=top, bottom=bottom, header=header, footer=footer
    )
    # 纯范围字符串即可，openpyxl 存盘时自动补成 'SheetName'!$A$1:$J$17
    ws.print_area = spec.print_area


def _snapshot_baseline_widths(ws, cols: tuple[int, ...]) -> dict[int, float]:
    """快照模板原始列宽，作为后续列宽预算的基线 + 下限。

    必须在任何写入 / ``_apply_budgeted_widths`` / ``copy_worksheet`` 之前调用，
    否则拿到的是被改写过的宽度而非模板设计值。模板没显式设宽的列回退到
    ``sheet_format.defaultColWidth``（缺省 9.0）。
    """
    default = ws.sheet_format.defaultColWidth or 9.0
    baseline: dict[int, float] = {}
    for col in cols:
        width = ws.column_dimensions[get_column_letter(col)].width
        baseline[col] = float(width) if width is not None else float(default)
    return baseline


def _apply_budgeted_widths(
    ws,
    baseline: dict[int, float],
    cfg: TemplateConfig,
    page_row_count: int,
) -> None:
    """模板基线列宽 + 预算内按需加宽 → 写回 column_dimensions。

    与旧的 ``_autosize_columns`` 的本质区别：
    - 模板列宽是**下限**，任何列都不会比模板更窄（不会破坏手工调好的版式）；
    - 只有 ``cfg.growable_cols`` 能申请加宽额度，额度总量 = 基线总宽 ×
      ``width_budget_ratio`` - 基线总宽，僧多粥少时按需求比例分配；
    - 测量范围只含表头行 + 本页实际填了数据的行，**不含**页脚 / 签字栏 /
      填写说明那些长文本（旧实现扫全表，正是列宽被撑爆的主因之一）。

    数学上 ``Σ width ≤ 预算`` 恒成立，不需要事后回压。
    """
    budget = sum(baseline.values()) * cfg.width_budget_ratio
    headroom = max(0.0, budget - sum(baseline.values()))

    scan_rows = list(cfg.header_rows) + list(
        range(cfg.start_row, cfg.start_row + page_row_count)
    )

    # 每个可增宽列「想要」多出多少（相对模板基线）
    want: dict[int, float] = {}
    for col in cfg.width_cols:
        if col not in cfg.growable_cols:
            continue
        content = 0
        for row in scan_rows:
            w = _estimate_cell_width(ws.cell(row=row, column=col).value)
            if w > content:
                content = w
        extra = (content + 2) - baseline[col]
        if extra > 0:
            want[col] = min(extra, cfg.grow_cap.get(col, DEFAULT_GROW_CAP))

    total_want = sum(want.values())
    if total_want > headroom and total_want > 0:
        ratio = headroom / total_want
        want = {col: w * ratio for col, w in want.items()}

    for col in cfg.width_cols:
        width = baseline[col] + want.get(col, 0.0)
        # 逐列向下取整到 3 位小数：四舍五入会让每列各涨最多 0.0005，
        # 累加后把总宽顶出预算。floor 只减不增，保证 Σ width ≤ 预算严格成立。
        # 外层 max() 再兜住「floor 把宽度压到基线之下」的边角情况
        # （如路达 col 8 基线 17.9423076923077），维持「模板列宽是下限」不变量。
        ws.column_dimensions[get_column_letter(col)].width = max(
            baseline[col], math.floor(width * 1000) / 1000
        )


def _apply_data_alignments(ws, cfg: TemplateConfig, page_row_count: int) -> None:
    """按 ``cfg.data_alignments`` 把数据行的 ``horizontal`` 对齐写到 cell。

    仅作用于 R[start_row] ~ R[start_row+page_row_count-1]，表头 R2 保持模板自带。
    复用 ``_apply_shrink_to_fit`` 的 copy+modify 模式：``copy(cell.alignment)`` 后
    仅设 ``horizontal``，**不能** ``cell.alignment = Alignment(horizontal=...)`` 整对象
    替换——那样会把 vertical / wrapText / shrinkToFit 等其他字段一并清零。

    调用顺序：必须在 ``_apply_shrink_to_fit`` **之前**，让 shrink_to_fit 后续
    ``copy()`` 时能看到这里写入的新 horizontal。
    """
    for col, align in cfg.data_alignments.items():
        for r in range(cfg.start_row, cfg.start_row + page_row_count):
            cell = ws.cell(row=r, column=col)
            alignment = copy(cell.alignment)
            alignment.horizontal = align
            cell.alignment = alignment


def _apply_shrink_to_fit(ws, cfg: TemplateConfig, page_row_count: int) -> None:
    """给长文本列开 shrinkToFit，让 Excel 自动缩字号把内容显示完整。

    列宽被预算约束后，超长的 名称 / 备注 会显示不全（旧实现靠把列撑到 60 字符
    来显示，代价就是跑版）。``shrinkToFit`` 把「显示不下」的问题局部化到单元格，
    不影响整张纸的版式。保留模板原有的对齐方式，只加这一个开关。
    """
    for col in cfg.shrink_fit_cols:
        for row in range(cfg.start_row, cfg.start_row + page_row_count):
            cell = ws.cell(row=row, column=col)
            alignment = copy(cell.alignment)
            alignment.shrinkToFit = True
            cell.alignment = alignment



def _format_fala_date(d: date) -> str:
    """法拉模板右下角日期文案：「送货日期：YYYY年M月D日》。

    与原模板字面量格式对齐（不去前导零；原模板写的是「2026年7月14日」）。
    """
    return f"送货日期：{d.year}年{d.month}月{d.day}日"


def _format_print_date(d: date | None) -> str | None:
    """2026-08-07：打印 XLSX 交期列用「M月D日」（无前导零）。

    例：date(2026, 8, 12) → "8月12日"。None 透传。
    """
    if d is None:
        return None
    return f"{d.month}月{d.day}日"


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