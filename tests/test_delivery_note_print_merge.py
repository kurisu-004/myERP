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
# T-merge-1: merge=False 默认 — 装配件子件逐行输出（散件无变化）
# ============================================================
async def test_print_xlsx_merge_assemblies_default_unmerged(clean_db):
    """merge_assemblies=False 默认行为：装配件子件与散件都逐行填表。"""
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

    xlsx_bytes, prefix = await svc.print_xlsx(note_id=str(note.id))
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
# T-merge-6: Excel 数据行高 25 磅 + 列宽自适配（法拉模板）
# ============================================================
async def test_print_xlsx_excel_layout(clean_db):
    """数据行全部 25 磅；列宽 non-zero 且 ≤ 40。"""
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

    # 序号列 col A 固定 5 字符宽
    assert ws.column_dimensions["A"].width == 5, (
        f"序号列应为固定 5 字符，实际 {ws.column_dimensions['A'].width}"
    )

    # 列宽约束：序号 col A=5；其他 8 ≤ width ≤ 40（备注 col J 宽上限 60）
    from openpyxl.utils import get_column_letter
    for col_idx in range(1, 11):
        letter = get_column_letter(col_idx)
        w = ws.column_dimensions[letter].width
        if col_idx == 1:
            assert w == 5, f"col A 应为 5，实际 {w}"
        elif col_idx == 10:
            # 备注列为 wide_cols → wide_max=60（内容短时仍 ≥ 8）
            assert 8 <= w <= 60, (
                f"备注 col J width={w} 不在 [8, 60] 合理范围"
            )
        else:
            assert 8 <= w <= 40, (
                f"col {letter} width={w} 不在 [8, 40] 合理范围"
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