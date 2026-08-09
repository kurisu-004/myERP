"""零件 / 批次「所在位置」的人类可读渲染（2026-08-08 抽出）。

规则与 `schema/part.py::PartListItem.current_holder_display` 的文档一致：

| location            | current_holder_id 指向   | 渲染               |
|---------------------|--------------------------|--------------------|
| `PRODUCTION_SHELF`  | `t_shelf.id`             | `货架 <code>`      |
| `INSPECTION_SHELF`  | `t_shelf.id`             | `货架 品检 <code>` |
| `WORKER`            | `t_worker.id`            | `工人 <name>`      |
| `OUTSOURCE_COMPANY` | `t_outsource_company.id` | `外协 <name>`      |
| `OFFICE`            | （无 holder）            | `编程员持有`       |
| 其余 / 查不到名字   | —                        | `None`             |

纯函数：不碰 DB、不碰 ORM。调用方负责先用 map 预取把 id → 名称解析好
（避免 `service/part.py::_to_out` 那种逐行 `list_by_ids([id])` 的 N+1）。

> 现状：`service/part.py` 的 `_to_out` / `_to_batch_out` / `_to_list_out` 里仍内联着
> 同一份规则，本模块目前只有 `service/mcp_query.py` 在用。把那三处也切过来是
> 待办（改动落在 4000 行的热路径文件上，与本次 MCP 需求无关，故未合并进来）。
"""
from __future__ import annotations

_SHELF_LOCATIONS = ("PRODUCTION_SHELF", "INSPECTION_SHELF")


def holder_display_from_maps(
    *,
    location: str | None,
    current_holder_id: int | None,
    shelf_map: dict[int, str],
    worker_map: dict[int, str],
    outsource_company_map: dict[int, str],
) -> str | None:
    """按 `location` 解析多态 `current_holder_id`，渲染成一句人话。

    `current_holder_id` 是多态列，指向哪张表由 `location` 决定。map 里查不到
    （已软删 / 未预取）时降级返回 None，而不是渲染出半截字符串。
    """
    holder_id = int(current_holder_id) if current_holder_id else None

    if holder_id is not None:
        if location in _SHELF_LOCATIONS:
            code = shelf_map.get(holder_id)
            if code is not None:
                prefix = "品检 " if location == "INSPECTION_SHELF" else ""
                return f"货架 {prefix}{code}"
        elif location == "WORKER":
            name = worker_map.get(holder_id)
            if name is not None:
                return f"工人 {name}"
        elif location == "OUTSOURCE_COMPANY":
            name = outsource_company_map.get(holder_id)
            if name is not None:
                return f"外协 {name}"

    if location == "OFFICE":
        return "编程员持有"
    return None
