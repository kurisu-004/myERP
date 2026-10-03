"""送货单 / 标签 Excel 渲染器的活覆盖（2026-10-04 新增）。

背景：`tests/test_delivery_note_print_merge.py` 随 2026-09-17 v1 业务路由下线被
文件级 `pytestmark.skip` 退役（35 例），渲染器 `service/delivery_note_print.py`
一度零覆盖，于是三个缺陷一起上线：custom_order 契约与前端不一致（同 part 多批次
必 422）、代表批次窄化导致同 part 求和丢失、``assembly_ids`` 硬编码 None 让
装配件合并从未生效。本文件直接测活的 `DeliveryNotePrintService`（seed DB，不走
已删除的 ``create_draft`` / ``add_parts`` 门面），把这三条钉成回归护栏。

列号口径（实测自模板，非照抄注释）：
- 法拉（Sheet1，数据 R3-R12）：1 序号 / 2 订单号 / 3 分厂 / 4 申请人 / 5 图号 /
  6 名称 / 7 数量 / 8 单位 / 9 预估交期 / 10 备注；
- 路达（杏南，数据 R5-R29）：1 序号 / 2 订单号 / 3 申请部门人 / 4 图号 / 5 名称 /
  6 数量 / 7-8 模板自带空列（**不写单位**，件/套 区分只在法拉可见）；
- 标签（无模板，数据 R2 起）：1 客户 / 2 订单号 / 3 申请人 / 4 名称 / 5 图号 /
  6 数量 / 7 单位。
"""
from __future__ import annotations

import io
from datetime import date
from typing import Any

import pytest
from openpyxl import load_workbook
from sqlalchemy.ext.asyncio import AsyncSession

from core.error_code import ErrCode
from core.exception import BizError
from model import TDeliveryNote, TPart, TPartBatch
from model.assembly import TAssembly
from model.customer import TCustomer
from model.enums import AssemblyStatus, DeliveryNoteStatus, PartStatus
from repository.assembly import AssemblyRepository
from repository.customer import CustomerRepository
from repository.delivery_note import DeliveryNoteRepository
from repository.part import PartRepository
from repository.part_batch import PartBatchRepository
from service.delivery_note_print import DeliveryNotePrintService

from tests.conftest import seed_root_batch


# ============================================================
# helpers：seed（全部直接建 ORM 行，绕开已退役的 v1 门面）
# ============================================================
def _svc(session: AsyncSession) -> DeliveryNotePrintService:
    """照 ``api/deps.py::get_delivery_note_print_service`` 的字段名构造 service。"""
    return DeliveryNotePrintService(
        notes=DeliveryNoteRepository(session),
        parts=PartRepository(session),
        customers=CustomerRepository(session),
        part_batches=PartBatchRepository(session),
        assemblies=AssemblyRepository(session),
    )


async def _make_l1_root(
    session: AsyncSession, *, name: str, prefix: str = "F"
) -> TCustomer:
    """一级客户（送货单挂它，``serial_prefix`` 决定用哪套模板）。"""
    c = TCustomer(name=name, parent_id=None)
    c.serial_prefix = prefix
    session.add(c)
    await session.flush()
    return c


async def _make_leaf(
    session: AsyncSession, *, name: str, parent_id: int
) -> TCustomer:
    """二级客户（零件的 ``customer_id``；法拉 col 3「分厂」取它的 name）。"""
    c = TCustomer(name=name, parent_id=parent_id)
    c.serial_prefix = None
    session.add(c)
    await session.flush()
    return c


async def _make_note(
    session: AsyncSession, *, customer_id: int, no: str = "DN-20261004-0001"
) -> TDeliveryNote:
    note = TDeliveryNote(
        delivery_note_no=no,
        customer_id=customer_id,
        status=DeliveryNoteStatus.SUBMITTED.value,
        delivery_date=date(2026, 10, 4),
    )
    session.add(note)
    await session.flush()
    return note


async def _make_assembly(
    session: AsyncSession,
    *,
    customer_id: int,
    serial_no: str = "A7001",
    drawing_no: str = "DA-7001",
    name: str = "装配件A",
    applicant_name: str | None = "总装测试员",
    order_no: str | None = "ON-ASM-001",
    system_delivery_date: date | None = date(2026, 8, 5),
) -> TAssembly:
    """直接构造 PENDING 装配件（绕过已下线的 AssemblyService）。"""
    a = TAssembly(
        serial_no=serial_no,
        drawing_no=drawing_no,
        name=name,
        applicant_name=applicant_name,
        customer_id=customer_id,
        request_date=date(2026, 7, 1),
        planned_delivery_date=date(2026, 7, 30),
        system_delivery_date=system_delivery_date,
        status=AssemblyStatus.PENDING.value,
    )
    a.order_no = order_no
    session.add(a)
    await session.flush()
    return a


async def _make_part(
    session: AsyncSession,
    *,
    customer_id: int,
    serial_no: str,
    drawing_no: str,
    name: str | None = None,
    order_no: str | None = "ON-2026-001",
    assembly_id: int | None = None,
    note: str | None = None,
    system_delivery_date: date | None = date(2026, 7, 25),
    quantity: int = 2,
    delivery_note_id: int | None = None,
) -> TPart:
    """零件 + 根批次（batch_no=1）。

    ``delivery_note_id`` 通过 transient 属性挂载（t_part 该列已随 2026-09-16 瘦身
    删除），由 ``seed_root_batch`` 镜像进根批次——与仓库其它夹具同款约定。
    """
    p = TPart(
        serial_no=serial_no,
        drawing_no=drawing_no,
        name=name or f"零件-{drawing_no}",
        applicant_name="测试申请人",
        quantity=quantity,
        request_date=date(2026, 7, 1),
        planned_delivery_date=date(2026, 7, 30),
        system_delivery_date=system_delivery_date,
        status=PartStatus.READY_TO_SHIP.value,
        customer_id=customer_id,
        assembly_id=assembly_id,
        note=note,
    )
    p.order_no = order_no
    # 2026-09-16 t_part 瘦身：location 列已删，作为 transient 属性供 seed_root_batch 镜像
    p.location = "INSPECTION_SHELF"
    p.delivery_note_id = delivery_note_id
    session.add(p)
    await session.flush()
    p.root_batch = await seed_root_batch(session, p)
    return p


async def _make_batch(
    session: AsyncSession,
    *,
    part: TPart,
    quantity: int,
    delivery_note_id: int | None = None,
    batch_no: int | None = None,
) -> TPartBatch:
    """给已有 part 补一个批次（模拟 ``_split`` 拆批 / 部分入单）。

    打印服务只读 ``t_part_batch.delivery_note_id`` + ``quantity`` + ``id``，不关心
    批次是怎么来的（v1 的 ``PartService.split_batch`` 已随业务下线删除），因此
    直接按 DB 形态补行。``(part_id, batch_no)`` 唯一 ⇒ batch_no 缺省取 MAX+1。
    """
    if batch_no is None:
        owned = await PartBatchRepository(session).list_by_part(part.id)
        batch_no = max((b.batch_no for b in owned), default=0) + 1
    batch = TPartBatch(
        part_id=part.id,
        batch_no=batch_no,
        quantity=quantity,
        status=PartStatus.READY_TO_SHIP.value,
        location="INSPECTION_SHELF",
    )
    batch.delivery_note_id = delivery_note_id
    session.add(batch)
    await session.flush()
    return batch


# ============================================================
# helpers：读 xlsx
# ============================================================
def _load(xlsx_bytes: bytes, sheet: str):
    return load_workbook(io.BytesIO(xlsx_bytes))[sheet]


def _data_rows(ws, *, start_row: int, ncols: int, limit: int) -> list[list[Any]]:
    """按 col 1（序号）非空判定数据行，返回每行的前 ``ncols`` 列值。

    ``limit`` 必须取该模板的数据区行数（法拉 10 / 路达 25），不能扫到页脚签字栏
    ——法拉 R13-R17 有「收货单位名称：…」「送货日期：…」等文本，col 1 非空会被
    误判成数据行。
    """
    out: list[list[Any]] = []
    for i in range(limit):
        r = start_row + i
        if ws.cell(row=r, column=1).value is None:
            continue
        out.append([ws.cell(row=r, column=c).value for c in range(1, ncols + 1)])
    return out


# 法拉：数据 R3-R12（10 行）
def _fala(xlsx_bytes: bytes) -> list[list[Any]]:
    return _data_rows(_load(xlsx_bytes, "Sheet1"), start_row=3, ncols=10, limit=10)


# 路达：数据 R5-R29（25 行）
def _luda(xlsx_bytes: bytes) -> list[list[Any]]:
    return _data_rows(_load(xlsx_bytes, "杏南"), start_row=5, ncols=9, limit=25)


# 标签：无模板自建表头，数据 R2 起
def _labels(xlsx_bytes: bytes) -> list[list[Any]]:
    return _data_rows(_load(xlsx_bytes, "标签"), start_row=2, ncols=7, limit=200)


# 法拉列号（见模块 docstring）
F_IDX, F_ORDER, F_CUST, F_APPLICANT, F_DRAWING, F_NAME, F_QTY, F_UNIT, F_DATE, F_NOTE = range(10)
# 路达列号
L_IDX, L_ORDER, L_APPLICANT, L_DRAWING, L_NAME, L_QTY, L_C1, L_C2, L_DATE = range(9)
# 标签列号
B_CUST, B_ORDER, B_APPLICANT, B_NAME, B_DRAWING, B_QTY, B_UNIT = range(7)


# ============================================================
# 模板列映射（无缺陷牵连，纯口径钉桩）
# ============================================================
async def test_render_fala_loose_parts_column_mapping(clean_db):
    """法拉模板列映射：订单号 / 分厂 / 申请人 / 图号 / 名称 / 数量 / 单位 / 交期 / 备注。"""
    root = await _make_l1_root(clean_db, name="法拉", prefix="F")
    leaf = await _make_leaf(clean_db, name="母排厂", parent_id=root.id)
    note = await _make_note(clean_db, customer_id=root.id)
    p = await _make_part(
        clean_db, customer_id=leaf.id, serial_no="F9001", drawing_no="D-F9001",
        name="母排折弯工装", order_no="ON-9001", note="急", delivery_note_id=note.id,
    )

    xlsx_bytes, prefix = await _svc(clean_db).render(note=note)
    assert prefix == "F"
    rows = _fala(xlsx_bytes)
    assert len(rows) == 1, f"应有 1 行数据，实际 {rows!r}"
    row = rows[0]
    assert row[F_IDX] == 1
    assert row[F_ORDER] == "ON-9001"
    assert row[F_CUST] == "母排厂", "col 3 分厂应取零件所属 L2 客户名"
    assert row[F_APPLICANT] == "测试申请人"
    assert row[F_DRAWING] == "D-F9001"
    assert row[F_NAME] == "母排折弯工装"
    assert row[F_QTY] == p.quantity
    assert row[F_UNIT] == "件"
    assert row[F_DATE] == "7月25日"
    assert row[F_NOTE] == "急"


async def test_render_luda_loose_parts_column_mapping(clean_db):
    """路达模板列映射（数据 R5 起；7/8 列是模板自带空列，不写单位）。"""
    root = await _make_l1_root(clean_db, name="路达", prefix="L")
    leaf = await _make_leaf(clean_db, name="开发一部", parent_id=root.id)
    note = await _make_note(clean_db, customer_id=root.id)
    await _make_part(
        clean_db, customer_id=leaf.id, serial_no="L9001", drawing_no="D-L9001",
        name="非标螺纹塞规", order_no="ON-L9001", delivery_note_id=note.id,
    )

    xlsx_bytes, prefix = await _svc(clean_db).render(note=note)
    assert prefix == "L"
    rows = _luda(xlsx_bytes)
    assert len(rows) == 1, f"应有 1 行数据，实际 {rows!r}"
    row = rows[0]
    assert row[L_IDX] == 1
    assert row[L_ORDER] == "ON-L9001"
    assert row[L_APPLICANT] == "测试申请人"
    assert row[L_DRAWING] == "D-L9001"
    assert row[L_NAME] == "非标螺纹塞规"
    assert row[L_QTY] == 2
    # 7/8 列是模板自带空列（CellBinding const ""），单位不落在这套模板上
    assert row[L_C1] in (None, "")
    assert row[L_C2] in (None, "")


# ============================================================
# 缺陷 B：同 part 拆批求和（custom_order 只给代表 id 的契约下）
# ============================================================
async def test_render_same_part_split_batches_sum_with_custom_order(clean_db):
    """同 part 3 批（2/3/1）+ 1 散件，custom_order 给 reps → 折叠 2 行，qty 求和 6。

    冻结契约：custom_order 每个 part 只发代表 batch id（最小 id）。代表 id 只决定
    顺序，数量必须由该 part 的**全部**批次求和——本例是缺陷 B 的回归护栏。
    """
    root = await _make_l1_root(clean_db, name="法拉", prefix="F")
    note = await _make_note(clean_db, customer_id=root.id)
    split_part = await _make_part(
        clean_db, customer_id=root.id, serial_no="F9101", drawing_no="D-F9101",
        quantity=6, delivery_note_id=note.id,
    )
    # 模拟 _split：源批次先扣减（6 → 2，代表批 = 最小 id 的根批次），再拆出 3 / 1
    split_part.root_batch.quantity = 2
    await _make_batch(clean_db, part=split_part, quantity=3, delivery_note_id=note.id)
    await _make_batch(clean_db, part=split_part, quantity=1, delivery_note_id=note.id)
    loose = await _make_part(
        clean_db, customer_id=root.id, serial_no="F9102", drawing_no="D-F9102",
        order_no="ON-9102", delivery_note_id=note.id,
    )
    await clean_db.flush()

    custom_order = [str(split_part.root_batch.id), str(loose.root_batch.id)]
    xlsx_bytes, _ = await _svc(clean_db).render(note=note, custom_order=custom_order)
    rows = _fala(xlsx_bytes)
    assert len(rows) == 2, f"同 part 应折叠成 1 行，实际 {rows!r}"
    assert rows[0][F_DRAWING] == "D-F9101"
    assert rows[0][F_QTY] == 6, f"同 part 求和应为 2+3+1=6，实际 {rows[0][F_QTY]!r}"
    assert rows[0][F_UNIT] == "件"
    assert rows[1][F_DRAWING] == "D-F9102"
    assert rows[1][F_QTY] == 2


async def test_render_same_part_split_batches_sum_without_custom_order(clean_db):
    """custom_order=None（走 ``TPartBatch.id ASC`` 旧路径）同样求和。"""
    root = await _make_l1_root(clean_db, name="法拉", prefix="F")
    note = await _make_note(clean_db, customer_id=root.id)
    split_part = await _make_part(
        clean_db, customer_id=root.id, serial_no="F9201", drawing_no="D-F9201",
        quantity=6, delivery_note_id=note.id,
    )
    split_part.root_batch.quantity = 2
    await _make_batch(clean_db, part=split_part, quantity=3, delivery_note_id=note.id)
    await _make_batch(clean_db, part=split_part, quantity=1, delivery_note_id=note.id)
    await clean_db.flush()

    xlsx_bytes, _ = await _svc(clean_db).render(note=note)
    rows = _fala(xlsx_bytes)
    assert len(rows) == 1, f"同 part 应折叠成 1 行，实际 {rows!r}"
    assert rows[0][F_QTY] == 6, f"求和应为 6，实际 {rows[0][F_QTY]!r}"


async def test_custom_order_rep_expansion_follows_part_order(clean_db):
    """展开后行顺序 = custom_order 给的 part 顺序，同 part 的多个批次不额外出行。"""
    root = await _make_l1_root(clean_db, name="法拉", prefix="F")
    note = await _make_note(clean_db, customer_id=root.id)
    p1 = await _make_part(
        clean_db, customer_id=root.id, serial_no="F9301", drawing_no="D-F9301",
        quantity=10, delivery_note_id=note.id,
    )
    p1.root_batch.quantity = 5
    await _make_batch(clean_db, part=p1, quantity=5, delivery_note_id=note.id)
    p2 = await _make_part(
        clean_db, customer_id=root.id, serial_no="F9302", drawing_no="D-F9302",
        order_no="ON-9302", delivery_note_id=note.id,
    )
    await clean_db.flush()

    # custom_order 把 p2 排到 p1 前面（= 预览里用户拖出来的顺序）
    xlsx_bytes, _ = await _svc(clean_db).render(
        note=note,
        custom_order=[str(p2.root_batch.id), str(p1.root_batch.id)],
    )
    rows = _fala(xlsx_bytes)
    assert [r[F_DRAWING] for r in rows] == ["D-F9302", "D-F9301"]
    assert rows[0][F_QTY] == 2
    assert rows[1][F_QTY] == 10, f"p1 两批 5+5 应为 10，实际 {rows[1][F_QTY]!r}"


async def test_custom_order_duplicate_rep_id_does_not_double_quantity(clean_db):
    """custom_order 重复发同一个代表 id → 只算一次（否则展开会把该 part 复制两份）。"""
    root = await _make_l1_root(clean_db, name="法拉", prefix="F")
    note = await _make_note(clean_db, customer_id=root.id)
    p1 = await _make_part(
        clean_db, customer_id=root.id, serial_no="F9401", drawing_no="D-F9401",
        quantity=9, delivery_note_id=note.id,
    )
    p1.root_batch.quantity = 6
    await _make_batch(clean_db, part=p1, quantity=3, delivery_note_id=note.id)
    await clean_db.flush()
    rep = str(p1.root_batch.id)

    xlsx_bytes, _ = await _svc(clean_db).render(note=note, custom_order=[rep, rep])
    rows = _fala(xlsx_bytes)
    assert len(rows) == 1, f"重复代表 id 不应复制行，实际 {rows!r}"
    assert rows[0][F_QTY] == 9, f"求和应为 6+3=9，实际 {rows[0][F_QTY]!r}"


# ============================================================
# 缺陷 B：line_item_ids 恢复到 per-batch 物理子集语义
# ============================================================
async def test_labels_line_item_ids_subset_sums_selected_batches(clean_db):
    """line_item_ids 选 2 个物理批次（其中含非代表）→ 只打 1 行，数量 = 选中两批之和。"""
    root = await _make_l1_root(clean_db, name="法拉", prefix="F")
    note = await _make_note(clean_db, customer_id=root.id)
    p1 = await _make_part(
        clean_db, customer_id=root.id, serial_no="F9501", drawing_no="D-F9501",
        quantity=1, delivery_note_id=note.id,
    )
    b2 = await _make_batch(clean_db, part=p1, quantity=2, delivery_note_id=note.id)
    b3 = await _make_batch(clean_db, part=p1, quantity=3, delivery_note_id=note.id)

    labels_bytes, _ = await _svc(clean_db).render_labels(
        note=note,
        custom_order=[str(p1.root_batch.id)],
        line_item_ids=[str(p1.root_batch.id), str(b2.id), str(b3.id)],
    )
    rows = _labels(labels_bytes)
    assert len(rows) == 1, f"同 part 勾选后仍折叠成 1 行，实际 {rows!r}"
    assert rows[0][B_DRAWING] == "D-F9501"
    assert rows[0][B_QTY] == 6, f"全选时求和应为 1+2+3=6，实际 {rows[0][B_QTY]!r}"
    assert rows[0][B_UNIT] == "件"

    # 只勾代表批 → 数量退化为该批的 1（per-batch 语义，不是全 part 求和）
    labels_bytes, _ = await _svc(clean_db).render_labels(
        note=note,
        custom_order=[str(p1.root_batch.id)],
        line_item_ids=[str(p1.root_batch.id)],
    )
    rows = _labels(labels_bytes)
    assert len(rows) == 1
    assert rows[0][B_QTY] == 1, "只勾一批时数量应等于该批数量"


async def test_labels_line_item_ids_rejects_empty_and_unknown(clean_db):
    """line_item_ids=[] → 400 BIZ_INVALID_VALUE；含非本单 id → 422。"""
    root = await _make_l1_root(clean_db, name="法拉", prefix="F")
    note = await _make_note(clean_db, customer_id=root.id)
    p1 = await _make_part(
        clean_db, customer_id=root.id, serial_no="F9601", drawing_no="D-F9601",
        delivery_note_id=note.id,
    )
    svc = _svc(clean_db)

    with pytest.raises(BizError) as ei:
        await svc.render_labels(note=note, line_item_ids=[])
    assert ei.value.code == ErrCode.BIZ_INVALID_VALUE
    assert ei.value.http_status == 400

    with pytest.raises(BizError) as ei2:
        await svc.render_labels(note=note, line_item_ids=["999999999"])
    assert ei2.value.code == ErrCode.BIZ_DELIVERY_PRINT_BAD_ORDER
    assert ei2.value.http_status == 422
    assert "999999999" in ei2.value.message


# ============================================================
# 缺陷 C：装配件合并（assembly_ids 此前硬编码 None ⇒ 从未生效）
# ============================================================
async def test_render_assembly_merge_produces_tao_row(clean_db):
    """注入 assembly_ids + merge_assemblies=True → 2 个子件合并成 1 行「套」。"""
    root = await _make_l1_root(clean_db, name="法拉", prefix="F")
    note = await _make_note(clean_db, customer_id=root.id)
    asm = await _make_assembly(clean_db, customer_id=root.id)
    c1 = await _make_part(
        clean_db, customer_id=root.id, serial_no="F9701", drawing_no="D-F9701",
        assembly_id=asm.id, delivery_note_id=note.id,
    )
    c2 = await _make_part(
        clean_db, customer_id=root.id, serial_no="F9702", drawing_no="D-F9702",
        assembly_id=asm.id, delivery_note_id=note.id,
    )
    loose = await _make_part(
        clean_db, customer_id=root.id, serial_no="F9703", drawing_no="D-F9703",
        order_no="ON-9703", delivery_note_id=note.id,
    )

    xlsx_bytes, _ = await _svc(clean_db).render(
        note=note,
        custom_order=[
            str(c1.root_batch.id), str(c2.root_batch.id), str(loose.root_batch.id),
        ],
        merge_assemblies=True,
        assembly_ids=[asm.id],
    )
    rows = _fala(xlsx_bytes)
    assert len(rows) == 2, f"2 子件应合并成 1 行 + 1 散件行，实际 {rows!r}"
    merged, loose_row = rows
    assert merged[F_IDX] == 1
    assert merged[F_DRAWING] == asm.drawing_no, "合并行图号取装配件总图号"
    assert merged[F_NAME] == asm.name
    assert merged[F_APPLICANT] == "总装测试员"
    assert merged[F_ORDER] == "ON-ASM-001"
    assert merged[F_QTY] == 1, "未注入套数时默认 1 套"
    assert merged[F_UNIT] == "套"
    assert loose_row[F_DRAWING] == "D-F9703"
    assert loose_row[F_UNIT] == "件"


async def test_render_without_assembly_ids_keeps_loose_rows(clean_db):
    """``assembly_ids`` 缺省（None）→ 不合并，子件逐行出「件」。

    这是缺陷 C 的反向护栏：合并**必须**由 assembly_ids 驱动，service 不得凭
    ``p.assembly_id`` 自作主张去查装配件表。
    """
    root = await _make_l1_root(clean_db, name="法拉", prefix="F")
    note = await _make_note(clean_db, customer_id=root.id)
    asm = await _make_assembly(clean_db, customer_id=root.id)
    await _make_part(
        clean_db, customer_id=root.id, serial_no="F9801", drawing_no="D-F9801",
        assembly_id=asm.id, delivery_note_id=note.id,
    )
    await _make_part(
        clean_db, customer_id=root.id, serial_no="F9802", drawing_no="D-F9802",
        assembly_id=asm.id, delivery_note_id=note.id,
    )

    xlsx_bytes, _ = await _svc(clean_db).render(
        note=note, merge_assemblies=True, assembly_ids=None,
    )
    rows = _fala(xlsx_bytes)
    assert [r[F_DRAWING] for r in rows] == ["D-F9801", "D-F9802"]
    assert all(r[F_UNIT] == "件" for r in rows), "未注入 assembly_ids 不得出「套」行"


async def test_render_assembly_merge_quantity_from_injected_string_key(clean_db):
    """``merge_quantities`` 走 JSON 字符串键（冻结契约）→ 套数取注入值。"""
    root = await _make_l1_root(clean_db, name="法拉", prefix="F")
    note = await _make_note(clean_db, customer_id=root.id)
    asm = await _make_assembly(clean_db, customer_id=root.id)
    await _make_part(
        clean_db, customer_id=root.id, serial_no="F9901", drawing_no="D-F9901",
        assembly_id=asm.id, delivery_note_id=note.id,
    )

    xlsx_bytes, _ = await _svc(clean_db).render(
        note=note,
        merge_assemblies=True,
        assembly_ids=[asm.id],
        merge_quantities={str(asm.id): 3},
    )
    rows = _fala(xlsx_bytes)
    assert len(rows) == 1
    assert rows[0][F_UNIT] == "套"
    assert rows[0][F_QTY] == 3, f"注入 3 套应生效，实际 {rows[0][F_QTY]!r}"


async def test_render_assembly_merge_zero_quantity_drops_children(clean_db):
    """注入套数 0 → 该装配件的子件行与合并行一起消失（凑不齐整套不能单发）。"""
    root = await _make_l1_root(clean_db, name="法拉", prefix="F")
    note = await _make_note(clean_db, customer_id=root.id)
    asm = await _make_assembly(clean_db, customer_id=root.id)
    c1 = await _make_part(
        clean_db, customer_id=root.id, serial_no="FA001", drawing_no="D-FA001",
        assembly_id=asm.id, delivery_note_id=note.id,
    )
    c2 = await _make_part(
        clean_db, customer_id=root.id, serial_no="FA002", drawing_no="D-FA002",
        assembly_id=asm.id, delivery_note_id=note.id,
    )
    loose = await _make_part(
        clean_db, customer_id=root.id, serial_no="FA003", drawing_no="D-FA003",
        order_no="ON-FA003", delivery_note_id=note.id,
    )

    xlsx_bytes, _ = await _svc(clean_db).render(
        note=note,
        custom_order=[
            str(c1.root_batch.id), str(c2.root_batch.id), str(loose.root_batch.id),
        ],
        merge_assemblies=True,
        assembly_ids=[asm.id],
        merge_quantities={str(asm.id): 0},
    )
    rows = _fala(xlsx_bytes)
    assert [r[F_DRAWING] for r in rows] == ["D-FA003"], (
        f"0 套装配件的子件与合并行都应消失，只剩散件，实际 {rows!r}"
    )
    assert rows[0][F_UNIT] == "件"


async def test_render_assembly_merge_zero_quantity_is_not_scaled_to_others(clean_db):
    """同单两个装配件：一个 0 套、一个注入 4 套 → 只丢 0 套那个的子件。"""
    root = await _make_l1_root(clean_db, name="法拉", prefix="F")
    note = await _make_note(clean_db, customer_id=root.id)
    asm_zero = await _make_assembly(
        clean_db, customer_id=root.id, serial_no="AZ001",
        drawing_no="DA-ZERO", name="凑不齐套装",
    )
    asm_ok = await _make_assembly(
        clean_db, customer_id=root.id, serial_no="AZ002",
        drawing_no="DA-OK", name="可出货套装",
    )
    await _make_part(
        clean_db, customer_id=root.id, serial_no="FB001", drawing_no="D-FB001",
        assembly_id=asm_zero.id, delivery_note_id=note.id,
    )
    await _make_part(
        clean_db, customer_id=root.id, serial_no="FB002", drawing_no="D-FB002",
        assembly_id=asm_ok.id, delivery_note_id=note.id,
    )

    xlsx_bytes, _ = await _svc(clean_db).render(
        note=note,
        merge_assemblies=True,
        assembly_ids=[asm_zero.id, asm_ok.id],
        merge_quantities={str(asm_zero.id): 0, str(asm_ok.id): 4},
    )
    rows = _fala(xlsx_bytes)
    assert len(rows) == 1, f"只应剩可出货装配件的合并行，实际 {rows!r}"
    assert rows[0][F_DRAWING] == "DA-OK"
    assert rows[0][F_QTY] == 4
    assert rows[0][F_UNIT] == "套"


async def test_labels_assembly_merge_survives_partial_line_item_subset(clean_db):
    """合并模式下只勾装配体部分子件 → 仍出 1 行「套」，数量不按存活子件缩放。"""
    root = await _make_l1_root(clean_db, name="法拉", prefix="F")
    note = await _make_note(clean_db, customer_id=root.id)
    asm = await _make_assembly(clean_db, customer_id=root.id)
    c1 = await _make_part(
        clean_db, customer_id=root.id, serial_no="FC001", drawing_no="D-FC001",
        assembly_id=asm.id, delivery_note_id=note.id,
    )
    c2 = await _make_part(
        clean_db, customer_id=root.id, serial_no="FC002", drawing_no="D-FC002",
        assembly_id=asm.id, delivery_note_id=note.id,
    )

    labels_bytes, _ = await _svc(clean_db).render_labels(
        note=note,
        custom_order=[str(c1.root_batch.id), str(c2.root_batch.id)],
        merge_assemblies=True,
        assembly_ids=[asm.id],
        merge_quantities={str(asm.id): 2},
        line_item_ids=[str(c1.root_batch.id)],
    )
    rows = _labels(labels_bytes)
    assert len(rows) == 1, f"应只出 1 行合并行，实际 {rows!r}"
    assert rows[0][B_DRAWING] == asm.drawing_no
    assert rows[0][B_QTY] == 2, "只勾 1/2 子件也不缩放套数（防合并行凭空变小）"
    assert rows[0][B_UNIT] == "套"
    assert rows[0][B_ORDER] == "ON-ASM-001"


# ============================================================
# custom_order 三类 422 校验
# ============================================================
async def test_custom_order_unknown_batch_id_raises_422(clean_db):
    """含非本单 batch id → 422 BIZ_DELIVERY_PRINT_BAD_ORDER。"""
    root = await _make_l1_root(clean_db, name="法拉", prefix="F")
    note = await _make_note(clean_db, customer_id=root.id)
    p1 = await _make_part(
        clean_db, customer_id=root.id, serial_no="FD001", drawing_no="D-FD001",
        delivery_note_id=note.id,
    )

    with pytest.raises(BizError) as ei:
        await _svc(clean_db).render(
            note=note, custom_order=[str(p1.root_batch.id), "999999999"],
        )
    assert ei.value.code == ErrCode.BIZ_DELIVERY_PRINT_BAD_ORDER
    assert ei.value.http_status == 422
    assert "999999999" in ei.value.message


async def test_custom_order_non_representative_batch_id_raises_422(clean_db):
    """同 part 的非代表 batch id → 422，且提示改发代表 id。

    这条正是线上 422 的形态（前端曾把每个 part 的全部批次 id 都发进来）。
    """
    root = await _make_l1_root(clean_db, name="法拉", prefix="F")
    note = await _make_note(clean_db, customer_id=root.id)
    p1 = await _make_part(
        clean_db, customer_id=root.id, serial_no="FE001", drawing_no="D-FE001",
        delivery_note_id=note.id,
    )
    non_rep = await _make_batch(
        clean_db, part=p1, quantity=1, delivery_note_id=note.id,
    )
    assert non_rep.id > p1.root_batch.id

    with pytest.raises(BizError) as ei:
        await _svc(clean_db).render(note=note, custom_order=[str(non_rep.id)])
    assert ei.value.code == ErrCode.BIZ_DELIVERY_PRINT_BAD_ORDER
    assert ei.value.http_status == 422
    assert str(non_rep.id) in ei.value.message
    assert str(p1.root_batch.id) in ei.value.message, "报错应给出代表 id 让前端自纠"


async def test_custom_order_missing_representative_raises_422(clean_db):
    """漏掉某个 part 的代表 id → 422，提示漏了几个。"""
    root = await _make_l1_root(clean_db, name="法拉", prefix="F")
    note = await _make_note(clean_db, customer_id=root.id)
    p1 = await _make_part(
        clean_db, customer_id=root.id, serial_no="FF001", drawing_no="D-FF001",
        delivery_note_id=note.id,
    )
    p2 = await _make_part(
        clean_db, customer_id=root.id, serial_no="FF002", drawing_no="D-FF002",
        delivery_note_id=note.id,
    )

    with pytest.raises(BizError) as ei:
        await _svc(clean_db).render(
            note=note, custom_order=[str(p1.root_batch.id)],
        )
    assert ei.value.code == ErrCode.BIZ_DELIVERY_PRINT_BAD_ORDER
    assert ei.value.http_status == 422
    assert str(p2.root_batch.id) in ei.value.message
    assert "漏" in ei.value.message


# ============================================================
# 其它渲染口径（缺字段跳过 / 客户与模板前置校验）
# ============================================================
async def test_render_skips_part_without_serial_no(clean_db):
    """缺 serial_no 的零件 warning + 跳过（不抛错），同单其余行照打。

    只能拿 ``serial_no`` 造「缺字段」——``t_part.drawing_no`` 是 NOT NULL 列，
    历史上靠图号缺失兜底的老数据在新库里造不出来。
    """
    root = await _make_l1_root(clean_db, name="法拉", prefix="F")
    note = await _make_note(clean_db, customer_id=root.id)
    await _make_part(
        clean_db, customer_id=root.id, serial_no="FG001", drawing_no="D-FG001",
        delivery_note_id=note.id,
    )
    broken = await _make_part(
        clean_db, customer_id=root.id, serial_no="FG002", drawing_no="D-FG002",
        delivery_note_id=note.id,
    )
    broken.serial_no = None
    await clean_db.flush()

    xlsx_bytes, _ = await _svc(clean_db).render(note=note)
    rows = _fala(xlsx_bytes)
    assert [r[F_DRAWING] for r in rows] == ["D-FG001"], (
        f"缺流水号的零件应被跳过，实际 {rows!r}"
    )


async def test_render_all_parts_unusable_raises_400(clean_db):
    """整单零件都缺流水号 → 400 BIZ_INVALID_VALUE（复用既有兜底，不静默出空表）。"""
    root = await _make_l1_root(clean_db, name="法拉", prefix="F")
    note = await _make_note(clean_db, customer_id=root.id)
    broken = await _make_part(
        clean_db, customer_id=root.id, serial_no="FG101", drawing_no="D-FG101",
        delivery_note_id=note.id,
    )
    broken.serial_no = None
    await clean_db.flush()

    with pytest.raises(BizError) as ei:
        await _svc(clean_db).render(note=note)
    assert ei.value.code == ErrCode.BIZ_INVALID_VALUE
    assert ei.value.http_status == 400


async def test_render_requires_l1_root_with_configured_prefix(clean_db):
    """送货单挂二级客户 / 缺前缀 / 前缀未配模板 → 各按 400 收口。"""
    root = await _make_l1_root(clean_db, name="法拉", prefix="F")
    leaf = await _make_leaf(clean_db, name="母排厂", parent_id=root.id)

    # 1) note 挂 L2 → 400 BIZ_INVALID_VALUE
    note_leaf = await _make_note(clean_db, customer_id=leaf.id, no="DN-20261004-0002")
    with pytest.raises(BizError) as ei:
        await _svc(clean_db).render(note=note_leaf)
    assert ei.value.code == ErrCode.BIZ_INVALID_VALUE
    assert ei.value.http_status == 400

    # 2) L1 缺 serial_prefix → 400 BIZ_DELIVERY_TEMPLATE_NOT_CONFIGURED
    noprefix = TCustomer(name="无前缀客户", parent_id=None)
    noprefix.serial_prefix = None
    clean_db.add(noprefix)
    await clean_db.flush()
    note_np = await _make_note(
        clean_db, customer_id=noprefix.id, no="DN-20261004-0003",
    )
    with pytest.raises(BizError) as ei2:
        await _svc(clean_db).render(note=note_np)
    assert ei2.value.code == ErrCode.BIZ_DELIVERY_TEMPLATE_NOT_CONFIGURED
    assert ei2.value.http_status == 400

    # 3) 前缀没配模板 → 400 BIZ_DELIVERY_TEMPLATE_NOT_CONFIGURED
    unconfigured = TCustomer(name="未配模板客户", parent_id=None)
    unconfigured.serial_prefix = "Z"
    clean_db.add(unconfigured)
    await clean_db.flush()
    note_z = await _make_note(
        clean_db, customer_id=unconfigured.id, no="DN-20261004-0004",
    )
    with pytest.raises(BizError) as ei3:
        await _svc(clean_db).render(note=note_z)
    assert ei3.value.code == ErrCode.BIZ_DELIVERY_TEMPLATE_NOT_CONFIGURED
    assert "Z" in ei3.value.message
