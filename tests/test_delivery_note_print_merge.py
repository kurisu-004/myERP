"""送货单打印 merge_assemblies 测试（2026-08-04 装配件合并）。

覆盖：
- merge=False（默认）：装配件子件与散件逐行输出；
- merge=True：同装配体子件合并为一行（数量 1，单位套，显示总装图号）；
- merge=True 但单上无装配件：行为等同 merge=False（行数 = 散件数）。
"""
from __future__ import annotations

import io
from datetime import date

from openpyxl import load_workbook

from model.assembly import TAssembly
from model.customer import TCustomer
from model.enums import AssemblyStatus, PartStatus
from model.part import TPart
from repository.customer import CustomerRepository
from repository.delivery_note import (
    DeliveryNoteCounterRepository,
    DeliveryNoteEventRepository,
    DeliveryNoteRepository,
)
from repository.part import PartRepository
from repository.part_batch import PartBatchRepository
from repository.worker import WorkerRepository
from service.delivery_note import DeliveryNoteService

from tests.conftest import seed_root_batch


# ============================================================
# helpers
# ============================================================
async def _make_l1_root(session, *, name: str, prefix: str = "F") -> TCustomer:
    c = TCustomer(name=name, parent_id=None)
    c.serial_prefix = prefix
    session.add(c)
    await session.flush()
    return c


async def _make_assembly(
    session,
    *,
    customer_id: int,
    serial_no: str = "A7001",
    drawing_no: str = "DA-7001",
    name: str = "装配件A",
    applicant_name: str = "总装测试员",
    planned_delivery_date: date | None = date(2026, 7, 30),
) -> TAssembly:
    """直接构造一个 PENDING 状态的装配件（绕过 AssemblyService.create_assembly）。"""
    a = TAssembly(
        serial_no=serial_no,
        drawing_no=drawing_no,
        name=name,
        applicant_name=applicant_name,
        customer_id=customer_id,
        request_date=date(2026, 7, 1),
        planned_delivery_date=planned_delivery_date,
        status=AssemblyStatus.PENDING.value,
    )
    session.add(a)
    await session.flush()
    return a


async def _make_part_with_assembly(
    session,
    *,
    customer_id: int,
    assembly_id: int,
    serial_no: str,
    drawing_no: str,
    name: str | None = None,
) -> TPart:
    """构造一个挂在指定装配件下的子件 part（带 root_batch）。"""
    p = TPart(
        serial_no=serial_no,
        drawing_no=drawing_no,
        name=name or f"子件-{drawing_no}",
        applicant_name="测试申请人",
        quantity=2,
        request_date=date(2026, 7, 1),
        planned_delivery_date=date(2026, 7, 30),
        order_no="ON-2026-001",
        customer_id=customer_id,
        status=PartStatus.READY_TO_SHIP.value,
        location="INSPECTION_SHELF",
        assembly_id=assembly_id,
    )
    session.add(p)
    await session.flush()
    p.root_batch = await seed_root_batch(session, p)
    return p


async def _make_loose_part(
    session,
    *,
    customer_id: int,
    serial_no: str,
    drawing_no: str,
    note: str | None = None,
) -> TPart:
    """无装配体的散件 part。"""
    p = TPart(
        serial_no=serial_no,
        drawing_no=drawing_no,
        name=f"散件-{drawing_no}",
        applicant_name="测试申请人",
        quantity=2,
        request_date=date(2026, 7, 1),
        planned_delivery_date=date(2026, 7, 30),
        order_no="ON-2026-002",
        customer_id=customer_id,
        status=PartStatus.READY_TO_SHIP.value,
        location="INSPECTION_SHELF",
        note=note,
    )
    session.add(p)
    await session.flush()
    p.root_batch = await seed_root_batch(session, p)
    return p


def _make_service(session) -> DeliveryNoteService:
    return DeliveryNoteService(
        session=session,
        notes=DeliveryNoteRepository(session),
        note_events=DeliveryNoteEventRepository(session),
        counter=DeliveryNoteCounterRepository(session),
        parts=PartRepository(session),
        part_batches=PartBatchRepository(session),
        customers=CustomerRepository(session),
        workers=WorkerRepository(session),
        part_events=None,
        current_user=None,
    )


def _item(part, qty=None):
    from schema.delivery_note import DeliveryNoteAddPartsItem
    return DeliveryNoteAddPartsItem(batch_id=str(part.root_batch.id), quantity=qty)


# ============================================================
# T-merge-1: 显式 merge_assemblies=False — 装配件子件逐行输出（散件无变化）
# ============================================================
async def test_print_xlsx_explicit_merge_false_unmerged(clean_db):
    """merge_assemblies=False（显式传）→ 装配件子件与散件都逐行填表。

    2026-08-07 起默认值翻转为 True；本测试是「显式 False 走散件逐行」路径的
    回归护栏，确保在默认翻转后这条路径仍工作。
    """
    customer = await _make_l1_root(clean_db, name="法拉", prefix="F")
    asm = await _make_assembly(clean_db, customer_id=customer.id)
    child1 = await _make_part_with_assembly(
        clean_db, customer_id=customer.id, assembly_id=asm.id,
        serial_no="F9001", drawing_no="D-F9001",
    )
    child2 = await _make_part_with_assembly(
        clean_db, customer_id=customer.id, assembly_id=asm.id,
        serial_no="F9002", drawing_no="D-F9002",
    )
    loose = await _make_loose_part(
        clean_db, customer_id=customer.id,
        serial_no="F9003", drawing_no="D-F9003",
    )

    svc = _make_service(clean_db)
    note = await svc.create_draft(customer_id=str(customer.id))
    detail = await svc.add_parts(
        note_id=str(note.id),
        items=[_item(child1), _item(child2), _item(loose)],
        version=note.version,
    )

    xlsx_bytes, prefix = await svc.print_xlsx(
        note_id=str(note.id), merge_assemblies=False,
    )
    assert prefix == "F"

    wb = load_workbook(io.BytesIO(xlsx_bytes))
    ws = wb["Sheet1"]
    # 3 行：2 子件 + 1 散件，全部 unit="件"
    # 行 3 = row_index 1；行 4 = 2；行 5 = 3
    for r in (3, 4, 5):
        assert ws.cell(row=r, column=8).value == "件", (
            f"row {r} 应为 '件'，实际 {ws.cell(row=r, column=8).value!r}"
        )
    # 行 3 是 F9001 子件；行 4 是 F9002 子件；行 5 是 F9003 散件
    assert ws.cell(row=3, column=5).value == "D-F9001"
    assert ws.cell(row=4, column=5).value == "D-F9002"
    assert ws.cell(row=5, column=5).value == "D-F9003"


# ============================================================
# T-default-merge-1: 2026-08-07 — 默认 merge_assemblies=True
# ============================================================
async def test_print_xlsx_default_merges_assemblies(clean_db):
    """不传 merge_assemblies → 默认 True → 装配件子件合并为一行。"""
    customer = await _make_l1_root(clean_db, name="法拉", prefix="F")
    asm = await _make_assembly(clean_db, customer_id=customer.id)
    child1 = await _make_part_with_assembly(
        clean_db, customer_id=customer.id, assembly_id=asm.id,
        serial_no="F9101", drawing_no="D-F9101",
    )
    child2 = await _make_part_with_assembly(
        clean_db, customer_id=customer.id, assembly_id=asm.id,
        serial_no="F9102", drawing_no="D-F9102",
    )

    svc = _make_service(clean_db)
    note = await svc.create_draft(customer_id=str(customer.id))
    await svc.add_parts(
        note_id=str(note.id),
        items=[_item(child1), _item(child2)],
        version=note.version,
    )

    # 不传 merge_assemblies — 默认应为 True
    xlsx_bytes, _ = await svc.print_xlsx(note_id=str(note.id))
    wb = load_workbook(io.BytesIO(xlsx_bytes))
    ws = wb["Sheet1"]
    # 只 1 行：装配体合并行（数量 1，单位套）
    assert ws.cell(row=3, column=7).value == 1
    assert ws.cell(row=3, column=8).value == "套"
    assert ws.cell(row=4, column=1).value is None, "不应有第 2 行"


# ============================================================
# T-merge-2: merge=True — 同装配体子件合并为一行
# ============================================================
async def test_print_xlsx_merge_assemblies_true(clean_db):
    """merge_assemblies=True：同装配体的 2 子件合并为 1 行（数量 1，单位套）。"""
    customer = await _make_l1_root(clean_db, name="法拉", prefix="F")
    asm = await _make_assembly(
        clean_db, customer_id=customer.id,
        serial_no="A7001", drawing_no="DA-7001", name="装配件A",
        applicant_name="总装申请人",
    )
    child1 = await _make_part_with_assembly(
        clean_db, customer_id=customer.id, assembly_id=asm.id,
        serial_no="F9001", drawing_no="D-F9001",
    )
    child2 = await _make_part_with_assembly(
        clean_db, customer_id=customer.id, assembly_id=asm.id,
        serial_no="F9002", drawing_no="D-F9002",
    )
    loose = await _make_loose_part(
        clean_db, customer_id=customer.id,
        serial_no="F9003", drawing_no="D-F9003",
    )

    svc = _make_service(clean_db)
    note = await svc.create_draft(customer_id=str(customer.id))
    detail = await svc.add_parts(
        note_id=str(note.id),
        items=[_item(child1), _item(child2), _item(loose)],
        version=note.version,
    )

    xlsx_bytes, prefix = await svc.print_xlsx(
        note_id=str(note.id), merge_assemblies=True,
    )
    assert prefix == "F"

    wb = load_workbook(io.BytesIO(xlsx_bytes))
    ws = wb["Sheet1"]
    # 预期 2 行：
    #   行 3 = 装配体合并行（row_index=1，unit="套"，图号=DA-7001，名称=装配件A）
    #   行 4 = 散件行（row_index=2，unit="件"，图号=D-F9003）
    assert ws.cell(row=3, column=1).value == 1
    assert ws.cell(row=3, column=8).value == "套", (
        f"装配体合并行 col 8 应为 '套'，实际 {ws.cell(row=3, column=8).value!r}"
    )
    assert ws.cell(row=3, column=5).value == "DA-7001"
    assert ws.cell(row=3, column=6).value == "装配件A"
    assert ws.cell(row=3, column=7).value == 1  # quantity=1 套
    # 申请人取装配件的 applicant_name
    assert ws.cell(row=3, column=4).value == "总装申请人"

    # 行 4 = 散件
    assert ws.cell(row=4, column=1).value == 2
    assert ws.cell(row=4, column=8).value == "件"
    assert ws.cell(row=4, column=5).value == "D-F9003"


# ============================================================
# T-merge-3: merge=True 但单上无装配件 — 行为等同 merge=False
# ============================================================
async def test_print_xlsx_merge_assemblies_true_with_no_assemblies(clean_db):
    """merge_assemblies=True 但单上没有装配件子件时，退化为逐行输出散件。"""
    customer = await _make_l1_root(clean_db, name="法拉", prefix="F")
    loose1 = await _make_loose_part(
        clean_db, customer_id=customer.id,
        serial_no="F9001", drawing_no="D-F9001",
    )
    loose2 = await _make_loose_part(
        clean_db, customer_id=customer.id,
        serial_no="F9002", drawing_no="D-F9002",
    )

    svc = _make_service(clean_db)
    note = await svc.create_draft(customer_id=str(customer.id))
    detail = await svc.add_parts(
        note_id=str(note.id),
        items=[_item(loose1), _item(loose2)],
        version=note.version,
    )

    xlsx_bytes, _ = await svc.print_xlsx(
        note_id=str(note.id), merge_assemblies=True,
    )

    wb = load_workbook(io.BytesIO(xlsx_bytes))
    ws = wb["Sheet1"]
    # 2 行散件，全部 unit="件"
    assert ws.cell(row=3, column=8).value == "件"
    assert ws.cell(row=4, column=8).value == "件"
    assert ws.cell(row=3, column=5).value == "D-F9001"
    assert ws.cell(row=4, column=5).value == "D-F9002"


# ============================================================
# T-merge-4: merge=True + custom_order — 组位置 = 组内最早 batch 位次
# ============================================================
async def test_print_xlsx_merge_assemblies_group_position_respects_custom_order(clean_db):
    """合并模式下装配体行落在 custom_order 中「组内最早 batch」的位次。

    顺序：[child_a, loose, child_b]，child_a/child_b 同装配体，
    合并行应出现在 row_index=1（child_a 的位置）；散件 loose 落在 row_index=2。
    """
    customer = await _make_l1_root(clean_db, name="法拉", prefix="F")
    asm = await _make_assembly(
        clean_db, customer_id=customer.id,
        serial_no="A7001", drawing_no="DA-7001", name="装配件A",
    )
    child_a = await _make_part_with_assembly(
        clean_db, customer_id=customer.id, assembly_id=asm.id,
        serial_no="F9A01", drawing_no="D-F9A01",
    )
    child_b = await _make_part_with_assembly(
        clean_db, customer_id=customer.id, assembly_id=asm.id,
        serial_no="F9B01", drawing_no="D-F9B01",
    )
    loose = await _make_loose_part(
        clean_db, customer_id=customer.id,
        serial_no="F9001", drawing_no="D-F9001",
    )

    svc = _make_service(clean_db)
    note = await svc.create_draft(customer_id=str(customer.id))
    detail = await svc.add_parts(
        note_id=str(note.id),
        items=[_item(child_a), _item(loose), _item(child_b)],
        version=note.version,
    )

    custom_order = [
        str(child_a.root_batch.id),
        str(loose.root_batch.id),
        str(child_b.root_batch.id),
    ]
    xlsx_bytes, _ = await svc.print_xlsx(
        note_id=str(note.id),
        custom_order=custom_order,
        merge_assemblies=True,
    )

    wb = load_workbook(io.BytesIO(xlsx_bytes))
    ws = wb["Sheet1"]
    # 行 3 = 装配体合并行（group 在 child_a 位置 → row_index 1）
    assert ws.cell(row=3, column=1).value == 1
    assert ws.cell(row=3, column=5).value == "DA-7001"
    assert ws.cell(row=3, column=8).value == "套"
    # 行 4 = 散件（loose 位置 → row_index 2）
    assert ws.cell(row=4, column=1).value == 2
    assert ws.cell(row=4, column=5).value == "D-F9001"
    assert ws.cell(row=4, column=8).value == "件"


# ============================================================
# T-merge-5: merge_quantities override 装配体行数量
# ============================================================
async def test_print_xlsx_merge_quantities_override(clean_db):
    """merge_assemblies=True + merge_quantities → 装配体行数量取 override，非默认 1。"""
    customer = await _make_l1_root(clean_db, name="法拉", prefix="F")
    asm = await _make_assembly(
        clean_db, customer_id=customer.id,
        serial_no="A9001", drawing_no="DA-9001", name="装配体B",
    )
    child1 = await _make_part_with_assembly(
        clean_db, customer_id=customer.id, assembly_id=asm.id,
        serial_no="F9C01", drawing_no="D-F9C01",
    )
    child2 = await _make_part_with_assembly(
        clean_db, customer_id=customer.id, assembly_id=asm.id,
        serial_no="F9C02", drawing_no="D-F9C02",
    )

    svc = _make_service(clean_db)
    note = await svc.create_draft(customer_id=str(customer.id))
    await svc.add_parts(
        note_id=str(note.id),
        items=[_item(child1), _item(child2)],
        version=note.version,
    )

    xlsx_bytes, _ = await svc.print_xlsx(
        note_id=str(note.id),
        merge_assemblies=True,
        merge_quantities={str(asm.id): 3},
    )

    wb = load_workbook(io.BytesIO(xlsx_bytes))
    ws = wb["Sheet1"]
    # 装配体合并行 1 行，数量=3
    assert ws.cell(row=3, column=7).value == 3, (
        f"merge_quantities override 应为 3，实际 {ws.cell(row=3, column=7).value}"
    )
    assert ws.cell(row=3, column=8).value == "套"


# ============================================================
# T-merge-6: Excel 数据行高 + 列宽以模板为基线（法拉模板）
# ============================================================
# 法拉模板 Sheet1 的 A-J 原始列宽（openpyxl 实测，作为独立 oracle 硬编码在测试里：
# 若有人改模板或改 TemplateConfig，这里应当先红）
FALA_BASELINE_WIDTHS = {
    "A": 3.375,   # 序号（定宽）
    "B": 10.875,  # 订单号
    "C": 4.875,   # 分厂
    "D": 7.625,   # 申请人
    "E": 16.25,   # 编码
    "F": 29.125,  # 名称
    "G": 3.875,   # 数量（定宽）
    "H": 4.0,     # 单位（定宽）
    "I": 6.625,   # 预估交期（定宽）
    "J": 7.625,   # 备注
}
FALA_BASELINE_TOTAL = 94.25
FALA_WIDTH_BUDGET = FALA_BASELINE_TOTAL * 1.15  # = 108.3875


def _total_width(ws, letters: str) -> float:
    return sum(ws.column_dimensions[c].width or 0 for c in letters)


async def test_print_xlsx_excel_layout(clean_db):
    """数据行 25 磅；列宽以模板为下限、总宽不超 A5 预算；定宽列锁死。"""
    customer = await _make_l1_root(clean_db, name="法拉", prefix="F")
    p1 = await _make_loose_part(
        clean_db, customer_id=customer.id,
        serial_no="F9101", drawing_no="D-F9101",
    )
    p2 = await _make_loose_part(
        clean_db, customer_id=customer.id,
        serial_no="F9102", drawing_no="D-F9102",
    )

    svc = _make_service(clean_db)
    note = await svc.create_draft(customer_id=str(customer.id))
    await svc.add_parts(
        note_id=str(note.id),
        items=[_item(p1), _item(p2)],
        version=note.version,
    )

    xlsx_bytes, _ = await svc.print_xlsx(note_id=str(note.id))
    wb = load_workbook(io.BytesIO(xlsx_bytes))
    ws = wb["Sheet1"]

    # 数据行 R3-R4 行高 25
    for r in (3, 4):
        assert ws.row_dimensions[r].height == 25, (
            f"row {r} height 应为 25，实际 {ws.row_dimensions[r].height}"
        )

    # 定宽列（序号 / 数量 / 单位）必须锁死在模板基线，
    # 不能再被旧的 min_width=8 撑大；col I（预估交期）可微增至容纳日期
    for letter in ("A", "G", "H"):
        assert ws.column_dimensions[letter].width == FALA_BASELINE_WIDTHS[letter], (
            f"定宽列 {letter} 应锁死在模板基线 {FALA_BASELINE_WIDTHS[letter]}，"
            f"实际 {ws.column_dimensions[letter].width}"
        )

    # 模板列宽是下限：任何列都不得比模板更窄
    for letter, baseline in FALA_BASELINE_WIDTHS.items():
        w = ws.column_dimensions[letter].width
        assert w >= baseline, f"col {letter} width={w} 窄于模板基线 {baseline}"

    # 总宽受 A5 预算约束（这是防止横向跑到第 2 页的核心不变量）
    total = _total_width(ws, "ABCDEFGHIJ")
    assert total <= FALA_WIDTH_BUDGET + 1e-6, (
        f"总列宽 {total} 超出 A5 预算 {FALA_WIDTH_BUDGET}"
    )


# ============================================================
# T-print-A5-1: 法拉 = A5 横向 + 宽高锁一页（打开即打印）
# ============================================================
async def test_print_xlsx_page_setup_a5_landscape(clean_db):
    """法拉送货单必须自带 A5 横向 + fitToPage，用户无需手工调页面布局。"""
    customer = await _make_l1_root(clean_db, name="法拉", prefix="F")
    p = await _make_loose_part(
        clean_db, customer_id=customer.id,
        serial_no="F9201", drawing_no="D-F9201",
    )
    svc = _make_service(clean_db)
    note = await svc.create_draft(customer_id=str(customer.id))
    await svc.add_parts(
        note_id=str(note.id), items=[_item(p)], version=note.version,
    )

    xlsx_bytes, prefix = await svc.print_xlsx(note_id=str(note.id))
    assert prefix == "F"
    ws = load_workbook(io.BytesIO(xlsx_bytes))["Sheet1"]

    assert ws.page_setup.paperSize == 11, (
        f"paperSize 应为 11 (A5)，实际 {ws.page_setup.paperSize}"
    )
    assert ws.page_setup.orientation == "landscape"
    # ⚠️ 没有这个开关，Excel 会静默忽略 fitToWidth / fitToHeight
    assert ws.sheet_properties.pageSetUpPr is not None
    assert ws.sheet_properties.pageSetUpPr.fitToPage is True, (
        "pageSetUpPr.fitToPage 必须为 True，否则 fitToWidth/Height 不生效"
    )
    assert ws.page_setup.fitToWidth == 1
    assert ws.page_setup.fitToHeight == 1
    assert ws.page_setup.scale is None, "fit 模式下 scale 必须清空，否则 Excel 按 scale 出图"
    assert ws.print_area and ws.print_area.endswith("$A$1:$J$17"), (
        f"print_area 应锁定 A1:J17，实际 {ws.print_area!r}"
    )


# ============================================================
# T-print-A5-2: 分页后第 2 页同样带页面设置（copy_worksheet 不带 print_area）
# ============================================================
async def test_print_xlsx_page_setup_propagates_to_page2(clean_db):
    """11 行强制分页；第 2 页必须与第 1 页有相同的打印配置。"""
    customer = await _make_l1_root(clean_db, name="法拉", prefix="F")
    parts = []
    for i in range(1, 12):  # 11 > max_rows=10
        parts.append(await _make_loose_part(
            clean_db, customer_id=customer.id,
            serial_no=f"F93{i:02d}", drawing_no=f"D-F93{i:02d}",
        ))

    svc = _make_service(clean_db)
    note = await svc.create_draft(customer_id=str(customer.id))
    await svc.add_parts(
        note_id=str(note.id),
        items=[_item(p) for p in parts],
        version=note.version,
    )

    xlsx_bytes, _ = await svc.print_xlsx(note_id=str(note.id))
    wb = load_workbook(io.BytesIO(xlsx_bytes))
    assert "Sheet1 (2)" in wb.sheetnames, f"应分成 2 页，实际 {wb.sheetnames}"

    for name in ("Sheet1", "Sheet1 (2)"):
        ws = wb[name]
        assert ws.page_setup.paperSize == 11, f"{name} paperSize"
        assert ws.page_setup.orientation == "landscape", f"{name} orientation"
        assert ws.sheet_properties.pageSetUpPr.fitToPage is True, f"{name} fitToPage"
        assert ws.page_setup.fitToWidth == 1, f"{name} fitToWidth"
        assert ws.page_setup.fitToHeight == 1, f"{name} fitToHeight"
        # WorksheetCopy 不复制 print_area — 这条是该坑的回归护栏
        assert ws.print_area and ws.print_area.endswith("$A$1:$J$17"), (
            f"{name} print_area 缺失/错误：{ws.print_area!r}"
        )
        assert _total_width(ws, "ABCDEFGHIJ") <= FALA_WIDTH_BUDGET + 1e-6, (
            f"{name} 总列宽超预算"
        )


# ============================================================
# T-print-A5-3: 超长备注不得撑爆列宽预算（旧实现会涨到 60 字符→跑版）
# ============================================================
async def test_print_xlsx_long_note_respects_width_budget(clean_db):
    """一条超长备注不能把总宽顶出 A5；靠 shrinkToFit 保证内容仍可读。"""
    customer = await _make_l1_root(clean_db, name="法拉", prefix="F")
    p = await _make_loose_part(
        clean_db, customer_id=customer.id,
        serial_no="F9401", drawing_no="D-F9401",
        note="极长备注内容" * 30,  # 远超旧实现的 wide_max=60
    )
    svc = _make_service(clean_db)
    note = await svc.create_draft(customer_id=str(customer.id))
    await svc.add_parts(
        note_id=str(note.id), items=[_item(p)], version=note.version,
    )

    xlsx_bytes, _ = await svc.print_xlsx(note_id=str(note.id))
    ws = load_workbook(io.BytesIO(xlsx_bytes))["Sheet1"]

    total = _total_width(ws, "ABCDEFGHIJ")
    assert total <= FALA_WIDTH_BUDGET + 1e-6, (
        f"超长备注把总列宽顶到 {total}，超出预算 {FALA_WIDTH_BUDGET}"
    )
    # 备注列仍不得窄于模板基线
    assert ws.column_dimensions["J"].width >= FALA_BASELINE_WIDTHS["J"]
    # 显示不下的部分交给 shrinkToFit，而不是把列撑宽
    assert ws.cell(row=3, column=10).alignment.shrink_to_fit is True, (
        "备注列数据单元格应开启 shrinkToFit"
    )


# ============================================================
# T-print-A5-4: 路达模板补上缺失的 print_area + 恢复模板原生行高
# ============================================================
async def test_print_xlsx_luda_print_area_and_row_height(clean_db):
    """路达模板 print_area 原本为空 → Excel 会连 J-Q 空列一起打印。"""
    customer = await _make_l1_root(clean_db, name="路达", prefix="L")
    p = await _make_loose_part(
        clean_db, customer_id=customer.id,
        serial_no="L9501", drawing_no="D-L9501",
    )
    svc = _make_service(clean_db)
    note = await svc.create_draft(customer_id=str(customer.id))
    await svc.add_parts(
        note_id=str(note.id), items=[_item(p)], version=note.version,
    )

    xlsx_bytes, prefix = await svc.print_xlsx(note_id=str(note.id))
    assert prefix == "L"
    ws = load_workbook(io.BytesIO(xlsx_bytes))["杏南"]

    assert ws.print_area and ws.print_area.endswith("$A$1:$I$31"), (
        f"路达 print_area 应钉死 A1:I31（否则打印 J-Q 空列），实际 {ws.print_area!r}"
    )
    assert ws.page_setup.paperSize == 9, "路达保持 A4"
    assert ws.page_setup.orientation == "landscape"
    assert ws.sheet_properties.pageSetUpPr.fitToPage is True
    assert ws.page_setup.fitToWidth == 1
    assert ws.page_setup.fitToHeight == 0, "路达纵向允许自然翻页"
    # 模板原生 18 磅；强制 25 磅会让满页 25 行超出 A4 横向可打印高度
    assert ws.row_dimensions[5].height == 18, (
        f"路达数据行高应为模板原生 18 磅，实际 {ws.row_dimensions[5].height}"
    )


# ============================================================
# T-print-A5-4b: 预估交期日期不能显示为 "####"（列宽必须容纳 "2026/10/15"）
# ============================================================
async def test_print_xlsx_planned_delivery_date_fits(clean_db):
    """col I (预估交期) 存的是 date 对象；Excel 渲染最长 "2026/10/15"。

    模板基线 6.625 字符容纳不下 10 字符的日期字符串，会显示为 "####"。
    修复：把 col 9 加入 growable_cols，加宽上限 10 字符。
    """
    customer = await _make_l1_root(clean_db, name="法拉", prefix="F")
    # 故意挑月份 / 日期都是两位数（最长宽度 10）的日期
    p = await _make_loose_part(
        clean_db, customer_id=customer.id,
        serial_no="F9701", drawing_no="D-F9701",
    )
    svc = _make_service(clean_db)
    note = await svc.create_draft(customer_id=str(customer.id))
    await svc.add_parts(
        note_id=str(note.id), items=[_item(p)], version=note.version,
    )

    xlsx_bytes, _ = await svc.print_xlsx(note_id=str(note.id))
    ws = load_workbook(io.BytesIO(xlsx_bytes))["Sheet1"]

    col_i = ws.column_dimensions["I"].width
    # Excel 渲染 "2026/10/15" 需要约 10 字符 + 边距
    assert col_i >= 10.0, (
        f"col I (预估交期) width={col_i} 不够容纳 2026/10/15，"
        f"Excel 会显示为 ####"
    )
    assert col_i <= 12.0, (
        f"col I width={col_i} 不应涨过 grow_cap 上限 12"
    )
    assert _total_width(ws, "ABCDEFGHIJ") <= FALA_WIDTH_BUDGET + 1e-6


async def test_print_labels_xlsx_layout_unchanged(clean_db):
    """render_labels 仍走旧的 _autosize_columns；不应被送货单的新逻辑波及。"""
    customer = await _make_l1_root(clean_db, name="法拉", prefix="F")
    p = await _make_loose_part(
        clean_db, customer_id=customer.id,
        serial_no="F9601", drawing_no="D-F9601",
    )
    svc = _make_service(clean_db)
    note = await svc.create_draft(customer_id=str(customer.id))
    await svc.add_parts(
        note_id=str(note.id), items=[_item(p)], version=note.version,
    )

    labels_bytes, _ = await svc.print_labels_xlsx(note_id=str(note.id))
    ws = load_workbook(io.BytesIO(labels_bytes))["标签"]

    # 旧行为：数量 / 单位列固定 8 字符宽
    assert ws.column_dimensions["E"].width == 8
    assert ws.column_dimensions["F"].width == 8
    # 标签表不做页面设置（明确不在本次范围内）
    assert ws.sheet_properties.pageSetUpPr is None or (
        ws.sheet_properties.pageSetUpPr.fitToPage is None
    )


# ============================================================
# T-merge-7: merge_assemblies API 透传回归（构造 PrintDeliveryNoteRequest）2026-08-04 fix
# ============================================================
async def test_print_xlsx_merge_assemblies_via_api_request(clean_db):
    """通过 service.print_xlsx 用真实 merge_quantities payload 构造，确认全链路。

    等价于构造一个 PrintDeliveryNoteRequest 的键值对：
    { custom_order: [], merge_assemblies: True,
      merge_quantities: {str(asm.id): 5} }
    """
    customer = await _make_l1_root(clean_db, name="法拉", prefix="F")
    asm = await _make_assembly(
        clean_db, customer_id=customer.id,
        serial_no="A9101", drawing_no="DA-9101", name="装配件C",
    )
    child1 = await _make_part_with_assembly(
        clean_db, customer_id=customer.id, assembly_id=asm.id,
        serial_no="F9D01", drawing_no="D-F9D01",
    )

    svc = _make_service(clean_db)
    note = await svc.create_draft(customer_id=str(customer.id))
    await svc.add_parts(
        note_id=str(note.id),
        items=[_item(child1)],
        version=note.version,
    )

    # 模拟前端 merge_quantities payload（dict[str, int]）
    merge_qty = {str(asm.id): 5}
    xlsx_bytes, _ = await svc.print_xlsx(
        note_id=str(note.id),
        merge_assemblies=True,
        merge_quantities=merge_qty,
    )

    wb = load_workbook(io.BytesIO(xlsx_bytes))
    ws = wb["Sheet1"]
    assert ws.cell(row=3, column=7).value == 5
    assert ws.cell(row=3, column=8).value == "套"


# ============================================================
# T-merge-8: 装配体合并行 order_no 取 TAssembly.order_no
#            + get_with_parts 返回的 LineItem 带 assembly_order_no
# ============================================================
async def test_print_xlsx_assembly_order_no(clean_db):
    """装配体合并行打印 order_no 取 TAssembly.order_no（非空），
    且 getNote 返回的 detail.line_items 带 assembly_order_no 字段。"""
    customer = await _make_l1_root(clean_db, name="法拉", prefix="F")
    asm = await _make_assembly(
        clean_db, customer_id=customer.id,
        serial_no="A8001", drawing_no="DA-8001", name="装配体D",
    )
    asm.order_no = "ON-ASM-008"
    await clean_db.flush()
    child1 = await _make_part_with_assembly(
        clean_db, customer_id=customer.id, assembly_id=asm.id,
        serial_no="F9E01", drawing_no="D-F9E01",
    )
    child2 = await _make_part_with_assembly(
        clean_db, customer_id=customer.id, assembly_id=asm.id,
        serial_no="F9E02", drawing_no="D-F9E02",
    )

    svc = _make_service(clean_db)
    note = await svc.create_draft(customer_id=str(customer.id))
    await svc.add_parts(
        note_id=str(note.id),
        items=[_item(child1), _item(child2)],
        version=note.version,
    )

    # 验证 get_with_parts 返回的 LineItem 带 assembly_order_no
    detail = await svc.get_with_parts(str(note.id))
    assert detail.line_items[0].assembly_order_no == "ON-ASM-008", (
        f"LineItem.assembly_order_no 应为 'ON-ASM-008'，"
        f"实际 {detail.line_items[0].assembly_order_no!r}"
    )

    # 验证合并打印 order_no 列
    xlsx_bytes, _ = await svc.print_xlsx(
        note_id=str(note.id),
        merge_assemblies=True,
    )
    wb = load_workbook(io.BytesIO(xlsx_bytes))
    ws = wb["Sheet1"]
    # 装配体合并行 1 行，col 2 = order_no
    assert ws.cell(row=3, column=2).value == "ON-ASM-008", (
        f"装配体合并行 order_no 应为 'ON-ASM-008'，"
        f"实际 {ws.cell(row=3, column=2).value!r}"
    )