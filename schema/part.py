from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from model.enums import PartEventType, PartSortKey, PartStatus, SortDir
from schema._types import IdStr, IdStrNonNull


class PartListQuery(BaseModel):
    """零件列表查询参数（同时作为 URL Query 参数）。"""

    customer_id: int | None = Field(default=None, description="客户 id（二级叶子节点）")
    status: PartStatus | None = Field(default=None, description="订单状态精确匹配")
    is_urgent: bool | None = Field(default=None, description="是否加急")
    drawing_no_like: str | None = Field(default=None, description="图号模糊匹配（ILIKE）")
    name_like: str | None = Field(default=None, description="名称模糊匹配（ILIKE）")
    sort_by: PartSortKey = Field(
        default=PartSortKey.PLANNED_DELIVERY_DATE, description="排序字段"
    )
    sort_dir: SortDir = Field(default=SortDir.ASC, description="排序方向")
    limit: int = Field(default=50, ge=1, le=500, description="分页大小")
    offset: int = Field(default=0, ge=0, description="分页偏移")


class PartOut(BaseModel):
    """零件展示用出参（数据大屏用）。

    `id` / `assembly_id` 序列化为字符串，避免 JS `Number.MAX_SAFE_INTEGER`
    精度截断。DB 仍存 BigInteger 雪花 ID。
    """

    model_config = ConfigDict(from_attributes=True)

    id: IdStrNonNull
    serial_no: str | None = Field(
        default=None, description="序列号（每客户独立循环，COMPLETED/CANCELLED 时释放）"
    )
    name: str = Field(description="名称/品名")
    drawing_no: str = Field(description="图号")
    quantity: int = Field(description="数量")
    planned_delivery_date: date = Field(description="计划交期")
    actual_delivery_date: date | None = Field(default=None, description="实际送货日期")
    is_urgent: bool = Field(description="是否加急")
    status: PartStatus = Field(description="订单状态")
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


class PartListOut(BaseModel):
    items: list[PartOut]
    total: int = Field(description="总条数")
    limit: int
    offset: int


class PartCreateRequest(BaseModel):
    """新增 PENDING 零件。系统自动分配序列号。"""

    name: str = Field(min_length=1, max_length=200)
    drawing_no: str = Field(min_length=1, max_length=100)
    applicant_name: str = Field(default="(未知)", max_length=50)
    quantity: int = Field(default=1, ge=1)
    unit_price: Decimal = Field(default=Decimal("0"), ge=0)
    total_price: Decimal | None = Field(default=None, ge=0)
    request_date: date
    planned_delivery_date: date
    actual_delivery_date: date | None = None
    is_urgent: bool = False
    customer_id: int = Field(description="二级叶子客户 id")

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
# 报工流程新增 schema
# ============================================================
class PartReleaseRequest(BaseModel):
    """文员点击"开始生产" — 空体，仅靠 path 上的 part_id。"""


class PartPickUpRequest(BaseModel):
    """工人扫码领取：流水号 + 工牌码。

    - `serial_no` 与 t_part.serial_no 完全相等时定位零件。
    - `badge_code` 与 t_worker.badge_code 完全相等时定位工人。
    """

    serial_no: str = Field(min_length=1, max_length=8)
    badge_code: str = Field(min_length=1, max_length=50)

    @field_validator("serial_no", "badge_code")
    @classmethod
    def strip(cls, v: str) -> str:
        return v.strip()


class PartScanRequest(BaseModel):
    """工人扫序列号（无工牌）触发归还 / 送检。

    event_type ∈ {RETURNED, INSPECTED}。其他值由 service 拒收。
    """

    serial_no: str = Field(min_length=1, max_length=8)
    event_type: PartEventType = Field(
        description="扫码事件类型；扫归还用 RETURNED，送检用 INSPECTED"
    )

    @field_validator("serial_no")
    @classmethod
    def strip(cls, v: str) -> str:
        return v.strip()


class PartEventOut(BaseModel):
    """零件事件流条目。"""

    model_config = ConfigDict(from_attributes=True)

    id: IdStrNonNull
    part_id: IdStrNonNull
    worker_id: IdStr = None
    worker_name: str | None = None
    event_type: str = Field(description="PartEventType 值")
    from_status: str | None = None
    to_status: str | None = None
    drawing_code: str | None = None
    badge_code: str | None = None
    note: str | None = None
    created_at: datetime