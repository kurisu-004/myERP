"""外协报价 (OutsourceQuote) 相关 Pydantic schema（2026-07-16 新增）。

- ID 全部用 `IdStr` / `IdStrNonNull`（CLAUDE.md §3，雪花 ID 入参用 str）。
- Decimal 序列化为字符串（前端 `el-input-number` 直接消费）。
- 带 `version` 字段用于 OCC（CLAUDE.md §21）。
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from model.enums import OutsourceQuoteSortKey, OutsourceQuoteStatus, SortDir
from schema._types import IdStr, IdStrNonNull


# ============================================================
# 基础出参
# ============================================================


class OutsourceQuoteOut(BaseModel):
    """单条外协报价展示用出参（PR-H 2026-07-29：扩为外协全生命周期统一事实表）。

    包含预解析字段（service 层手动注入），便于前端列表直接渲染而无需 JOIN。
    """

    model_config = ConfigDict(from_attributes=True)

    id: IdStrNonNull
    version: int = Field(description="乐观锁版本号；每次 UPDATE 自增")
    part_id: IdStrNonNull
    outsource_company_id: IdStrNonNull
    process_id: IdStrNonNull
    price: Decimal
    note: str | None = None
    status: OutsourceQuoteStatus
    submitted_at: datetime | None = None
    reviewed_at: datetime | None = None
    review_note: str | None = None
    # PR-H 2026-07-29 新加字段
    sent_at: datetime | None = None
    received_at: datetime | None = None
    quantity: int | None = None
    is_billed: bool = False
    is_direct: bool = False
    created_at: datetime
    updated_at: datetime

    # 预解析字段（service._to_out 注入；列表展示用）
    part_serial_no: str | None = None
    part_drawing_no: str | None = None
    part_name: str | None = None
    outsource_company_name: str | None = None
    process_code: str | None = None
    process_name: str | None = None
    customer_path: str | None = None  # "法拉电子 / 三厂" 格式
    # 2026-08-02 新增：所属零件的客户下单单价（CNY；与 price 对比谈判空间）
    part_unit_price: Decimal | None = None


# ============================================================
# 列表查询 / 响应
# ============================================================


class OutsourceQuoteListQuery(BaseModel):
    """报价列表查询参数。

    - `customer_id`：按 L1 客户 id 过滤时，service 端把它 + 全部子节点展平为 IN(...)
      （与 PartRepository.list_with_filters 的 customer_ids_in 同款行为）。
    - `keyword`：匹配 `t_part.serial_no` / `t_part.drawing_no` / `t_part.name`。
    - `status` / `statuses` 二选一或多选合并：`statuses` 是多选，service 端
      与 `status` 折叠为 list 传给 repo（用 SQL `IN(...)`）；旧调用方仅传
      `status=DRAFT` 仍兼容。
    """

    status: OutsourceQuoteStatus | None = None
    statuses: list[OutsourceQuoteStatus] | None = None
    part_id: str | None = None
    outsource_company_id: str | None = None
    customer_id: str | None = None
    keyword: str | None = None
    sort_by: OutsourceQuoteSortKey = OutsourceQuoteSortKey.CREATED_AT
    sort_dir: SortDir = SortDir.DESC
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class OutsourceQuoteListOut(BaseModel):
    items: list[OutsourceQuoteOut]
    total: int
    limit: int
    offset: int


# ============================================================
# 入参（CRUD + 状态流转）
# ============================================================


class OutsourceQuoteCreateRequest(BaseModel):
    """新增 DRAFT 报价单。"""

    part_id: str = Field(min_length=1, description="零件雪花 ID 字符串")
    outsource_company_id: str = Field(min_length=1, description="外协公司雪花 ID 字符串")
    process_id: str = Field(min_length=1, description="工序雪花 ID 字符串（必须 OUTSOURCE）")
    price: Decimal = Field(gt=0, max_digits=12, decimal_places=2, description="单件单价")
    note: str | None = Field(default=None, max_length=500)


class OutsourceQuoteUpdateRequest(BaseModel):
    """更新报价单字段（DRAFT 状态才能改）。"""

    version: int = Field(description="乐观锁版本号；不匹配会抛 BIZ_VERSION_CONFLICT 409")
    price: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    note: str | None = Field(default=None, max_length=500)


class OutsourceQuoteApproveRequest(BaseModel):
    """审批通过。"""

    version: int = Field(description="乐观锁版本号")
    review_note: str | None = Field(default=None, max_length=500)


class OutsourceQuoteRejectRequest(BaseModel):
    """审批拒绝（review_note 必填）。"""

    version: int = Field(description="乐观锁版本号")
    review_note: str = Field(min_length=1, max_length=500)


class OutsourceReconciliationUpdateRequest(BaseModel):
    """PR-H 2026-07-29：对账页双击编辑（CLERK + MANAGER）。

    允许状态 OUTSOURCING / RECEIVED / BILLED：
    - unit_price / quantity：直接更新 quote 对应列
    - is_billed 勾选/取消：触发 mark_billed（RECEIVED → BILLED）或
      直接 ORM 写 status=RECEIVED（BILLED → RECEIVED，library 不允许 final 出向）
    - 三者均可独立传 None 表示不更新该字段
    """

    version: int = Field(description="t_outsource_quote.version（OCC）")
    unit_price: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    quantity: int | None = Field(default=None, ge=1)
    is_billed: bool | None = None


# ============================================================
# 发送辅助列表（外协发送列表页用）
# ============================================================


class ApprovedQuoteForSendItem(BaseModel):
    """报价已批准且零件可发送外协时的快捷列出。

    直接返回 PartListItem 形态 + 预解析字段，便于外协发送列表页直接渲染。

    2026-07-29 PR-fix-0.2.0 批次化：行=批次，添加 batch_id / batch_no / batch_quantity，
    version 改为批次 version（OCC 在批次上）。
    """

    version: int = Field(
        default=0,
        description="批次 TPartBatch.version（OCC；前端发送时需回传，2026-07-29 由工单 version 改为批次 version）",
    )
    part_id: IdStrNonNull
    part_serial_no: str | None = None
    part_drawing_no: str | None = None
    part_name: str | None = None
    quantity: int | None = Field(
        default=None,
        description="可发送数量（2026-07-29 批次化：等于 batch_quantity）",
    )
    # 2026-07-29 PR-fix-0.2.0：批次级字段
    batch_id: IdStrNonNull = Field(description="可发送批次 id")
    batch_no: int = Field(description="批次号（per-part 递增）")
    batch_quantity: int = Field(description="批次数量")
    planned_delivery_date: str | None = None
    is_urgent: bool = False
    customer_path: str | None = None
    next_process_id: IdStr | None = None
    next_process_name: str | None = None
    # PR-H 2026-07-28：源货架 code（绑了外协工序的货架，如 C2）
    shelf_code: str | None = None
    # 已批准的最优报价（仅展示用途）
    outsource_company_id: IdStrNonNull
    outsource_company_name: str | None = None
    process_id: IdStrNonNull
    process_name: str | None = None
    price: Decimal
    status_label: Literal["sendable"] = "sendable"


class ApprovedForSendListOut(BaseModel):
    items: list[ApprovedQuoteForSendItem]
    total: int
    limit: int
    offset: int


# ============================================================
# 外协发货记录 (OutsourceShipment) schema（2026-07-30 新增）
# ============================================================


class OutsourceShipmentOut(BaseModel):
    """外协发货记录出参。"""

    model_config = ConfigDict(from_attributes=True)

    id: IdStrNonNull
    version: int = Field(description="乐观锁版本号")
    quote_id: IdStrNonNull
    part_id: IdStrNonNull
    batch_id: IdStr | None = None
    batch_no: int | None = None
    outsource_company_id: IdStrNonNull
    process_id: IdStrNonNull
    quantity: int
    unit_price: Decimal
    status: str = Field(description="OUTSOURCING / RECEIVED / CANCELLED")
    sent_at: datetime
    received_at: datetime | None = None
    is_billed: bool = False
    created_at: datetime
    updated_at: datetime

    # 预解析字段
    part_drawing_no: str | None = None
    part_name: str | None = None
    outsource_company_name: str | None = None
    process_name: str | None = None
    customer_path: str | None = None


class OutsourceShipmentReconcileUpdateRequest(BaseModel):
    """对账页双击编辑 shipment（CLERK + MANAGER）。

    允许状态 OUTSOURCING / RECEIVED：
    - unit_price / quantity：直接更新对应列
    - is_billed 勾选/取消：纯标志位，不驱动状态机
    - 三者均可独立传 None 表示不更新该字段
    """

    version: int = Field(description="t_outsource_shipment.version（OCC）")
    unit_price: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    quantity: int | None = Field(default=None, ge=1)
    is_billed: bool | None = None


class OutsourceInFlightItem(BaseModel):
    """外协中批次行（GET /parts/outsource-in-flight）。"""

    part_id: IdStrNonNull
    batch_id: IdStrNonNull
    batch_no: int
    quantity: int
    serial_no: str | None = None
    drawing_no: str | None = None
    name: str | None = None
    customer_path: str | None = None
    next_process_id: IdStr | None = None
    next_process_name: str | None = None
    outsource_company_id: IdStr | None = None
    outsource_company_name: str | None = None
    sent_at: datetime | None = None
    version: int = Field(description="批次 version（OCC）")
