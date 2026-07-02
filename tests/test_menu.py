"""菜单 service + schema 的测试。

- 纯函数单元测试（`build_menu_tree._to_tree`）覆盖组树逻辑
  （孤儿节点、深层嵌套、排序、循环依赖）；不依赖 DB。
- schema 序列化测试覆盖 `MenuNodeOut` 的 JSON 行为（ID → str）。
- 集成测试覆盖 `MenuRepository.list_active_for_roles` 与
  `build_menu_tree` 端到端：依赖本地 PG 已 migrate 到 head，调用
  seed 中定义的默认菜单。
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from repository.menu import MenuRepository, MenuRow
from schema.menu import MenuNodeOut
from service.menu import _to_tree, build_menu_tree


pytestmark = pytest.mark.integration


# ============================================================
# 1. _to_tree 纯函数
# ============================================================
def _row(id_: int, parent_id: int | None, code: str, sort_order: int = 0,
         title: str | None = None, path: str | None = None) -> MenuRow:
    return MenuRow(
        id=id_,
        parent_id=parent_id,
        code=code,
        title=title or code,
        path=path,
        icon=None,
        sort_order=sort_order,
    )


def test_to_tree_empty() -> None:
    assert _to_tree([]) == []


def test_to_tree_single_root() -> None:
    rows = [_row(1, None, "home", sort_order=10)]
    tree = _to_tree(rows)
    assert len(tree) == 1
    assert tree[0].code == "home"
    assert tree[0].id == 1
    assert tree[0].children == []


def test_to_tree_two_levels() -> None:
    rows = [
        _row(1, None, "order", sort_order=20),
        _row(2, 1, "parts_list", sort_order=10),
        _row(3, 1, "parts_new", sort_order=20),
        _row(4, None, "home", sort_order=10),
    ]
    tree = _to_tree(rows)
    assert [n.code for n in tree] == ["home", "order"]
    order = tree[1]
    assert order.code == "order"
    assert [c.code for c in order.children] == ["parts_list", "parts_new"]


def test_to_tree_orphan_promoted_to_root() -> None:
    """parent_id 指向不可见节点 → 升为根（孤儿兜底）。"""
    rows = [
        _row(1, 999, "orphan", sort_order=99),  # 999 不在 rows
        _row(2, None, "real", sort_order=10),
    ]
    tree = _to_tree(rows)
    codes = [n.code for n in tree]
    # real 排第一（sort_order=10），orphan 排第二（99）
    assert codes == ["real", "orphan"]


def test_to_tree_three_levels() -> None:
    rows = [
        _row(1, None, "a", sort_order=10),
        _row(2, 1, "b", sort_order=10),
        _row(3, 2, "c", sort_order=10),
    ]
    tree = _to_tree(rows)
    assert len(tree) == 1
    assert tree[0].code == "a"
    assert len(tree[0].children) == 1
    assert tree[0].children[0].code == "b"
    assert len(tree[0].children[0].children) == 1
    assert tree[0].children[0].children[0].code == "c"


def test_to_tree_sort_with_tiebreak_by_code() -> None:
    """sort_order 相同时，按 code 字典序兜底。"""
    rows = [
        _row(1, None, "z_group", sort_order=10),
        _row(2, None, "a_group", sort_order=10),
        _row(3, 1, "z_child", sort_order=5),
        _row(4, 1, "a_child", sort_order=5),
    ]
    tree = _to_tree(rows)
    assert [n.code for n in tree] == ["a_group", "z_group"]
    z = tree[1]
    assert [c.code for c in z.children] == ["a_child", "z_child"]


# ============================================================
# 2. MenuNodeOut schema 序列化
# ============================================================
def test_schema_id_serializes_as_string() -> None:
    """ID 是 BigInt，序列化到 JSON 必须变 str（防止 JS 精度截断）。"""
    node = MenuNodeOut(
        id=9007199254740992,  # 大于 Number.MAX_SAFE_INTEGER
        parent_id=None,
        code="home",
        title="首页",
        path="/dashboard",
        icon="House",
        sort_order=10,
    )
    payload = node.model_dump(mode="json")
    assert payload["id"] == "9007199254740992"
    assert isinstance(payload["id"], str)
    assert payload["parent_id"] is None
    assert payload["code"] == "home"


def test_schema_recursive_children() -> None:
    parent = MenuNodeOut(
        id=1, parent_id=None, code="order", title="订单",
        path=None, icon=None, sort_order=10,
        children=[
            MenuNodeOut(
                id=2, parent_id=1, code="parts_list", title="零件一览",
                path="/parts", icon="Box", sort_order=10,
            ),
        ],
    )
    payload = parent.model_dump(mode="json")
    assert payload["id"] == "1"
    assert payload["parent_id"] is None
    assert payload["children"][0]["id"] == "2"
    assert payload["children"][0]["parent_id"] == "1"


def test_schema_path_null_renders_as_null() -> None:
    """path 为 NULL（分组节点）→ JSON 仍是 null，不是省略。"""
    node = MenuNodeOut(
        id=1, parent_id=None, code="order_group", title="订单管理",
        path=None, icon=None, sort_order=10,
    )
    payload = json.loads(node.model_dump_json())
    assert "path" in payload
    assert payload["path"] is None


def test_schema_default_empty_children() -> None:
    """children 缺省是空 list，不报错。"""
    node = MenuNodeOut(
        id=1, parent_id=None, code="home", title="首页",
        path="/dashboard", icon="House", sort_order=10,
    )
    assert node.children == []


# ============================================================
# 3. 集成：MenuRepository + build_menu_tree（依赖 seed）
# ============================================================
async def _truncate_menu_tables(session: AsyncSession) -> None:
    """测试间隔离菜单表。"""
    await session.execute(text("TRUNCATE TABLE t_role_menu RESTART IDENTITY"))
    await session.execute(text("TRUNCATE TABLE t_menu RESTART IDENTITY"))
    await session.commit()


async def _insert_menu(
    session: AsyncSession,
    *,
    code: str,
    title: str,
    parent_code: str | None = None,
    path: str | None = None,
    icon: str | None = None,
    sort_order: int = 0,
    is_active: bool = True,
) -> int:
    """直接 SQL 插一行 t_menu，返回 id（snowflake 现算）。"""
    from utils.id_gen import new_id
    mid = new_id()
    parent_id: int | None = None
    if parent_code:
        r = await session.execute(
            text("SELECT id FROM t_menu WHERE code = :c AND deleted_at IS NULL"),
            {"c": parent_code},
        )
        row = r.first()
        if row is not None:
            parent_id = int(row[0])
    await session.execute(
        text(
            """
            INSERT INTO t_menu
              (id, parent_id, code, title, path, icon, sort_order, is_active,
               created_at, updated_at)
            VALUES
              (:id, :parent_id, :code, :title, :path, :icon, :sort_order, :is_active,
               now(), now())
            """
        ),
        {
            "id": mid, "parent_id": parent_id,
            "code": code, "title": title,
            "path": path, "icon": icon,
            "sort_order": sort_order, "is_active": is_active,
        },
    )
    return mid


async def _insert_role_menu(
    session: AsyncSession, *, role: str, menu_id: int
) -> None:
    from utils.id_gen import new_id
    await session.execute(
        text(
            """
            INSERT INTO t_role_menu (id, role, menu_id, created_at, updated_at)
            VALUES (:id, :role, :menu_id, now(), now())
            """
        ),
        {"id": new_id(), "role": role, "menu_id": menu_id},
    )


async def _seed_full_menus(session: AsyncSession) -> dict[str, int]:
    """铺与 migration 默认 seed 一致的 12 行菜单；返回 code -> id 映射。"""
    await _truncate_menu_tables(session)
    ids: dict[str, int] = {}
    # 顶层
    ids["home"] = await _insert_menu(session, code="home", title="首页",
                                     path="/dashboard", icon="House", sort_order=10)
    ids["order_group"] = await _insert_menu(session, code="order_group",
                                            title="订单管理", path=None, icon="Tickets", sort_order=20)
    ids["auth_group"] = await _insert_menu(session, code="auth_group",
                                           title="权限管理", path=None, icon="Key", sort_order=30)
    ids["floor_group"] = await _insert_menu(session, code="floor_group",
                                            title="车间", path=None, icon="Tools", sort_order=40)
    # order_group 子项
    ids["parts_list"] = await _insert_menu(
        session, code="parts_list", title="零件一览",
        parent_code="order_group", path="/parts", icon="Box", sort_order=10,
    )
    ids["parts_new"] = await _insert_menu(
        session, code="parts_new", title="新建零件",
        parent_code="order_group", path="/parts/new", icon="Plus", sort_order=20,
    )
    ids["assemblies_list"] = await _insert_menu(
        session, code="assemblies_list", title="装配件一览",
        parent_code="order_group", path="/assemblies", icon="Connection", sort_order=30,
    )
    ids["assemblies_new"] = await _insert_menu(
        session, code="assemblies_new", title="新建装配件",
        parent_code="order_group", path="/assemblies/new", icon="Plus", sort_order=40,
    )
    # auth_group 子项
    ids["workers_list"] = await _insert_menu(
        session, code="workers_list", title="工人一览",
        parent_code="auth_group", path="/workers", icon="User", sort_order=10,
    )
    ids["users_list"] = await _insert_menu(
        session, code="users_list", title="账号管理",
        parent_code="auth_group", path="/users", icon="List", sort_order=20,
    )
    # floor_group 子项
    ids["shelves_list"] = await _insert_menu(
        session, code="shelves_list", title="货架管理",
        parent_code="floor_group", path="/shelves", icon="Platform", sort_order=10,
    )
    ids["scan_badge"] = await _insert_menu(
        session, code="scan_badge", title="扫码台",
        parent_code="floor_group", path="/scan/badge", icon="Promotion", sort_order=20,
    )
    # MANAGER 全部可见
    for code in (
        "home", "order_group", "parts_list", "parts_new",
        "assemblies_list", "assemblies_new",
        "auth_group", "workers_list", "users_list",
        "floor_group", "shelves_list", "scan_badge",
    ):
        await _insert_role_menu(session, role="MANAGER", menu_id=ids[code])
    # SHELF_ACCOUNT 仅 home + floor_group + scan_badge
    for code in ("home", "floor_group", "scan_badge"):
        await _insert_role_menu(session, role="SHELF_ACCOUNT", menu_id=ids[code])
    await session.commit()
    return ids


async def test_build_tree_manager_full(db_session: AsyncSession) -> None:
    """MANAGER 角色：4 个顶层分组；order_group 4 子项；auth_group 2；floor_group 2。"""
    await _seed_full_menus(db_session)
    repo = MenuRepository(db_session)
    tree = await build_menu_tree(repo, ["MANAGER"])
    assert [n.code for n in tree] == ["home", "order_group", "auth_group", "floor_group"]
    order = next(n for n in tree if n.code == "order_group")
    assert [c.code for c in order.children] == [
        "parts_list", "parts_new", "assemblies_list", "assemblies_new",
    ]
    auth = next(n for n in tree if n.code == "auth_group")
    assert [c.code for c in auth.children] == ["workers_list", "users_list"]
    floor = next(n for n in tree if n.code == "floor_group")
    assert [c.code for c in floor.children] == ["shelves_list", "scan_badge"]


async def test_build_tree_shelf_account(db_session: AsyncSession) -> None:
    """SHELF_ACCOUNT：仅 home + floor_group（含 scan_badge 单子项）。"""
    await _seed_full_menus(db_session)
    repo = MenuRepository(db_session)
    tree = await build_menu_tree(repo, ["SHELF_ACCOUNT"])
    assert [n.code for n in tree] == ["home", "floor_group"]
    floor = next(n for n in tree if n.code == "floor_group")
    assert [c.code for c in floor.children] == ["scan_badge"]


async def test_build_tree_empty_roles(db_session: AsyncSession) -> None:
    await _seed_full_menus(db_session)
    repo = MenuRepository(db_session)
    assert await build_menu_tree(repo, []) == []
    assert await build_menu_tree(repo, ["CLERK"]) == []  # 角色无任何菜单


async def test_build_tree_inactive_menu_filtered(db_session: AsyncSession) -> None:
    """is_active=false 的菜单被过滤掉。"""
    await _seed_full_menus(db_session)
    # 把 users_list 关掉
    await db_session.execute(
        text("UPDATE t_menu SET is_active = false WHERE code = 'users_list'")
    )
    await db_session.commit()
    repo = MenuRepository(db_session)
    tree = await build_menu_tree(repo, ["MANAGER"])
    auth = next(n for n in tree if n.code == "auth_group")
    assert [c.code for c in auth.children] == ["workers_list"]


async def test_build_tree_id_and_path_serialization(db_session: AsyncSession) -> None:
    """序列化到 JSON 时 ID 是字符串、path=null 保留。"""
    await _seed_full_menus(db_session)
    repo = MenuRepository(db_session)
    tree = await build_menu_tree(repo, ["MANAGER"])
    raw = tree[0].model_dump(mode="json")
    assert isinstance(raw["id"], str)
    assert isinstance(raw["sort_order"], int)
    assert raw["path"] == "/dashboard"  # home 有 path
    # 第二个节点 order_group 应该是 path=null
    raw2 = tree[1].model_dump(mode="json")
    assert raw2["path"] is None