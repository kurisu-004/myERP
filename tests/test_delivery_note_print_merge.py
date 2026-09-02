"""送货单打印 merge_assemblies 测试（2026-08-04 装配件合并）。

覆盖：
- merge=False（默认）：装配件子件与散件逐行输出；
- merge=True：同装配体子件合并为一行（数量 1，单位套，显示总装图号）；
- merge=True 但单上无装配件：行为等同 merge=False（行数 = 散件数）。
"""
from __future__ import annotations

import io
from datetime import date

import pytest
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
    system_delivery_date: date | None = date(2026, 8, 5),
) -> TAssembly:
    """直接构造一个 PENDING 状态的装配件（绕过 AssemblyService.create_assembly）。

    2026-09-02 新增 ``system_delivery_date`` 参数（默认非空，让 col I 列宽
    测试仍有真实内容参与测量）；保持 ``planned_delivery_date`` 既有默认，
    调用方可独立覆盖两个字段做对照。
    """
    a = TAssembly(
        serial_no=serial_no,
        drawing_no=drawing_no,
        name=name,
        applicant_name=applicant_name,
        customer_id=customer_id,
        request_date=date(2026, 7, 1),
        planned_delivery_date=planned_delivery_date,
        system_delivery_date=system_delivery_date,
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
    system_delivery_date: date | None = date(2026, 7, 25),
) -> TPart:
    """构造一个挂在指定装配件下的子件 part（带 root_batch）。

    2026-09-02 新增 ``system_delivery_date`` 参数；默认非空，
    调用方传 ``None`` 模拟未填系统交期。
    """
    p = TPart(
        serial_no=serial_no,
        drawing_no=drawing_no,
        name=name or f"子件-{drawing_no}",
        applicant_name="测试申请人",
        quantity=2,
        request_date=date(2026, 7, 1),
        planned_delivery_date=date(2026, 7, 30),
        system_delivery_date=system_delivery_date,
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
    system_delivery_date: date | None = date(2026, 7, 25),
) -> TPart:
    """无装配体的散件 part。

    2026-09-02 新增 ``system_delivery_date`` 参数；默认非空，
    调用方传 ``None`` 模拟未填系统交期。
    """
    p = TPart(
        serial_no=serial_no,
        drawing_no=drawing_no,
        name=f"散件-{drawing_no}",
        applicant_name="测试申请人",
        quantity=2,
        request_date=date(2026, 7, 1),
        planned_delivery_date=date(2026, 7, 30),
        system_delivery_date=system_delivery_date,
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


def _item_batch(batch, qty=None):
    """直接用 batch 对象（绕过 part.root_batch），供拆分后多个批次入单用。"""
    from schema.delivery_note import DeliveryNoteAddPartsItem
    return DeliveryNoteAddPartsItem(batch_id=str(batch.id), quantity=qty)


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
# T-print-A5-4b: 预估交期列不能显示为 "####"
# ============================================================
async def test_print_xlsx_planned_delivery_date_fits(clean_db):
    """col I (预估交期) 写 ``M月D日`` 字符串（commit afc089a 后）。

    模板基线 6.625 单位 < 实测最长 ``12月31日`` 8 单位，会显示为 ``####``。
    修复：把 col 9 加入 growable_cols，``grow_cap[9]=1.5`` → 列宽 ≈ 8.125 单位，
    能装下 ``12月31日``；超出走 shrink_to_fit / Excel 默认截断兜底。

    2026-08-08 改：旧测试假设 date 对象渲染 ``2026/10/15``，col I ≥ 10.0；
    新规范要求 ``grow_cap=1.5`` 紧凑（col I 停在 ≤ 9.0 单位）。
    """
    customer = await _make_l1_root(clean_db, name="法拉", prefix="F")
    # 故意挑月份 / 日期都是两位数（M月D日 实测最宽 8 单位）
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
    # M月D日 字符串最长 "12月31日" ≈ 8 单位；列宽 ≥ 7 单位才能装下
    assert col_i >= 7.0, (
        f"col I (预估交期) width={col_i} 不够容纳 '12月31日'，"
        f"Excel 会显示为 ####"
    )
    # 2026-08-08：grow_cap[9]=1.5 → col I 实测稳定 8.125，不应涨过 9
    assert col_i <= 9.0, (
        f"col I width={col_i} 不应涨过 grow_cap 上限 1.5 + baseline 6.625"
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


# ============================================================
# 2026-08-07：line_item_ids 标签勾选子集（标签独立导出支持部分行）
# ============================================================
async def test_print_labels_line_item_ids_subset(clean_db):
    """2026-08-07：3 零件传 2 个 line_item_ids → 标签 sheet 只含 2 行 + 表头。"""
    customer = await _make_l1_root(clean_db, name="法拉", prefix="F")
    p1 = await _make_loose_part(
        clean_db, customer_id=customer.id,
        serial_no="FA001", drawing_no="D-FA001",
    )
    p2 = await _make_loose_part(
        clean_db, customer_id=customer.id,
        serial_no="FA002", drawing_no="D-FA002",
    )
    p3 = await _make_loose_part(
        clean_db, customer_id=customer.id,
        serial_no="FA003", drawing_no="D-FA003",
    )

    svc = _make_service(clean_db)
    note = await svc.create_draft(customer_id=str(customer.id))
    await svc.add_parts(
        note_id=str(note.id),
        items=[_item(p1), _item(p2), _item(p3)],
        version=note.version,
    )

    # 只勾 p1、p3
    line_item_ids = [str(p1.root_batch.id), str(p3.root_batch.id)]
    labels_bytes, _ = await svc.print_labels_xlsx(
        note_id=str(note.id),
        line_item_ids=line_item_ids,
    )
    ws = load_workbook(io.BytesIO(labels_bytes))["标签"]
    # max_row = 1 (表头) + 2 (数据) = 3
    assert ws.max_row == 3, (
        f"应只生成 2 行数据 + 1 表头 = 3，实际 {ws.max_row}"
    )
    drawing_values = [ws.cell(row=r, column=4).value for r in (2, 3)]
    assert drawing_values == ["D-FA001", "D-FA003"], (
        f"应只含 D-FA001 + D-FA003，实际 {drawing_values!r}"
    )


async def test_print_labels_line_item_ids_preserves_custom_order(clean_db):
    """2026-08-07：line_item_ids 与 custom_order 正交——custom_order 定顺序（须全量），
    line_item_ids 裁成员；裁后行顺序与 custom_order 一致。"""
    customer = await _make_l1_root(clean_db, name="法拉", prefix="F")
    p1 = await _make_loose_part(
        clean_db, customer_id=customer.id,
        serial_no="FB001", drawing_no="D-FB001",
    )
    p2 = await _make_loose_part(
        clean_db, customer_id=customer.id,
        serial_no="FB002", drawing_no="D-FB002",
    )
    p3 = await _make_loose_part(
        clean_db, customer_id=customer.id,
        serial_no="FB003", drawing_no="D-FB003",
    )

    svc = _make_service(clean_db)
    note = await svc.create_draft(customer_id=str(customer.id))
    await svc.add_parts(
        note_id=str(note.id),
        items=[_item(p1), _item(p2), _item(p3)],
        version=note.version,
    )

    # custom_order 倒序（仍全量），line_item_ids 子集含全部 → 行顺序 = 倒序
    custom_order = [str(p3.root_batch.id), str(p2.root_batch.id), str(p1.root_batch.id)]
    line_item_ids = [str(p1.root_batch.id), str(p2.root_batch.id), str(p3.root_batch.id)]
    labels_bytes, _ = await svc.print_labels_xlsx(
        note_id=str(note.id),
        custom_order=custom_order,
        line_item_ids=line_item_ids,
    )
    ws = load_workbook(io.BytesIO(labels_bytes))["标签"]
    # 行 2 = p3, 行 3 = p2, 行 4 = p1
    assert ws.cell(row=2, column=4).value == "D-FB003"
    assert ws.cell(row=3, column=4).value == "D-FB002"
    assert ws.cell(row=4, column=4).value == "D-FB001"


async def test_print_labels_line_item_ids_unknown_raises(clean_db):
    """2026-08-07：line_item_ids 含非本单 batch id → 422 BIZ_DELIVERY_PRINT_BAD_ORDER。"""
    from core.error_code import ErrCode
    from core.exception import BizError

    customer = await _make_l1_root(clean_db, name="法拉", prefix="F")
    p1 = await _make_loose_part(
        clean_db, customer_id=customer.id,
        serial_no="FC001", drawing_no="D-FC001",
    )

    svc = _make_service(clean_db)
    note = await svc.create_draft(customer_id=str(customer.id))
    await svc.add_parts(
        note_id=str(note.id),
        items=[_item(p1)],
        version=note.version,
    )

    # 999999999 不属于本单
    with pytest.raises(BizError) as ei:
        await svc.print_labels_xlsx(
            note_id=str(note.id),
            line_item_ids=["999999999"],
        )
    assert ei.value.code == ErrCode.BIZ_DELIVERY_PRINT_BAD_ORDER
    assert "999999999" in ei.value.message


async def test_print_labels_line_item_ids_empty_raises(clean_db):
    """2026-08-07：line_item_ids=[] 视为非法（区分 None=全打）→ 400 BIZ_INVALID_VALUE。"""
    from core.error_code import ErrCode
    from core.exception import BizError

    customer = await _make_l1_root(clean_db, name="法拉", prefix="F")
    p1 = await _make_loose_part(
        clean_db, customer_id=customer.id,
        serial_no="FD001", drawing_no="D-FD001",
    )

    svc = _make_service(clean_db)
    note = await svc.create_draft(customer_id=str(customer.id))
    await svc.add_parts(
        note_id=str(note.id),
        items=[_item(p1)],
        version=note.version,
    )

    with pytest.raises(BizError) as ei:
        await svc.print_labels_xlsx(
            note_id=str(note.id),
            line_item_ids=[],
        )
    assert ei.value.code == ErrCode.BIZ_INVALID_VALUE


async def test_print_labels_line_item_ids_merged_assembly(clean_db):
    """2026-08-07：合并模式下传子件 batch id → 装配体合并为 1 行「套」。"""
    customer = await _make_l1_root(clean_db, name="法拉", prefix="F")
    asm = await _make_assembly(
        clean_db, customer_id=customer.id,
        serial_no="A8801", drawing_no="DA-8801", name="合并件X",
    )
    child1 = await _make_part_with_assembly(
        clean_db, customer_id=customer.id, assembly_id=asm.id,
        serial_no="F8A01", drawing_no="D-F8A01",
    )
    child2 = await _make_part_with_assembly(
        clean_db, customer_id=customer.id, assembly_id=asm.id,
        serial_no="F8A02", drawing_no="D-F8A02",
    )
    loose = await _make_loose_part(
        clean_db, customer_id=customer.id,
        serial_no="F8A99", drawing_no="D-F8A99",
    )

    svc = _make_service(clean_db)
    note = await svc.create_draft(customer_id=str(customer.id))
    await svc.add_parts(
        note_id=str(note.id),
        items=[_item(child1), _item(child2), _item(loose)],
        version=note.version,
    )

    # 合并模式 + 只勾装配体两个子件（不勾散件）→ 标签 sheet 只剩 1 行「套」
    line_item_ids = [str(child1.root_batch.id), str(child2.root_batch.id)]
    labels_bytes, _ = await svc.print_labels_xlsx(
        note_id=str(note.id),
        merge_assemblies=True,
        merge_quantities={str(asm.id): 1},
        line_item_ids=line_item_ids,
    )
    ws = load_workbook(io.BytesIO(labels_bytes))["标签"]
    assert ws.max_row == 2, f"应 1 表头 + 1 合并行 = 2，实际 {ws.max_row}"
    # 合并行 unit = "套"
    assert ws.cell(row=2, column=6).value == "套"
    # 合并行 drawing_no = 装配件 drawing_no
    assert ws.cell(row=2, column=4).value == "DA-8801"


async def test_print_labels_line_item_ids_none_legacy_behavior(clean_db):
    """2026-08-07：line_item_ids=None（默认）→ 行为与旧版完全一致（回归护栏）。

    本测试与 test_print_labels_xlsx_layout_unchanged 互证：默认 None 路径
    不能因新参数破坏既有行为。
    """
    customer = await _make_l1_root(clean_db, name="法拉", prefix="F")
    p1 = await _make_loose_part(
        clean_db, customer_id=customer.id,
        serial_no="FE001", drawing_no="D-FE001",
    )
    p2 = await _make_loose_part(
        clean_db, customer_id=customer.id,
        serial_no="FE002", drawing_no="D-FE002",
    )

    svc = _make_service(clean_db)
    note = await svc.create_draft(customer_id=str(customer.id))
    await svc.add_parts(
        note_id=str(note.id),
        items=[_item(p1), _item(p2)],
        version=note.version,
    )

    labels_bytes, _ = await svc.print_labels_xlsx(note_id=str(note.id))
    ws = load_workbook(io.BytesIO(labels_bytes))["标签"]
    # 2 散件全打
    assert ws.max_row == 3
    assert ws.column_dimensions["E"].width == 8  # 旧行为：数量列固定 8


# ============================================================
# 2026-08-07：同 part 多批次折叠（_split 产生同 part 同送货单）
# 永远开启；与 merge_assemblies 正交。
# ============================================================
async def _make_part_service(session) -> "PartService":  # type: ignore[name-defined]
    """构造 PartService（拆分批次用）。"""
    from repository.part import PartRepository
    from repository.part_batch import PartBatchRepository
    from repository.part_event import PartEventRepository
    from repository.process import ProcessRepository
    from repository.serial_counter import SerialCounterRepository
    from repository.shelf import ShelfRepository
    from repository.shelf_process import ShelfProcessRepository
    from repository.work_type import WorkTypeRepository
    from repository.work_type_process import WorkTypeProcessRepository
    from repository.customer import CustomerRepository
    from repository.worker import WorkerRepository
    from service.part import PartService
    return PartService(
        parts=PartRepository(session),
        part_batches=PartBatchRepository(session),
        customers=CustomerRepository(session),
        workers=WorkerRepository(session),
        events=PartEventRepository(session),
        serial_counters=SerialCounterRepository(session),
        shelves=ShelfRepository(session),
        processes=ProcessRepository(session),
        work_types=WorkTypeRepository(session),
        work_type_process=WorkTypeProcessRepository(session),
        shelf_process_repo=ShelfProcessRepository(session),
        broadcaster=None,
        event_broadcaster=None,
    )


async def _split_root_into(
    session, *, part, root_batch, splits: list[int],
) -> list:
    """把 root_batch 按 splits 列表顺序拆成 N 个新批次（依次扣减 root）。

    例如 splits=[2,3] 把 qty=6 的 root 拆成：root→1, new1→2, new2→3。
    返回 [root, new1, new2]。
    """
    part_svc = await _make_part_service(session)
    batches = [root_batch]
    for q in splits:
        new_batches = await part_svc.split_batch(part.id, batch_id=root_batch.id, quantity=q)
        # new_batches 是最新批次列表（按 batch_no 升序）；root 还在但 qty 已减
        for nb in new_batches:
            if nb.id not in (b.id for b in batches):
                batches.append(nb)
        # 找到 root 的最新对象
        for nb in new_batches:
            if nb.batch_no == 1:
                root_batch = nb
                break
    return batches


async def test_print_xlsx_same_part_split_batches_merge(clean_db):
    """同 part 的 2 批次在同送货单 → 打印 1 行（quantity = sum，unit = "件"）。

    2026-08-07 同 part 折叠：永远开启，与 merge_assemblies 无关。
    """
    customer = await _make_l1_root(clean_db, name="法拉", prefix="F")
    part = await _make_loose_part(
        clean_db, customer_id=customer.id,
        serial_no="F9501", drawing_no="D-F9501",
    )
    # 拆出 1 个 qty=1 批次（root 变 qty=1）
    split_batches = await _split_root_into(
        clean_db, part=part, root_batch=part.root_batch, splits=[1],
    )
    assert len(split_batches) == 2
    root_b, new_b = split_batches[0], split_batches[1]
    # 两个批次分别入单（用 _item_batch 避免重复 root_batch.id）
    note_svc = _make_service(clean_db)
    note = await note_svc.create_draft(customer_id=str(customer.id))
    await note_svc.add_parts(
        note_id=str(note.id),
        items=[_item_batch(root_b, qty=1),
               _item_batch(new_b, qty=1)],
        version=note.version,
    )

    xlsx_bytes, _ = await note_svc.print_xlsx(note_id=str(note.id))
    wb = load_workbook(io.BytesIO(xlsx_bytes))
    ws = wb["Sheet1"]
    # 折叠后只剩 1 行；起始 row=3（法拉模板 start_row=3）
    assert ws.cell(row=3, column=1).value == 1  # row_index=1
    assert ws.cell(row=3, column=5).value == "D-F9501"  # drawing_no
    assert ws.cell(row=3, column=6).value == "散件-D-F9501"  # name
    assert ws.cell(row=3, column=7).value == 2  # quantity = 1+1 求和
    assert ws.cell(row=3, column=8).value == "件"
    # 不应有第 2 行（row 4 col 1 应为 None）
    assert ws.cell(row=4, column=1).value is None, "应折叠为 1 行"


async def test_print_xlsx_same_part_mixed_with_unrelated_loose(clean_db):
    """同 part 2 批（折叠）+ 1 散件 = 2 行。"""
    customer = await _make_l1_root(clean_db, name="法拉", prefix="F")
    split_part = await _make_loose_part(
        clean_db, customer_id=customer.id,
        serial_no="F9601", drawing_no="D-F9601",
    )
    loose = await _make_loose_part(
        clean_db, customer_id=customer.id,
        serial_no="F9602", drawing_no="D-F9602",
    )
    split_batches = await _split_root_into(
        clean_db, part=split_part, root_batch=split_part.root_batch, splits=[1],
    )
    root_b, new_b = split_batches[0], split_batches[1]

    note_svc = _make_service(clean_db)
    note = await note_svc.create_draft(customer_id=str(customer.id))
    # split_part root + new 各 qty=1；loose 整批入单
    await note_svc.add_parts(
        note_id=str(note.id),
        items=[_item_batch(root_b, qty=1),
               _item_batch(new_b, qty=1),
               _item(loose)],
        version=note.version,
    )

    xlsx_bytes, _ = await note_svc.print_xlsx(note_id=str(note.id))
    ws = load_workbook(io.BytesIO(xlsx_bytes))["Sheet1"]
    # 2 行：折叠后的 split_part + loose
    assert ws.cell(row=4, column=1).value is not None, "应有第 2 行"
    # 折叠行的 quantity = 2（split_part 两批之和）
    split_rows = [r for r in range(3, 5) if ws.cell(row=r, column=5).value == "D-F9601"]
    assert len(split_rows) == 1
    assert ws.cell(row=split_rows[0], column=7).value == 2
    # loose 行 quantity = 2
    loose_rows = [r for r in range(3, 5) if ws.cell(row=r, column=5).value == "D-F9602"]
    assert len(loose_rows) == 1
    assert ws.cell(row=loose_rows[0], column=7).value == 2


async def test_print_xlsx_same_part_with_assembly_merge(clean_db):
    """折叠在装配合并之前：装配体子件被拆 2 批，merge=True → 1 行（套）。"""
    customer = await _make_l1_root(clean_db, name="法拉", prefix="F")
    asm = await _make_assembly(
        clean_db, customer_id=customer.id,
        serial_no="A7002", drawing_no="DA-7002", name="装配件B",
    )
    child = await _make_part_with_assembly(
        clean_db, customer_id=customer.id, assembly_id=asm.id,
        serial_no="F9701", drawing_no="D-F9701",
    )
    # 拆出 1 个新批次（child qty=2 → root qty=1, new qty=1）
    split_batches = await _split_root_into(
        clean_db, part=child, root_batch=child.root_batch, splits=[1],
    )
    root_b, new_b = split_batches[0], split_batches[1]

    note_svc = _make_service(clean_db)
    note = await note_svc.create_draft(customer_id=str(customer.id))
    await note_svc.add_parts(
        note_id=str(note.id),
        items=[_item_batch(root_b, qty=1),
               _item_batch(new_b, qty=1)],
        version=note.version,
    )

    xlsx_bytes, _ = await note_svc.print_xlsx(
        note_id=str(note.id), merge_assemblies=True,
    )
    ws = load_workbook(io.BytesIO(xlsx_bytes))["Sheet1"]
    # 1 行：装配体合并行；折叠后子件只 1 行，assembly merge 把它转成"套"行
    assert ws.cell(row=3, column=5).value == "DA-7002"
    assert ws.cell(row=3, column=6).value == "装配件B"
    assert ws.cell(row=3, column=7).value == 1  # quantity=1 套（默认 override）
    assert ws.cell(row=3, column=8).value == "套"
    assert ws.cell(row=4, column=1).value is None, "折叠 + 装配合并 = 1 行"


async def test_print_xlsx_custom_order_with_asm_merge_same_part_split(clean_db):
    """2026-08-07 bugfix：合并一套 + 装配件子件拆批 → custom_order 用代表 id 应通过。

    前端 PrintPreviewDialog.onConfirm 在 asm-merge 分支枚举装配件子件时用未折叠的
    line_items，会把同 part 的非代表 batch id 也推入 custom_order；后端 rep-id 校验 422。
    修复后前端应只用 foldSamePart 的折叠结果（每 part 仅代表 id）。本测试模拟修复后前端
    的 custom_order 形态（仅 rep id），确认后端接受并产出正确的 asm 合并行。
    """
    customer = await _make_l1_root(clean_db, name="法拉", prefix="F")
    asm = await _make_assembly(
        clean_db, customer_id=customer.id,
        serial_no="A7101", drawing_no="DA-7101", name="装配件C",
    )
    # 装配件下 2 子件
    child_a = await _make_part_with_assembly(
        clean_db, customer_id=customer.id, assembly_id=asm.id,
        serial_no="F9801", drawing_no="D-F9801",
    )
    child_b = await _make_part_with_assembly(
        clean_db, customer_id=customer.id, assembly_id=asm.id,
        serial_no="F9802", drawing_no="D-F9802",
    )
    # 拆 child_a：qty=2 → root(qty=1) + new(qty=1)
    split_a = await _split_root_into(
        clean_db, part=child_a, root_batch=child_a.root_batch, splits=[1],
    )
    a_root, a_new = split_a[0], split_a[1]
    # child_a 两批 + child_b 一批都入单
    note_svc = _make_service(clean_db)
    note = await note_svc.create_draft(customer_id=str(customer.id))
    await note_svc.add_parts(
        note_id=str(note.id),
        items=[_item_batch(a_root, qty=1),
               _item_batch(a_new, qty=1),
               _item_batch(child_b.root_batch)],
        version=note.version,
    )

    # 模拟修复后前端：custom_order = [a 的代表 id, b 的代表 id]
    # a 的代表 id = a_root.id (batch_no=1，最小 b.id)
    # b 的代表 id = child_b.root_batch.id (唯一一批)
    custom_order = [str(a_root.id), str(child_b.root_batch.id)]
    xlsx_bytes, _ = await note_svc.print_xlsx(
        note_id=str(note.id),
        custom_order=custom_order,
        merge_assemblies=True,
    )
    ws = load_workbook(io.BytesIO(xlsx_bytes))["Sheet1"]
    # 折叠 + 装配合并 → 1 行 asm（quantity 默认 1，unit 套）
    assert ws.cell(row=3, column=5).value == "DA-7101"
    assert ws.cell(row=3, column=6).value == "装配件C"
    assert ws.cell(row=3, column=7).value == 1
    assert ws.cell(row=3, column=8).value == "套"
    assert ws.cell(row=4, column=1).value is None, "折叠 + 装配合并 = 1 行"


async def test_print_xlsx_custom_order_asm_merge_non_rep_id_rejected(clean_db):
    """反例：custom_order 含同 part 非代表 batch id → 422。

    这是前端 bug 触发的错误形态；锁定后端行为作为回归护栏，防止后续改动放松校验。
    """
    customer = await _make_l1_root(clean_db, name="法拉", prefix="F")
    asm = await _make_assembly(
        clean_db, customer_id=customer.id,
        serial_no="A7102", drawing_no="DA-7102", name="装配件D",
    )
    child = await _make_part_with_assembly(
        clean_db, customer_id=customer.id, assembly_id=asm.id,
        serial_no="F9901", drawing_no="D-F9901",
    )
    split = await _split_root_into(
        clean_db, part=child, root_batch=child.root_batch, splits=[1],
    )
    a_root, a_new = split[0], split[1]
    note_svc = _make_service(clean_db)
    note = await note_svc.create_draft(customer_id=str(customer.id))
    await note_svc.add_parts(
        note_id=str(note.id),
        items=[_item_batch(a_root, qty=1),
               _item_batch(a_new, qty=1)],
        version=note.version,
    )
    # 发非代表 id → 422
    from core.exception import BizError
    from core.error_code import ErrCode
    with pytest.raises(BizError) as ei:
        await note_svc.print_xlsx(
            note_id=str(note.id),
            custom_order=[str(a_new.id)],  # 非代表
            merge_assemblies=True,
        )
    assert ei.value.code == ErrCode.BIZ_DELIVERY_PRINT_BAD_ORDER
    assert "已合并" in ei.value.message or "代表" in ei.value.message


async def test_print_xlsx_same_part_split_merge_quantity_sum_correctness(clean_db):
    """3 批 quantity 各 1/2/3 → 合并后 quantity = 6。

    显式验证求和正确性，避免 off-by-one / 漏批 bug。
    """
    customer = await _make_l1_root(clean_db, name="法拉", prefix="F")
    # 直接构造 qty=6 的 part（绕过 _make_loose_part 的 qty=2 默认值）
    part = TPart(
        serial_no="F9801", drawing_no="D-F9801",
        name="sum-correctness", applicant_name="测试申请人",
        quantity=6,
        request_date=date(2026, 7, 1),
        planned_delivery_date=date(2026, 7, 30),
        order_no="ON-2026-SUM",
        customer_id=customer.id,
        status=PartStatus.READY_TO_SHIP.value,
        location="INSPECTION_SHELF",
    )
    clean_db.add(part)
    await clean_db.flush()
    from tests.conftest import seed_root_batch
    root_batch = await seed_root_batch(clean_db, part)
    assert root_batch.quantity == 6

    # 把 qty=6 的 root_batch 拆成 3 个：root(qty=1) + new1(qty=2) + new2(qty=3)
    split_batches = await _split_root_into(
        clean_db, part=part, root_batch=root_batch, splits=[2, 3],
    )
    quantities = sorted(b.quantity for b in split_batches)
    assert quantities == [1, 2, 3], f"拆批结果 {quantities} 不等于 [1,2,3]"
    root_b, new1_b, new2_b = split_batches[0], split_batches[1], split_batches[2]

    note_svc = _make_service(clean_db)
    note = await note_svc.create_draft(customer_id=str(customer.id))
    await note_svc.add_parts(
        note_id=str(note.id),
        items=[
            _item_batch(root_b, qty=1),
            _item_batch(new1_b, qty=2),
            _item_batch(new2_b, qty=3),
        ],
        version=note.version,
    )

    xlsx_bytes, _ = await note_svc.print_xlsx(note_id=str(note.id))
    ws = load_workbook(io.BytesIO(xlsx_bytes))["Sheet1"]
    # 折叠后 1 行，quantity = 1+2+3 = 6
    assert ws.cell(row=3, column=5).value == "D-F9801"
    assert ws.cell(row=3, column=7).value == 6, (
        f"求和应为 6，实际 {ws.cell(row=3, column=7).value!r}"
    )
    assert ws.cell(row=3, column=8).value == "件"
    assert ws.cell(row=4, column=1).value is None


async def test_print_xlsx_custom_order_with_non_representative_id_rejected(clean_db):
    """custom_order 含同 part 的非代表 batch id → 422 + "已合并" 提示。"""
    customer = await _make_l1_root(clean_db, name="法拉", prefix="F")
    part = await _make_loose_part(
        clean_db, customer_id=customer.id,
        serial_no="F9901", drawing_no="D-F9901",
    )
    split_batches = await _split_root_into(
        clean_db, part=part, root_batch=part.root_batch, splits=[1],
    )
    root_b, new_b = split_batches[0], split_batches[1]

    note_svc = _make_service(clean_db)
    note = await note_svc.create_draft(customer_id=str(customer.id))
    await note_svc.add_parts(
        note_id=str(note.id),
        items=[_item_batch(root_b, qty=1),
               _item_batch(new_b, qty=1)],
        version=note.version,
    )

    # 找出"非代表 batch id"（即 batch_no != 1 的那个）
    detail = await note_svc.get_with_parts(str(note.id))
    non_rep = [li for li in detail.line_items if li.batch_no != 1][0]
    from core.exception import BizError
    from core.error_code import ErrCode
    with pytest.raises(BizError) as ei:
        await note_svc.print_xlsx(
            note_id=str(note.id),
            custom_order=[str(non_rep.id)],  # 雪花 ID 入参须 str（CLAUDE.md §3）
        )
    assert ei.value.code == ErrCode.BIZ_DELIVERY_PRINT_BAD_ORDER
    assert "已合并" in ei.value.message or "代表" in ei.value.message


async def test_print_xlsx_custom_order_missing_representative_raises(clean_db):
    """custom_order 漏掉代表 batch id → 422 + "漏掉" 提示。"""
    customer = await _make_l1_root(clean_db, name="法拉", prefix="F")
    p1 = await _make_loose_part(
        clean_db, customer_id=customer.id,
        serial_no="F9001", drawing_no="D-F9001",
    )
    p2 = await _make_loose_part(
        clean_db, customer_id=customer.id,
        serial_no="F9002", drawing_no="D-F9002",
    )

    note_svc = _make_service(clean_db)
    note = await note_svc.create_draft(customer_id=str(customer.id))
    await note_svc.add_parts(
        note_id=str(note.id),
        items=[_item(p1), _item(p2)],
        version=note.version,
    )
    detail = await note_svc.get_with_parts(str(note.id))
    # IdStrNonNull 在 Python 里仍是 int（CLAUDE.md §3），不要 str() 转换比较
    p1_rep = [li for li in detail.line_items if int(li.part_id) == p1.id][0]
    # 只发 p1 的代表 id（漏掉 p2）→ 422
    from core.exception import BizError
    from core.error_code import ErrCode
    with pytest.raises(BizError) as ei:
        await note_svc.print_xlsx(
            note_id=str(note.id),
            custom_order=[str(p1_rep.id)],  # 雪花 ID 入参须 str
        )
    assert ei.value.code == ErrCode.BIZ_DELIVERY_PRINT_BAD_ORDER
    assert "漏掉" in ei.value.message


async def test_print_labels_xlsx_same_part_split_batches_merge(clean_db):
    """标签路径同样折叠：同 part 2 批 → 1 行，quantity 求和。"""
    customer = await _make_l1_root(clean_db, name="法拉", prefix="F")
    part = await _make_loose_part(
        clean_db, customer_id=customer.id,
        serial_no="FA001", drawing_no="D-FA001",
    )
    split_batches = await _split_root_into(
        clean_db, part=part, root_batch=part.root_batch, splits=[1],
    )
    root_b, new_b = split_batches[0], split_batches[1]

    note_svc = _make_service(clean_db)
    note = await note_svc.create_draft(customer_id=str(customer.id))
    await note_svc.add_parts(
        note_id=str(note.id),
        items=[_item_batch(root_b, qty=1),
               _item_batch(new_b, qty=1)],
        version=note.version,
    )

    labels_bytes, _ = await note_svc.print_labels_xlsx(note_id=str(note.id))
    ws = load_workbook(io.BytesIO(labels_bytes))["标签"]
    # 表头 + 1 行折叠 = max_row=2
    assert ws.max_row == 2, (
        f"折叠后应 1 数据行（max_row=2），实际 max_row={ws.max_row}"
    )
    # 数量列 (col 5) = 2
    assert ws.cell(row=2, column=5).value == 2
    # 单位列 (col 6) = "件"
    assert ws.cell(row=2, column=6).value == "件"


# ============================================================
# 2026-08-07：打印交期列格式「M月D日」（无前导零）
# 2026-09-02：来源改为系统交期（t_part.system_delivery_date / t_assembly.system_delivery_date）；
#   NULL 时留空，不再回退到 planned_delivery_date。
# ============================================================
async def test_print_xlsx_loose_part_system_delivery_date_format(clean_db):
    """散件行的交期列应取 ``t_part.system_delivery_date``（2026-09-02 改来源）。

    _make_loose_part 默认 ``system_delivery_date=date(2026, 7, 25)``
    而 ``planned_delivery_date=date(2026, 7, 30)``——两者刻意不同：
    断言 col 9 == "7月25日"（系统交期），并追加 ``!= "7月30日"`` 证明
    计划交期未泄漏到打印列。
    """
    customer = await _make_l1_root(clean_db, name="法拉", prefix="F")
    loose = await _make_loose_part(
        clean_db, customer_id=customer.id,
        serial_no="F7001", drawing_no="D-F7001",
    )
    note_svc = _make_service(clean_db)
    note = await note_svc.create_draft(customer_id=str(customer.id))
    await note_svc.add_parts(
        note_id=str(note.id),
        items=[_item(loose)],
        version=note.version,
    )

    xlsx_bytes, _ = await note_svc.print_xlsx(note_id=str(note.id))
    ws = load_workbook(io.BytesIO(xlsx_bytes))["Sheet1"]
    # 法拉模板 col 9 = 系统交期（2026-09-02 起改）
    assert ws.cell(row=3, column=9).value == "7月25日", (
        f"系统交期列应为 '7月25日'，实际 {ws.cell(row=3, column=9).value!r}"
    )
    # 反向断言：确认打印列没有回退到 planned_delivery_date（"7月30日"）
    assert ws.cell(row=3, column=9).value != "7月30日", (
        "系统交期列不应回退到 planned_delivery_date=7月30日——"
        f"实际 {ws.cell(row=3, column=9).value!r}"
    )


async def test_print_xlsx_assembly_merge_system_delivery_date_format(clean_db):
    """装配件合并行的交期列同样应取 ``t_assembly.system_delivery_date``。

    装配体两个字段刻意不同：
    ``planned_delivery_date=date(2026, 8, 12)``、
    ``system_delivery_date=date(2026, 8, 5)``——后者才是打印列的来源。
    """
    customer = await _make_l1_root(clean_db, name="法拉", prefix="F")
    asm = await _make_assembly(
        clean_db, customer_id=customer.id,
        serial_no="A7201", drawing_no="DA-7201", name="装配件E",
        planned_delivery_date=date(2026, 8, 12),
        system_delivery_date=date(2026, 8, 5),
    )
    child = await _make_part_with_assembly(
        clean_db, customer_id=customer.id, assembly_id=asm.id,
        serial_no="F7201", drawing_no="D-F7201",
    )
    note_svc = _make_service(clean_db)
    note = await note_svc.create_draft(customer_id=str(customer.id))
    await note_svc.add_parts(
        note_id=str(note.id),
        items=[_item(child)],
        version=note.version,
    )

    xlsx_bytes, _ = await note_svc.print_xlsx(
        note_id=str(note.id), merge_assemblies=True,
    )
    ws = load_workbook(io.BytesIO(xlsx_bytes))["Sheet1"]
    # 装配件合并行：交期取自 asm.system_delivery_date（8月5日）
    assert ws.cell(row=3, column=9).value == "8月5日", (
        f"合并行系统交期应为 '8月5日'，实际 {ws.cell(row=3, column=9).value!r}"
    )


async def test_print_xlsx_system_delivery_date_null_leaves_blank(clean_db):
    """散件 ``system_delivery_date`` 为 NULL → 打印列留空（不再回退到计划交期）。

    planned_delivery_date 固定 ``date(2026, 7, 30)``（"7月30日"），
    系统交期刻意传 ``None``；openpyxl 读出 ``None`` 即为留空。
    """
    customer = await _make_l1_root(clean_db, name="法拉", prefix="F")
    loose = await _make_loose_part(
        clean_db, customer_id=customer.id,
        serial_no="FNUL1", drawing_no="D-FNUL1",
        system_delivery_date=None,
    )
    note_svc = _make_service(clean_db)
    note = await note_svc.create_draft(customer_id=str(customer.id))
    await note_svc.add_parts(
        note_id=str(note.id),
        items=[_item(loose)],
        version=note.version,
    )

    xlsx_bytes, _ = await note_svc.print_xlsx(note_id=str(note.id))
    ws = load_workbook(io.BytesIO(xlsx_bytes))["Sheet1"]
    # 2026-09-02 起 system_delivery_date=NULL → 该单元格留空；不得回退到 planned。
    val = ws.cell(row=3, column=9).value
    assert val is None, (
        "系统交期为空必须留空，且不得回退到计划交期——"
        f"实际 col 9 = {val!r}（planned_delivery_date=7月30日）"
    )


async def test_print_xlsx_assembly_merge_system_delivery_date_null_leaves_blank(clean_db):
    """装配件合并行：``system_delivery_date`` NULL → 打印列也留空。

    planned_delivery_date 固定 ``date(2026, 8, 12)``（"8月12日"），
    系统交期传 ``None``；同样不应回退。
    """
    customer = await _make_l1_root(clean_db, name="法拉", prefix="F")
    asm = await _make_assembly(
        clean_db, customer_id=customer.id,
        serial_no="ANUL1", drawing_no="DA-NUL1", name="装配件NUL",
        planned_delivery_date=date(2026, 8, 12),
        system_delivery_date=None,
    )
    child = await _make_part_with_assembly(
        clean_db, customer_id=customer.id, assembly_id=asm.id,
        serial_no="FNUL2", drawing_no="D-FNUL2",
    )
    note_svc = _make_service(clean_db)
    note = await note_svc.create_draft(customer_id=str(customer.id))
    await note_svc.add_parts(
        note_id=str(note.id),
        items=[_item(child)],
        version=note.version,
    )

    xlsx_bytes, _ = await note_svc.print_xlsx(
        note_id=str(note.id), merge_assemblies=True,
    )
    ws = load_workbook(io.BytesIO(xlsx_bytes))["Sheet1"]
    val = ws.cell(row=3, column=9).value
    assert val is None, (
        "装配体合并行系统交期为空必须留空，且不得回退到计划交期——"
        f"实际 col 9 = {val!r}（planned_delivery_date=8月12日）"
    )
