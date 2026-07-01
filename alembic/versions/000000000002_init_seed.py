"""init_seed: load 20 customers, 8 workers, 10 assemblies, 50 parts

Revision ID: 000000000002
Revises: 000000000001
Create Date: 2026-07-01

说明：
- 测试用种子数据，**不**进入生产库。仅在本地 dev 库跑。
- 数据分布：
  - 20 条 t_customer：2 个一级（法拉电子/路达）+ 18 个二级叶子节点。
  - 8 条 t_worker：5 人持有 IN_PROCESS 零件、3 人空闲（其中 1 人停用）。
  - 10 条 t_assembly：4 态 PENDING/IN_PROCESS/COMPLETED/CANCELLED 分布
    = 2 PENDING + 5 IN_PROCESS + 2 COMPLETED + 1 CANCELLED；含 4 条加急。
  - 50 条 t_part：覆盖 PartStatus 全 9 状态均匀分布
    (PENDING×6 / READY×6 / IN_PROCESS×6 / INSPECTION×5 / READY_TO_SHIP×5
     / DELIVERED×6 / REPAIRING×5 / COMPLETED×6 / CANCELLED×5)；
    12 条加急；16 条挂装配体（前 9 个装配体各 1-4 个子件，第 10 个无子件）。
- 装配体状态与子件状态自洽：
    PENDING    → 所有子件都 PENDING
    IN_PROCESS → 任一子件处于 IN_PROCESS/INSPECTION/READY_TO_SHIP/DELIVERED
    COMPLETED  → 所有子件都 COMPLETED
    CANCELLED  → 所有未完成子件被级联置 CANCELLED（这里 1 个子件 CANCELLED）
- 6 条 IN_PROCESS 零件中 5 条关联到工人（current_worker_id = 工人雪花 ID）。
- 雪花 ID 用 `utils.id_gen.new_id` 在 Python 端生成；序列号 serial_no
  按客户前缀（L/F）分配，COMPLETED/CANCELLED 时置 NULL。
- upgrade 头部会清理可能的残留（dev 期反复 rerun 累积），保证幂等。
- downgrade 清理所有种子（不删 schema）。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from utils.id_gen import new_id

revision: str = "000000000002"
down_revision: Union[str, None] = "000000000001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# =============================================================================
# 客户数据：2 个一级 + 18 个二级
# =============================================================================
_CUSTOMERS = [
    (1, "法拉电子", None),
    (2, "路达", None),
    (3, "母排厂", 1),
    (4, "母排厂一组", 3),
    (5, "母排厂二组", 3),
    (6, "薄膜电容厂", 1),
    (7, "变压器厂", 1),
    (8, "电极箔厂", 1),
    (9, "组件一厂", 1),
    (10, "组件二厂", 1),
    (11, "研发部", 1),
    (12, "品质部", 1),
    (13, "开发一部", 2),
    (14, "开发二部", 2),
    (15, "生产一部", 2),
    (16, "生产二部", 2),
    (17, "装配组", 2),
    (18, "品质部", 2),
    (19, "采购部", 2),
    (20, "销售部", 2),
]


# =============================================================================
# 工人数据：8 条
# =============================================================================
_WORKERS = [
    # (badge_code, name, id_card_no, phone, is_active)
    ("Z001", "张志强", "110101198501012131", "13800138001", True),
    ("Z002", "王建国", "110101198803153217", "13800138002", True),
    ("Z003", "李大伟", "110101199002204518", "13800138003", True),
    ("Z004", "赵小敏", "110101199205156427", "13800138004", True),
    ("Z005", "陈丽华", "110101199408102323", "13800138005", True),
    ("Z006", "周小杰", "110101199612081218", "13800138006", True),
    ("Z007", "黄文斌", "110101198705123910", "13800138007", False),  # 停用
    ("Z008", "吴文军", "110101198912305216", "13800138008", True),
]
# 工牌 → 持有零件 drawing_no
_WORKER_HOLDS: dict[str, list[str]] = {
    "Z001": ["E42804FZJ076101"],
    "Z002": ["F42XJ-3380-1", "F42XJ-3380-2"],
    "Z003": ["E42BZCF90491103"],
    "Z004": ["E42FX1020107105"],
    "Z005": ["L22-9012-C"],
    # Z006/Z007/Z008 不持有
}


# =============================================================================
# 装配体数据：10 条
# =============================================================================
_ASSEMBLIES = [
    # (drawing_no, name, applicant, customer_id, request_date, planned, is_urgent, status)
    ("E42804FZJ076100", "精研挡料座总装", "林雪强", 4,  "2026-06-10", "2026-07-25", True,  "IN_PROCESS"),
    ("E42FX1020107101", "精研挡料座",     "陈伟杰", 7,  "2026-05-20", "2026-06-30", False, "COMPLETED"),
    ("E42BZCF90491101", "激光工位固定内夹", "王莉",   6,  "2026-06-18", "2026-07-30", False, "IN_PROCESS"),
    ("L21-7884",        "分选夹爪总成",   "张磊",   13, "2026-05-12", "2026-06-28", True,  "IN_PROCESS"),
    ("L22-9012",        "总装测试架",     "李娜",   15, "2026-06-15", "2026-08-05", False, "PENDING"),
    ("F42XJ-3380",      "校形定位块",     "赵勇",   5,  "2026-06-20", "2026-08-10", False, "IN_PROCESS"),
    ("F42JC-7711",      "检验夹具",       "孙芳",   12, "2026-05-25", "2026-06-20", False, "COMPLETED"),
    ("L23-0114",        "自动贴标机底座", "周健",   17, "2026-06-22", "2026-07-28", True,  "IN_PROCESS"),
    ("E42DZ-5550",      "绕线模具",       "吴敏",   7,  "2026-06-05", "2026-08-15", False, "CANCELLED"),
    ("F42HG-2200",      "清洗工装",       "郑浩",   8,  "2026-05-18", "2026-06-25", False, "PENDING"),
]


# =============================================================================
# 零件数据：50 条
# 装配体 0-8 各 1-4 子件，装配体 9 无子件
# 状态分布：9 状态均匀（6/6/6/5/5/6/5/6/5），加急 12 条
# =============================================================================
_PARTS = [
    # (drawing_no, name, applicant, qty, unit_price, customer_id,
    #  request_date, planned, actual_or_None, status, is_urgent, assembly_idx)
    # —— 装配体 0 子件（IN_PROCESS/INSPECTION/READY_TO_SHIP/DELIVERED） ——
    ("E42804FZJ076101", "精研挡料座上模",     "林雪强", 1, 2200.00, 4,  "2026-06-10", "2026-07-25", None, "IN_PROCESS",     True,  0),
    ("E42804FZJ076102", "精研挡料座下模",     "林雪强", 1, 2400.00, 4,  "2026-06-10", "2026-07-25", None, "INSPECTION",     True,  0),
    ("E42804FZJ076103", "精研挡料座侧板",     "林雪强", 2,  680.00, 4,  "2026-06-10", "2026-07-25", None, "READY_TO_SHIP",  True,  0),
    ("E42804FZJ076104", "精研挡料座紧固件",   "林雪强", 4,   45.00, 4,  "2026-06-10", "2026-07-25", "2026-07-15", "DELIVERED", True,  0),
    # —— 装配体 1 子件（COMPLETED×2） ——
    ("E42FX1020107102", "底座压板",           "陈伟杰", 3,  680.00, 7,  "2026-05-20", "2026-06-30", "2026-06-25", "COMPLETED", False, 1),
    ("E42FX1020107103", "精研滑块",           "陈伟杰", 2,  720.00, 7,  "2026-05-20", "2026-06-30", "2026-06-26", "COMPLETED", False, 1),
    # —— 装配体 2 子件（1 PENDING + 1 IN_PROCESS） ——
    ("E42BZCF90491102", "激光内夹A",          "王莉",   1, 2100.00, 6,  "2026-06-18", "2026-07-30", None, "PENDING",     False, 2),
    ("E42BZCF90491103", "激光内夹B",          "王莉",   1, 2300.00, 6,  "2026-06-15", "2026-07-28", None, "IN_PROCESS", False, 2),
    # —— 装配体 3 子件（INSPECTION/READY_TO_SHIP/DELIVERED） ——
    ("L21-7884-A",      "分选左夹爪",         "张磊",   2,  560.00, 13, "2026-05-10", "2026-06-26", None, "INSPECTION",     True,  3),
    ("L21-7884-B",      "分选右夹爪",         "张磊",   2,  560.00, 13, "2026-05-08", "2026-06-24", None, "READY_TO_SHIP",  True,  3),
    ("L21-7884-C",      "分选连接板",         "张磊",   1,  920.00, 13, "2026-05-05", "2026-06-20", "2026-06-22", "DELIVERED",  True,  3),
    # —— 装配体 4 子件（PENDING） ——
    ("L22-9012-A",      "测试架立柱",         "李娜",   4,  320.00, 15, "2026-06-15", "2026-08-05", None, "PENDING",     False, 4),
    # —— 装配体 5 子件（IN_PROCESS） ——
    ("F42XJ-3380-1",    "校形定位块",         "赵勇",   1, 4500.00, 5,  "2026-06-20", "2026-08-10", None, "IN_PROCESS", False, 5),
    # —— 装配体 6 子件（COMPLETED） ——
    ("F42JC-7711-1",    "检验夹具手柄",       "孙芳",   1,  220.00, 12, "2026-05-25", "2026-06-20", "2026-06-18", "COMPLETED", False, 6),
    # —— 装配体 7 子件（INSPECTION） ——
    ("L23-0114-A",      "贴标机底板",         "周健",   1, 3200.00, 17, "2026-06-22", "2026-07-28", None, "INSPECTION", True,  7),
    # —— 装配体 8 子件（CANCELLED 级联） ——
    ("E42DZ-5550-A",    "绕线模具芯",         "吴敏",   1, 6800.00, 7,  "2026-06-05", "2026-08-15", None, "CANCELLED", False, 8),
    # —— 剩余 34 条（不挂装配体），凑齐 9 状态均匀分布 ——
    # PENDING：补 3
    ("E42804FZJ076105", "精研定位板",         "林雪强", 1,  950.00, 4,  "2026-06-18", "2026-08-01", None, "PENDING",     False, None),
    ("L22-9012-B",      "测试架横梁",         "李娜",   2,  780.00, 15, "2026-06-20", "2026-08-08", None, "PENDING",     False, None),
    ("F42HG-2200-2",    "清洗喷嘴",           "郑浩",   4,  240.00, 8,  "2026-05-25", "2026-07-05", None, "PENDING",     False, None),
    # READY ×6
    ("E42804FZJ076106", "精研上模备件",       "林雪强", 1, 1800.00, 4,  "2026-06-15", "2026-07-30", None, "READY",       False, None),
    ("E42FX1020107104", "精研端盖",           "陈伟杰", 1, 1500.00, 7,  "2026-05-22", "2026-07-08", None, "READY",       False, None),
    ("L21-7884-D",      "分选气缸座",         "张磊",   2,  340.00, 13, "2026-05-15", "2026-06-28", None, "READY",       False, None),
    ("F42JC-7711-2",    "检验底座",           "孙芳",   1,  980.00, 12, "2026-05-26", "2026-06-25", None, "READY",       False, None),
    ("L23-0114-B",      "贴标机侧板",         "周健",   2,  640.00, 17, "2026-06-23", "2026-07-30", None, "READY",       True,  None),
    ("F42HG-2200-3",    "清洗托盘",           "郑浩",   2,  420.00, 8,  "2026-05-19", "2026-06-30", None, "READY",       False, None),
    # IN_PROCESS：补 3
    ("E42FX1020107105", "精研压板",           "陈伟杰", 2,  640.00, 7,  "2026-05-18", "2026-06-28", None, "IN_PROCESS",  False, None),
    ("L22-9012-C",      "测试架底座",         "李娜",   1, 1800.00, 15, "2026-06-12", "2026-08-05", None, "IN_PROCESS",  False, None),
    ("F42XJ-3380-2",    "校形下模",           "赵勇",   1, 4800.00, 5,  "2026-06-18", "2026-08-12", None, "IN_PROCESS",  False, None),
    # INSPECTION：补 2
    ("E42804FZJ076107", "精研垫片",           "林雪强", 10,  18.00, 4,  "2026-05-12", "2026-06-25", None, "INSPECTION",  False, None),
    ("F42JC-7711-3",    "检验滑轨",           "孙芳",   2,  380.00, 12, "2026-05-20", "2026-06-18", None, "INSPECTION",  False, None),
    # READY_TO_SHIP：补 2
    ("E42FX1020107106", "精研弹簧",           "陈伟杰", 6,   45.00, 7,  "2026-05-15", "2026-06-25", None, "READY_TO_SHIP", False, None),
    ("L22-9012-D",      "测试架连接件",       "李娜",   8,   85.00, 15, "2026-05-28", "2026-07-15", None, "READY_TO_SHIP", False, None),
    # DELIVERED：补 3
    ("E42804FZJ076108", "精研压板",           "林雪强", 1, 1050.00, 4,  "2026-05-25", "2026-06-30", "2026-07-02", "DELIVERED", False, None),
    ("E42BZCF90491104", "激光定位销",         "王莉",   6,   85.00, 6,  "2026-05-12", "2026-06-25", "2026-06-28", "DELIVERED", False, None),
    ("F42HG-2200-4",    "清洗密封圈",         "郑浩",   8,   35.00, 8,  "2026-05-08", "2026-06-20", "2026-06-23", "DELIVERED", False, None),
    # REPAIRING ×5
    ("E42FX1020107107", "精研上模",           "陈伟杰", 1, 2200.00, 7,  "2026-05-08", "2026-06-15", None, "REPAIRING",   True,  None),
    ("L21-7884-E",      "分选感应块",         "张磊",   2,  180.00, 13, "2026-04-28", "2026-06-12", None, "REPAIRING",   True,  None),
    ("F42JC-7711-4",    "检验支架",           "孙芳",   2,  480.00, 12, "2026-05-05", "2026-06-18", None, "REPAIRING",   False, None),
    ("F42HG-2200-5",    "清洗过滤网",         "郑浩",   1,  280.00, 8,  "2026-05-05", "2026-06-18", None, "REPAIRING",   False, None),
    ("E42DZ-5550-B",    "绕线导轨",           "吴敏",   1, 5400.00, 7,  "2026-05-28", "2026-07-30", None, "REPAIRING",   False, None),
    # COMPLETED：补 3
    ("E42804FZJ076109", "精研导套",           "林雪强", 1,  680.00, 4,  "2026-04-08", "2026-05-22", "2026-05-20", "COMPLETED", False, None),
    ("E42BZCF90491105", "激光备用夹",         "王莉",   1, 1900.00, 6,  "2026-04-20", "2026-06-05", "2026-06-02", "COMPLETED", False, None),
    ("L22-9012-E",      "测试架盖板",         "李娜",   1, 1200.00, 15, "2026-04-12", "2026-05-28", "2026-05-26", "COMPLETED", False, None),
    # CANCELLED：补 3
    ("E42804FZJ076110", "精研隔套",           "林雪强", 2,  340.00, 4,  "2026-05-15", "2026-07-10", None, "CANCELLED",  False, None),
    ("L21-7884-F",      "分选废品盒",         "张磊",   1,  150.00, 13, "2026-05-02", "2026-06-10", None, "CANCELLED",  True,  None),
    ("F42HG-2200-6",    "清洗试验件",         "郑浩",   1,  420.00, 8,  "2026-05-10", "2026-06-12", None, "CANCELLED",  False, None),
    # 凑数（再补 1 PENDING + 1 READY_TO_SHIP + 1 DELIVERED + 1 CANCELLED）
    ("E42FX1020107108", "精研调整块",         "陈伟杰", 2,  640.00, 7,  "2026-06-20", "2026-08-05", None, "PENDING",        False, None),
    ("L22-9012-F",      "测试架导轨",         "李娜",   2,  260.00, 15, "2026-05-25", "2026-07-10", None, "READY_TO_SHIP",  False, None),
    ("F42JC-7711-5",    "检验转接板",         "孙芳",   1,  560.00, 12, "2026-05-08", "2026-06-12", "2026-06-15", "DELIVERED", False, None),
    ("E42DZ-5550-C",    "绕线试验件",         "吴敏",   1,  780.00, 7,  "2026-05-18", "2026-07-20", None, "CANCELLED",      False, None),
]
# 验证：状态分布
assert len(_PARTS) == 50, f"expect 50 parts, got {len(_PARTS)}"


# =============================================================================
# 升级
# =============================================================================
def upgrade() -> None:
    from datetime import date, datetime, timezone
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    def _d(s: str | None) -> date | None:
        return date.fromisoformat(s) if s else None

    # 0. 清理可能的残留（dev 期反复 rerun 保护）。
    op.execute("DELETE FROM t_part")
    op.execute("DELETE FROM t_assembly")
    op.execute("DELETE FROM t_worker")
    op.execute("DELETE FROM t_customer")
    op.execute("UPDATE t_serial_counter SET counter = 0 WHERE prefix IN ('F', 'L')")

    # 1. 客户：20 条
    customer_rows = [
        {
            "id": cid, "name": name, "parent_id": parent_id,
            "created_at": now, "updated_at": now, "deleted_at": None,
        }
        for cid, name, parent_id in _CUSTOMERS
    ]
    op.bulk_insert(sa.table("t_customer",
        sa.column("id", sa.BigInteger),
        sa.column("name", sa.String),
        sa.column("parent_id", sa.BigInteger),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
        sa.column("deleted_at", sa.DateTime),
    ), customer_rows)

    # 2. 工人：8 条（雪花 ID 在 Python 端生成；记录 badge→id 映射给 part 行用）
    worker_id_by_badge: dict[str, int] = {}
    worker_rows = []
    for badge, name, id_card, phone, is_active in _WORKERS:
        wid = new_id()
        worker_id_by_badge[badge] = wid
        worker_rows.append({
            "id": wid,
            "badge_code": badge, "name": name,
            "id_card_no": id_card, "phone": phone,
            "is_active": is_active,
            "created_at": now, "updated_at": now, "deleted_at": None,
        })
    op.bulk_insert(sa.table("t_worker",
        sa.column("id", sa.BigInteger),
        sa.column("badge_code", sa.String),
        sa.column("name", sa.String),
        sa.column("id_card_no", sa.String),
        sa.column("phone", sa.String),
        sa.column("is_active", sa.Boolean),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
        sa.column("deleted_at", sa.DateTime),
    ), worker_rows)

    # 3. 装配体：10 条
    assembly_ids: list[int] = []
    assembly_rows = []
    for drawing_no, name, applicant, cust_id, req_d, plan_d, urgent, status in _ASSEMBLIES:
        aid = new_id()
        assembly_ids.append(aid)
        assembly_rows.append({
            "id": aid,
            "drawing_no": drawing_no, "name": name, "applicant_name": applicant,
            "customer_id": cust_id,
            "request_date": _d(req_d),
            "planned_delivery_date": _d(plan_d),
            "actual_delivery_date": None,
            "is_urgent": urgent, "status": status,
            "created_at": now, "updated_at": now, "deleted_at": None,
        })
    op.bulk_insert(sa.table("t_assembly",
        sa.column("id", sa.BigInteger),
        sa.column("drawing_no", sa.String),
        sa.column("name", sa.String),
        sa.column("applicant_name", sa.String),
        sa.column("customer_id", sa.BigInteger),
        sa.column("request_date", sa.Date),
        sa.column("planned_delivery_date", sa.Date),
        sa.column("actual_delivery_date", sa.Date),
        sa.column("is_urgent", sa.Boolean),
        sa.column("status", sa.String),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
        sa.column("deleted_at", sa.DateTime),
    ), assembly_rows)

    # 4. 零件：50 条
    #    serial_no 按一级客户前缀分配：法拉 F、路达 L；COMPLETED/CANCELLED 置 NULL。
    #    IN_PROCESS 时给一个 current_worker_id：根据 _WORKER_HOLDS 反向查。
    f_counter = 1000
    l_counter = 1000
    drawing_to_worker_id: dict[str, int] = {}
    for badge, drawings in _WORKER_HOLDS.items():
        for d in drawings:
            drawing_to_worker_id[d] = worker_id_by_badge[badge]

    part_rows = []
    for drawing_no, name, applicant, qty, unit_price, cust_id, req_d, plan_d, actual_d, status, urgent, assy_idx in _PARTS:
        if cust_id in (1, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12):  # 法拉
            serial_no = f"F{f_counter}"
            f_counter += 1
        else:  # 路达
            serial_no = f"L{l_counter}"
            l_counter += 1

        if status in ("COMPLETED", "CANCELLED"):
            serial_no = None

        current_worker_id: int | None = drawing_to_worker_id.get(drawing_no)
        req_date_obj = _d(req_d)
        released_at = None
        if status in ("READY", "IN_PROCESS", "INSPECTION", "READY_TO_SHIP",
                      "DELIVERED", "REPAIRING", "COMPLETED"):
            released_at = datetime.combine(req_date_obj, datetime.min.time())

        part_rows.append({
            "id": new_id(),
            "serial_no": serial_no,
            "name": name, "drawing_no": drawing_no, "applicant_name": applicant,
            "quantity": qty,
            "unit_price": unit_price,
            "total_price": unit_price * qty,
            "request_date": req_date_obj,
            "planned_delivery_date": _d(plan_d),
            "actual_delivery_date": _d(actual_d),
            "status": status, "is_urgent": urgent,
            "current_worker_id": current_worker_id,
            "released_at": released_at,
            "customer_id": cust_id,
            "assembly_id": assembly_ids[assy_idx] if assy_idx is not None else None,
            "created_at": now, "updated_at": now, "deleted_at": None,
        })
    op.bulk_insert(sa.table("t_part",
        sa.column("id", sa.BigInteger),
        sa.column("serial_no", sa.String),
        sa.column("name", sa.String),
        sa.column("drawing_no", sa.String),
        sa.column("applicant_name", sa.String),
        sa.column("quantity", sa.Integer),
        sa.column("unit_price", sa.Numeric),
        sa.column("total_price", sa.Numeric),
        sa.column("request_date", sa.Date),
        sa.column("planned_delivery_date", sa.Date),
        sa.column("actual_delivery_date", sa.Date),
        sa.column("status", sa.String),
        sa.column("is_urgent", sa.Boolean),
        sa.column("current_worker_id", sa.BigInteger),
        sa.column("released_at", sa.DateTime),
        sa.column("customer_id", sa.BigInteger),
        sa.column("assembly_id", sa.BigInteger),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
        sa.column("deleted_at", sa.DateTime),
    ), part_rows)

    # 5. 更新 t_serial_counter counter 到种子用到的最大号
    op.execute(
        f"UPDATE t_serial_counter SET counter = {f_counter}, updated_at = now() "
        f"WHERE prefix = 'F'"
    )
    op.execute(
        f"UPDATE t_serial_counter SET counter = {l_counter}, updated_at = now() "
        f"WHERE prefix = 'L'"
    )


def downgrade() -> None:
    # 清掉所有种子数据（不删 schema）
    op.execute("DELETE FROM t_part")
    op.execute("DELETE FROM t_assembly")
    op.execute("DELETE FROM t_worker")
    op.execute("DELETE FROM t_customer")
    op.execute("UPDATE t_serial_counter SET counter = 0 WHERE prefix IN ('F', 'L')")
