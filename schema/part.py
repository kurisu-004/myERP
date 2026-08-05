from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from model.enums import PartEventType, PartLocation, PartSortKey, PartStatus, SortDir
from schema._types import IdStr, IdStrNonNull


# 2026-08-05：零件一览行类型筛选。ALL=零件+装配件；PART=仅零件；ASSEMBLY=仅装配件。
class PartRowTypeFilter(str, Enum):
    ALL = "ALL"
    PART = "PART"
    ASSEMBLY = "ASSEMBLY"

# 仅为 Pydantic 类型注解（TYPE_CHECKING 守卫）做静态类型提示；
# 真正的运行时前向引用通过 model_rebuild() 配合 module globals 解析。
if TYPE_CHECKING:
    from schema.assembly import AssemblyOut
    from schema.part_file import PartFileOut


class PartListQuery(BaseModel):
    """零件列表查询参数（同时作为 URL Query 参数）。"""

    customer_id: str | None = Field(default=None, description="客户 id（雪花 ID 字符串）")
    statuses: list[PartStatus] | None = Field(
        default=None, description="订单状态多选（空=全部）"
    )
    is_urgent: bool | None = Field(default=None, description="是否加急（null=全部）")
    keyword: str | None = Field(
        default=None,
        description="搜索关键字（图号 ILIKE 包含匹配 %kw%；名称 ILIKE 前缀匹配 kw%）",
    )
    order_no: str | None = Field(
        default=None, description="订单号搜索（ILIKE 包含匹配 %kw%；2026-07-22 新增）"
    )
    # 2026-07-31：序列号独立搜索框（ILIKE 包含匹配 %kw%）。
    # 子件序列号是 {父装配序列号}-{i:02d} 派生，搜子件序列号时，装配件本身
    # 不带匹配 serial_no —— 需要 EXISTS 子件命中才能带出母装配件行。
    serial_no: str | None = Field(
        default=None,
        description="序列号搜索（ILIKE 包含匹配 %kw%；2026-07-31 新增；装配件子序列号自动带出母装配件）",
    )
    has_outsource_history: bool | None = Field(
        default=None,
        description=(
            "仅返回「曾外协过」的零件（按 t_part_event 存在 "
            "SENT_TO_OUTSOURCE / RECEIVED_FROM_OUTSOURCE / "
            "INSPECTED+note ILIKE '%外协%' 判定；2026-07-20 新增，外协接收历史页用）"
        ),
    )
    # —— PR-F 日期区间筛选（2026-07-21 新增）——
    request_date_from: date | None = Field(default=None, description="请购日期区间起点（含）")
    request_date_to: date | None = Field(default=None, description="请购日期区间终点（含）")
    planned_delivery_date_from: date | None = Field(default=None, description="计划交期区间起点（含）")
    planned_delivery_date_to: date | None = Field(default=None, description="计划交期区间终点（含）")
    system_delivery_date_from: date | None = Field(default=None, description="系统交期区间起点（含）")
    system_delivery_date_to: date | None = Field(default=None, description="系统交期区间终点（含）")
    sort_by: PartSortKey = Field(
        default=PartSortKey.PLANNED_DELIVERY_DATE, description="排序字段"
    )
    sort_dir: SortDir = Field(default=SortDir.ASC, description="排序方向")
    limit: int = Field(default=50, ge=1, le=500, description="分页大小")
    offset: int = Field(default=0, ge=0, description="分页偏移")
    # —— 2026-07-30：装配体并入零件一览 ——
    include_assemblies: bool = Field(
        default=False, description="True 时合并返回装配件行（子件从顶层隐藏）"
    )
    # —— 2026-08-01：下一道工序 / 物理位置多选筛选 ——
    next_process_ids: list[int] | None = Field(
        default=None,
        description="下一道工序 id 多选（雪花 ID int；空=全部；NULL 工序的零件会被自然排除）",
    )
    locations: list[PartLocation] | None = Field(
        default=None,
        description=(
            "物理位置多选（OFFICE / PRODUCTION_SHELF / WORKER / "
            "INSPECTION_SHELF / OUTSOURCE_COMPANY；空=全部）"
        ),
    )
    # —— 2026-08-05：位置筛选细化到具体 holder ——
    holder_ids: list[int] | None = Field(
        default=None,
        description="具体 holder（货架/工人/外协公司）ID 多选；与 locations 为 OR 关系。API 层已由雪花字符串转 int。",
    )
    row_type: PartRowTypeFilter = Field(
        default=PartRowTypeFilter.ALL,
        description="行类型筛选：ALL=零件+装配件 / PART=仅零件 / ASSEMBLY=仅装配件",
    )


class PartOut(BaseModel):
    """零件展示用出参（数据大屏用）。

    `id` / `assembly_id` / `current_holder_id` 序列化为字符串，避免 JS
    `Number.MAX_SAFE_INTEGER` 精度截断。DB 仍存 BigInteger 雪花 ID。
    """

    model_config = ConfigDict(from_attributes=True)

    id: IdStrNonNull
    version: int = Field(description="乐观锁版本号；每次 UPDATE 自增；前端可用于冲突检测")
    serial_no: str | None = Field(
        default=None, description="序列号（每客户独立循环，COMPLETED/CANCELLED 时释放）"
    )
    name: str = Field(description="名称/品名")
    drawing_no: str = Field(description="图号")
    quantity: int = Field(description="数量")
    total_price: Decimal = Field(
        default=Decimal("0"),
        description="总价（quantity * unit_price；2026-07-24 起 PartOut 一并下发，前端一览展示）",
    )
    planned_delivery_date: date = Field(description="计划交期")
    actual_delivery_date: date | None = Field(default=None, description="实际送货日期")
    is_urgent: bool = Field(description="是否加急")
    status: PartStatus = Field(description="订单状态")
    # —— 送货单字段（PR-F 2026-07-17，可选）——
    order_no: str | None = Field(default=None, max_length=30, description="订单号（法拉/路达共用）")
    system_delivery_date: date | None = Field(default=None, description="订单方系统内部交期")
    note: str | None = Field(default=None, max_length=500, description="备注（文员手填）")
    customer_name: str | None = Field(
        default=None, description="客户名（二级节点，如 母排厂）"
    )
    parent_customer_name: str | None = Field(
        default=None, description="上级客户名（一级节点，如 法拉电子）"
    )
    customer_path: str | None = Field(
        default=None, description="客户完整路径，如 法拉电子 / 母排厂"
    )
    assembly_id: IdStr = Field(
        default=None,
        description="所属装配件 id（NULL = 普通独立零件，非任何装配件的子件）",
    )
    # —— 送货单字段（PR-G 2026-07-22 新增）——
    delivery_note_id: IdStr = Field(
        default=None,
        description="所属送货单 id（NULL = 未开单；详见 t_part.delivery_note_id）",
    )
    delivery_note_no: str | None = Field(
        default=None,
        description="所属送货单单号（DN-YYYYMMDD-NNNN）；NULL = 未开单",
    )
    delivery_note_status: str | None = Field(
        default=None,
        description="所属送货单状态（DRAFT / SUBMITTED / PICKED_UP / ARCHIVED）；NULL = 未开单",
    )
    # —— 多态 holder ——
    current_holder_id: IdStr = Field(
        default=None,
        description="当前持有者（worker.id 或 shelf.id；含义看 current_holder_kind）",
    )
    current_holder_kind: Literal["shelf", "worker", "outsource_company"] | None = Field(
        default=None,
        description="holder 实际指向哪张表（service 层批查 t_shelf ∪ t_worker ∪ t_outsource_company 判定）",
    )
    shelf_code: str | None = Field(
        default=None,
        description="若 holder 是 shelf，填它的 code（便于前端展示）",
    )
    placed_at: datetime | None = Field(
        default=None,
        description="首次放到生产货架的时间（PENDING→IN_PROCESS 时置位）",
    )
    location: str | None = Field(
        default=None,
        description="零件物理位置: OFFICE / PRODUCTION_SHELF / WORKER / INSPECTION_SHELF",
    )
    worker_name: str | None = Field(
        default=None,
        description="当 holder 是工人时返回工人姓名；否则 null",
    )
    outsource_company_name: str | None = Field(
        default=None,
        description="当 holder 是外协公司时返回公司名；否则 null（2026-07-15 新增）",
    )
    current_holder_display: str | None = Field(
        default=None,
        description=(
            "所在位置的人类可读描述："
            "PRODUCTION_SHELF → '货架 A-01'；"
            "INSPECTION_SHELF → '品检 A-01'；"
            "WORKER → '工人 张三'；"
            "OFFICE → '编程员持有'；"
            "None → '—'。"
        ),
    )
    next_process_id: IdStr = Field(
        default=None,
        description="下一道工序 id（NULL = 未设置）",
    )
    next_process_name: str | None = Field(
        default=None,
        description="下一道工序名称（NULL = 未设置；避免前端再查 processes 表）",
    )
    last_inspection_fail_note: str | None = Field(
        default=None,
        description=(
            "最近一次品检打回事件的 note（含「打回到货架：xxx | 备注：xxx」格式，"
            "由 LEFT JOIN LATERAL t_part_event 计算；工人扫码领取时显示，"
            "2026-07-21 新增）"
        ),
    )
    # —— 批次字段（2026-07-29 批次化；仅「行=批次」的列表场景填充）——
    batch_id: IdStr = Field(
        default=None,
        description="批次 id（扫码台/品检等待批次级列表填充；请求回传定位批次用）",
    )
    batch_no: int | None = Field(
        default=None, description="批次序号（工单内 1 起）",
    )
    batch_label: str | None = Field(
        default=None,
        description="批次展示码（serial||'B'||batch_no，如 F1234B01；serial 释放后回退 批次N）",
    )
    # —— 2026-08-04 「返修接收」PR-M：返修件标识 ——
    has_been_repaired: bool = Field(
        default=False,
        description="是否经历过返修；为 true 时列表 / 卡片显示「返修」el-tag（PR-M）",
    )


class PartBatchOut(BaseModel):
    """批次监控出参（2026-07-29 批次化；详情页批次卡片）。"""

    model_config = ConfigDict(from_attributes=True)

    id: IdStrNonNull
    version: int = Field(description="乐观锁版本号")
    part_id: IdStrNonNull
    batch_no: int = Field(description="批次序号（工单内 1 起）")
    batch_label: str = Field(description="批次展示码（serial||'B'||batch_no）")
    quantity: int = Field(description="本批次数量")
    status: str = Field(description="批次状态（取值同 PartStatus）")
    location: str | None = Field(default=None, description="批次物理位置")
    current_holder_id: IdStr = Field(default=None, description="当前持有者 id")
    current_holder_display: str | None = Field(
        default=None, description="所在位置的人类可读描述（同 PartOut）",
    )
    next_process_id: IdStr = Field(default=None, description="下一道工序 id")
    next_process_name: str | None = Field(default=None, description="下一道工序名称")
    placed_at: datetime | None = Field(default=None, description="进入 ON_SHELF 时间")
    delivery_note_id: IdStr = Field(default=None, description="所属送货单 id")
    delivery_note_no: str | None = Field(default=None, description="所属送货单单号")
    parent_batch_id: IdStr = Field(default=None, description="拆分谱系：源批次 id")
    # —— 2026-08-04 「返修接收」PR-M：批次级返修件标识 ——
    has_been_repaired: bool = Field(
        default=False,
        description="本批次是否经历过返修；与服务层 t_part_batch.has_been_repaired 同步",
    )
    created_at: datetime
    updated_at: datetime


class BatchSplitRequest(BaseModel):
    """手动拆分批次请求（详情页操作）。"""

    batch_id: IdStrNonNull = Field(description="源批次 id")
    quantity: int = Field(gt=0, description="拆出数量（必须 < 源批次量）")


class PartBatchActionRequest(BaseModel):
    """无 body 流转端点的可选批次参数（pass/deliver/complete/repair/cancel 等）。"""

    batch_id: IdStr = Field(default=None, description="目标批次 id；缺省按状态唯一批次解析")
    quantity: int | None = Field(
        default=None, gt=0, description="部分数量；缺省 = 批次全量",
    )


class RepairDispatchRequest(BaseModel):
    """PR-M 2026-08-04 续：一步式返修下发（DELIVERED → REPAIRING → ON_SHELF/INSPECTION）。

    - shelf_id 必填；zone 必须 PRODUCTION 或 INSPECTION（其它 zone 拒绝）。
    - next_process_id 可选；缺省沿用 start_repair 携带的下一道工序（PRODUCTION 区校验映射）。
    - batch_id / quantity 可选；部分量走 _maybe_split 拆批。
    """

    shelf_id: int = Field(..., description="目标货架 id（PRODUCTION 或 INSPECTION）")
    next_process_id: int | None = Field(
        default=None, description="下一道工序 id（可选；缺省沿用 REPAIRING 携带的下一工序）",
    )
    batch_id: IdStr = Field(default=None, description="目标批次 id（可选；缺省按状态唯一批次解析）")
    quantity: int | None = Field(default=None, gt=0, description="部分数量（可选；缺省 = 批次全量")


class InspectionBatchListOut(BaseModel):
    """品检待办（批次级）分页出参。"""

    items: list[PartOut]
    total: int
    limit: int
    offset: int


class PartListItem(BaseModel):
    """零件列表展示用窄出参（仅前端一览所需字段）。

    与 PartOut 的差异：
    - 不含 `assembly_id` / `current_holder_id` / `current_holder_kind`：
      一览不再显示装配链接、多态 holder；
    - 不含 `placed_at`：仅放上架时间不暴露给 picker。

    2026-07-28 PR-H：补 `next_process_id` / `next_process_name` 给 picker 自动填工序用。
    2026-07-30：加 `row_type` / `has_children` / `child_count` 支持装配体合并展示。
    详情 / 创建 / 编辑响应仍用 PartOut；本 schema 仅服务于 list 端点。
    """

    model_config = ConfigDict(from_attributes=True)

    id: IdStrNonNull
    version: int = Field(description="乐观锁版本号；每次 UPDATE 自增；前端可用于冲突检测")
    serial_no: str | None = Field(
        default=None, description="序列号（每客户独立循环，COMPLETED/CANCELLED 时释放）"
    )
    name: str
    drawing_no: str
    applicant_name: str | None = Field(default=None, description="申请人姓名快照")
    quantity: int
    unit_price: Decimal = Field(default=Decimal("0"), description="单价")
    total_price: Decimal = Field(
        default=Decimal("0"),
        description="总价 = quantity * unit_price（2026-07-24 新增；UI 与后端落库字段一致）",
    )
    request_date: date = Field(description="请购日期")
    planned_delivery_date: date
    actual_delivery_date: date | None = None
    is_urgent: bool
    status: PartStatus
    # —— 送货单字段（PR-F 2026-07-17，可选）——
    order_no: str | None = Field(default=None, description="订单号")
    system_delivery_date: date | None = Field(default=None, description="订单方系统内部交期")
    note: str | None = Field(default=None, description="备注")
    customer_name: str | None = Field(
        default=None, description="客户名（二级节点）"
    )
    parent_customer_name: str | None = Field(
        default=None, description="上级客户名（一级节点）"
    )
    customer_path: str | None = Field(
        default=None, description="客户完整路径"
    )
    delivery_note_id: IdStr = Field(
        default=None,
        description="所属送货单 id（NULL = 未开单；PR-G 2026-07-22 新增；零件一览浅蓝染色依据）",
    )
    location: str | None = Field(
        default=None, description="OFFICE / PRODUCTION_SHELF / WORKER / INSPECTION_SHELF"
    )
    shelf_code: str | None = Field(
        default=None, description="holder 为货架时的 code"
    )
    worker_name: str | None = Field(
        default=None, description="holder 为工人时的姓名"
    )
    # 2026-08-05：外协公司名（location=OUTSOURCE_COMPANY 时填充；前端展示用）
    outsource_company_name: str | None = Field(
        default=None, description="外协公司名（location=OUTSOURCE_COMPANY 时）"
    )
    current_holder_display: str | None = Field(
        default=None,
        description=(
            "所在位置的人类可读描述；见 PartOut 字段说明"
        ),
    )
    # 2026-07-28 PR-H：picker 自动填工序
    next_process_id: IdStr = Field(
        default=None,
        description="下一工序 id（NULL = 未设置；新建外协报价 picker 自动填工序用）",
    )
    next_process_name: str | None = Field(
        default=None, description="下一工序名（NULL = 未设置）"
    )
    # 2026-07-29 PR-fix-0.2.0：批次化字段（仅 picker 走批次时填充；普通 /parts 列表为 NULL）
    batch_id: IdStr = Field(
        default=None,
        description="批次 id（仅 /outsource-quotes/quotable-parts 走批次时填充）",
    )
    batch_no: int | None = Field(
        default=None, description="批次号（per-part 递增）",
    )
    batch_quantity: int | None = Field(
        default=None, description="批次数量（picker 选中的可报价批次量）",
    )
    # —— 2026-07-30：装配体合并展示字段 ——
    created_at: datetime | None = Field(
        default=None, description="创建时间（排序用；仅列表场景填充）"
    )
    row_type: Literal["PART", "ASSEMBLY"] = Field(
        default="PART", description="行类型：PART=独立零件/子件；ASSEMBLY=装配件"
    )
    has_children: bool = Field(
        default=False, description="是否为有子件的装配件行"
    )
    child_count: int | None = Field(
        default=None, description="装配件子件数量（仅 row_type=ASSEMBLY 时填充）"
    )
    # —— 2026-08-04 「返修接收」PR-M：一览返修标记 ——
    has_been_repaired: bool = Field(
        default=False,
        description="是否经历过返修；为 true 时列表行展示「返修」el-tag（PR-M）",
    )
    # —— 2026-08-05 C2：装配件携带的「命中子件」 ——
    # 仅当 next_process_ids / locations / holder_ids 筛选激活时填充；
    # 装配件下属子件中满足筛选的子零件全集（按当前排序）。其余情况为 null。
    # 前端 loadChildren 优先消费本字段，避免每次展开都触发 /assemblies/{id} 详情查询。
    matched_children: list["PartListItem"] | None = Field(
        default=None,
        description=(
            "仅当 next_process_ids / locations / holder_ids 筛选激活时填充；"
            "装配件下属子件中满足筛选的子零件全集（按当前排序）。其余情况为 null。"
        ),
    )


class PartListOut(BaseModel):
    items: list[PartListItem]
    total: int = Field(description="总条数")
    limit: int
    offset: int


class PartCreateRequest(BaseModel):
    """新增 PENDING 零件。系统自动分配序列号。"""

    name: str = Field(min_length=1, max_length=200)
    drawing_no: str = Field(min_length=1, max_length=100)
    applicant_name: str = Field(default="(未知)", max_length=50)
    applicant_id: str | None = Field(
        default=None,
        description=(
            "申请人表 id（雪花 ID 字符串）。必须是字符串，因为 JS Number "
            "在 19 位雪花 ID 上会丢失精度。非空时按姓名快照写入 t_part。"
        ),
    )
    quantity: int = Field(default=1, ge=1)
    unit_price: Decimal = Field(default=Decimal("0"), ge=0)
    total_price: Decimal | None = Field(default=None, ge=0)
    request_date: date
    planned_delivery_date: date
    actual_delivery_date: date | None = None
    is_urgent: bool = False
    # —— 送货单字段（PR-F 2026-07-17，可选）——
    order_no: str | None = Field(default=None, max_length=30, description="订单号")
    system_delivery_date: date | None = Field(default=None, description="订单方系统内部交期")
    note: str | None = Field(default=None, max_length=500, description="备注")
    customer_id: str = Field(description="二级叶子客户 id（雪花 ID 字符串）")

    @field_validator("name", "drawing_no", "applicant_name")
    @classmethod
    def strip(cls, v: str) -> str:
        return v.strip()


class PartStatusChangeRequest(BaseModel):
    """修改零件状态。COMPLETED / CANCELLED 会自动释放序列号。"""

    status: PartStatus


# ============================================================
# 批量新增（Excel 导入用）
# ============================================================
class PartBatchCreateRequest(BaseModel):
    """批量新增零件的请求体（前端从 Excel 解析后一次性提交）。

    所有 `items[i].customer_id` 必须已经在 t_customer 中存在且为叶子节点。
    全部成功才提交；任一行 DB 失败会整体回滚并返回 400，由前端按行提示用户修正后重提。
    """

    items: list[PartCreateRequest] = Field(min_length=1, max_length=1000)


class PartBatchCreateItemFailure(BaseModel):
    """单行失败明细（校验失败 / 依赖缺失）。"""

    index: int = Field(description="0-based 行号，对应 items[i]")
    message: str = Field(description="失败原因")


class PartBatchCreateResult(BaseModel):
    """批量新增结果。

    - `created` 列创建成功的零件（含系统自动分配的 serial_no）。
    - `failed` 列创建前的校验失败（如 customer_id 不存在、必填字段缺失）。
      校验阶段不会修改任何数据。
    """

    created: list[PartOut]
    failed: list[PartBatchCreateItemFailure]


# ============================================================
# 批量树形创建（PDF 自动按页拆分，单页=独立零件，多页=装配件+子件）
# ============================================================
class PartBatchTreeItem(BaseModel):
    """批量树形创建的单条节点（PDF 拆分后每个单页对应一条）。

    - `pdf_index` / `page_index` 由前端基于「解析并预览」步骤生成。
    - `assembly_uid` 在多页 PDF 的所有 page 间共享；单页 PDF 时填 null。
    - `is_master=True` 表示当前页作为该 PDF 的总装图上传（kind=ASSEMBLY_MASTER）；
      多页 PDF 最多 1 条为 True。
    """

    pdf_index: int = Field(ge=0, description="0-based 对应上传 PDF 数组下标")
    page_index: int = Field(ge=0, description="0-based 对应 PDF 内的页码（0 = 第 1 页）")
    assembly_uid: str | None = Field(
        default=None,
        description="所属装配件的客户端 uid（同 PDF 内所有 page 必须共享）；单页 PDF 填 null",
    )
    is_master: bool = Field(
        default=False,
        description="True：当前页作为该 PDF 的总装图上传（kind=ASSEMBLY_MASTER）",
    )
    # —— 元数据 ——
    drawing_no: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    applicant_name: str | None = Field(default=None, max_length=50)
    applicant_id: str | None = Field(default=None, description="申请人 id（雪花 ID 字符串）")
    quantity: int = Field(default=1, ge=1)
    customer_id: str = Field(description="二级叶子客户 id（雪花 ID 字符串）")
    request_date: date
    planned_delivery_date: date
    # —— 送货单字段（PR-F 2026-07-17，可空；用户也可留空，后续 update）——
    order_no: str | None = Field(default=None, max_length=30, description="订单号")
    system_delivery_date: date | None = Field(default=None, description="订单方系统内部交期")
    note: str | None = Field(default=None, max_length=500, description="备注")
    is_urgent: bool = False
    # —— 价格（PR-H 2026-07-28：批量导入时由历史价确认单回填，可空）——
    unit_price: Decimal | None = Field(default=None, ge=0, description="含税单价；来自历史价确认单 G 列")
    total_price: Decimal | None = Field(default=None, ge=0, description="含税价格；空时由 service 自动按 unit_price × quantity 计算")
    # —— 3D 模型（PR-H 2026-07-28：批量导入 STEP 文件）——
    three_d_index: int | None = Field(
        default=None, ge=0,
        description="0-based 对应 three_d_models 数组下标；null = 不挂 3D 模型",
    )

    @field_validator("drawing_no", "name", "applicant_name")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        return v.strip() if v is not None else None


class PartBatchTreeAssembly(BaseModel):
    """一个多页 PDF 对应一个虚拟装配件元数据（前端组装，服务端用其 uid 聚合）。"""

    uid: str = Field(description="前端 uid，与 PartBatchTreeItem.assembly_uid 对齐")
    drawing_no: str | None = Field(
        default=None, max_length=100,
        description="总图图号（仅当 is_master=true 时必填；未选允许空）",
    )
    name: str | None = Field(
        default=None, max_length=200,
        description="装配体名称（仅当 is_master=true 时必填）",
    )
    applicant_name: str | None = Field(default=None, max_length=50)
    applicant_id: str | None = Field(default=None)
    customer_id: str = Field(description="二级叶子客户 id（雪花 ID 字符串）")
    request_date: date
    planned_delivery_date: date
    # 装配体层目前不写 order_no/system_delivery_date（t_assembly 无这两列），
    # 但 schema 接受以便前端统一处理。
    order_no: str | None = Field(default=None, max_length=30, description="订单号（装配体层 schema 接受，DB 不写）")
    system_delivery_date: date | None = Field(default=None, description="系统交期（装配体层 schema 接受，DB 不写）")
    note: str | None = Field(default=None, max_length=500, description="备注")
    is_urgent: bool = False
    quantity: int = Field(default=1, ge=1, description="装配体套数")

    @field_validator("drawing_no", "name", "applicant_name")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        return v.strip() if v is not None else None


class PartBatchTreeRequest(BaseModel):
    """批量树形创建请求体（multipart 中的 `data` JSON 字符串）。"""

    items: list[PartBatchTreeItem] = Field(
        min_length=1, max_length=1000,
        description="所有 PDF 的所有页面展开成的列表（单页 PDF 只有 1 条）",
    )
    assemblies: list[PartBatchTreeAssembly] = Field(
        default_factory=list,
        description="多页 PDF 的装配件元数据；单页 PDF 没有装配体，列表为空",
    )


class PartBatchTreePartResult(BaseModel):
    """创建好的单条零件（含 t_part.id + serial_no）。"""

    uid: str = Field(
        description="前端回传的临时 id（单页 PDF 用 page_index 字符串；多页用 assembly_uid+'-'+page_index）",
    )
    kind: Literal["part", "assembly_child"] = Field(
        description="part = 单页独立零件；assembly_child = 多页装配体子件",
    )
    part: PartOut


class PartBatchTreeAssemblyResult(BaseModel):
    """创建好的装配体（如有）。"""

    uid: str
    assembly: "AssemblyOut"
    master_file: "PartFileOut | None" = Field(
        description="kind=ASSEMBLY_MASTER 的图纸；用户未选 master 时为 null",
    )
    children: list[PartBatchTreePartResult]
    child_files: list["PartFileOut"]


class PartBatchTreeResult(BaseModel):
    """批量树形创建结果。"""

    standalone_parts: list[PartBatchTreePartResult] = Field(
        description="单页 PDF 对应的独立零件列表（无 assembly_id）",
    )
    assemblies: list[PartBatchTreeAssemblyResult] = Field(
        description="多页 PDF 对应的装配件 + 子件列表",
    )
    failed: list[PartBatchCreateItemFailure] = Field(
        default_factory=list,
        description="整体前置校验失败明细（与 /parts/batch 同款结构）",
    )


# `PartBatchTree*` 的前向引用（"AssemblyOut" / "PartFileOut"）在 `schema/__init__.py`
# 末尾解析，那里能保证 schema.assembly / schema.part_file 都已 full-load。
# 这里不再做 model_rebuild，也不再做反向 import（曾经的 try/except 兜底会把
# 循环 import 的失败静默吞成 PydanticUndefinedAnnotation）。


# ============================================================
# 报工流程 schema（货架 → IN_PROCESS / 工人 → IN_PROCESS）
# ============================================================
class PlaceOnShelfRequest(BaseModel):
    """文员把 PENDING 零件放到生产货架：PENDING → IN_PROCESS。

    `next_process_id` 必填 — 第一次下发时必须指定该零件的下一道工序，
    之后工人在 RETURN 时可继续指定下一道。
    """

    shelf_id: int = Field(description="目标生产货架 id")
    next_process_id: int = Field(description="下一道工序 id（必填）")
    # 2026-07-28：外协对账审计字段，仅 receive_from_outsource 路径写入
    # TPartEvent.outsource_company_id；place_on_shelf 路径忽略。可空。
    outsource_company_id: str | None = Field(
        default=None,
        description=(
            "外协公司雪花 ID 字符串（仅 receive_from_outsource 用，对账审计）；"
            "可选；空时 TPartEvent.outsource_company_id 为 NULL"
        ),
    )
    # —— 批次参数（2026-07-29 批次化，可选）——
    batch_id: IdStr = Field(
        default=None, description="目标批次 id；缺省按状态唯一批次解析",
    )
    quantity: int | None = Field(
        default=None, gt=0, description="部分数量；缺省 = 批次全量",
    )

    @field_validator("shelf_id", "next_process_id")
    @classmethod
    def _positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("must be > 0")
        return v


class FailInspectionRequest(BaseModel):
    """2026-07-21 新增：品检打回（INSPECTION → IN_PROCESS）。

    - `shelf_id` 必须在 t_shelf 中存在 / is_active / zone=PRODUCTION。
    - `next_process_id` 必填 —— 由品检员在下道工序下拉里指定，
      service 端走 `_validate_production_shelf_and_process` 校验
      `t_shelf_process` 映射（`BIZ_SHELF_PROCESS_NOT_MAPPED` 422），
      行为与 `place_on_shelf` / `release_from_programming` 对齐。
    - `note` 可选，品检员填不合格原因等；写入 `t_part_event.note`
      （前缀 `"打回到货架：<code> 下一工序：<code> | 备注：<note>"`）。
    - 2026-07-29：`batch_id` / `quantity` 可选（部分打回先拆再转）。
    """

    shelf_id: IdStrNonNull
    next_process_id: IdStrNonNull
    note: str | None = Field(default=None, max_length=500, description="品检备注（不合格原因等）")
    batch_id: IdStr = Field(
        default=None, description="目标批次 id；缺省取唯一 INSPECTION 批次",
    )
    quantity: int | None = Field(
        default=None, gt=0, description="部分数量；缺省 = 批次全量",
    )


class PartUpdateRequest(BaseModel):
    """编辑零件基本信息（所有字段可选，只更新传入的非 None 字段）。"""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    drawing_no: str | None = Field(default=None, min_length=1, max_length=100)
    applicant_name: str | None = Field(default=None, max_length=50)
    quantity: int | None = Field(default=None, ge=1)
    unit_price: Decimal | None = Field(default=None, ge=0)
    total_price: Decimal | None = Field(default=None, ge=0)
    request_date: date | None = None
    planned_delivery_date: date | None = None
    actual_delivery_date: date | None = None
    is_urgent: bool | None = None
    # —— 送货单字段（PR-F 2026-07-17，可选）——
    order_no: str | None = Field(default=None, max_length=30, description="订单号")
    system_delivery_date: date | None = Field(default=None, description="订单方系统内部交期")
    note: str | None = Field(default=None, max_length=500, description="备注")
    customer_id: str | None = Field(default=None, description="客户 id（雪花 ID 字符串）")


class PartPickUpRequest(BaseModel):
    """工人扫码领取：serial_no + 当前货架 id + 工牌码。

    - `serial_no` 与 t_part.serial_no 完全相等时定位零件。
    - `shelf_id` 用于「跨货架拒绝」校验（JWT 内 SHELF_ACCOUNT 的 scope 必须包含该 shelf）。
    - `badge_code` 与 t_worker.badge_code 完全相等时定位工人。
    """

    serial_no: str = Field(min_length=1, max_length=8)
    shelf_id: int = Field(description="操作所在货架 id")
    badge_code: str = Field(min_length=1, max_length=50)
    # —— 批次参数（2026-07-29 批次化，可选）——
    batch_id: IdStr = Field(
        default=None, description="目标批次 id（扫码台卡片回传）；缺省取该货架唯一可领批次",
    )
    quantity: int | None = Field(
        default=None, gt=0, description="领取数量；缺省 = 批次全量",
    )

    @field_validator("serial_no", "badge_code")
    @classmethod
    def strip(cls, v: str) -> str:
        return v.strip()


class PartScanRequest(BaseModel):
    """工人扫序列号触发归还 / 送检。

    event_type ∈ {RETURNED, INSPECTED}。

    - RETURNED：把当前由工人持有的零件放回货架；`shelf_id` 为目标货架。
      `next_process_id` 必填，工人指定下一道工序。
    - INSPECTED：送品检；`shelf_id` 为当前操作货架，`target_inspection_shelf_id` 为目标品检货架。
      `next_process_id` 忽略（送检后由品检环节决定）。
    """

    serial_no: str = Field(min_length=1, max_length=8)
    event_type: PartEventType = Field(
        description="扫码事件类型；扫归还用 RETURNED，送检用 INSPECTED"
    )
    shelf_id: int = Field(description="操作所在货架 id")
    badge_code: str = Field(min_length=1, max_length=50)
    target_inspection_shelf_id: int | None = Field(
        default=None,
        description="仅 INSPECTED 需要；目标品检货架 id（必须 zone=INSPECTION）",
    )
    next_process_id: int | None = Field(
        default=None,
        description="仅 RETURNED 需要；工人指定的下一道工序 id",
    )
    # —— 批次参数（2026-07-29 批次化，可选）——
    batch_id: IdStr = Field(
        default=None, description="目标批次 id（扫码台卡片回传）；缺省取工人唯一持有批次",
    )
    quantity: int | None = Field(
        default=None, gt=0, description="归还/送检数量；缺省 = 批次全量",
    )

    @field_validator("serial_no", "badge_code")
    @classmethod
    def strip(cls, v: str) -> str:
        return v.strip()


class SendToOutsourceRequest(BaseModel):
    """文员把零件发送到外协公司。

    支持来源状态：
    - PENDING  （办公室待生产）
    - IN_PROCESS（任意 sub-state：生产货架上 / 工人手中）
      —— 工人加工几道工序后由 CLERK 选外协公司发送

    服务端校验：
    - outsource_company_id 存在 + 未软删 + is_active=True
    - next_process_id 存在 + category=OUTSOURCE
    - 公司映射了该 OUTSOURCE 工序（t_outsource_company_process）
    - version 与 part.version 一致（OCC）；不一致返 BIZ_VERSION_CONFLICT 409

    行为分支（由 next_process.requires_approval 决定，2026-07-28 新增）：
    - True（默认）：必须有该 (part, company, process) 元组的 APPROVED 报价；
      发送后把报价 mark_used。
    - False（无需审批）：跳过报价检查；要求 part 位于 C2 货架
      （BIZ_OUTSOURCE_DIRECT_REQUIRES_C2_SHELF 422）；事件 note 追加
      「直接发送（无需审批）」。

    雪花 ID 入参用 `str`（CLAUDE.md §3 — 19 位 > JS Number.MAX_SAFE_INTEGER），
    service 层 `parse_snowflake_id` 转 int。
    """

    outsource_company_id: str = Field(
        description="外协公司 id（雪花 ID 字符串；JS Number 会丢精度，必须 str）",
    )
    next_process_id: str = Field(
        description="外协工序 id（雪花 ID 字符串；service 层 parse_snowflake_id 转 int）",
    )
    version: int = Field(
        description=(
            "乐观锁版本号；必须与目标批次 TPartBatch.version 一致"
            "（前端从外协可发送列表返回的 version 取值），否则返回 BIZ_VERSION_CONFLICT 409。"
            "AuditMixin 自动给 UPDATE 加 WHERE version=? 保证并发安全。"
        ),
    )
    # —— 批次参数（2026-07-29 批次化，可选）——
    batch_id: IdStr = Field(
        default=None, description="目标批次 id；缺省取唯一「在产货架」批次",
    )
    quantity: int | None = Field(
        default=None, gt=0, description="部分发送数量；缺省 = 批次全量",
    )

    @field_validator("next_process_id")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v or v == "0":
            raise ValueError("must be non-empty snowflake id")
        return v


class DirectOutsourceCompanyOption(BaseModel):
    """直接发送外协候选返回的可用公司选项。"""

    id: IdStrNonNull
    name: str


class OutsourceSendableItem(BaseModel):
    """外协可发送一览的统一返回项（2026-07-28 新增，取代旧的 ApprovedQuoteForSendItem /
    DirectOutsourceCandidateItem 在「可发送」tab 中的角色）。

    `send_mode` 决定后端 send_to_outsource 的校验分支：
    - `APPROVAL`：必须已有 APPROVED 报价；前端用 `outsource_company_id` 直接发送。
    - `DIRECT`：跳过报价检查；按 `source_status` 走两条路径：
      - PENDING（起始外协）：OFFICE 直接发出，不要求 C2 货架。
      - IN_PROCESS（中间外协）：要求 part 位于 C2 货架。
      前端用 `company_options` 选公司。

    `source_status` 区分起始 vs 中间外协（前端 UI 提示用）。
    """

    version: int = Field(
        default=0,
        description="批次 TPartBatch.version（OCC；前端发送时需回传，2026-07-29 由工单 version 改为批次 version）",
    )
    send_mode: Literal["APPROVAL", "DIRECT"]
    source_status: Literal["PENDING", "IN_PROCESS"]
    part_id: IdStrNonNull
    part_serial_no: str | None = None
    part_drawing_no: str | None = None
    part_name: str | None = None
    quantity: int | None = Field(
        default=None,
        description="可发送数量（2026-07-29 批次化：等于批次 quantity；同 batch_quantity 字段保持兼容）",
    )
    # 2026-07-29 PR-fix-0.2.0：批次级字段（行=批次）
    batch_id: IdStrNonNull = Field(
        description="可发送批次 id（每行=一个批次；前端 picker 直接回传）",
    )
    batch_no: int = Field(description="批次号（per-part 递增；前端展示「批次 N」）")
    batch_quantity: int = Field(
        description="批次数量（等于 quantity；显式暴露避免与 part.quantity 混淆）",
    )
    planned_delivery_date: str | None = None
    is_urgent: bool = False
    customer_path: str | None = None
    next_process_id: IdStrNonNull
    next_process_name: str | None = None
    # APPROVAL 单值（直接发送时也用单值，因前端可能选过）；DIRECT 多值（默认无）
    outsource_company_id: IdStrNonNull | None = None
    outsource_company_name: str | None = None
    company_options: list[DirectOutsourceCompanyOption] = Field(
        default_factory=list,
        description="DIRECT 时为该 part 可用的全部外协公司；APPROVAL 时为空（用 single 字段）",
    )
    # APPROVAL 时为该报价的 Decimal 价格；DIRECT 时为 None（直发无报价）
    price: Decimal | None = None
    status_label: Literal["sendable"] = "sendable"


class OutsourceSendableListOut(BaseModel):
    items: list[OutsourceSendableItem]
    total: int
    limit: int
    offset: int


# 兼容旧字段：DirectOutsourceCandidateItem 仍保留，供仍在使用的服务层路径
# （已迁移到 OutsourceSendableItem + send_mode='DIRECT'，旧 list 端点已下线）。
# 前端不再使用。后续可删除。
class DirectOutsourceCandidateItem(BaseModel):
    """旧版直接发送外协候选（已被 OutsourceSendableItem.send_mode='DIRECT' 取代）。"""

    version: int = Field(default=0)
    part_id: IdStrNonNull
    part_serial_no: str | None = None
    part_drawing_no: str | None = None
    part_name: str | None = None
    quantity: int | None = None
    planned_delivery_date: str | None = None
    is_urgent: bool = False
    customer_path: str | None = None
    next_process_id: IdStrNonNull
    next_process_name: str | None = None
    requires_approval: Literal[False] = False
    status_label: Literal["sendable"] = "sendable"
    company_options: list[DirectOutsourceCompanyOption]


class DirectOutsourceCandidateListOut(BaseModel):
    items: list[DirectOutsourceCandidateItem]
    total: int
    limit: int
    offset: int


class ReceiveToInspectionRequest(BaseModel):
    """2026-07-16 新增：OUTSOURCE → INSPECTION「外协回收送检」分支。

    - shelf_id 必须是 INSPECTION 区 active 货架
    - auto_pass_inspection=True：一次性走「外协→品检→自动通过品检→待送货」三步压缩流程
      （信任外协质量时用；audit 链仍保留 OUTSOURCE→INSPECTION→READY_TO_SHIP 两条事件）
    """

    shelf_id: str = Field(
        min_length=1,
        description="品检货架雪花 ID 字符串；service 端 parse_snowflake_id 转 int",
    )
    auto_pass_inspection: bool = Field(
        default=False,
        description="True 时连发 pass_inspection 一次性推到 READY_TO_SHIP",
    )
    # 2026-07-28：外协对账审计字段，写入 TPartEvent.outsource_company_id；可空。
    outsource_company_id: str | None = Field(
        default=None,
        description=(
            "外协公司雪花 ID 字符串（对账审计）；可选；空时 TPartEvent.outsource_company_id 为 NULL"
        ),
    )
    # —— 批次参数（2026-07-29 批次化，可选）——
    batch_id: IdStr = Field(
        default=None, description="目标批次 id；缺省取唯一 OUTSOURCE 批次",
    )
    quantity: int | None = Field(
        default=None, gt=0, description="部分数量；缺省 = 批次全量",
    )


class PartEventOut(BaseModel):
    """零件事件流条目。"""

    model_config = ConfigDict(from_attributes=True)

    id: IdStrNonNull
    part_id: IdStrNonNull
    batch_id: IdStr = Field(
        default=None, description="事件归属批次 id（2026-07-29；NULL = 工单级事件）",
    )
    batch_no: int | None = Field(
        default=None, description="事件归属批次序号（展示「批次N」用）",
    )
    quantity: int | None = Field(
        default=None, description="本次事件涉及的数量；NULL = 历史数据 / 不适用",
    )
    worker_id: IdStr = None
    worker_name: str | None = None
    event_type: str = Field(description="PartEventType 值")
    from_status: str | None = None
    to_status: str | None = None
    drawing_code: str | None = None
    badge_code: str | None = None
    note: str | None = None
    created_by: IdStr = Field(
        default=None,
        description="操作者 t_user.id（NULL = 系统/历史）",
    )
    operator_username: str | None = Field(
        default=None,
        description=(
            "操作者用户名（list_events 时通过 JOIN t_user 算出，模型不冗余存储）"
        ),
    )
    # 2026-07-17：histories 一览显示操作者姓名（CREATE / 下发 / CANCELLED 等）。
    # 同样通过 list_events JOIN t_user 取，不冗余存；前端 UI 默认用 operator_name 显示。
    operator_name: str | None = Field(
        default=None,
        description=(
            "操作者姓名（display_name = t_user.full_name）。list_events JOIN 算"
        ),
    )
    created_at: datetime


# 2026-08-05：零件一览位置树响应 schema，供 GET /parts/location-tree 用。
# el-tree-select 节点：父节点 = PartLocation 大类；子节点 = 具体 holder（货架/工人/外协公司）。
class LocationTreeNode(BaseModel):
    """el-tree-select 节点。父节点 = PartLocation 大类；子节点 = 具体 holder。"""

    id: str = Field(description="父节点=PartLocation 值；叶子=holder 雪花 ID 字符串")
    name: str = Field(description="显示名")
    location: PartLocation | None = Field(
        default=None, description="父节点有值；叶子为 None"
    )
    children: list["LocationTreeNode"] = Field(default_factory=list)


LocationTreeNode.model_rebuild()


# 2026-08-05 C2：PartListItem.matched_children 自引用（forward ref），模块底部 rebuild。
PartListItem.model_rebuild()


class LocationTreeOut(BaseModel):
    items: list[LocationTreeNode]
