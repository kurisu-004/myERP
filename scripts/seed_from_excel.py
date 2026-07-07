"""从 docs/example/ 两份 Excel 导入客户和零件数据。

数据源：
- docs/example/2026年法拉生产明细表最新.xlsx → Sheet1
- docs/example/2026路达加工明细.xlsx.xls → Sheet1

执行：
    uv run python scripts/seed_from_excel.py
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import openpyxl
import xlrd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import SessionLocal
from model import TCustomer, TPart, PartStatus
from utils.id_gen import new_id

EXAMPLES = Path("docs/example")
FARA_FILE = EXAMPLES / "2026年法拉生产明细表最新.xlsx"
LUDA_FILE = EXAMPLES / "2026路达加工明细.xlsx.xls"

EXCEL_EPOCH = date(1899, 12, 30)


def to_date(value) -> date | None:
    """把 Excel 单元格值转成 date。

    支持 datetime / date / Excel 序列号(int|float) / 空。
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)):
        return EXCEL_EPOCH + timedelta(days=int(value))
    return None


@dataclass
class CustomerNode:
    name: str
    parent_name: str | None  # 一级节点 parent_name = None


@dataclass
class PartRow:
    name: str
    drawing_no: str
    applicant_name: str
    quantity: int
    unit_price: Decimal
    total_price: Decimal
    request_date: date
    planned_delivery_date: date
    actual_delivery_date: date | None
    is_urgent: bool
    customer_name: str  # 二级节点名


def _clean_text(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def load_fara() -> tuple[list[CustomerNode], list[PartRow]]:
    """读法拉 Sheet1，返回 (customers, parts)。

    列顺序：订单编号, 订单号, 分厂, 申请人, 图号, 名称, 数量, 单价, 确认单价,
            金额, 客户名称, 接单日期, 计划交期, 系统交期, 交货日期, 编号, 备注
    """
    wb = openpyxl.load_workbook(FARA_FILE, read_only=True)
    ws = wb["Sheet1"]

    customers: dict[str, CustomerNode] = {}
    parts: list[PartRow] = []

    for row in ws.iter_rows(min_row=3, values_only=True):
        if not row:
            continue
        drawing_no = _clean_text(row[4])
        name = _clean_text(row[5])
        sub_factory = _clean_text(row[2])
        applicant = _clean_text(row[3]) or "(未知)"
        # 跳过无效行：没有图号或没有名称
        if not drawing_no or not name:
            continue

        # 一级客户固定为"法拉电子"
        if "法拉电子" not in customers:
            customers["法拉电子"] = CustomerNode(name="法拉电子", parent_name=None)
        # 二级：每个出现的分厂
        if sub_factory and sub_factory not in customers:
            customers[sub_factory] = CustomerNode(
                name=sub_factory, parent_name="法拉电子"
            )

        quantity_raw = row[6]
        unit_price_raw = row[8]  # 确认单价
        request = to_date(row[11]) or to_date(row[12])
        planned = to_date(row[12])
        actual = to_date(row[14])

        if not planned:
            # 没有计划交期就跳过，避免触发 NOT NULL
            continue

        try:
            quantity = int(quantity_raw) if quantity_raw is not None else 1
        except (TypeError, ValueError):
            quantity = 1
        if quantity <= 0:
            quantity = 1

        try:
            unit_price = Decimal(str(unit_price_raw)) if unit_price_raw is not None else Decimal("0")
        except Exception:
            unit_price = Decimal("0")
        if unit_price < 0:
            unit_price = Decimal("0")

        total_price = unit_price * quantity

        # 备注里有"加急"字样就置为加急
        remark = _clean_text(row[16])
        is_urgent = "加急" in remark

        parts.append(
            PartRow(
                name=name,
                drawing_no=drawing_no,
                applicant_name=applicant,
                quantity=quantity,
                unit_price=unit_price,
                total_price=total_price,
                request_date=request or planned,
                planned_delivery_date=planned,
                actual_delivery_date=actual,
                is_urgent=is_urgent,
                customer_name=sub_factory or "法拉电子",
            )
        )

    return list(customers.values()), parts


def load_luda() -> tuple[list[CustomerNode], list[PartRow]]:
    """读路达 Sheet1。

    列顺序：内部编号, 系统交期, 订单编号, 申请部门, 图号, 品名, 数量, 单价,
            慢丝加工单价, 总价, 请购日期, 计划交期, 送检日期, 入库情况, 返修日期
    """
    wb = xlrd.open_workbook(str(LUDA_FILE))
    ws = wb.sheet_by_name("Sheet1")

    customers: dict[str, CustomerNode] = {}
    parts: list[PartRow] = []

    for r in range(2, ws.nrows):
        row = ws.row_values(r)
        types = ws.row_types(r)

        drawing_no = _clean_text(row[4])
        name = _clean_text(row[5])
        dept = _clean_text(row[3])
        applicant = _clean_text(row[3]) or "(未知)"

        if not drawing_no or not name:
            continue
        # 跳过被 Excel 合并污染的行（"上海全丰" 出现在不该出现的列）
        if drawing_no.startswith("HSH") or "上海全丰" in (row[0] or ""):
            continue
        # 数量为 0 或缺失 → 跳过
        try:
            quantity = int(row[6])
        except (TypeError, ValueError):
            continue
        if quantity <= 0:
            continue

        # 一级客户固定为"路达"
        if "路达" not in customers:
            customers["路达"] = CustomerNode(name="路达", parent_name=None)
        # 二级：申请部门
        if dept and dept not in customers:
            customers[dept] = CustomerNode(name=dept, parent_name="路达")

        try:
            unit_price = Decimal(str(row[7])) if row[7] not in ("", None) else Decimal("0")
        except Exception:
            unit_price = Decimal("0")
        try:
            total_price = Decimal(str(row[9])) if row[9] not in ("", None) else unit_price * quantity
        except Exception:
            total_price = unit_price * quantity

        request = to_date(row[10])
        planned = to_date(row[11])
        actual = to_date(row[12])  # 送检日期当 actual_delivery_date

        if not planned:
            continue

        # 入库情况是字符串，根据内容推断 status
        storage = _clean_text(row[13])
        if "返修" in storage:
            status = PartStatus.REPAIRING
        elif actual is not None:
            status = PartStatus.DELIVERED
        else:
            status = PartStatus.PENDING

        is_urgent = "加急" in storage

        parts.append(
            PartRow(
                name=name,
                drawing_no=drawing_no,
                applicant_name=applicant,
                quantity=quantity,
                unit_price=unit_price,
                total_price=total_price,
                request_date=request or planned,
                planned_delivery_date=planned,
                actual_delivery_date=actual,
                is_urgent=is_urgent,
                customer_name=dept or "路达",
            )
        )

    return list(customers.values()), parts


async def seed() -> None:
    fara_customers, fara_parts = load_fara()
    luda_customers, luda_parts = load_luda()

    all_customers = fara_customers + luda_customers
    all_parts = fara_parts + luda_parts
    print(f"[load] customers={len(all_customers)} parts={len(all_parts)}")

    async with SessionLocal() as session:
        async with session.begin():
            # 1. 清空旧数据（仅限 t_customer / t_part，方便重复跑）。
            #    t_customer 2026-07-07 起改雪花 ID（t_customer_id_seq 已 DROP），
            #    不能再 RESTART 序列。
            await session.execute(TPart.__table__.delete())
            await session.execute(TCustomer.__table__.delete())
            # 无 ALTER SEQUENCE 步骤（雪花 ID 不可重置；删除时连同 ID 一起删）

            # 2. 插入一级
            name_to_id: dict[str, int] = {}
            for node in all_customers:
                if node.parent_name is None:
                    session.add(TCustomer(name=node.name, parent_id=None))
            await session.flush()
            result = await session.execute(
                select(TCustomer).where(TCustomer.parent_id.is_(None))
            )
            for c in result.scalars():
                name_to_id[c.name] = c.id

            # 3. 插入二级
            for node in all_customers:
                if node.parent_name is not None:
                    parent_id = name_to_id.get(node.parent_name)
                    if parent_id is None:
                        print(f"[warn] missing parent for {node.name} -> {node.parent_name}")
                        continue
                    session.add(TCustomer(name=node.name, parent_id=parent_id))
            await session.flush()
            result = await session.execute(select(TCustomer))
            for c in result.scalars():
                name_to_id[c.name] = c.id

            # 4. 插入零件
            for pr in all_parts:
                customer_id = name_to_id.get(pr.customer_name)
                if customer_id is None:
                    # 二级客户不存在则退化到一级
                    if pr.customer_name.startswith("开发") or "IQC" in pr.customer_name:
                        customer_id = name_to_id.get("路达")
                    elif pr.customer_name in (
                        "一厂", "七厂", "二厂", "五厂", "八厂", "六厂",
                        "动力班", "南海厂", "南海路厂区", "技术研发中心",
                        "母排厂", "计量科", "设备部", "镀膜厂",
                    ):
                        customer_id = name_to_id.get("法拉电子")
                    else:
                        # 完全未知就跳过
                        continue
                if customer_id is None:
                    continue

                if pr.actual_delivery_date is not None:
                    status = PartStatus.DELIVERED
                else:
                    status = PartStatus.PENDING

                session.add(
                    TPart(
                        id=new_id(),
                        name=pr.name,
                        drawing_no=pr.drawing_no,
                        applicant_name=pr.applicant_name,
                        quantity=pr.quantity,
                        unit_price=pr.unit_price,
                        total_price=pr.total_price,
                        request_date=pr.request_date,
                        planned_delivery_date=pr.planned_delivery_date,
                        actual_delivery_date=pr.actual_delivery_date,
                        status=status,
                        is_urgent=pr.is_urgent,
                        customer_id=customer_id,
                    )
                )

            await session.flush()

            # 5. 统计
            from sqlalchemy import func

            cust_count = await session.scalar(
                select(func.count(TCustomer.id))
            )
            part_count = await session.scalar(select(func.count(TPart.id)))
            print(f"[done] t_customer rows={cust_count}, t_part rows={part_count}")


if __name__ == "__main__":
    asyncio.run(seed())