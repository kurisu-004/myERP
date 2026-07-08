"""prod_seed: 洪升宏真实员工生产种子（admin/文员×2/CNC编程 + 19 工人 + 9 工种 + 20 菜单 + 角色菜单）

Revision ID: 000000000012
Revises: 000000000011
Create Date: 2026-07-08

说明（prod_data 类别种子，**生产专用**）：
- 数据来源：docs/26洪升宏在职人员统计表.xlsx（19 名在职员工）。
- 写入 9 个工种（与 dev_seed 同步，ON CONFLICT 幂等），
  保证 dev_data 链路不跑时 t_work_type 也有完整 9 行。
- 写入 19 个真实工人（badge_code = 电话，与 user.username 逻辑同源）；
  按 Excel「备注」列映射到 9 个工种。
  * 线割 → 线切割
  * 磨床 → 磨床
  * 铣床 → 铣床
  * NC  → CNC操机
  * 编程 → CNC编程
  * 车床 → 车床
  * 品鉴 → 品检
  * 文员 → 文员
  * 送货师傅 → 送货司机
- 写入 4 个生产账号（username = 电话，password = changeme，bcrypt rounds=12）：
  * admin (15060779955 → 系统管理员 / MANAGER)        — 不在 19 人里
  * 陈燕 (13359114794 → 文员 / CLERK)                  — 同时也是工人
  * 翁美月 (15105972335 → 文员 / CLERK)                — 同时也是工人
  * 童敏华 (18064554025 → CNC 编程 / CNC_PROGRAMMER)   — 同时也是工人
- 写入 20 个菜单 + MANAGER/CLERK/CNC_PROGRAMMER 角色菜单关联（ON CONFLICT 幂等），
  保证 prod 路径只跑本迁移也能登录看到对应菜单。
  * MANAGER → 全部 20
  * CLERK → home, order_group, parts_list, parts_new, assemblies_list, assemblies_new
  * CNC_PROGRAMMER → home, parts_list, floor_group, scan_badge (与 0008 revoke 后一致)
- 不动 schema 与 dev_data；可独立运行。
- down_revision 仅依赖 schema 链末端 000000000005，所以 prod 库可走
  `alembic upgrade 000000000012` 直达本步，跳过 dev_data 整条链。
"""
import bcrypt as _bc
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from utils.id_gen import new_id

revision: str = "000000000012"
down_revision: Union[str, None] = "000000000011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# =============================================================================
# 工种
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
# 工人（19 名洪升宏在职员工；badge_code=phone 与 user.username 逻辑同源）
# =============================================================================
# 字段: (badge_code, name, id_card_no, phone, work_type_code)
_WORKERS: list[tuple[str, str, str, str, str]] = [
    # 车床
    ("13656021091", "彭飞",   "510522197411286910", "13656021091", "车床"),
    # 铣床
    ("13606907983", "李小兵", "36210219820920103X", "13606907983", "铣床"),
    ("18550486197", "钟枝桂", "360781199411092915", "18550486197", "铣床"),
    ("13799783724", "林军",   "350524198606113013", "13799783724", "铣床"),
    ("15980967791", "袁永生", "411322198210054575", "15980967791", "铣床"),
    # 磨床
    ("15805908539", "吴发谅", "35042519850503371X", "15805908539", "磨床"),
    ("13219276551", "赵艺萍", "511621199710186396", "13219276551", "磨床"),
    # 线切割
    ("15859204802", "万长生", "510303196303261914", "15859204802", "线切割"),
    ("13695009691", "张万烁", "35082119880604331X", "13695009691", "线切割"),
    # CNC 操机（Excel 标 "NC"）
    ("16623111794", "李润",   "500226200404196230", "16623111794", "CNC操机"),
    ("19370989849", "韦钱中", "522129198811123513", "19370989849", "CNC操机"),
    ("15960396271", "林群峰", "350628199705253017", "15960396271", "CNC操机"),
    ("13295097813", "陈万宝", "370881200201164493", "13295097813", "CNC操机"),
    # CNC 编程（Excel 标 "编程"）
    ("18064554025", "童敏华", "350629200109194518", "18064554025", "CNC编程"),
    # 品检（Excel 标 "品鉴"）
    ("18250705779", "黄道玉", "342401199009058567", "18250705779", "品检"),
    ("18046244109", "曾学辉", "510303197012091622", "18046244109", "品检"),
    # 文员
    ("13359114794", "陈燕",   "510302199702011047", "13359114794", "文员"),
    ("15105972335", "翁美月", "350205199004051045", "15105972335", "文员"),
    # 送货司机（Excel 标 "送货师傅"）
    ("18059214776", "幸世从", "51032119681002289X", "18059214776", "送货司机"),
]


# =============================================================================
# 用户（4 个生产账号：1 admin + 2 文员 + 1 CNC 编程员）
# =============================================================================
# 字段: (username=phone, full_name, role)
_USERS: list[tuple[str, str, str]] = [
    ("15060779955", "系统管理员", "MANAGER"),
    ("13359114794", "陈燕",       "CLERK"),
    ("15105972335", "翁美月",     "CLERK"),
    ("18064554025", "童敏华",     "CNC_PROGRAMMER"),
]


# =============================================================================
# 菜单（20 条；与 dev_seed 完整集合一致）
# =============================================================================
# 字段: (code, parent_code_or_None, title, path, icon, sort_order)
_MENUS: list[tuple[str, str | None, str, str | None, str, int]] = [
    # 12 base
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
    # 4 settings
    ("settings_root",           None,        "设置",                None,                       "Setting",    50),
    ("work_types_list",         "settings_root", "工种管理",       "/settings/work-types",     "User",       10),
    ("processes_list",          "settings_root", "工序管理",       "/settings/processes",      "Operation",  20),
    ("work_type_processes_list","settings_root", "工种-工序映射",  "/settings/work-type-processes", "Connection", 30),
    # 1 cnc
    ("pending_programming", None, "待编程一览", "/cnc/pending", "Cpu", 25),
    # 3 customer
    ("customer_management", None,             "客户管理",   None,          "OfficeBuilding", 15),
    ("customers_list",      "customer_management", "客户一览", "/customers", "Connection", 10),
    ("applicants_list",     "customer_management", "申请人一览", "/applicants", "User",      20),
]


# 角色 → 可见菜单 codes
_MANAGER_MENUS: list[str] = [
    "home", "order_group", "parts_list", "parts_new", "assemblies_list", "assemblies_new",
    "auth_group", "workers_list", "users_list",
    "floor_group", "shelves_list", "scan_badge",
    "settings_root", "work_types_list", "processes_list", "work_type_processes_list",
    "pending_programming",
    "customer_management", "customers_list", "applicants_list",
]

_CLERK_MENUS: list[str] = [
    "home", "order_group", "parts_list", "parts_new",
    "assemblies_list", "assemblies_new",
]

_CNC_PROGRAMMER_MENUS: list[str] = [
    "home", "parts_list", "floor_group", "scan_badge",
]


# =============================================================================
# upgrade
# =============================================================================
def upgrade() -> None:
    bind = op.get_bind()
    _seed_work_types(bind)
    _seed_workers(bind)
    _seed_users(bind)
    _seed_menus(bind)


# =============================================================================
# 阶段 1：工种
# =============================================================================
def _seed_work_types(bind) -> None:
    """写入 9 条工种；ON CONFLICT 幂等。"""
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


# =============================================================================
# 阶段 2：工人
# =============================================================================
def _seed_workers(bind) -> None:
    """写入 19 个真实工人；work_type_code → work_type_id 解析后写入。"""
    # 1) 查工种 id 映射
    rows = bind.execute(
        sa.text(
            "SELECT code, id FROM t_work_type "
            "WHERE deleted_at IS NULL AND code IN :codes"
        ).bindparams(
            sa.bindparam("codes", expanding=True)
        ),
        {"codes": [w[4] for w in _WORKERS]},
    ).fetchall()
    wt_ids: dict[str, int] = {code: int(wid) for code, wid in rows}

    # 2) 插入工人
    for badge_code, name, id_card_no, phone, wt_code in _WORKERS:
        wt_id = wt_ids.get(wt_code)
        if wt_id is None:
            # 不会发生：工种已在前一步 seed
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


# =============================================================================
# 阶段 3：用户
# =============================================================================
def _seed_users(bind) -> None:
    """写入 4 个生产账号（admin/2 文员/1 CNC 编程员）；username=电话，bcrypt rounds=12。"""
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
                "phone": username,  # phone 与 username 同源
            },
        )

    # 4) 关联 user_role（无 scope）
    rows = bind.execute(
        sa.text(
            "SELECT username, id FROM t_user "
            "WHERE deleted_at IS NULL AND username IN :usernames"
        ).bindparams(
            sa.bindparam("usernames", expanding=True)
        ),
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


# =============================================================================
# 阶段 5：菜单 + 角色菜单关联
# =============================================================================
def _seed_menus(bind) -> None:
    """写入 20 条菜单 + MANAGER/CLERK/CNC_PROGRAMMER 角色菜单关联。"""
    # 1) 预分配所有菜单 id（parent_id 也是预分配的，规避插入顺序问题）
    id_by_code: dict[str, int] = {}
    for code, _parent, _title, _path, _icon, _sort in _MENUS:
        id_by_code[code] = new_id()

    # 2) 插入所有菜单行
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

    # 3) 回查实际 id（ON CONFLICT 时新生成的 id 可能没写入）
    rows = bind.execute(
        sa.text(
            "SELECT code, id FROM t_menu "
            "WHERE deleted_at IS NULL AND code IN :codes"
        ).bindparams(
            sa.bindparam("codes", expanding=True)
        ),
        {"codes": [m[0] for m in _MENUS]},
    ).fetchall()
    id_by_code = {code: int(mid) for code, mid in rows}

    # 4) 角色菜单关联
    role_menu_seed: list[tuple[str, list[str]]] = [
        ("MANAGER", _MANAGER_MENUS),
        ("CLERK", _CLERK_MENUS),
        ("CNC_PROGRAMMER", _CNC_PROGRAMMER_MENUS),
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


# =============================================================================
# downgrade
# =============================================================================
def downgrade() -> None:
    """倒序删除本迁移写入的 prod 种子（不动 schema / dev_data）。"""
    bind = op.get_bind()

    # 1) 角色菜单（MANAGER / CLERK / CNC_PROGRAMMER）+ 角色关联
    op.execute(
        "DELETE FROM t_role_menu "
        "WHERE role IN ('MANAGER', 'CLERK', 'CNC_PROGRAMMER')"
    )
    op.execute(
        "DELETE FROM t_user_role WHERE role IN ('MANAGER', 'CLERK', 'CNC_PROGRAMMER')"
    )

    # 2) 用户
    op.execute(
        "DELETE FROM t_user WHERE username IN ('15060779955', '13359114794', "
        "                                    '15105972335', '18064554025') "
        "AND deleted_at IS NULL"
    )

    # 3) 工人
    op.execute(
        sa.text(
            "DELETE FROM t_worker WHERE badge_code IN :codes AND deleted_at IS NULL"
        ).bindparams(sa.bindparam("codes", expanding=True)),
        {"codes": [w[0] for w in _WORKERS]},
    )

    # 不删 9 个工种（与 dev_seed 共享）和 20 个菜单（跨 dev_data 共享）
    # 跨迁移删除会破坏 alembic 单 head 与 dev/prod 数据隔离
