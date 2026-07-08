"""init_seed: load all seed data — serial counters, customers, workers, assemblies,
parts, users, shelves, menus

Revision ID: 000000000002
Revises: 000000000001
Create Date: 2026-07-02

说明：
- 测试用种子数据，**不**进入生产库。仅在本地 dev 库跑。
- 整合原 5 个迁移中的全部 seed。
- 数据分布：
  - 20 条 t_customer：2 个一级（法拉电子/路达）+ 18 个二级叶子节点。
  - 8 条 t_worker：5 人在职 + 2 人在职空闲 + 1 人停用。
  - 3 条 t_shelf：PROD-A1 / PROD-B1（生产区）+ INSP-I1（品检区）。
  - 10 条 t_assembly：4 态分布 = 2 PENDING + 5 IN_PROCESS + 2 COMPLETED + 1 CANCELLED。
  - 50 条 t_part：覆盖 PartStatus 全 8 状态均匀分布
    (PENDING×6 / IN_PROCESS×12 / INSPECTION×5 / READY_TO_SHIP×5
     / DELIVERED×6 / REPAIRING×5 / COMPLETED×6 / CANCELLED×5)；
    12 条加急；16 条挂装配体。
  - IN_PROCESS 零件随机分布在工人手中和生产货架上（固定种子可复现）；
    INSPECTION 零件全部放在品检货架 INSP-I1。
  - location 严格匹配 status：
    PENDING→OFFICE, IN_PROCESS(worker)→WORKER,
    IN_PROCESS(shelf)→PRODUCTION_SHELF, INSPECTION→INSPECTION_SHELF,
    其余状态→NULL。
  - 1 admin MANAGER + 3 SHELF_ACCOUNT 账号 + 角色关联。
  - 12 menu + MANAGER/SHELF_ACCOUNT 角色菜单分配。
- 装配体状态与子件状态自洽。
- upgrade 头部会清理可能的残留（dev 期反复 rerun 累积），保证幂等。
- downgrade 清理所有种子（不删 schema）。
"""
import random
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from utils.id_gen import new_id

revision: str = "000000000002"
down_revision: Union[str, None] = "000000000001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 固定随机种子，保证每次运行种子数据分布一致
_RANDOM_SEED = 42


# =============================================================================
# 客户数据：2 个一级 + 18 个二级
# 2026-07-07 改造：t_customer 改雪花 ID 后，seed 不能再显式指定 id=1..20；
# 改为 (name, parent_name) 元组，运行时按 parent_name→id 解析。
# =============================================================================
_CUSTOMERS: list[tuple[str, str | None]] = [
    ("法拉电子",   None),
    ("路达",       None),
    ("母排厂",     "法拉电子"),
    ("母排厂一组", "母排厂"),
    ("母排厂二组", "母排厂"),
    ("薄膜电容厂", "法拉电子"),
    ("变压器厂",   "法拉电子"),
    ("电极箔厂",   "法拉电子"),
    ("组件一厂",   "法拉电子"),
    ("组件二厂",   "法拉电子"),
    ("研发部",     "法拉电子"),
    ("品质部",     "法拉电子"),
    ("开发一部",   "路达"),
    ("开发二部",   "路达"),
    ("生产一部",   "路达"),
    ("生产二部",   "路达"),
    ("装配组",     "路达"),
    ("品质部",     "路达"),
    ("采购部",     "路达"),
    ("销售部",     "路达"),
]


def _customer_id(name_to_id: dict[str, int], name: str) -> int:
    """按 name 解析 customer_id（test 期望 1-20 的别名可由迁移完成后
    数据库实际值回查；这里假设同一迁移链中 name_to_id 已建好）。"""
    cid = name_to_id.get(name)
    if cid is None:
        raise RuntimeError(
            f"seed 引用了未定义的一级客户 name={name!r}；检查 _CUSTOMERS / _ASSEMBLIES / _PARTS 是否一致",
        )
    return cid


# =============================================================================
# 工人数据：20 条（覆盖 9 个工种，1 未分配 + 1 停用）
# 工种分配见 _WORK_TYPE_SEEDS；
# (badge_code, name, id_card_no, phone, is_active, work_type_code_or_None)
# =============================================================================
_WORKERS = [
    # ---------- 车床 (3人) ----------
    ("Z001", "张志强", "110101198501012131", "13800138001", True,  "车床"),
    ("Z009", "刘建国", "110101198801015432", "13800138009", True,  "车床"),
    ("Z010", "陈明辉", "110101199003056789", "13800138010", True,  "车床"),
    # ---------- 铣床 (2人) ----------
    ("Z002", "王建国", "110101198803153217", "13800138002", True,  "铣床"),
    ("Z011", "林永强", "110101199106151234", "13800138011", True,  "铣床"),
    # ---------- 磨床 (1人) ----------
    ("Z012", "郑国栋", "110101198903058765", "13800138012", True,  "磨床"),
    # ---------- 线切割 (2人) ----------
    ("Z006", "周小杰", "110101199612081218", "13800138006", True,  "线切割"),
    ("Z013", "许志远", "110101198706154321", "13800138013", True,  "线切割"),
    # ---------- CNC操机 (3人) ----------
    ("Z003", "李大伟", "110101199002204518", "13800138003", True,  "CNC操机"),
    ("Z014", "苏文博", "110101199205305678", "13800138014", True,  "CNC操机"),
    ("Z015", "何俊杰", "110101199308109012", "13800138015", True,  "CNC操机"),
    # ---------- CNC编程 (1人) ----------
    ("Z016", "潘志明", "110101198905152345", "13800138016", True,  "CNC编程"),
    # ---------- 品检 (3人) ----------
    ("Z004", "赵小敏", "110101199205156427", "13800138004", True,  "品检"),
    ("Z005", "陈丽华", "110101199408102323", "13800138005", True,  "品检"),
    ("Z017", "梁美玲", "110101199506204321", "13800138017", True,  "品检"),
    # ---------- 文员 (1人) ----------
    ("Z018", "吴晓燕", "110101199707303210", "13800138018", True,  "文员"),
    # ---------- 送货司机 (2人) ----------
    ("Z008", "吴文军", "110101198912305216", "13800138008", True,  "送货司机"),
    ("Z019", "冯国威", "110101198507189876", "13800138019", True,  "送货司机"),
    # ---------- 未分配 / 停用 ----------
    ("Z007", "黄文斌", "110101198705123910", "13800138007", False, None),  # 停用+未分配
    ("Z020", "唐志强", "110101198807151234", "13800138020", True,  None),  # 在职但未分配
]


# =============================================================================
# 工种 seed（9 条）
# =============================================================================
_WORK_TYPE_SEEDS: list[tuple[str, str, int]] = [
    # (code, name, sort_order)
    ("车床",     "车床工",   10),
    ("铣床",     "铣床工",   20),
    ("磨床",     "磨床工",   30),
    ("线切割",   "线切割工", 40),
    ("CNC操机",  "CNC操机",  50),
    ("CNC编程",  "CNC编程",  60),
    ("品检",     "品检员",   70),
    ("文员",     "文员",     80),
    ("送货司机", "送货司机", 90),
]


# =============================================================================
# 工序 seed（8 条）
# =============================================================================
_PROCESS_SEEDS: list[tuple[str, str, str, bool, int]] = [
    # (code, name, category, is_inspection, sort_order)
    ("车",     "车床加工", "INHOUSE",   False, 10),
    ("铣",     "铣床加工", "INHOUSE",   False, 20),
    ("磨",     "磨床加工", "INHOUSE",   False, 30),
    ("线切割", "线切割",   "INHOUSE",   False, 40),
    ("CNC",    "CNC加工",  "INHOUSE",   False, 50),
    ("热处理", "热处理",   "OUTSOURCE", False, 60),
    ("电镀",   "电镀",     "OUTSOURCE", False, 70),
    ("阳极",   "阳极氧化", "OUTSOURCE", False, 80),
]


# =============================================================================
# 工种↔工序 初始映射
# key = work_type_code, value = list[process_code]
# =============================================================================
_WORK_TYPE_PROCESS_MAPPING: dict[str, list[str]] = {
    "车床":     ["车"],
    "铣床":     ["铣"],
    "磨床":     ["磨"],
    "线切割":   ["线切割"],
    "CNC操机":  ["CNC"],
    "CNC编程":  ["CNC"],
    "品检":     ["车", "铣", "磨", "线切割", "CNC", "热处理", "电镀", "阳极"],
    # 文员 / 送货司机 无映射 → 不在 dict 中
}


# =============================================================================
# 零件 next_process 分配：idx % 5 → process code
# =============================================================================
_PROCESS_CODE_BY_MOD: dict[int, str] = {
    0: "车",
    1: "铣",
    2: "磨",
    3: "CNC",
    4: "线切割",
}


# =============================================================================
# 货架数据：2 生产 + 1 品检
# =============================================================================
_SHELVES = [
    # (code, name, zone)
    ("PROD-A1", "生产区-A1 货架", "PRODUCTION"),
    ("PROD-B1", "生产区-B1 货架", "PRODUCTION"),
    ("INSP-I1", "品检区-1 货架",  "INSPECTION"),
]


# =============================================================================
# 装配体数据：10 条
# 2026-07-07 改造：customer_id 由 int 改为 customer_name，运行时 name_to_id 解析。
# =============================================================================
_ASSEMBLIES: list[tuple[str, str, str, str, str, str, bool, str]] = [
    # (drawing_no, name, applicant, customer_name, request_date, planned, is_urgent, status)
    ("E42804FZJ076100", "精研挡料座总装", "林雪强", "母排厂一组", "2026-06-10", "2026-07-25", True,  "IN_PROCESS"),
    ("E42FX1020107101", "精研挡料座",     "陈伟杰", "变压器厂",   "2026-05-20", "2026-06-30", False, "COMPLETED"),
    ("E42BZCF90491101", "激光工位固定内夹", "王莉",   "薄膜电容厂", "2026-06-18", "2026-07-30", False, "IN_PROCESS"),
    ("L21-7884",        "分选夹爪总成",   "张磊",   "开发一部",   "2026-05-12", "2026-06-28", True,  "IN_PROCESS"),
    ("L22-9012",        "总装测试架",     "李娜",   "生产一部",   "2026-06-15", "2026-08-05", False, "PENDING"),
    ("F42XJ-3380",      "校形定位块",     "赵勇",   "母排厂二组", "2026-06-20", "2026-08-10", False, "IN_PROCESS"),
    ("F42JC-7711",      "检验夹具",       "孙芳",   "品质部",     "2026-05-25", "2026-06-20", False, "COMPLETED"),
    ("L23-0114",        "自动贴标机底座", "周健",   "装配组",     "2026-06-22", "2026-07-28", True,  "IN_PROCESS"),
    ("E42DZ-5550",      "绕线模具",       "吴敏",   "变压器厂",   "2026-06-05", "2026-08-15", False, "CANCELLED"),
    ("F42HG-2200",      "清洗工装",       "郑浩",   "电极箔厂",   "2026-05-18", "2026-06-25", False, "PENDING"),
]


# =============================================================================
# 零件数据：50 条
# 装配体 0-8 各 1-4 子件，装配体 9 无子件
# 状态分布：8 状态均匀（7/12/5/5/6/5/6/5），加急 12 条
# 2026-07-07 改造：customer_id 改为 customer_name 引用，运行时解析。
# =============================================================================
_PARTS: list[tuple] = [
    # (drawing_no, name, applicant, qty, unit_price, customer_name,
    #  request_date, planned, actual_or_None, status, is_urgent, assembly_idx)
    # —— 装配体 0 子件（IN_PROCESS/INSPECTION/READY_TO_SHIP/DELIVERED） ——
    ("E42804FZJ076101", "精研挡料座上模",     "林雪强", 1, 2200.00, "母排厂一组", "2026-06-10", "2026-07-25", None, "IN_PROCESS",     True,  0),
    ("E42804FZJ076102", "精研挡料座下模",     "林雪强", 1, 2400.00, "母排厂一组", "2026-06-10", "2026-07-25", None, "INSPECTION",     True,  0),
    ("E42804FZJ076103", "精研挡料座侧板",     "林雪强", 2,  680.00, "母排厂一组", "2026-06-10", "2026-07-25", None, "READY_TO_SHIP",  True,  0),
    ("E42804FZJ076104", "精研挡料座紧固件",   "林雪强", 4,   45.00, "母排厂一组", "2026-06-10", "2026-07-25", "2026-07-15", "DELIVERED", True,  0),
    # —— 装配体 1 子件（COMPLETED×2） ——
    ("E42FX1020107102", "底座压板",           "陈伟杰", 3,  680.00, "变压器厂",   "2026-05-20", "2026-06-30", "2026-06-25", "COMPLETED", False, 1),
    ("E42FX1020107103", "精研滑块",           "陈伟杰", 2,  720.00, "变压器厂",   "2026-05-20", "2026-06-30", "2026-06-26", "COMPLETED", False, 1),
    # —— 装配体 2 子件（1 PENDING + 1 IN_PROCESS） ——
    ("E42BZCF90491102", "激光内夹A",          "王莉",   1, 2100.00, "薄膜电容厂", "2026-06-18", "2026-07-30", None, "PENDING",     False, 2),
    ("E42BZCF90491103", "激光内夹B",          "王莉",   1, 2300.00, "薄膜电容厂", "2026-06-15", "2026-07-28", None, "IN_PROCESS", False, 2),
    # —— 装配体 3 子件（INSPECTION/READY_TO_SHIP/DELIVERED） ——
    ("L21-7884-A",      "分选左夹爪",         "张磊",   2,  560.00, "开发一部", "2026-05-10", "2026-06-26", None, "INSPECTION",     True,  3),
    ("L21-7884-B",      "分选右夹爪",         "张磊",   2,  560.00, "开发一部", "2026-05-08", "2026-06-24", None, "READY_TO_SHIP",  True,  3),
    ("L21-7884-C",      "分选连接板",         "张磊",   1,  920.00, "开发一部", "2026-05-05", "2026-06-20", "2026-06-22", "DELIVERED",  True,  3),
    # —— 装配体 4 子件（PENDING） ——
    ("L22-9012-A",      "测试架立柱",         "李娜",   4,  320.00, "生产一部", "2026-06-15", "2026-08-05", None, "PENDING",     False, 4),
    # —— 装配体 5 子件（IN_PROCESS） ——
    ("F42XJ-3380-1",    "校形定位块",         "赵勇",   1, 4500.00, "母排厂二组", "2026-06-20", "2026-08-10", None, "IN_PROCESS", False, 5),
    # —— 装配体 6 子件（COMPLETED） ——
    ("F42JC-7711-1",    "检验夹具手柄",       "孙芳",   1,  220.00, "品质部", "2026-05-25", "2026-06-20", "2026-06-18", "COMPLETED", False, 6),
    # —— 装配体 7 子件（INSPECTION） ——
    ("L23-0114-A",      "贴标机底板",         "周健",   1, 3200.00, "装配组", "2026-06-22", "2026-07-28", None, "INSPECTION", True,  7),
    # —— 装配体 8 子件（CANCELLED 级联） ——
    ("E42DZ-5550-A",    "绕线模具芯",         "吴敏",   1, 6800.00, "变压器厂", "2026-06-05", "2026-08-15", None, "CANCELLED", False, 8),
    # —— 剩余 34 条（不挂装配体），凑齐 8 状态均匀分布 ——
    # PENDING：补 4
    ("E42804FZJ076105", "精研定位板",         "林雪强", 1,  950.00, "母排厂一组", "2026-06-18", "2026-08-01", None, "PENDING",     False, None),
    ("L22-9012-B",      "测试架横梁",         "李娜",   2,  780.00, "生产一部", "2026-06-20", "2026-08-08", None, "PENDING",     False, None),
    ("F42HG-2200-2",    "清洗喷嘴",           "郑浩",   4,  240.00, "电极箔厂", "2026-05-25", "2026-07-05", None, "PENDING",     False, None),
    ("E42FX1020107108", "精研调整块",         "陈伟杰", 2,  640.00, "变压器厂", "2026-06-20", "2026-08-05", None, "PENDING",     False, None),
    # IN_PROCESS：9 条（原 3 + 原 READY 6 条改为 IN_PROCESS）
    ("E42804FZJ076106", "精研上模备件",       "林雪强", 1, 1800.00, "母排厂一组", "2026-06-15", "2026-07-30", None, "IN_PROCESS",  False, None),
    ("E42FX1020107104", "精研端盖",           "陈伟杰", 1, 1500.00, "变压器厂", "2026-05-22", "2026-07-08", None, "IN_PROCESS",  False, None),
    ("L21-7884-D",      "分选气缸座",         "张磊",   2,  340.00, "开发一部", "2026-05-15", "2026-06-28", None, "IN_PROCESS",  False, None),
    ("F42JC-7711-2",    "检验底座",           "孙芳",   1,  980.00, "品质部", "2026-05-26", "2026-06-25", None, "IN_PROCESS",  False, None),
    ("L23-0114-B",      "贴标机侧板",         "周健",   2,  640.00, "装配组", "2026-06-23", "2026-07-30", None, "IN_PROCESS",  True,  None),
    ("F42HG-2200-3",    "清洗托盘",           "郑浩",   2,  420.00, "电极箔厂", "2026-05-19", "2026-06-30", None, "IN_PROCESS",  False, None),
    ("E42FX1020107105", "精研压板",           "陈伟杰", 2,  640.00, "变压器厂", "2026-05-18", "2026-06-28", None, "IN_PROCESS",  False, None),
    ("L22-9012-C",      "测试架底座",         "李娜",   1, 1800.00, "生产一部", "2026-06-12", "2026-08-05", None, "IN_PROCESS",  False, None),
    ("F42XJ-3380-2",    "校形下模",           "赵勇",   1, 4800.00, "母排厂二组", "2026-06-18", "2026-08-12", None, "IN_PROCESS",  False, None),
    # INSPECTION：补 2
    ("E42804FZJ076107", "精研垫片",           "林雪强", 10,  18.00, "母排厂一组", "2026-05-12", "2026-06-25", None, "INSPECTION",  False, None),
    ("F42JC-7711-3",    "检验滑轨",           "孙芳",   2,  380.00, "品质部", "2026-05-20", "2026-06-18", None, "INSPECTION",  False, None),
    # READY_TO_SHIP：补 3
    ("E42FX1020107106", "精研弹簧",           "陈伟杰", 6,   45.00, "变压器厂", "2026-05-15", "2026-06-25", None, "READY_TO_SHIP", False, None),
    ("L22-9012-D",      "测试架连接件",       "李娜",   8,   85.00, "生产一部", "2026-05-28", "2026-07-15", None, "READY_TO_SHIP", False, None),
    ("L22-9012-F",      "测试架导轨",         "李娜",   2,  260.00, "生产一部", "2026-05-25", "2026-07-10", None, "READY_TO_SHIP", False, None),
    # DELIVERED：补 4
    ("E42804FZJ076108", "精研压板",           "林雪强", 1, 1050.00, "母排厂一组", "2026-05-25", "2026-06-30", "2026-07-02", "DELIVERED", False, None),
    ("E42BZCF90491104", "激光定位销",         "王莉",   6,   85.00, "薄膜电容厂", "2026-05-12", "2026-06-25", "2026-06-28", "DELIVERED", False, None),
    ("F42HG-2200-4",    "清洗密封圈",         "郑浩",   8,   35.00, "电极箔厂", "2026-05-08", "2026-06-20", "2026-06-23", "DELIVERED", False, None),
    ("F42JC-7711-5",    "检验转接板",         "孙芳",   1,  560.00, "品质部", "2026-05-08", "2026-06-12", "2026-06-15", "DELIVERED", False, None),
    # REPAIRING ×5
    ("E42FX1020107107", "精研上模",           "陈伟杰", 1, 2200.00, "变压器厂", "2026-05-08", "2026-06-15", None, "REPAIRING",   True,  None),
    ("L21-7884-E",      "分选感应块",         "张磊",   2,  180.00, "开发一部", "2026-04-28", "2026-06-12", None, "REPAIRING",   True,  None),
    ("F42JC-7711-4",    "检验支架",           "孙芳",   2,  480.00, "品质部", "2026-05-05", "2026-06-18", None, "REPAIRING",   False, None),
    ("F42HG-2200-5",    "清洗过滤网",         "郑浩",   1,  280.00, "电极箔厂", "2026-05-05", "2026-06-18", None, "REPAIRING",   False, None),
    ("E42DZ-5550-B",    "绕线导轨",           "吴敏",   1, 5400.00, "变压器厂", "2026-05-28", "2026-07-30", None, "REPAIRING",   False, None),
    # COMPLETED：补 3
    ("E42804FZJ076109", "精研导套",           "林雪强", 1,  680.00, "母排厂一组", "2026-04-08", "2026-05-22", "2026-05-20", "COMPLETED", False, None),
    ("E42BZCF90491105", "激光备用夹",         "王莉",   1, 1900.00, "薄膜电容厂", "2026-04-20", "2026-06-05", "2026-06-02", "COMPLETED", False, None),
    ("L22-9012-E",      "测试架盖板",         "李娜",   1, 1200.00, "生产一部", "2026-04-12", "2026-05-28", "2026-05-26", "COMPLETED", False, None),
    # CANCELLED：补 4
    ("E42804FZJ076110", "精研隔套",           "林雪强", 2,  340.00, "母排厂一组", "2026-05-15", "2026-07-10", None, "CANCELLED",  False, None),
    ("L21-7884-F",      "分选废品盒",         "张磊",   1,  150.00, "开发一部", "2026-05-02", "2026-06-10", None, "CANCELLED",  True,  None),
    ("F42HG-2200-6",    "清洗试验件",         "郑浩",   1,  420.00, "电极箔厂", "2026-05-10", "2026-06-12", None, "CANCELLED",  False, None),
    ("E42DZ-5550-C",    "绕线试验件",         "吴敏",   1,  780.00, "变压器厂", "2026-05-18", "2026-07-20", None, "CANCELLED",  False, None),
]
assert len(_PARTS) == 50, f"expect 50 parts, got {len(_PARTS)}"


# =============================================================================
# 菜单 seed
# =============================================================================
_MENU_SEED: list[dict] = [
    {"code": "home",            "parent": None,           "title": "首页",       "path": "/dashboard",      "icon": "House",     "sort_order": 10},
    {"code": "order_group",     "parent": None,           "title": "订单管理",   "path": None,              "icon": "Tickets",   "sort_order": 20},
    {"code": "parts_list",      "parent": "order_group",  "title": "零件一览",   "path": "/parts",          "icon": "Box",       "sort_order": 10},
    {"code": "parts_new",       "parent": "order_group",  "title": "新建零件",   "path": "/parts/new",      "icon": "Plus",      "sort_order": 20},
    {"code": "assemblies_list", "parent": "order_group",  "title": "装配件一览", "path": "/assemblies",     "icon": "Connection","sort_order": 30},
    {"code": "assemblies_new",  "parent": "order_group",  "title": "新建装配件", "path": "/assemblies/new", "icon": "Plus",      "sort_order": 40},
    {"code": "auth_group",      "parent": None,           "title": "权限管理",   "path": None,              "icon": "Key",       "sort_order": 30},
    {"code": "workers_list",    "parent": "auth_group",   "title": "工人一览",   "path": "/workers",        "icon": "User",      "sort_order": 10},
    {"code": "users_list",      "parent": "auth_group",   "title": "账号管理",   "path": "/users",          "icon": "List",      "sort_order": 20},
    {"code": "floor_group",     "parent": None,           "title": "车间",       "path": None,              "icon": "Tools",     "sort_order": 40},
    {"code": "shelves_list",    "parent": "floor_group",  "title": "货架管理",   "path": "/shelves",        "icon": "Platform",  "sort_order": 10},
    {"code": "scan_badge",      "parent": "floor_group",  "title": "扫码台",     "path": "/scan/badge",     "icon": "Promotion", "sort_order": 20},
]

_ROLE_MENU_SEED: list[tuple[str, str]] = [
    ("MANAGER", "home"),
    ("MANAGER", "order_group"),
    ("MANAGER", "parts_list"),
    ("MANAGER", "parts_new"),
    ("MANAGER", "assemblies_list"),
    ("MANAGER", "assemblies_new"),
    ("MANAGER", "auth_group"),
    ("MANAGER", "workers_list"),
    ("MANAGER", "users_list"),
    ("MANAGER", "floor_group"),
    ("MANAGER", "shelves_list"),
    ("MANAGER", "scan_badge"),
    ("SHELF_ACCOUNT", "home"),
    ("SHELF_ACCOUNT", "floor_group"),
    ("SHELF_ACCOUNT", "scan_badge"),
]


# =============================================================================
# location 推导
# =============================================================================
def _derive_location(status: str, holder_type: str | None) -> str | None:
    """根据状态 + holder 类型推导 location 值。

    holder_type: 'worker' | 'shelf' | None
    """
    if status == "PENDING":
        return "OFFICE"
    if status == "IN_PROCESS":
        if holder_type == "worker":
            return "WORKER"
        if holder_type == "shelf":
            return "PRODUCTION_SHELF"
        # 不应该出现 IN_PROCESS 但无 holder 的情况，但保留安全值
        return "PRODUCTION_SHELF"
    if status == "INSPECTION":
        return "INSPECTION_SHELF"
    # READY_TO_SHIP / DELIVERED / REPAIRING / COMPLETED / CANCELLED
    return None


# =============================================================================
# 随机分配 IN_PROCESS 零件到 worker 或 shelf（固定种子，可复现）
# =============================================================================
def _assign_in_process_holders(
    parts: list[tuple],
    active_worker_ids: list[int],
    shelf_ids: dict[str, int],
) -> tuple[dict[int, int | None], dict[int, str | None]]:
    """为 IN_PROCESS 零件随机分配 holder（工人或货架）。

    返回:
      holder_map: part_index → current_holder_id (worker_id 或 shelf_id)
      holder_type_map: part_index → 'worker' | 'shelf'
    """
    rng = random.Random(_RANDOM_SEED)

    # 找到所有 IN_PROCESS 零件索引
    in_process_indices = [
        i for i, p in enumerate(parts) if p[9] == "IN_PROCESS"
    ]

    # 生产货架列表
    prod_shelf_codes = ["PROD-A1", "PROD-B1"]

    # 约 40% 分配给工人（至少有 1 个给工人，体现 WORKER 状态）
    num_to_workers = max(1, len(in_process_indices) * 2 // 5)
    worker_bound_indices = set(rng.sample(in_process_indices, num_to_workers))

    holder_map: dict[int, int | None] = {}
    holder_type_map: dict[int, str | None] = {}

    for idx in in_process_indices:
        if idx in worker_bound_indices:
            holder_map[idx] = rng.choice(active_worker_ids)
            holder_type_map[idx] = "worker"
        else:
            shelf_code = rng.choice(prod_shelf_codes)
            holder_map[idx] = shelf_ids[shelf_code]
            holder_type_map[idx] = "shelf"

    # INSPECTION 零件全部放在品检货架
    for i, p in enumerate(parts):
        if p[9] == "INSPECTION":
            holder_map[i] = shelf_ids["INSP-I1"]
            holder_type_map[i] = "shelf"

    return holder_map, holder_type_map


# =============================================================================
# 升级
# =============================================================================
def upgrade() -> None:
    from datetime import date, datetime, timezone
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    def _d(s: str | None) -> date | None:
        return date.fromisoformat(s) if s else None

    # =========================================================================
    # 0. 清理可能的残留（dev 期反复 rerun 保护）
    # =========================================================================
    op.execute("DELETE FROM t_part_event")
    op.execute("DELETE FROM t_role_menu")
    op.execute("DELETE FROM t_menu")
    op.execute("DELETE FROM t_user_role")
    op.execute("DELETE FROM t_part")
    op.execute("DELETE FROM t_assembly")
    op.execute("DELETE FROM t_user")
    op.execute("DELETE FROM t_shelf")
    op.execute("DELETE FROM t_work_type_process")
    op.execute("DELETE FROM t_worker")
    op.execute("DELETE FROM t_work_type")
    op.execute("DELETE FROM t_process")
    op.execute("DELETE FROM t_customer")
    op.execute("DELETE FROM t_serial_counter")

    # =========================================================================
    # 1. 流水号种子：L / F / H，counter=0
    # =========================================================================
    op.execute(
        sa.text(
            "INSERT INTO t_serial_counter (prefix, counter) "
            "VALUES ('L', 0), ('F', 0), ('H', 0)"
        )
    )

    # =========================================================================
    # 2. 客户：20 条（雪花 ID；name → id 映射给后续 _ASSEMBLIES / _PARTS 用）
    # =========================================================================
    customer_rows = []
    name_to_id: dict[str, int] = {}
    # 阶段 1：插入一级客户（parent_id IS NULL）
    for name, parent_name in [c for c in _CUSTOMERS if c[1] is None]:
        new_pk = new_id()
        name_to_id[name] = new_pk
        customer_rows.append({
            "id": new_pk, "name": name, "parent_id": None,
            "created_at": now, "updated_at": now, "deleted_at": None,
        })
    # 阶段 2：插入二级客户，parent_id 用 name_to_id 解析
    for name, parent_name in [c for c in _CUSTOMERS if c[1] is not None]:
        new_pk = new_id()
        name_to_id[name] = new_pk
        customer_rows.append({
            "id": new_pk, "name": name, "parent_id": name_to_id[parent_name],
            "created_at": now, "updated_at": now, "deleted_at": None,
        })
    op.bulk_insert(sa.table("t_customer",
        sa.column("id", sa.BigInteger),
        sa.column("name", sa.String),
        sa.column("parent_id", sa.BigInteger),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
        sa.column("deleted_at", sa.DateTime),
    ), customer_rows)

    # =========================================================================
    # 3. 工种：9 条
    # =========================================================================
    wt_ids: dict[str, int] = {}
    wt_rows = []
    for code, name, sort_order in _WORK_TYPE_SEEDS:
        wid = new_id()
        wt_ids[code] = wid
        wt_rows.append({
            "id": wid, "code": code, "name": name, "sort_order": sort_order,
            "description": None,
            "created_at": now, "updated_at": now, "deleted_at": None,
        })
    op.bulk_insert(sa.table("t_work_type",
        sa.column("id", sa.BigInteger),
        sa.column("code", sa.String),
        sa.column("name", sa.String),
        sa.column("description", sa.String),
        sa.column("sort_order", sa.Integer),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
        sa.column("deleted_at", sa.DateTime),
    ), wt_rows)

    # =========================================================================
    # 4. 工序：8 条
    # =========================================================================
    proc_ids: dict[str, int] = {}
    proc_rows = []
    for code, name, category, is_insp, sort_order in _PROCESS_SEEDS:
        pid = new_id()
        proc_ids[code] = pid
        proc_rows.append({
            "id": pid, "code": code, "name": name,
            "category": category, "is_inspection": is_insp,
            "sort_order": sort_order, "description": None,
            "created_at": now, "updated_at": now, "deleted_at": None,
        })
    op.bulk_insert(sa.table("t_process",
        sa.column("id", sa.BigInteger),
        sa.column("code", sa.String),
        sa.column("name", sa.String),
        sa.column("category", sa.String),
        sa.column("is_inspection", sa.Boolean),
        sa.column("sort_order", sa.Integer),
        sa.column("description", sa.String),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
        sa.column("deleted_at", sa.DateTime),
    ), proc_rows)

    # =========================================================================
    # 5. 工种↔工序映射
    # =========================================================================
    wtp_rows: list[dict] = []
    for wt_code, p_codes in _WORK_TYPE_PROCESS_MAPPING.items():
        wt_id = wt_ids[wt_code]
        for sort_idx, p_code in enumerate(p_codes):
            p_id = proc_ids[p_code]
            wtp_rows.append({
                "id": new_id(),
                "work_type_id": wt_id,
                "process_id": p_id,
                "sort_order": sort_idx,
                "created_at": now, "updated_at": now, "deleted_at": None,
            })
    if wtp_rows:
        op.bulk_insert(sa.table("t_work_type_process",
            sa.column("id", sa.BigInteger),
            sa.column("work_type_id", sa.BigInteger),
            sa.column("process_id", sa.BigInteger),
            sa.column("sort_order", sa.Integer),
            sa.column("created_at", sa.DateTime),
            sa.column("updated_at", sa.DateTime),
            sa.column("deleted_at", sa.DateTime),
        ), wtp_rows)

    # =========================================================================
    # 6. 工人：20 条（含 work_type_id）
    # =========================================================================
    worker_id_by_badge: dict[str, int] = {}
    worker_rows = []
    for badge, name, id_card, phone, is_active, wt_code in _WORKERS:
        wid = new_id()
        worker_id_by_badge[badge] = wid
        worker_rows.append({
            "id": wid,
            "badge_code": badge, "name": name,
            "id_card_no": id_card, "phone": phone,
            "is_active": is_active,
            "work_type_id": wt_ids.get(wt_code) if wt_code else None,
            "created_at": now, "updated_at": now, "deleted_at": None,
        })
    op.bulk_insert(sa.table("t_worker",
        sa.column("id", sa.BigInteger),
        sa.column("badge_code", sa.String),
        sa.column("name", sa.String),
        sa.column("id_card_no", sa.String),
        sa.column("phone", sa.String),
        sa.column("is_active", sa.Boolean),
        sa.column("work_type_id", sa.BigInteger),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
        sa.column("deleted_at", sa.DateTime),
    ), worker_rows)
    active_worker_ids = [
        worker_id_by_badge[w[0]] for w in _WORKERS if w[4]  # is_active
    ]

    # =========================================================================
    # 7. 装配体：10 条（customer_name → customer_id 用 name_to_id 解析）
    # =========================================================================
    assembly_ids: list[int] = []
    assembly_rows = []
    for drawing_no, name, applicant, cust_name, req_d, plan_d, urgent, status in _ASSEMBLIES:
        aid = new_id()
        assembly_ids.append(aid)
        assembly_rows.append({
            "id": aid,
            "drawing_no": drawing_no, "name": name, "applicant_name": applicant,
            "customer_id": name_to_id[cust_name],
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

    # =========================================================================
    # 8. 货架：3 条（必须在零件之前创建，以便零件引用 shelf.id）
    # =========================================================================
    bind = op.get_bind()
    shelf_ids: dict[str, int] = {}
    for code, name, zone in _SHELVES:
        sid = new_id()
        shelf_ids[code] = sid
        bind.execute(
            sa.text(
                """
                INSERT INTO t_shelf (id, code, name, zone, is_active, created_at, updated_at)
                VALUES (:id, :code, :name, :zone, true, now(), now())
                """
            ),
            {"id": sid, "code": code, "name": name, "zone": zone},
        )

    # =========================================================================
    # 9. 零件：50 条（含 next_process_id）
    #    - IN_PROCESS 零件随机分配到工人或生产货架（固定种子可复现）
    #    - INSPECTION 零件全部放在品检货架 INSP-I1
    #    - serial_no 按一级客户前缀：法拉 F、路达 L；COMPLETED/CANCELLED 置 NULL
    #    - placed_at 对已下发过的状态设置为 request_date
    #    - next_process_id 根据 part index 模 5 分配到 车/铣/磨/CNC/线切割
    # =========================================================================
    holder_map, holder_type_map = _assign_in_process_holders(
        _PARTS, active_worker_ids, shelf_ids,
    )

    f_counter = 1000
    l_counter = 1000

    # 一级客户「法拉电子」及其所有子节点都用 F 流水号前缀；其余（路达）用 L。
    f_company_root = "法拉电子"

    def _is_under_f(cname: str) -> bool:
        """递归向上查父链：cust_name 自身或任意祖先是「法拉电子」→ True。"""
        cur = cname
        for _ in range(5):  # 树深度上限（防御用）
            if cur == f_company_root:
                return True
            row = next((c for c in _CUSTOMERS if c[0] == cur), None)
            if row is None or row[1] is None:
                return False
            cur = row[1]
        return False

    part_rows = []
    for i, (drawing_no, name, applicant, qty, unit_price, cust_name, req_d, plan_d, actual_d, status, urgent, assy_idx) in enumerate(_PARTS):
        cust_id = name_to_id[cust_name]
        if _is_under_f(cust_name):
            serial_no = f"F{f_counter}"
            f_counter += 1
        else:  # 路达
            serial_no = f"L{l_counter}"
            l_counter += 1

        if status in ("COMPLETED", "CANCELLED"):
            serial_no = None

        current_holder_id = holder_map.get(i)
        holder_type = holder_type_map.get(i)
        location = _derive_location(status, holder_type)

        req_date_obj = _d(req_d)
        placed_at = None
        if status in ("IN_PROCESS", "INSPECTION", "READY_TO_SHIP",
                      "DELIVERED", "REPAIRING", "COMPLETED"):
            placed_at = datetime.combine(req_date_obj, datetime.min.time())

        # next_process_id：按零件 index 模 5 分配
        p_code = _PROCESS_CODE_BY_MOD[i % 5]
        next_pid = proc_ids[p_code]
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
            "status": status,
            "location": location,
            "is_urgent": urgent,
            "current_holder_id": current_holder_id,
            "placed_at": placed_at,
            "next_process_id": next_pid,
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
        sa.column("location", sa.String),
        sa.column("is_urgent", sa.Boolean),
        sa.column("current_holder_id", sa.BigInteger),
        sa.column("placed_at", sa.DateTime),
        sa.column("next_process_id", sa.BigInteger),
        sa.column("customer_id", sa.BigInteger),
        sa.column("assembly_id", sa.BigInteger),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
        sa.column("deleted_at", sa.DateTime),
    ), part_rows)

    # =========================================================================
    # 10. 更新 t_serial_counter counter 到种子用到的最大号
    # =========================================================================
    op.execute(
        f"UPDATE t_serial_counter SET counter = {f_counter}, updated_at = now() "
        f"WHERE prefix = 'F'"
    )
    op.execute(
        f"UPDATE t_serial_counter SET counter = {l_counter}, updated_at = now() "
        f"WHERE prefix = 'L'"
    )

    # =========================================================================
    # 11. 用户 / 角色 seed（admin + 3 shelf accounts + 角色关联）
    # =========================================================================
    _seed_users(shelf_ids)

    # =========================================================================
    # 12. 菜单 / 角色菜单 seed
    # =========================================================================
    _seed_menus()

    # =========================================================================
    # 13. 设置菜单 seed（settings_root + 3 settings 子菜单 + MANAGER 关联）
    # =========================================================================
    _seed_settings_menus()


# =============================================================================
# 子函数：用户 / 角色 seed
# =============================================================================
def _seed_users(shelf_ids: dict[str, int]) -> None:
    """插入 1 admin + 3 shelf accounts + 角色关联。

    密码统一 `changeme`（bcrypt rounds=4，dev only）。
    货架已在零件之前创建，这里只创建用户和角色。
    """
    import bcrypt as _bc

    bind = op.get_bind()
    changeme_hash = _bc.hashpw(b"changeme", _bc.gensalt(rounds=4)).decode("utf-8")

    # 1) admin MANAGER
    admin_id = new_id()
    bind.execute(
        sa.text(
            """
            INSERT INTO t_user (id, username, password_hash, full_name, is_active, created_at, updated_at)
            VALUES (:id, 'admin', :pwd, '系统管理员', true, now(), now())
            ON CONFLICT (username) WHERE deleted_at IS NULL DO NOTHING
            """
        ),
        {"id": admin_id, "pwd": changeme_hash},
    )

    # 2) 3 个 SHELF_ACCOUNT 账号
    shelf_users = [
        ("proda1", "proda1", "生产-A1 操作员", "PROD-A1"),
        ("prodb1", "prodb1", "生产-B1 操作员", "PROD-B1"),
        ("inspi1", "inspi1", "品检-1 操作员", "INSP-I1"),
    ]
    for _, username, full_name, _shelf_code in shelf_users:
        uid = new_id()
        bind.execute(
            sa.text(
                """
                INSERT INTO t_user (id, username, password_hash, full_name, is_active, created_at, updated_at)
                VALUES (:id, :username, :pwd, :full_name, true, now(), now())
                ON CONFLICT (username) WHERE deleted_at IS NULL DO NOTHING
                """
            ),
            {"id": uid, "username": username, "pwd": changeme_hash, "full_name": full_name},
        )

    # 回查 user id
    user_id_map: dict[str, int] = {}
    rows = bind.execute(
        sa.text(
            "SELECT username, id FROM t_user WHERE deleted_at IS NULL AND username IN ('proda1','prodb1','inspi1','admin')"
        )
    ).fetchall()
    for username, uid in rows:
        user_id_map[username] = int(uid)

    # 3) SHELF_ACCOUNT role 关联
    for _discard_code, username, _discard_name, shelf_code in shelf_users:
        uid = user_id_map.get(username)
        sid = shelf_ids.get(shelf_code)
        if uid is None or sid is None:
            continue
        bind.execute(
            sa.text(
                """
                INSERT INTO t_user_role (id, user_id, role, scope_type, scope_id, created_at, updated_at)
                VALUES (:id, :uid, 'SHELF_ACCOUNT', 'shelf', :sid, now(), now())
                ON CONFLICT (user_id, role, scope_type, scope_id) DO NOTHING
                """
            ),
            {"id": new_id(), "uid": uid, "sid": sid},
        )

    # 4) admin MANAGER role
    admin_uid = user_id_map.get("admin")
    if admin_uid is not None:
        bind.execute(
            sa.text(
                """
                INSERT INTO t_user_role (id, user_id, role, scope_type, scope_id, created_at, updated_at)
                VALUES (:id, :uid, 'MANAGER', NULL, NULL, now(), now())
                ON CONFLICT (user_id, role, scope_type, scope_id) DO NOTHING
                """
            ),
            {"id": new_id(), "uid": admin_uid},
        )


# =============================================================================
# 子函数：菜单 seed
# =============================================================================
def _seed_menus() -> None:
    """插入 12 条默认菜单 + MANAGER / SHELF_ACCOUNT 角色菜单关联。"""
    bind = op.get_bind()

    id_by_code: dict[str, int] = {}
    for row in _MENU_SEED:
        mid = new_id()
        id_by_code[row["code"]] = mid

    for row in _MENU_SEED:
        mid = id_by_code[row["code"]]
        parent_id = id_by_code.get(row["parent"]) if row["parent"] else None
        bind.execute(
            sa.text(
                """
                INSERT INTO t_menu
                  (id, parent_id, code, title, path, icon, sort_order, is_active,
                   created_at, updated_at)
                VALUES
                  (:id, :parent_id, :code, :title, :path, :icon, :sort_order, true,
                   now(), now())
                ON CONFLICT (code) WHERE deleted_at IS NULL DO NOTHING
                """
            ),
            {
                "id": mid,
                "parent_id": parent_id,
                "code": row["code"],
                "title": row["title"],
                "path": row["path"],
                "icon": row["icon"],
                "sort_order": row["sort_order"],
            },
        )

    # 回查实际写入的 id
    rows = bind.execute(
        sa.text("SELECT code, id FROM t_menu WHERE deleted_at IS NULL")
    ).fetchall()
    for code, mid in rows:
        id_by_code[code] = int(mid)

    # t_role_menu seed
    for role, code in _ROLE_MENU_SEED:
        mid = id_by_code.get(code)
        if mid is None:
            continue
        bind.execute(
            sa.text(
                """
                INSERT INTO t_role_menu (id, role, menu_id, created_at, updated_at)
                VALUES (:id, :role, :menu_id, now(), now())
                ON CONFLICT (role, menu_id) WHERE deleted_at IS NULL DO NOTHING
                """
            ),
            {"id": new_id(), "role": role, "menu_id": mid},
        )


# =============================================================================
# 子函数：settings 菜单 seed
# =============================================================================
def _seed_settings_menus() -> None:
    """插入 settings_root + 3 个 settings 子菜单, MANAGER 角色关联。"""
    bind = op.get_bind()

    settings_root_id = new_id()
    bind.execute(
        sa.text(
            """
            INSERT INTO t_menu
              (id, parent_id, code, title, path, icon, sort_order, is_active,
               created_at, updated_at)
            VALUES
              (:id, NULL, 'settings_root', '设置', NULL, 'Setting', 50, true,
               now(), now())
            ON CONFLICT (code) WHERE deleted_at IS NULL DO NOTHING
            """
        ),
        {"id": settings_root_id},
    )

    # 回查实际 id（ON CONFLICT 可能不写，第二次运行要拿到已有 id）
    row = bind.execute(
        sa.text(
            "SELECT id FROM t_menu WHERE code='settings_root' AND deleted_at IS NULL"
        )
    ).fetchone()
    if row:
        settings_root_id = int(row[0])

    sub_menus = [
        ("work_types_list",          "工种管理",     "/settings/work-types",          "User",       10),
        ("processes_list",           "工序管理",     "/settings/processes",           "Operation",  20),
        ("work_type_processes_list", "工种-工序映射", "/settings/work-type-processes", "Connection", 30),
    ]
    for code, title, path, icon, sort_order in sub_menus:
        sub_id = new_id()
        bind.execute(
            sa.text(
                """
                INSERT INTO t_menu
                  (id, parent_id, code, title, path, icon, sort_order, is_active,
                   created_at, updated_at)
                VALUES
                  (:id, :pid, :code, :title, :path, :icon, :sort_order, true,
                   now(), now())
                ON CONFLICT (code) WHERE deleted_at IS NULL DO NOTHING
                """
            ),
            {
                "id": sub_id, "pid": settings_root_id,
                "code": code, "title": title, "path": path,
                "icon": icon, "sort_order": sort_order,
            },
        )

    # MANAGER 角色关联 settings 菜单
    settings_codes = [
        "settings_root", "work_types_list",
        "processes_list", "work_type_processes_list",
    ]
    rows = bind.execute(
        sa.text(
            "SELECT id FROM t_menu WHERE code = ANY(:codes) AND deleted_at IS NULL"
        ),
        {"codes": settings_codes},
    ).fetchall()
    for (mid,) in rows:
        bind.execute(
            sa.text(
                """
                INSERT INTO t_role_menu (id, role, menu_id, created_at, updated_at)
                VALUES (:id, 'MANAGER', :menu_id, now(), now())
                ON CONFLICT (role, menu_id) WHERE deleted_at IS NULL DO NOTHING
                """
            ),
            {"id": new_id(), "menu_id": mid},
        )


def downgrade() -> None:
    # 清掉所有种子数据（不删 schema）
    op.execute("DELETE FROM t_part_event")
    op.execute("DELETE FROM t_role_menu")
    op.execute("DELETE FROM t_menu")
    op.execute("DELETE FROM t_user_role")
    op.execute("DELETE FROM t_part")
    op.execute("DELETE FROM t_assembly")
    op.execute("DELETE FROM t_user")
    op.execute("DELETE FROM t_shelf")
    op.execute("DELETE FROM t_work_type_process")
    op.execute("DELETE FROM t_worker")
    op.execute("DELETE FROM t_work_type")
    op.execute("DELETE FROM t_process")
    op.execute("DELETE FROM t_customer")
    op.execute("DELETE FROM t_serial_counter")
