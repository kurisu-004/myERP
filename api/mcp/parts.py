"""MCP 只读查询端点：零件（2026-08-08 新增）。

这些端点**没有任何鉴权依赖**——它们是给 AI（MCP host）用的。安全性靠部署层
保证：nginx / 安全组不对公网暴露 `/api/mcp` 与 `/mcp` 前缀。请不要在这里
加 `Depends(get_current_user)`，也不要新增任何写操作端点。

约定：
- 每个端点必须显式给 `operation_id`——它就是 MCP tool 名。不给的话 FastAPI 会
  自动生成 `query_parts_due_api_mcp_parts_due_get` 这种噪音名字。
- 端点的 **docstring 就是 MCP tool 的 description**，AI 靠它决定怎么调。
  写业务语义、写边界、写字段歧义，别写实现细节。
- `response_model` 会被 fastmcp 提取成 MCP `outputSchema`，必须给。
"""
from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from api.deps import get_mcp_query_service
from schema.mcp import McpDueListOut, McpPartDetail
from service.mcp_query import McpQueryService

router = APIRouter(prefix="/parts")


@router.get(
    "/due",
    response_model=McpDueListOut,
    operation_id="query_parts_due",
    summary="查询到期但尚未送货的零件",
)
async def query_parts_due(
    due_date: Annotated[
        date,
        Query(
            alias="date",
            description="交期截止日（格式 YYYY-MM-DD，含当天）",
        ),
    ],
    customer_name: Annotated[
        str | None,
        Query(
            description=(
                "客户名称关键字，子串模糊匹配。命中父客户时自动包含其全部下级分厂，"
                "例如传「法拉」会同时捞到「法拉电子 / 母排厂」下的零件"
            ),
        ),
    ] = None,
    is_urgent: Annotated[
        bool | None,
        Query(description="true=只看加急件，false=只看非加急件，不传=全都要"),
    ] = None,
    statuses: Annotated[
        list[str] | None,
        Query(
            description=(
                "状态白名单，用来缩小范围。可选值：PENDING / PROGRAMMING / IN_PROCESS / "
                "OUTSOURCE / INSPECTION / READY_TO_SHIP / REPAIRING。"
                "不传=以上全部。传已送货的状态无效（会与未送货状态集取交集）"
            ),
        ),
    ] = None,
    limit: Annotated[
        int, Query(ge=1, le=500, description="本页最多返回多少个零件")
    ] = 100,
    offset: Annotated[int, Query(ge=0, description="跳过前多少个零件，用于翻页")] = 0,
    svc: McpQueryService = Depends(get_mcp_query_service),
) -> McpDueListOut:
    """查询系统交期已到（或已逾期）但还没送货的零件，含它们当前在哪、图纸在哪。

    用于回答「X 月 X 日该交的货还有哪些没送出去」「哪些活儿逾期了」「某个逾期零件
    卡在谁手上」这类问题。

    筛选规则（务必理解，否则会误读结果）：

    1. **日期是 `<=` 而不是 `=`**。传 2026-08-08 会返回系统交期在 8 月 8 日**及之前**
       的全部零件，也就是「当天该交的 + 之前就该交却还没交的」。要精确查某一天，
       结果里按 `system_delivery_date` 字段自行过滤。
    2. **没设系统交期的零件不会出现**。系统交期为空表示还没排期，不算「到期」。
    3. **「未送货」= 还在厂内流转**。已送达(DELIVERED)、已完成(COMPLETED)、
       已取消(CANCELLED)的零件都被排除。

    返回值要点：

    - `total` 数的是**零件个数**，不是 `items` 的行数。装配体的子件会被聚合成一行，
      所以 `len(items)` 通常小于本页零件数。判断还有没有下一页请用
      `offset + 本页零件数 < total`，不要用 `len(items)`。
    - `items` 里 `row_type="ASSEMBLY"` 的行是装配体聚合行：它自己的业务字段为 null，
      真实数据在 `children` 里，且 `children` 只含本次命中筛选的子件。
    - 一个零件可能拆成多个批次分散在不同位置。行上的 `location_summary` 只反映其中
      一处（最落后的批次），要完整回答「东西在哪」必须读 `batches` 数组。
    - `drawing.download_path` 拼上服务 base URL 后可直接 GET 到图纸文件字节。
    """
    return await svc.query_due(
        due_date=due_date,
        customer_name=customer_name,
        is_urgent=is_urgent,
        statuses=statuses,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/by-serial/{serial_no}",
    response_model=McpPartDetail,
    operation_id="get_part_by_serial",
    summary="按序列号查零件详情",
)
async def get_part_by_serial(
    serial_no: Annotated[
        str,
        Path(description="零件序列号，车间实际流转用的编号，形如 F0123 或 F0123-01"),
    ],
    svc: McpQueryService = Depends(get_mcp_query_service),
) -> McpPartDetail:
    """按序列号查单个零件工单的完整快照：当前状态、全部批次的位置、图纸、流转历史。

    用于在拿到零件清单后钻取细节，回答「这个件到底卡在哪一步」「它什么时候被谁领走的」
    「品检为什么打回」这类问题——打回原因写在 `events[].note` 里。

    与列表接口的区别：

    - `all_batches` 含**全部**批次，包括已完成 / 已取消的终态批次（列表接口会隐藏它们）。
    - 额外返回 `events` 流转历史，按时间升序（旧→新），最多 50 条。
    - 不受「未送货」状态过滤影响：已送达 / 已完成 / 已取消的零件也查得到。

    序列号在零件完成或取消后会被回收，所以查不到不代表这个件不存在，可能是已经完结了。
    """
    return await svc.get_by_serial(serial_no)
