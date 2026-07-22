"""送货单管理 Pydantic schema（2026-07-22 新增）。

形态参考外协报价（`schema/outsource_quote.py`）+ 零件列表（`schema/part.py`）。
所有雪花 ID 入参用 `str`（CLAUDE.md §3 — JS Number.MAX_SAFE_INTEGER 精度截断）。
"""
from datetime import datetime
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
    created_at: datetime
    updated_at: datetime


class DeliveryNoteLineItem(BaseModel):
    """送货单下一行零件的最小投影。"""

    id: IdStrNonNull
    serial_no: str = Field(description="序列号")
    drawing_no: str
    name: str
    quantity: int
    is_urgent: bool
    status: PartStatus
    is_scanned: bool = Field(
        description="该行是否已被司机扫码（仅 PICKUP 流程使用）",
    )
    scanned: bool = Field(
        description="is_scanned 的别名；前端表格两种命名都可读",
    )


class DeliveryNoteDetailOut(DeliveryNoteOut):
    """送货单详情（含 line_items + 扫码进度）。"""

    line_items: list[DeliveryNoteLineItem]
    scanned_serials: list[str] = Field(
        description="已经累积扫到的 serial_no 列表",
    )


class DeliveryNoteEventOut(BaseModel):
    """送货单事件条目。"""

    id: IdStrNonNull
    delivery_note_id: IdStrNonNull
    event_type: str
    from_status: str | None = None
    to_status: str | None = None
    drawing_code: str | None = Field(default=None, description="扫码时图纸码")
    badge_code: str | None = None
    note: str | None = None
    scanned_count: int | None = None
    expected_count: int | None = None
    created_by: IdStr = None
    created_at: datetime | None = None


# ============================================================
# 入参
# ============================================================
class DeliveryNoteCreateRequest(BaseModel):
    """POST /delivery-notes：创建草稿。"""

    customer_id: str = Field(description="二级叶子客户 id（雪花 ID 字符串）")
    note: str | None = Field(default=None, max_length=500)


class DeliveryNotePartIdsRequest(BaseModel):
    """POST /delivery-notes/{id}/{add,remove}-parts：version OCC + 雪花 ID 列表。"""

    part_ids: list[str] = Field(
        min_length=1, max_length=500,
        description="雪花 ID 字符串列表；service 层 int() 转换",
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
