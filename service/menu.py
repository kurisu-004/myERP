"""菜单树组装。

`build_menu_tree` 不持有 session，只读 repository 返回的拍平行，组树。

不实现深循环检测：seed 数据可信，DB CheckConstraint 已防单行自环；
若未来允许外部写入 menu，再补深循环 DFS（见 plan Edge Cases §5）。
"""
from repository.menu import MenuRepository, MenuRow
from schema.menu import MenuNodeOut


async def build_menu_tree(
    menus: MenuRepository, roles: list[str]
) -> list[MenuNodeOut]:
    """从 repository 拉所有 `roles` 可见的菜单行，组树返回。

    排序：顶层按 (sort_order, code)，同 parent 下 children 也按 (sort_order, code)。
    若某节点 `parent_id` 指向不可见/已删的父节点，会被提升为根节点（孤儿兜底）。
    """
    rows = await menus.list_active_for_roles(roles)
    return _to_tree(rows)


def _to_tree(rows: list[MenuRow]) -> list[MenuNodeOut]:
    # O(n) 组装：先建 id -> node 映射，再挂 children
    nodes: dict[int, MenuNodeOut] = {
        r.id: MenuNodeOut(
            id=r.id,
            version=r.version,
            parent_id=r.parent_id,
            code=r.code,
            title=r.title,
            path=r.path,
            icon=r.icon,
            sort_order=r.sort_order,
        )
        for r in rows
    }
    roots: list[MenuNodeOut] = []
    for r in rows:
        node = nodes[r.id]
        if r.parent_id is None:
            roots.append(node)
        else:
            parent = nodes.get(r.parent_id)
            if parent is not None:
                parent.children.append(node)
            else:
                # 父节点不在可见集合里 → 升为根节点（孤儿兜底）
                roots.append(node)
    roots.sort(key=lambda n: (n.sort_order, n.code))
    for n in nodes.values():
        n.children.sort(key=lambda c: (c.sort_order, c.code))
    return roots