"""MCP 只读查询接口的响应 schema（2026-08-08 新增）。

这些 model 的 `response_model` 会被 `fastmcp` 从 OpenAPI 里提取成 MCP tool 的
**`outputSchema`**——AI 直接读这份 schema 决定怎么解释返回值。因此：

- 每个字段都要有 `description`，写业务语义而不是类型（类型 schema 里已经有了）；
- 字段名用直白的英文，不要缩写；
- 有歧义的（`quantity` 是工单总量还是批次量、`total` 数的是行还是零件）必须在
  description 里点明。

⚠️ 与 `api/v1` 不同：`/api/mcp/*` 的响应**不走** `UnifiedResponseMiddleware` 信封，
顶层就是这里定义的对象，没有 `{code, message, data}` 包装。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from schema._types import IdStr, IdStrNonNull


class McpBatchItem(BaseModel):
    """一个生产批次：工单被拆分后，数量与位置真正的载体。

    一个工单（零件）可以拆成多个批次分散在不同位置——比如 100 件里 60 件在货架上
    等下一道工序、40 件已被某个工人领走。工单层面的 `status` / `location` 是这些
    批次里「最落后」那个的 rollup，所以要精确回答「东西在哪」必须看这个数组。
    """

    batch_id: IdStrNonNull = Field(description="批次雪花 ID")
    batch_no: int = Field(description="工单内的批次序号，从 1 递增")
    batch_label: str | None = Field(
        default=None,
        description="批次的人类可读标签，形如 `F0123B2`（零件序列号 + B + 批次号）",
    )
    quantity: int = Field(description="**本批次**的件数（不是工单总量）")
    status: str = Field(
        description=(
            "批次状态：PENDING(待下发) / PROGRAMMING(编程中) / IN_PROCESS(加工中) / "
            "OUTSOURCE(外协中) / INSPECTION(待品检) / READY_TO_SHIP(待发货) / "
            "REPAIRING(返修中) / DELIVERED(已送达) / COMPLETED(已完成) / CANCELLED(已取消)"
        ),
    )
    location: str | None = Field(
        default=None,
        description=(
            "物理位置枚举：OFFICE(编程员办公室) / PRODUCTION_SHELF(生产货架) / "
            "WORKER(工人手上) / INSPECTION_SHELF(品检货架) / OUTSOURCE_COMPANY(外协厂)"
        ),
    )
    holder_display: str | None = Field(
        default=None,
        description=(
            "位置的人话描述，可直接读给人听。例：`货架 A-01` / `货架 品检 B-02` / "
            "`工人 张三` / `外协 XX电镀厂` / `编程员持有`。持有方已被删除时为 null"
        ),
    )
    next_process_name: str | None = Field(
        default=None, description="下一道待执行工序的名称；无待执行工序时为 null"
    )
    has_been_repaired: bool = Field(
        default=False, description="该批次是否经历过返修"
    )
    placed_at: datetime | None = Field(
        default=None, description="进入当前位置的时间；据此可判断压了多久"
    )


class McpDrawingRef(BaseModel):
    """零件图纸文件的引用（只包含 `kind=DRAWING` 的图纸，不含 3D 模型 / G 代码 / CAD 源文件）。"""

    file_id: IdStrNonNull = Field(description="文件雪花 ID")
    filename: str | None = Field(default=None, description="上传时的原始文件名")
    content_type: str | None = Field(
        default=None, description="MIME 类型，通常是 application/pdf 或 image/*"
    )
    file_size: int | None = Field(default=None, description="文件字节数")
    download_path: str = Field(
        description=(
            "图纸内容的下载路径，形如 `/api/mcp/files/{file_id}/content`。"
            "拼上服务的 base URL 后直接 GET 即可拿到文件字节，无需鉴权，链接不过期"
        ),
    )


class McpEventItem(BaseModel):
    """零件流转历史里的一条事件。"""

    event_type: str = Field(
        description="事件类型，如 CREATED / PLACED_ON_SHELF / PICKED_UP / RETURNED / INSPECTED"
    )
    from_status: str | None = Field(default=None, description="变更前状态")
    to_status: str | None = Field(default=None, description="变更后状态")
    quantity: int | None = Field(default=None, description="本次涉及的件数")
    batch_id: IdStr = Field(default=None, description="事件所属批次；工单级事件为 null")
    note: str | None = Field(default=None, description="备注，品检打回原因等写在这里")
    created_at: datetime | None = Field(default=None, description="事件发生时间")


class McpDueRow(BaseModel):
    """到期未送货清单里的一行。

    `row_type` 决定这行怎么读：

    - `PART`：一个独立零件工单。业务字段全部有值，`children` 为空数组。
    - `ASSEMBLY`：一个装配体。装配体表本身**没有系统交期**，所以它不直接参与日期
      筛选——是它下面的子件命中了筛选条件，才把这些子件聚成这一行。因此装配体行
      只有 `id` / `serial_no` / `drawing_no` / `name` 有值，其余业务字段为 null，
      真正的数据在 `children` 里；而且 `children` **只含本次命中的子件**，不是该
      装配体的全部子件。
    """

    row_type: Literal["PART", "ASSEMBLY"] = Field(
        description="行类型：PART=独立零件工单，ASSEMBLY=装配体聚合行（数据在 children 里）"
    )
    id: IdStrNonNull = Field(description="零件或装配体的雪花 ID")
    serial_no: str | None = Field(
        default=None,
        description="序列号（车间实际流转用的编号，如 F0123）；已完成/取消后会被回收为 null",
    )
    drawing_no: str | None = Field(default=None, description="图号")
    name: str = Field(description="零件 / 装配体名称")

    quantity: int | None = Field(
        default=None,
        description="工单**总**件数。各批次件数之和等于它。ASSEMBLY 行为 null",
    )
    status: str | None = Field(
        default=None,
        description="工单状态，是全部活跃批次里「最落后」那个的 rollup。ASSEMBLY 行为 null",
    )
    system_delivery_date: date | None = Field(
        default=None, description="系统交期——本接口筛选依据的日期。ASSEMBLY 行为 null"
    )
    planned_delivery_date: date | None = Field(
        default=None, description="计划交期（与系统交期是两个独立字段）。ASSEMBLY 行为 null"
    )
    order_no: str | None = Field(default=None, description="客户订单号")
    customer_path: str | None = Field(
        default=None, description="客户全路径，形如 `法拉电子 / 母排厂`"
    )
    applicant_name: str | None = Field(default=None, description="申请人姓名")
    is_urgent: bool | None = Field(default=None, description="是否加急件")
    location_summary: str | None = Field(
        default=None,
        description=(
            "工单层面的位置人话描述（rollup 自最落后的批次）。多批次分散在不同位置时"
            "这个字段只反映其中一处，要完整信息请读 `batches`"
        ),
    )

    batches: list[McpBatchItem] = Field(
        default_factory=list,
        description=(
            "该工单的生产批次，**已排除 COMPLETED / CANCELLED 终态批次**。"
            "件数与位置的真实来源。ASSEMBLY 行为空数组"
        ),
    )
    drawing: McpDrawingRef | None = Field(
        default=None, description="该零件的图纸文件；未上传图纸时为 null"
    )
    children: list["McpDueRow"] = Field(
        default_factory=list,
        description="仅 ASSEMBLY 行非空：本次命中筛选条件的子件（不是该装配体的全部子件）",
    )


class McpDueQueryEcho(BaseModel):
    """回显实际生效的查询条件，方便 AI 自检是不是问对了。"""

    due_date: date = Field(description="传入的交期截止日")
    customer_name: str | None = Field(default=None, description="传入的客户名关键字")
    is_urgent: bool | None = Field(default=None, description="传入的加急过滤")
    statuses: list[str] = Field(
        description="实际生效的状态集合（已与「未送货」状态集取交集）"
    )


class McpDueListOut(BaseModel):
    """`query_parts_due` 的返回。"""

    items: list[McpDueRow] = Field(
        description="结果行。注意装配体子件会被聚合，所以行数通常少于 `total`"
    )
    total: int = Field(
        description=(
            "命中筛选条件的**零件**总数（不是 `items` 的行数，也不受分页影响）。"
            "`total > offset + 返回的零件数` 说明还有下一页"
        ),
    )
    limit: int = Field(description="本次每页零件数上限")
    offset: int = Field(description="本次跳过的零件数")
    query: McpDueQueryEcho = Field(description="实际生效的查询条件回显")


class McpPartDetail(BaseModel):
    """`get_part_by_serial` 的返回：单个零件工单的完整快照。"""

    id: IdStrNonNull = Field(description="零件雪花 ID")
    serial_no: str | None = Field(default=None, description="序列号")
    drawing_no: str | None = Field(default=None, description="图号")
    name: str = Field(description="零件名称")
    quantity: int = Field(description="工单总件数")
    status: str = Field(description="工单状态（rollup 自最落后的活跃批次）")
    location_summary: str | None = Field(
        default=None, description="工单层面的位置人话描述"
    )
    system_delivery_date: date | None = Field(default=None, description="系统交期")
    planned_delivery_date: date | None = Field(default=None, description="计划交期")
    actual_delivery_date: date | None = Field(
        default=None, description="实际送达日期；未送货时为 null"
    )
    order_no: str | None = Field(default=None, description="客户订单号")
    note: str | None = Field(default=None, description="工单备注")
    customer_path: str | None = Field(
        default=None, description="客户全路径，形如 `法拉电子 / 母排厂`"
    )
    applicant_name: str | None = Field(default=None, description="申请人姓名")
    is_urgent: bool = Field(description="是否加急件")
    assembly_id: IdStr = Field(
        default=None, description="所属装配体 ID；独立零件为 null"
    )
    assembly_name: str | None = Field(
        default=None, description="所属装配体名称；独立零件为 null"
    )
    drawing: McpDrawingRef | None = Field(default=None, description="图纸文件")
    all_batches: list[McpBatchItem] = Field(
        default_factory=list,
        description="该工单的**全部**批次，含 COMPLETED / CANCELLED 终态批次",
    )
    events: list[McpEventItem] = Field(
        default_factory=list, description="流转历史，按时间升序（旧→新），最多 50 条"
    )
