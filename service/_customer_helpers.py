"""客户树助手（PartService / OutsourceQuoteService 共享）。

历史：原本是 `OutsourceQuoteService` 的私有方法（`_preload_customer_cache` /
`_make_customer_path_cached` / `_expand_customer_ids`）。2026-07-28
`PartService.list_outsource_sendable` 也需要拼客户路径（"法拉电子 / 三厂"），
但 PartService 没继承这些方法导致运行时报
`'PartService' object has no attribute '_preload_customer_cache'`。抽到共享模块。

依赖：调用方需持有 `CustomerRepository` 实例（`customers.list_by_ids` / `list_children`）。
"""
from model import TCustomer
from repository.customer import CustomerRepository


async def expand_customer_ids(
    customers: CustomerRepository,
    root_customer_id: int,
) -> list[int]:
    """L1 + L2 子节点展平。v1 客户树只有 2 层。"""
    ids: list[int] = [root_customer_id]
    children = await customers.list_children(root_customer_id)
    if children:
        ids.extend(c.id for c in children)
    return list(dict.fromkeys(ids))


async def preload_customer_cache(
    customers: CustomerRepository,
    leaf_ids: list[int],
) -> dict[int, TCustomer]:
    """按客户树深度逐层批量预载（`list_by_ids`），供 `make_customer_path_cached` 用。

    v1 客户树只有 2 层，通常 1~2 次查询即可覆盖全部祖先。
    """
    cache: dict[int, TCustomer] = {}
    frontier = list({cid for cid in leaf_ids if cid is not None})
    while frontier:
        rows = await customers.list_by_ids(frontier)
        if not rows:
            break
        for c in rows:
            cache[c.id] = c
        next_frontier: list[int] = []
        for c in rows:
            pid = c.parent_id
            if pid and pid not in cache and pid not in next_frontier:
                next_frontier.append(pid)
        frontier = next_frontier
    return cache


def make_customer_path_cached(
    cust: TCustomer,
    cache: dict[int, TCustomer],
) -> str | None:
    """纯内存拼客户路径（不再逐条 `get_by_id`）。`cache` 缺祖先则安全截断。"""
    cur: TCustomer | None = cust
    path: list[str] = []
    seen: set[int] = set()
    while cur is not None and cur.id not in seen:
        seen.add(cur.id)
        path.append(cur.name)
        if cur.parent_id is None:
            break
        cur = cache.get(cur.parent_id)
    path.reverse()
    return " / ".join(path) if path else None