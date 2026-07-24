"""装配件相关 schema。"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from repository.assembly import AssemblySortKey
from schema._types import IdStr, IdStrNonNull
from schema.drawing import DrawingFileOut  # noqa: F401  # 兼容旧 import
from schema.part_file import PartFileOut
from schema.part import PartOut
from model.enums import SortDir


class AssemblyOut(BaseModel):
    """装配件展示用出参。`id` / `customer_id` 序列化为字符串。"""

    model_config = ConfigDict(from_attributes=True)

    id: IdStrNonNull
    version: int = Field(description="乐观锁版本号；每次 UPDATE 自增；前端可用于冲突检测")
    serial_no: str | None = Field(
        default=None, description="装配件流水号；旧装配件为 NULL"
    )
    drawing_no: str = Field(description="总图图号（如 E42FX1020107101）")
    name: str = Field(description="装配体名称（如 精研挡料座）")
    applicant_name: str | None = None
    customer_id: IdStrNonNull
    customer_name: str | None = Field(
        default=None, description="客户名（二级叶子节点）"
    )
    parent_customer_name: str | None = Field(
        default=None, description="上级客户名（一级集团）"
    )
    customer_path: str | None = Field(default=None, description="客户完整路径")
    request_date: date
    planned_delivery_date: date
    actual_delivery_date: date | None = None
    is_urgent: bool
    status: str = Field(description="PENDING / IN_PROCESS / COMPLETED / CANCELLED")
    child_count: int = Field(description="子零件数量")
    # —— 2026-07-24 新增：装配体自身价格 + 送货单字段 ——
    quantity: int = Field(default=1, description="装配体套数")
    unit_price: Decimal = Field(default=Decimal("0"), description="装配体单价")
    total_price: Decimal = Field(
        default=Decimal("0"),
        description="装配体总价；service 层在 quantity/unit_price 改动时自动重算",
    )
    order_no: str | None = Field(default=None, max_length=30, description="订单号")
    system_delivery_date: date | None = Field(default=None, description="订单方系统内部交期")
    note: str | None = Field(default=None, max_length=500, description="备注")
    created_at: datetime
    updated_at: datetime


class AssemblyListItem(BaseModel):
    """装配件列表展示用窄出参，与 AssemblyOut 字段一致（已含 serial_no）。

    详情 / 创建响应仍用 AssemblyOut；本 schema 仅服务于 list 端点。
    2026-07-24 起与 AssemblyOut 字段完全对齐（含数量/单价/总价/订单号/系统交期/备注）。
    """

    model_config = ConfigDict(from_attributes=True)

    id: IdStrNonNull
    version: int = Field(description="乐观锁版本号；每次 UPDATE 自增；前端可用于冲突检测")
    serial_no: str | None = None
    drawing_no: str
    name: str
    applicant_name: str | None = None
    customer_id: IdStrNonNull
    customer_name: str | None = None
    parent_customer_name: str | None = None
    customer_path: str | None = None
    request_date: date
    planned_delivery_date: date
    actual_delivery_date: date | None = None
    is_urgent: bool
    status: str
    child_count: int
    # —— 2026-07-24 新增：装配体自身价格 + 送货单字段 ——
    quantity: int = 1
    unit_price: Decimal = Decimal("0")
    total_price: Decimal = Decimal("0")
    order_no: str | None = None
    system_delivery_date: date | None = None
    note: str | None = None
    created_at: datetime
    updated_at: datetime


class AssemblyListQuery(BaseModel):
    """装配体列表查询参数。"""

    customer_id: str | None = Field(default=None, description="客户 id（雪花 ID 字符串）")
    status: str | None = Field(default=None, description="PENDING / IN_PROCESS / COMPLETED / CANCELLED")
    is_urgent: bool | None = Field(default=None, description="是否加急")
    drawing_no_like: str | None = Field(default=None, description="图号模糊匹配")
    name_like: str | None = Field(default=None, description="名称模糊匹配")
    sort_by: AssemblySortKey = Field(
        default=AssemblySortKey.PLANNED_DELIVERY_DATE, description="排序字段"
    )
    sort_dir: SortDir = Field(default=SortDir.ASC, description="排序方向")
    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


class AssemblyListOut(BaseModel):
    items: list[AssemblyListItem]
    total: int
    limit: int
    offset: int


class AssemblyChildCreateRequest(BaseModel):
    """装配件子零件条目（PDF 自动按页拆分后填入）。

    - 子零件数 = PDF 页数 - 1（page 1 = 总图，page 2..N = 子件 1..N-1）
    - 不再接受 unit_price / total_price / page_index；DB 层由 model 默认值
      （unit_price=0, total_price=0）兜底。顺序由前端行序隐式表达：
      position 0 ↔ PDF page 2。
    """

    drawing_no: str = Field(..., min_length=1, max_length=100)
    name: str = Field(..., min_length=1, max_length=200)
    quantity: int = Field(default=1, ge=1)
    applicant_name: str | None = Field(default=None, max_length=50)

    @field_validator("drawing_no", "name", "applicant_name")
    @classmethod
    def strip(cls, v: str | None) -> str | None:
        return v.strip() if v is not None else None


class AssemblyCreateRequest(BaseModel):
    """装配件创建请求体（不含 PDF，PDF 通过 multipart 单文件传）。"""

    name: str = Field(..., max_length=200)
    drawing_no: str = Field(..., min_length=1, max_length=100)
    applicant_name: str | None = Field(default=None, max_length=50)
    applicant_id: str | None = Field(
        default=None,
        description=(
            "申请人表 id（雪花 ID 字符串）。必须是字符串，详见 PartCreateRequest 同名字段。"
        ),
    )
    customer_id: str = Field(description="二级叶子客户 id（雪花 ID 字符串）")
    request_date: date
    planned_delivery_date: date
    is_urgent: bool = False
    # —— 2026-07-24 新增：装配体自身价格 + 送货单字段 ——
    quantity: int = Field(default=1, ge=1, description="装配体套数（默认 1）")
    unit_price: Decimal = Field(default=Decimal("0"), ge=0, description="装配体单价")
    total_price: Decimal | None = Field(
        default=None, ge=0,
        description="装配体总价；不传时由 service 按 unit_price * quantity 计算",
    )
    order_no: str | None = Field(default=None, max_length=30, description="订单号")
    system_delivery_date: date | None = Field(default=None, description="订单方系统内部交期")
    note: str | None = Field(default=None, max_length=500, description="备注")
    # children 可为空（创建空装配体；后续到详情页 add_child / upload-pdf）
    children: list[AssemblyChildCreateRequest] = Field(default_factory=list)

    @field_validator("name", "drawing_no", "applicant_name")
    @classmethod
    def strip(cls, v: str | None) -> str | None:
        return v.strip() if v is not None else None


class AssemblyCreateResult(BaseModel):
    """装配件创建结果。"""

    assembly: AssemblyOut
    children: list[PartOut] = Field(description="子零件列表（含分配的序列号）")
    files: list[PartFileOut] = Field(
        description="装配件 master (kind=ASSEMBLY_MASTER) + 各子件 drawings (kind=DRAWING)"
    )


class AddAssemblyChildRequest(BaseModel):
    """详情页添加单个子件（无 PDF；如需 PDF 走 POST /parts/{id}/files）。"""

    drawing_no: str = Field(..., min_length=1, max_length=100)
    name: str = Field(..., min_length=1, max_length=200)
    quantity: int = Field(default=1, ge=1)

    @field_validator("drawing_no", "name")
    @classmethod
    def strip(cls, v: str) -> str:
        return v.strip()


class AssemblyDetail(BaseModel):
    """装配件详情（统一用于装配体详情页 / 子零件反查）。"""

    assembly: AssemblyOut
    children: list[PartOut]
    files: list[PartFileOut]

class AssemblyUpdateRequest(BaseModel):
    """编辑装配件元数据（field-level partial update；2026-07-11 接入）。

    - 所有字段可选；只更新传入的非 None 字段。
    - 终态（CANCELLED / COMPLETED）禁止再编辑——service 层校验。
    - 改变 `customer_id` **不会**重新生成子件流水号（子件创建时已固化）；
      如确需换 prefix，请取消后重建装配体。
    - `applicant_id` 解析后回填 `applicant_name`；同时传两个时以 id 为准。
    """

    drawing_no: str | None = Field(default=None, min_length=1, max_length=100)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    customer_id: str | None = Field(
        default=None, description="二级叶子客户 id（雪花 ID 字符串）"
    )
    applicant_name: str | None = Field(default=None, max_length=50)
    applicant_id: str | None = Field(
        default=None, description="申请人 id（雪花 ID 字符串）"
    )
    request_date: date | None = None
    planned_delivery_date: date | None = None
    actual_delivery_date: date | None = None
    is_urgent: bool | None = None
    # —— 2026-07-24 新增：装配体自身价格 + 送货单字段 ——
    quantity: int | None = Field(default=None, ge=1, description="装配体套数")
    unit_price: Decimal | None = Field(default=None, ge=0, description="装配体单价")
    total_price: Decimal | None = Field(default=None, ge=0, description="装配体总价；不传时按 unit_price * quantity 重算")
    order_no: str | None = Field(default=None, max_length=30, description="订单号")
    system_delivery_date: date | None = Field(default=None, description="订单方系统内部交期")
    note: str | None = Field(default=None, max_length=500, description="备注")

    @field_validator("drawing_no", "name", "applicant_name")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        return v.strip() if v is not None else None
