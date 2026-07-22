"""data_init: 生产必要初始数据（工种/工人/账号/菜单/角色菜单/流水号计数器/工序/外协菜单）

Revision ID: 000000000002
Revises: 000000000001
Create Date: 2026-07-10（多次 squash）

说明（squash 合并 —— 生产必要种子）：
- 本文件是 myERP 的**唯一数据种子迁移**，把历史 prod_data 类别的所有必要种子
  （prod_seed / inspection_seed / delivery_note_menu / seed_serial_counter_a_z /
  seed_outsource）合并为一份「必要初始数据」。**不含任何 dev 假数据**（假零件/假客户/假工人/
  changeme dev 账号等已全部剔除）。
- 数据来源：docs/26洪升宏在职人员统计表.xlsx（19 名在职员工）。
- 写入内容：
  * 9 工种
  * 10 工序（5 INHOUSE：车/铣/磨/CNC/线切割；5 OUTSOURCE：数控车/慢走丝/外圆磨/热处理/深孔钻）
  * 5 条 工种↔工序 映射（车床→车 / 铣床→铣 / 磨床→磨 / 线切割→线切割 / CNC操机→CNC）
  * 19 真实工人（badge_code=phone）
  * 6 账号：admin(MANAGER) + 陈燕/翁美月(CLERK) + 童敏华(CNC_PROGRAMMER)
    + 黄道玉/曾学辉(INSPECTOR)，密码 changeme（bcrypt rounds=12）
  * 23 菜单（22 base + 外协管理「outsource_list」）+ 角色菜单关联
  * t_serial_counter 预置 A-Z 全 26 行（counter=0）
- 所有 INSERT 走 ON CONFLICT，幂等可重跑。
"""
import bcrypt as _bc
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from utils.id_gen import new_id

revision: str = "000000000002"
down_revision: Union[str, None] = "000000000001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# =============================================================================
# 工种（9 条）
# =============================================================================
_WORK_TYPES: list[tuple[str, str, int]] = [
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
# 工序（10 条：5 INHOUSE + 5 OUTSOURCE；2026-07-15 第四次 squash 合并自
# prod_data/005_seed_outsource）
# =============================================================================
_PROCESSES: list[tuple[str, str, str, int]] = [
    # INHOUSE 5 — 与既有工种名匹配（见下方 _WORK_TYPE_PROCESS_MAP）
    ("车",      "车床加工",   "INHOUSE",   10),
    ("铣",      "铣床加工",   "INHOUSE",   20),
    ("磨",      "磨床加工",   "INHOUSE",   30),
    ("CNC",     "CNC加工",    "INHOUSE",   40),
    ("线切割",  "线切割",     "INHOUSE",   50),
    # OUTSOURCE 5 — 数控车 / 慢走丝 / 外圆磨 / 热处理 / 深孔钻
    ("数控车",  "数控车外协", "OUTSOURCE", 110),
    ("慢走丝",  "慢走丝外协", "OUTSOURCE", 120),
    ("外圆磨",  "外圆磨外协", "OUTSOURCE", 130),
    ("热处理",  "热处理外协", "OUTSOURCE", 140),
    ("深孔钻",  "深孔钻外协", "OUTSOURCE", 150),
]


# =============================================================================
# 工种 ↔ 工序 映射（按 name 匹配，5 条；2026-07-15 第四次 squash 合并自
# prod_data/005_seed_outsource）
# =============================================================================
_WORK_TYPE_PROCESS_MAP: dict[str, list[str]] = {
    "车床":   ["车"],
    "铣床":   ["铣"],
    "磨床":   ["磨"],
    "线切割": ["线切割"],
    "CNC操机": ["CNC"],
}


# =============================================================================
# 工人（19 名洪升宏在职员工；badge_code=phone 与 user.username 逻辑同源）
# 字段: (badge_code, name, id_card_no, phone, work_type_code)
# =============================================================================
_WORKERS: list[tuple[str, str, str, str, str]] = [
    ("13656021091", "彭飞",   "510522197411286910", "13656021091", "车床"),
    ("13606907983", "李小兵", "36210219820920103X", "13606907983", "铣床"),
    ("18550486197", "钟枝桂", "360781199411092915", "18550486197", "铣床"),
    ("13799783724", "林军",   "350524198606113013", "13799783724", "铣床"),
    ("15980967791", "袁永生", "411322198210054575", "15980967791", "铣床"),
    ("15805908539", "吴发谅", "35042519850503371X", "15805908539", "磨床"),
    ("13219276551", "赵艺萍", "511621199710186396", "13219276551", "磨床"),
    ("15859204802", "万长生", "510303196303261914", "15859204802", "线切割"),
    ("13695009691", "张万烁", "35082119880604331X", "13695009691", "线切割"),
    ("16623111794", "李润",   "500226200404196230", "16623111794", "CNC操机"),
    ("19370989849", "韦钱中", "522129198811123513", "19370989849", "CNC操机"),
    ("15960396271", "林群峰", "350628199705253017", "15960396271", "CNC操机"),
    ("13295097813", "陈万宝", "370881200201164493", "13295097813", "CNC操机"),
    ("18064554025", "童敏华", "350629200109194518", "18064554025", "CNC编程"),
    ("18250705779", "黄道玉", "342401199009058567", "18250705779", "品检"),
    ("18046244109", "曾学辉", "510303197012091622", "18046244109", "品检"),
    ("13359114794", "陈燕",   "510302199702011047", "13350114794", "文员"),
    ("15105972335", "翁美月", "350205199004051045", "15105972335", "文员"),
    ("18059214776", "幸世从", "51032119681002289X", "18059214776", "送货司机"),
]


# =============================================================================
# 用户（4 个基础账号：1 admin + 2 文员 + 1 CNC 编程员；INSPECTOR 见下方）
# 字段: (username=phone, full_name, role)
# =============================================================================
_USERS: list[tuple[str, str, str]] = [
    ("15060779955", "系统管理员", "MANAGER"),
    ("13359114794", "陈燕",       "CLERK"),
    ("15105972335", "翁美月",     "CLERK"),
    ("18064554025", "童敏华",     "CNC_PROGRAMMER"),
]

# 品检员账号（与 19 工人工种=品检 一一对应；username=phone）
_INSPECTOR_USERS: list[tuple[str, str]] = [
    ("18250705779", "黄道玉"),
    ("18046244109", "曾学辉"),
]


# =============================================================================
# 客户（3 一级 + 11 二级；2026-07-16 新增）
# 字段: (name, parent_name_or_None, serial_prefix_or_None)
# 一级客户 serial_prefix 必填 A-Z 单字；二级客户继承父，serial_prefix 留空。
# 部分唯一索引 uq_t_customer_root_prefix 强制一级客户 prefix 互不冲突。
# =============================================================================
_CUSTOMERS: list[tuple[str, str | None, str | None]] = [
    # 一级 3 — 法拉电子 / 路达 / 宏发
    ("法拉电子", None, "F"),
    ("路达",     None, "L"),
    ("宏发",     None, "H"),
    # 二级 11 — 全部挂 法拉电子
    ("一厂",       "法拉电子", None),
    ("二厂",       "法拉电子", None),
    ("五厂",       "法拉电子", None),
    ("六厂",       "法拉电子", None),
    ("七厂",       "法拉电子", None),
    ("八厂",       "法拉电子", None),
    ("母排厂",     "法拉电子", None),
    ("镀膜长",     "法拉电子", None),  # 按用户原话录入（疑为「镀膜厂」之误，但需用户确认）
    ("设备部",     "法拉电子", None),
    ("IQC",        "法拉电子", None),
    ("南海路厂区", "法拉电子", None),
]


# =============================================================================
# 货架（4 条；2026-07-16 新增）
# 字段: (code, name, zone, location)
# A1/A2/B1 是生产区（PRODUCTION），C1 是品检区（INSPECTION）。
# B1 仍是生产区（与 hmi-b1 绑定的「另一个生产一体机」语义一致）。
# =============================================================================
_SHELVES: list[tuple[str, str, str, str | None]] = [
    ("A1", "生产区 A1", "PRODUCTION", None),
    ("A2", "生产区 A2", "PRODUCTION", None),
    ("B1", "生产区 B1", "PRODUCTION", None),
    ("C1", "品检区 C1", "INSPECTION", None),
]


# =============================================================================
# 工控机账号（SHELF_ACCOUNT 角色；2026-07-16 新增）
# 字段: (username, full_name)
# 密码统一用 changeme（与现有 6 个账号一致，bcrypt rounds=12）。
# =============================================================================
_HMI_USERS: list[tuple[str, str]] = [
    ("hmi-a1", "工控机 A1"),
    ("hmi-b1", "工控机 B1"),
]

# HMI → 货架 一对多（每个绑定写一行 t_user_role，scope_type='shelf' / scope_id=shelf.id）
# 字段: (hmi_username, [shelf_code, ...])
_HMI_SHELF_BINDINGS: list[tuple[str, list[str]]] = [
    ("hmi-a1", ["A1", "A2"]),
    ("hmi-b1", ["B1"]),
]


# =============================================================================
# 菜单（20 条 base；inspection_pending / delivery_notes_new 单独 seed）
# 字段: (code, parent_code_or_None, title, path, icon, sort_order)
# =============================================================================
_MENUS: list[tuple[str, str | None, str, str | None, str, int]] = [
    ("home",            None,          "首页",            "/dashboard",      "House",      10),
    ("order_group",     None,          "订单管理",        None,              "Tickets",    20),
    ("parts_list",      "order_group", "零件一览",        "/parts",          "Box",        10),
    ("parts_new",       "order_group", "新建零件",        "/parts/new",      "Plus",       20),
    ("assemblies_list", "order_group", "装配件一览",      "/assemblies",     "Connection", 30),
    ("assemblies_new",  "order_group", "新建装配件",      "/assemblies/new", "Plus",       40),
    ("auth_group",      None,          "权限管理",        None,              "Key",        30),
    ("workers_list",    "auth_group",  "工人一览",        "/workers",        "User",       10),
    ("users_list",      "auth_group",  "账号管理",        "/users",          "List",       20),
    ("floor_group",     None,          "车间",            None,              "Tools",      40),
    ("shelves_list",    "floor_group", "货架管理",        "/shelves",        "Platform",   10),
    ("scan_badge",      "floor_group", "扫码台",          "/scan/badge",     "Promotion",  20),
    ("settings_root",           None,        "设置",                None,                       "Setting",    50),
    ("work_types_list",         "settings_root", "工种管理",       "/settings/work-types",     "User",       10),
    ("processes_list",          "settings_root", "工序管理",       "/settings/processes",      "Operation",  20),
    ("work_type_processes_list","settings_root", "工种-工序映射",  "/settings/work-type-processes", "Connection", 30),
    ("pending_programming", None, "待编程一览", "/cnc/pending", "Cpu", 25),
    ("customer_management", None,             "客户管理",   None,          "OfficeBuilding", 15),
    ("customers_list",      "customer_management", "客户一览", "/customers", "Connection", 10),
    ("applicants_list",     "customer_management", "申请人一览", "/applicants", "User",      20),
    ("outsource_list",      None,             "外协管理",   None,          "Promotion",    35),
    ("outsource_companies_list", "outsource_list", "外协厂一览", "/outsource/companies", "OfficeBuilding", 10),
    ("outsource_quotes_list",    "outsource_list", "报价一览",    "/outsource/quotes",    "Document", 20),
    # 2026-07-16：合并「外协发送」+「外协接收」为一个「外协发送/接收」
    ("outsource_send_receive_list", "outsource_list", "外协发送/接收", "/outsource/send-receive", "Promotion", 30),
]

_MANAGER_MENUS: list[str] = [
    "home", "order_group", "parts_list", "parts_new", "assemblies_list", "assemblies_new",
    "auth_group", "workers_list", "users_list",
    "floor_group", "shelves_list",
    "settings_root", "work_types_list", "processes_list", "work_type_processes_list",
    "pending_programming",
    "customer_management", "customers_list", "applicants_list",
    "outsource_list", "outsource_companies_list", "outsource_quotes_list",
    # 2026-07-16：合并后的「外协发送/接收」
    "outsource_send_receive_list",
]

_CLERK_MENUS: list[str] = [
    "home", "order_group", "parts_list", "parts_new",
    "assemblies_list", "assemblies_new",
    # 2026-07-14：补客户管理组入口（backend customer/applicant API 早已允许 CLERK，
    # 此前仅前端侧栏入口缺失）。inspection_pending / delivery_notes_new 仍由
    # 各 seed 函数单独授予，无需在此列出。
    "customer_management", "customers_list", "applicants_list",
    # 2026-07-15：CLERK 也能进外协管理（与 MANAGER 同款权限）。
    # 2026-07-16：合并后的「外协发送/接收」一个菜单替代原 send + receive 两个。
    "outsource_list", "outsource_companies_list", "outsource_quotes_list",
    "outsource_send_receive_list",
]

_CNC_PROGRAMMER_MENUS: list[str] = [
    # 2026-07-14：缩到首页 + 待编程一览（严格）。
    # 移除 floor_group（含 shelves_list 子项）与 parts_list（订单管理子项），
    # 编程员只走 /cnc/pending 专属流程，不再暴露车间/扫码台/零件一览。
    "home", "pending_programming",
]

# SHELF_ACCOUNT（货架一体机账号）—— 2026-07-13 起扫码台菜单专属：
# SHELF_ACCOUNT 是车间 HMI 一体机登录账号；业务上唯一进扫码台的角色。
# MANAGER / CNC_PROGRAMMER 不再挂 scan_badge（编程员用 /cnc/pending 走专属流，
# 不再走扫码台；MANAGER 通过账号管理 + 货架管理后台处理）。
_SHELF_ACCOUNT_MENUS: list[str] = [
    "home", "floor_group", "scan_badge",
]


# =============================================================================
# upgrade
# =============================================================================
def upgrade() -> None:
    bind = op.get_bind()
    _seed_work_types(bind)
    _seed_processes(bind)
    _seed_work_type_process_map(bind)
    _seed_workers(bind)
    _update_chen_yan_phone(bind)   # 2026-07-16：先改工人 phone，再 INSERT 账号用新 phone
    _seed_users(bind)
    _update_user_chen_yan_phone(bind)  # 2026-07-16：同步账号 phone
    _seed_menus(bind)
    _seed_inspection_menu(bind)
    _seed_inspector_users(bind)
    _seed_delivery_note_menu(bind)
    _seed_serial_counter(bind)
    # 2026-07-16：新增 客户 / 货架 / 工控机
    _seed_customers(bind)
    _seed_shelves(bind)
    _seed_shelf_process_map(bind)  # 2026-07-17：补灌货架↔工序映射
    _seed_hmi_users(bind)


def _seed_work_types(bind) -> None:
    for code, name, sort_order in _WORK_TYPES:
        bind.execute(
            sa.text(
                """
                INSERT INTO t_work_type (id, code, name, sort_order,
                                         created_at, updated_at)
                VALUES (:id, :code, :name, :sort_order, now(), now())
                ON CONFLICT (code) WHERE deleted_at IS NULL DO NOTHING
                """
            ),
            {"id": new_id(), "code": code, "name": name, "sort_order": sort_order},
        )


def _seed_processes(bind) -> None:
    """灌入 10 条 t_process（5 INHOUSE + 5 OUTSOURCE；2026-07-15 第四次 squash 合并）。"""
    for code, name, category, sort_order in _PROCESSES:
        bind.execute(
            sa.text(
                """
                INSERT INTO t_process
                  (id, code, name, category, sort_order, description,
                   created_at, updated_at)
                VALUES
                  (:id, :code, :name, :category, :sort_order, NULL,
                   now(), now())
                ON CONFLICT (code) WHERE deleted_at IS NULL DO NOTHING
                """
            ),
            {
                "id": new_id(),
                "code": code,
                "name": name,
                "category": category,
                "sort_order": sort_order,
            },
        )


def _seed_work_type_process_map(bind) -> None:
    """按 _WORK_TYPE_PROCESS_MAP 灌入 5 条工种-工序映射（2026-07-15 squash 合并）。"""
    # 1. 解析 work_type_code → id
    rows = bind.execute(
        sa.text("SELECT code, id FROM t_work_type WHERE deleted_at IS NULL")
    ).fetchall()
    wt_id_by_code: dict[str, int] = {code: int(wid) for code, wid in rows}

    # 2. 解析 process_code → id
    rows = bind.execute(
        sa.text("SELECT code, id FROM t_process WHERE deleted_at IS NULL")
    ).fetchall()
    proc_id_by_code: dict[str, int] = {code: int(pid) for code, pid in rows}

    # 3. 灌入
    for wt_code, proc_codes in _WORK_TYPE_PROCESS_MAP.items():
        wt_id = wt_id_by_code.get(wt_code)
        if wt_id is None:
            continue
        for idx, proc_code in enumerate(proc_codes):
            proc_id = proc_id_by_code.get(proc_code)
            if proc_id is None:
                continue
            bind.execute(
                sa.text(
                    """
                    INSERT INTO t_work_type_process
                      (id, work_type_id, process_id, sort_order,
                       created_at, updated_at)
                    VALUES
                      (:id, :wt, :proc, :ord, now(), now())
                    ON CONFLICT (work_type_id, process_id)
                      WHERE deleted_at IS NULL DO NOTHING
                    """
                ),
                {
                    "id": new_id(),
                    "wt": wt_id,
                    "proc": proc_id,
                    "ord": idx,
                },
            )


def _seed_workers(bind) -> None:
    rows = bind.execute(
        sa.text(
            "SELECT code, id FROM t_work_type "
            "WHERE deleted_at IS NULL AND code IN :codes"
        ).bindparams(sa.bindparam("codes", expanding=True)),
        {"codes": [w[4] for w in _WORKERS]},
    ).fetchall()
    wt_ids: dict[str, int] = {code: int(wid) for code, wid in rows}

    for badge_code, name, id_card_no, phone, wt_code in _WORKERS:
        wt_id = wt_ids.get(wt_code)
        if wt_id is None:
            continue
        bind.execute(
            sa.text(
                """
                INSERT INTO t_worker (id, badge_code, name, id_card_no, phone,
                                      is_active, work_type_id,
                                      created_at, updated_at)
                VALUES (:id, :badge_code, :name, :id_card_no, :phone,
                        true, :wt_id, now(), now())
                ON CONFLICT (badge_code) WHERE deleted_at IS NULL DO NOTHING
                """
            ),
            {
                "id": new_id(),
                "badge_code": badge_code,
                "name": name,
                "id_card_no": id_card_no,
                "phone": phone,
                "wt_id": wt_id,
            },
        )


def _seed_users(bind) -> None:
    prod_hash = _bc.hashpw(b"changeme", _bc.gensalt(rounds=12)).decode("utf-8")

    for username, full_name, _role in _USERS:
        bind.execute(
            sa.text(
                """
                INSERT INTO t_user (id, username, password_hash, full_name, phone,
                                    is_active, created_at, updated_at)
                VALUES (:id, :username, :pwd, :full_name, :phone,
                        true, now(), now())
                ON CONFLICT (username) WHERE deleted_at IS NULL DO NOTHING
                """
            ),
            {
                "id": new_id(),
                "username": username,
                "pwd": prod_hash,
                "full_name": full_name,
                "phone": username,
            },
        )

    rows = bind.execute(
        sa.text(
            "SELECT username, id FROM t_user "
            "WHERE deleted_at IS NULL AND username IN :usernames"
        ).bindparams(sa.bindparam("usernames", expanding=True)),
        {"usernames": [u[0] for u in _USERS]},
    ).fetchall()
    user_id_map: dict[str, int] = {uname: int(uid) for uname, uid in rows}

    for username, _full_name, role in _USERS:
        uid = user_id_map.get(username)
        if uid is None:
            continue
        bind.execute(
            sa.text(
                """
                INSERT INTO t_user_role (id, user_id, role, scope_type, scope_id,
                                         created_at, updated_at)
                VALUES (:id, :uid, :role, NULL, NULL, now(), now())
                ON CONFLICT (user_id, role, scope_type, scope_id) DO NOTHING
                """
            ),
            {"id": new_id(), "uid": uid, "role": role},
        )


def _update_chen_yan_phone(bind) -> None:
    """2026-07-16：把工人陈燕的手机号从 13359114794 改成 13350114794。
    走 UPDATE 不走 INSERT；幂等。"""
    bind.execute(
        sa.text(
            """
            UPDATE t_worker
            SET phone = :new_phone, updated_at = now()
            WHERE badge_code = :old_phone AND deleted_at IS NULL
            """
        ),
        {"new_phone": "13350114794", "old_phone": "13359114794"},
    )


def _update_user_chen_yan_phone(bind) -> None:
    """2026-07-16：把账号陈燕的 username + phone 同步改成 13350114794。
    注意 _USERS 仍用旧 username=13359114794，保证 ON CONFLICT 在已部署库上
    能命中旧行；本函数负责 UPDATE username + phone。"""
    bind.execute(
        sa.text(
            """
            UPDATE t_user
            SET username = :new_phone,
                phone = :new_phone,
                updated_at = now()
            WHERE username = :old_phone AND deleted_at IS NULL
            """
        ),
        {"new_phone": "13350114794", "old_phone": "13359114794"},
    )


def _seed_customers(bind) -> None:
    """2026-07-16：灌入 3 一级 + 11 二级客户。
    t_customer.name 没有 UNIQUE 约束（只有普通 INDEX）→ 不能用 ON CONFLICT。
    改用先 SELECT 后插入已存在则跳过的模式。"""
    all_names = [c[0] for c in _CUSTOMERS]
    existing_rows = bind.execute(
        sa.text(
            "SELECT name, id FROM t_customer "
            "WHERE deleted_at IS NULL AND name IN :names"
        ).bindparams(sa.bindparam("names", expanding=True)),
        {"names": all_names},
    ).fetchall()
    existing: set[str] = {name for name, _ in existing_rows}
    id_by_name: dict[str, int] = {name: int(cid) for name, cid in existing_rows}

    # 第一遍：插入一级客户（只插不存在的）
    for name, parent_name, prefix in _CUSTOMERS:
        if parent_name is not None:
            continue
        if name in existing:
            continue
        new_cid = new_id()
        id_by_name[name] = new_cid
        bind.execute(
            sa.text(
                """
                INSERT INTO t_customer (id, name, parent_id, serial_prefix,
                                         version, created_at, updated_at)
                VALUES (:id, :name, NULL, :prefix, 0, now(), now())
                """
            ),
            {"id": new_cid, "name": name, "prefix": prefix},
        )

    # 第二遍：插入二级客户（只插不存在的）
    for name, parent_name, _prefix in _CUSTOMERS:
        if parent_name is None:
            continue
        if name in existing:
            continue
        parent_id = id_by_name.get(parent_name)
        if parent_id is None:
            continue
        new_cid = new_id()
        id_by_name[name] = new_cid
        bind.execute(
            sa.text(
                """
                INSERT INTO t_customer (id, name, parent_id, serial_prefix,
                                         version, created_at, updated_at)
                VALUES (:id, :name, :parent_id, NULL, 0, now(), now())
                """
            ),
            {"id": new_cid, "name": name, "parent_id": parent_id},
        )


def _seed_shelves(bind) -> None:
    """2026-07-16：灌入 4 条货架（A1/A2/B1 生产 + C1 品检）。"""
    for code, name, zone, location in _SHELVES:
        bind.execute(
            sa.text(
                """
                INSERT INTO t_shelf (id, code, name, zone, location, is_active,
                                     display_order, version, created_at, updated_at)
                VALUES (:id, :code, :name, :zone, :loc, true, 0, 0, now(), now())
                ON CONFLICT (code) WHERE deleted_at IS NULL DO NOTHING
                """
            ),
            {
                "id": new_id(),
                "code": code,
                "name": name,
                "zone": zone,
                "loc": location,
            },
        )


def _seed_shelf_process_map(bind) -> None:
    """2026-07-17：补灌货架↔工序映射。

    之前 seed 没灌任何 `t_shelf_process` 行，导致新启用的 422 守卫（commit 3
    `_assert_shelf_maps_process`）会拒绝所有下发放回。给每个 active PRODUCTION
    货架默认映射「车/铣/磨/CNC/线切割」5 个 INHOUSE 工序（与既有工种-工序
    映射同源），INSPECTION 区货架不映射（送检流程不走工序校验）。

    幂等：`uk_t_shelf_process (shelf_id, process_id) WHERE deleted_at IS NULL`
    唯一索引 + `ON CONFLICT DO NOTHING` 保证重复跑不出错。
    """
    rows = bind.execute(
        sa.text(
            "SELECT id FROM t_shelf "
            "WHERE deleted_at IS NULL AND zone = 'PRODUCTION' AND is_active = true"
        )
    ).fetchall()
    shelf_ids: list[int] = [int(sid) for (sid,) in rows]
    if not shelf_ids:
        return

    # 5 个 INHOUSE 工序——所有 active PRODUCTION 货架都映射这 5 个
    proc_rows = bind.execute(
        sa.text(
            "SELECT id, code FROM t_process "
            "WHERE deleted_at IS NULL AND category = 'INHOUSE' "
            "ORDER BY sort_order ASC"
        )
    ).fetchall()
    proc_ids: list[tuple[int, str]] = [(int(pid), code) for (pid, code) in proc_rows]
    if not proc_ids:
        return

    for sid in shelf_ids:
        for idx, (pid, _code) in enumerate(proc_ids):
            bind.execute(
                sa.text(
                    """
                    INSERT INTO t_shelf_process
                      (id, shelf_id, process_id, sort_order,
                       created_at, updated_at, version, created_by, updated_by)
                    VALUES (:id, :sid, :pid, :so, now(), now(), 0, NULL, NULL)
                    ON CONFLICT (shelf_id, process_id)
                      WHERE deleted_at IS NULL DO NOTHING
                    """
                ),
                {"id": new_id(), "sid": sid, "pid": pid, "so": idx},
            )


def _seed_hmi_users(bind) -> None:
    """2026-07-16：灌入 2 个工控机账号（hmi-a1 / hmi-b1），
    密码 changeme，绑定 SHELF_ACCOUNT 角色（scope_type='shelf' / scope_id=shelf.id）。

    流程：先 INSERT 账号 → 查 user id → 查每个 shelf code 的 shelf id →
    为每个 (user, shelf) 配对写一条 t_user_role。"""
    pwd_hash = _bc.hashpw(b"changeme", _bc.gensalt(rounds=12)).decode("utf-8")

    for username, full_name in _HMI_USERS:
        bind.execute(
            sa.text(
                """
                INSERT INTO t_user (id, username, password_hash, full_name, phone,
                                    is_active, created_at, updated_at)
                VALUES (:id, :username, :pwd, :full_name, :phone,
                        true, now(), now())
                ON CONFLICT (username) WHERE deleted_at IS NULL DO NOTHING
                """
            ),
            {
                "id": new_id(),
                "username": username,
                "pwd": pwd_hash,
                "full_name": full_name,
                "phone": username,
            },
        )

    # 查 user id + shelf id（按 username / code 查）
    rows = bind.execute(
        sa.text(
            "SELECT username, id FROM t_user "
            "WHERE deleted_at IS NULL AND username IN :usernames"
        ).bindparams(sa.bindparam("usernames", expanding=True)),
        {"usernames": [u[0] for u in _HMI_USERS]},
    ).fetchall()
    user_id_by_username: dict[str, int] = {u: int(uid) for u, uid in rows}

    all_shelf_codes = [code for _, codes in _HMI_SHELF_BINDINGS for code in codes]
    rows = bind.execute(
        sa.text(
            "SELECT code, id FROM t_shelf "
            "WHERE deleted_at IS NULL AND code IN :codes"
        ).bindparams(sa.bindparam("codes", expanding=True)),
        {"codes": all_shelf_codes},
    ).fetchall()
    shelf_id_by_code: dict[str, int] = {code: int(sid) for code, sid in rows}

    for hmi_username, shelf_codes in _HMI_SHELF_BINDINGS:
        uid = user_id_by_username.get(hmi_username)
        if uid is None:
            continue
        for code in shelf_codes:
            sid = shelf_id_by_code.get(code)
            if sid is None:
                continue
            bind.execute(
                sa.text(
                    """
                    INSERT INTO t_user_role
                      (id, user_id, role, scope_type, scope_id,
                       created_at, updated_at)
                    VALUES
                      (:id, :uid, 'SHELF_ACCOUNT', 'shelf', :sid, now(), now())
                    ON CONFLICT (user_id, role, scope_type, scope_id) DO NOTHING
                    """
                ),
                {"id": new_id(), "uid": uid, "sid": sid},
            )


def _seed_menus(bind) -> None:
    id_by_code: dict[str, int] = {}
    for code, _parent, _title, _path, _icon, _sort in _MENUS:
        id_by_code[code] = new_id()

    for code, parent, title, path, icon, sort_order in _MENUS:
        parent_id = id_by_code.get(parent) if parent else None
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
                "id": id_by_code[code],
                "parent_id": parent_id,
                "code": code,
                "title": title,
                "path": path,
                "icon": icon,
                "sort_order": sort_order,
            },
        )

    rows = bind.execute(
        sa.text(
            "SELECT code, id FROM t_menu "
            "WHERE deleted_at IS NULL AND code IN :codes"
        ).bindparams(sa.bindparam("codes", expanding=True)),
        {"codes": [m[0] for m in _MENUS]},
    ).fetchall()
    id_by_code = {code: int(mid) for code, mid in rows}

    role_menu_seed: list[tuple[str, list[str]]] = [
        ("MANAGER", _MANAGER_MENUS),
        ("CLERK", _CLERK_MENUS),
        ("CNC_PROGRAMMER", _CNC_PROGRAMMER_MENUS),
        ("SHELF_ACCOUNT", _SHELF_ACCOUNT_MENUS),
    ]
    for role, codes in role_menu_seed:
        for code in codes:
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


def _seed_inspection_menu(bind) -> None:
    """inspection_pending 菜单（挂 order_group）+ MANAGER/CLERK/INSPECTOR 角色关联。"""
    parent_row = bind.execute(
        sa.text(
            "SELECT id FROM t_menu "
            "WHERE code='order_group' AND deleted_at IS NULL"
        )
    ).fetchone()
    if parent_row is None:
        return
    parent_id = int(parent_row[0])

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
            "id": new_id(),
            "parent_id": parent_id,
            "code": "inspection_pending",
            "title": "待品检",
            "path": "/inspection/pending",
            "icon": "CircleCheck",
            "sort_order": 50,
        },
    )

    mid_row = bind.execute(
        sa.text(
            "SELECT id FROM t_menu "
            "WHERE code='inspection_pending' AND deleted_at IS NULL"
        )
    ).fetchone()
    if mid_row is None:
        return
    mid = int(mid_row[0])

    for role in ("MANAGER", "CLERK", "INSPECTOR"):
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


def _seed_inspector_users(bind) -> None:
    """2 个品检员账号 + INSPECTOR 角色绑定。"""
    pwd_hash = _bc.hashpw(b"changeme", _bc.gensalt(rounds=12)).decode("utf-8")

    for username, full_name in _INSPECTOR_USERS:
        user_row = bind.execute(
            sa.text(
                "SELECT id FROM t_user "
                "WHERE username=:u AND deleted_at IS NULL"
            ),
            {"u": username},
        ).fetchone()

        if user_row is None:
            uid = new_id()
            bind.execute(
                sa.text(
                    """
                    INSERT INTO t_user
                      (id, username, password_hash, full_name, phone,
                       is_active, created_at, updated_at)
                    VALUES
                      (:id, :username, :pwd, :full_name, :phone,
                       true, now(), now())
                    ON CONFLICT (username) WHERE deleted_at IS NULL DO NOTHING
                    """
                ),
                {
                    "id": uid,
                    "username": username,
                    "pwd": pwd_hash,
                    "full_name": full_name,
                    "phone": username,
                },
            )
            user_id = uid
        else:
            user_id = int(user_row[0])

        bind.execute(
            sa.text(
                """
                INSERT INTO t_user_role
                  (id, user_id, role, scope_type, scope_id,
                   created_at, updated_at)
                VALUES
                  (:id, :uid, 'INSPECTOR', NULL, NULL, now(), now())
                ON CONFLICT (user_id, role, scope_type, scope_id) DO NOTHING
                """
            ),
            {"id": new_id(), "uid": user_id},
        )


def _seed_delivery_note_menu(bind) -> None:
    """送货单管理菜单（PR-G 2026-07-22：code 已从老 delivery_notes_new
    重命名为 delivery_notes_manage，指向新管理页面 /delivery-notes）。
    挂 order_group + MANAGER/CLERK 角色关联。

    schema/000000000009 同时携带一条 UPDATE 把老库上
    `delivery_notes_new` 老菜单改名为 `delivery_notes_manage`，
    本 seed 文件直接插入新 code（与 schema/009 的 UPDATE 二选一执行）。
    """
    parent_row = bind.execute(
        sa.text(
            "SELECT id FROM t_menu "
            "WHERE code='order_group' AND deleted_at IS NULL"
        )
    ).fetchone()
    if parent_row is None:
        return
    parent_id = int(parent_row[0])

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
            "id": new_id(),
            "parent_id": parent_id,
            "code": "delivery_notes_manage",
            "title": "送货单",
            "path": "/delivery-notes",
            "icon": "Document",
            "sort_order": 30,
        },
    )

    mid_row = bind.execute(
        sa.text(
            "SELECT id FROM t_menu "
            "WHERE code='delivery_notes_manage' AND deleted_at IS NULL"
        )
    ).fetchone()
    if mid_row is None:
        return
    mid = int(mid_row[0])

    for role in ("MANAGER", "CLERK"):
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


def _seed_serial_counter(bind) -> None:
    """t_serial_counter 预置 A-Z 全 26 行（counter=0）；ON CONFLICT 幂等。"""
    bind.execute(
        sa.text(
            "INSERT INTO t_serial_counter (prefix, counter) "
            "SELECT chr(ascii('A') + i), 0 "
            "FROM generate_series(0, 25) i "
            "ON CONFLICT (prefix) DO NOTHING"
        )
    )


# =============================================================================
# downgrade
# =============================================================================
def downgrade() -> None:
    """删除本迁移写入的所有种子（t_serial_counter 作为基础设施保留）。"""
    bind = op.get_bind()

    op.execute(
        "DELETE FROM t_role_menu "
        "WHERE role IN ('MANAGER', 'CLERK', 'CNC_PROGRAMMER', 'INSPECTOR')"
    )
    op.execute(
        "DELETE FROM t_user_role "
        "WHERE role IN ('MANAGER', 'CLERK', 'CNC_PROGRAMMER', 'INSPECTOR', 'SHELF_ACCOUNT')"
    )

    all_usernames = (
        [u[0] for u in _USERS]
        + [u[0] for u in _INSPECTOR_USERS]
        + [u[0] for u in _HMI_USERS]   # 2026-07-16
    )
    bind.execute(
        sa.text(
            "DELETE FROM t_user WHERE username IN :usernames AND deleted_at IS NULL"
        ).bindparams(sa.bindparam("usernames", expanding=True)),
        {"usernames": all_usernames},
    )

    bind.execute(
        sa.text(
            "DELETE FROM t_worker WHERE badge_code IN :codes AND deleted_at IS NULL"
        ).bindparams(sa.bindparam("codes", expanding=True)),
        {"codes": [w[0] for w in _WORKERS]},
    )

    # 2026-07-16：清掉新加的 4 个货架 + 14 个客户
    bind.execute(
        sa.text(
            "DELETE FROM t_shelf WHERE code IN :codes AND deleted_at IS NULL"
        ).bindparams(sa.bindparam("codes", expanding=True)),
        {"codes": [s[0] for s in _SHELVES]},
    )
    bind.execute(
        sa.text(
            "DELETE FROM t_customer WHERE name IN :names AND deleted_at IS NULL"
        ).bindparams(sa.bindparam("names", expanding=True)),
        {"names": [c[0] for c in _CUSTOMERS]},
    )

    op.execute("DELETE FROM t_menu WHERE deleted_at IS NULL")
    op.execute(
        sa.text(
            "DELETE FROM t_work_type WHERE code IN :codes AND deleted_at IS NULL"
        ).bindparams(sa.bindparam("codes", expanding=True)),
        {"codes": [w[0] for w in _WORK_TYPES]},
    )
    # t_serial_counter 是基础设施；down 时保留 26 行不动。
