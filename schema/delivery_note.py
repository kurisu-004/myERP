"""送货单管理 Pydantic schema（2026-07-22 新增）。

形态参考外协报价（`schema/outsource_quote.py`）+ 零件列表（`schema/part.py`）。
所有雪花 ID 入参用 `str`（CLAUDE.md §3 — JS Number.MAX_SAFE_INTEGER 精度截断）。
"""
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from model.enums import DeliveryNoteStatus, PartStatus
from schema._types import IdStr, IdStrNonNull


# ============================================================
# 出参：单子概要 / 详情 / 行项
# ============================================================
class DeliveryNoteOut(BaseModel):
    """送货单概要（list + 大部分接口的公共响应）。"""

    model_config = ConfigDict(from_attributes=True)

    id: IdStrNonNull
    version: int = Field(description="乐观锁版本号；前端 edit 时回传")
    delivery_note_no: str = Field(description="单号，DN-YYYYMMDD-NNNN")
    customer_id: IdStrNonNull
    customer_name: str | None = Field(default=None, description="客户名（二级叶子）")
    parent_customer_name: str | None = Field(default=None, description="一级客户名")
    customer_path: str | None = Field(default=None, description="一级 / 二级 路径")
    status: DeliveryNoteStatus = Field(description="DRAFT / SUBMITTED / PICKED_UP / ARCHIVED")
    submitted_at: datetime | None = None
    picked_up_at: datetime | None = None
    submitted_by: IdStr = Field(default=None, description="提交人 t_user.id")
    picked_up_by: IdStr = Field(default=None, description="领取时登入账号 t_user.id")
    driver_worker_id: IdStr = Field(default=None, description="司机 t_worker.id")
    driver_worker_name: str | None = Field(default=None, description="司机姓名")
    part_count: int = Field(description="本单有效零件数")
    note: str | None = Field(default=None, max_length=500)
    delivery_date: date | None = Field(
        default=None,
        description="送货日期；创建时默认今天；DRAFT/SUBMITTED 可手动改",
    )
    created_at: datetime
    updated_at: datetime


class DeliveryNoteLineItem(BaseModel):
    """送货单下一行零件的投影（2026-07-23 扩展：含完整打印字段 + 二级客户）。

    2026-07-29 批次化：行=批次。`id` 为批次 id（行身份），`part_id` 为工单 id；
    quantity/status 取批次值。与 ``frontend/src/views/parts/PartsList.vue`` 列对齐；
    详情页用它渲染内嵌的 line_items el-table，并被 ``XLSX 打印`` 与 ``详情`` 接口共享。
    """

    id: IdStrNonNull = Field(description="批次 id（行身份）")
    part_id: IdStrNonNull = Field(description="工单 id")
    batch_no: int | None = Field(default=None, description="批次序号")
    batch_label: str | None = Field(default=None, description="批次展示码")
    serial_no: str = Field(description="序列号")
    drawing_no: str
    name: str
    quantity: int = Field(description="批次量（本行送货数量）")
    is_urgent: bool
    status: PartStatus = Field(description="批次状态")

    # —— 2026-07-23 新增 ——
    applicant_name: str | None = Field(
        default=None, description="申请人（来自 TPart.applicant_name）",
    )
    request_date: date | None = Field(default=None, description="请购日期")
    planned_delivery_date: date | None = Field(default=None, description="计划交期")
    system_delivery_date: date | None = Field(default=None, description="系统交期")
    order_no: str | None = Field(default=None, description="订单号")
    note: str | None = Field(default=None, max_length=500, description="备注")
    # 客户信息：part.customer_id 是 L2 叶子（与 note.customer_id=L1 root 不同）
    customer_name: str | None = Field(
        default=None, description="零件所属二级（L2）客户名",
    )
    parent_customer_name: str | None = Field(
        default=None, description="所属一级（L1 root）客户名",
    )
    customer_path: str | None = Field(
        default=None, description="L1 / L2 路径（与 note.customer_path 同格式）",
    )

    # —— 既有字段 ——
    is_scanned: bool = Field(
        description="该行是否已被司机扫码（仅 PICKUP 流程使用）",
    )
    scanned: bool = Field(
        description="is_scanned 的别名；前端表格两种命名都可读",
    )

    # —— 2026-08-04 装配件展示与打印 ——
    assembly_id: IdStr = Field(
        default=None,
        description="所属装配件 id（子件行填；散件为 None）",
    )
    assembly_serial_no: str | None = Field(
        default=None, description="装配件序列号（仅子件行填）",
    )
    assembly_drawing_no: str | None = Field(
        default=None, description="装配件总装图号（仅子件行填）",
    )
    assembly_name: str | None = Field(
        default=None, description="装配件名称（仅子件行填）",
    )
    assembly_order_no: str | None = Field(
        default=None, description="装配件订单号（仅子件行填）",
    )


class DeliveryNoteDetailOut(DeliveryNoteOut):
    """送货单详情（含 line_items + 扫码进度）。"""

    line_items: list[DeliveryNoteLineItem]
    scanned_serials: list[str] = Field(
        description="已经累积扫到的 serial_no 列表",
    )


class DeliveryNoteEventOut(BaseModel):
    """送货单事件条目（2026-07-23 精简）。

    字段裁剪背景：
    - drawing_code / badge_code / scanned_count / expected_count 仅 PICKUP_SCANNED
      事件使用，已 drop（alembic 000000000013）。
    - note 字段保留（CREATED / WITHDRAWN 等可附说明）。
    - from_status / to_status 保留（状态机迁移事件需要）。
    """

    id: IdStrNonNull
    delivery_note_id: IdStrNonNull
    event_type: str
    from_status: str | None = None
    to_status: str | None = None
    note: str | None = None
    created_by: IdStr = None
    created_at: datetime | None = None


# ============================================================
# 入参
# ============================================================
class DeliveryNoteAddPartsItem(BaseModel):
    """入单条目（2026-07-29 批次化）：批次 + 可选部分数量。"""

    batch_id: str = Field(description="批次 id（雪花 ID 字符串）")
    quantity: int | None = Field(
        default=None, gt=0,
        description="本次入单数量；缺省 = 批次全量；小于批次量时服务端自动拆分",
    )


class DeliveryNoteCreateRequest(BaseModel):
    """POST /delivery-notes：创建草稿（2026-07-23 扩展 + delivery_date / items）。"""

    customer_id: str = Field(description="一级客户（L1 root）雪花 ID 字符串")
    delivery_date: date | None = Field(
        default=None,
        description="送货日期；默认 = 创建当天（服务端 fallback）",
    )
    items: list[DeliveryNoteAddPartsItem] = Field(
        default_factory=list, max_length=500,
        description=(
            "原子带入首批零件（批次级）；服务层先 create_draft 再 add_parts。"
            "允许批次 status ∈ {INSPECTION, READY_TO_SHIP}；同 L1。"
        ),
    )
    note: str | None = Field(default=None, max_length=500)


class DeliveryNoteUpdateRequest(BaseModel):
    """POST /delivery-notes/{id}/update：partial 字段更新（DRAFT/SUBMITTED）。"""

    version: int = Field(description="乐观锁版本号；不匹配 → BIZ_VERSION_CONFLICT")
    delivery_date: date | None = Field(
        default=None,
        description=(
            "新送货日期；None = 服务端视作「不改」（Pydantic 默认）。"
            "如需清空，由 service 配套加 clear_delivery_date bool；当前 PR 不实现。"
        ),
    )
    note: str | None = Field(default=None, max_length=500)


class DeliveryNoteCandidatePart(BaseModel):
    """候选入单零件（批次级：INSPECTION + READY_TO_SHIP 批次）。

    2026-07-29 批次化：行=批次；quantity 为批次量（可改小，服务端入单时自动拆）。
    2026-08-07 picker 富化：增 L2 / L1 客户字段供工具栏多选筛选与扫描拦截用。
    """

    id: IdStrNonNull = Field(description="工单 id（展示 / 反查用）")
    batch_id: IdStrNonNull = Field(description="批次 id（入单回传用）")
    batch_no: int | None = Field(default=None, description="批次序号")
    batch_label: str | None = Field(default=None, description="批次展示码")
    serial_no: str
    drawing_no: str
    name: str
    quantity: int = Field(description="批次量（可入单的最大数量）")
    applicant_name: str | None = None
    status: PartStatus = Field(description="批次状态")
    planned_delivery_date: date | None = None
    order_no: str | None = Field(default=None, description="订单号（2026-08-01 picker 新增）")
    # —— 2026-08-07 picker 富化 ——
    customer_name: str | None = Field(
        default=None, description="零件所属二级（L2）客户名",
    )
    parent_customer_name: str | None = Field(
        default=None, description="所属一级（L1 root）客户名",
    )
    customer_path: str | None = Field(
        default=None, description="L1 / L2 路径（与 note.customer_path 同格式）",
    )


class DeliveryNoteCandidatePartsOut(BaseModel):
    """GET /delivery-notes/candidate-parts 响应。"""

    items: list[DeliveryNoteCandidatePart] = Field(
        description="同 L1 根、status IN (INSPECTION, READY_TO_SHIP) 的批次",
    )


class DeliveryNoteAddPartsRequest(BaseModel):
    """POST /delivery-notes/{id}/add-parts（2026-07-29 批次化）。"""

    items: list[DeliveryNoteAddPartsItem] = Field(
        min_length=1, max_length=500,
        description="入单批次条目；quantity 小于批次量时服务端自动拆分",
    )
    version: int = Field(description="乐观锁版本号；不匹配 → BIZ_VERSION_CONFLICT")


class DeliveryNoteRemovePartsRequest(BaseModel):
    """POST /delivery-notes/{id}/remove-parts（2026-07-29 批次化）。"""

    batch_ids: list[str] = Field(
        min_length=1, max_length=500,
        description="要移出的批次 id（雪花 ID 字符串列表）",
    )
    version: int = Field(description="乐观锁版本号；不匹配 → BIZ_VERSION_CONFLICT")


class DeliveryNoteVersionedRequest(BaseModel):
    """POST /delivery-notes/{id}/{submit,recall,soft-delete,pickup}：version OCC。"""

    version: int = Field(description="乐观锁版本号；不匹配 → BIZ_VERSION_CONFLICT")


class DeliveryNotePickupScanRequest(BaseModel):
    """POST /delivery-notes/{id}/pickup-scan：司机每扫一个件调一次。"""

    part_serial: str = Field(
        min_length=1, max_length=8,
        description="零件序列号（图背 Code128 条码的载荷）",
    )
    badge_code: str | None = Field(
        default=None, max_length=50,
        description="司机工牌码（可选）；audit 记录 badge_code",
    )


class DeliveryNotePickupScanOut(BaseModel):
    """POST /delivery-notes/{id}/pickup-scan 响应。"""

    delivery_note_id: IdStrNonNull
    scanned_count: int
    expected_count: int
    ready: bool = Field(description="scanned >= expected 且 expected > 0")
    scanned_serials: list[str]


class DeliveryNotePickupRequest(BaseModel):
    """POST /delivery-notes/{id}/pickup：扫齐后一次性 finalize。"""

    driver_worker_id: str = Field(description="司机 t_worker.id 雪花 ID 字符串")
    badge_code: str | None = Field(default=None, max_length=50)
    version: int = Field(description="乐观锁版本号")


# ============================================================
# 一览响应
# ============================================================
class DeliveryNoteListOut(BaseModel):
    """GET /delivery-notes 响应（含分页总计）。"""

    items: list[DeliveryNoteOut]
    total: int
    limit: int
    offset: int


class DeliveryNotePickupListOut(BaseModel):
    """GET /delivery-notes/pickup-pending 响应（司机扫码台用）。"""

    items: list[DeliveryNoteOut]
