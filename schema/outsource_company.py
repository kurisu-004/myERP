"""外协公司 (OutsourceCompany) 相关 Pydantic schema。

设计要点：
- ID / process_id 用 IdStr / IdStrNonNull（雪花 ID 字符串化），
  与 CLAUDE.md §3 / schema/_types.py 一致。
- 名单独承载 `*WithProcessesOut`（嵌套映射条目），与 `*Out`（无映射）
  解耦，避免列表接口每次都 JOIN 出全部映射行。
- `SetOutsourceCompanyProcessRequest` 用「整体替换」语义（参考
  schema/work_type_process.py::SetWorkTypeProcessRequest）。
- 2026-07-28：新增 `OutsourceSentPartItem` / `OutsourceSentPartListOut`
  用于外协对账端点（按公司聚合 SENT_TO_OUTSOURCE 事件）。
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from model.enums import PartLocation, PartStatus, ProcessCategory
from schema._types import IdStr, IdStrNonNull


# ============================================================
# 基础出参 / 入参
# ============================================================


class OutsourceCompanyOut(BaseModel):
    """单条外协公司展示用出参（无映射）。"""

    model_config = ConfigDict(from_attributes=True)

    id: IdStrNonNull
    version: int = Field(description="乐观锁版本号；每次 UPDATE 自增；前端可用于冲突检测")
    name: str
    contact_name: str | None = None
    contact_phone: str | None = None
    address: str | None = None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class OutsourceCompanyProcessLinkOut(BaseModel):
    """单条映射条目（用于嵌套显示该公司能力清单）。"""

    model_config = ConfigDict(from_attributes=True)

    process_id: IdStrNonNull
    process_code: str
    process_name: str
    category: ProcessCategory
    sort_order: int


class OutsourceCompanyWithProcessesOut(OutsourceCompanyOut):
    """外协公司 + 该公司映射的全部工序（含 sort_order）。"""

    processes: list[OutsourceCompanyProcessLinkOut] = Field(default_factory=list)


class OutsourceCompanyCreateRequest(BaseModel):
    """新增外协公司。

    雪花 ID 入参用 `str` 类型（CLAUDE.md §3 — 19 位 > JS Number.MAX_SAFE_INTEGER，
    数值化会丢精度）。service 层用 `parse_snowflake_id` 转回 int。
    """

    name: str = Field(min_length=1, max_length=100, description="外协公司名")
    contact_name: str | None = Field(default=None, max_length=50, description="联系人")
    contact_phone: str | None = Field(default=None, max_length=50, description="联系电话")
    address: str | None = Field(default=None, max_length=200, description="地址")
    is_active: bool = Field(default=True, description="是否启用")
    # 创建时可一并提交能力清单（OUTSOURCE 工序 id 列表，**字符串**）；可空。
    # 提交顺序即 sort_order；服务端去重保序。
    process_ids: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def strip(cls, v: str) -> str:
        return v.strip()


class OutsourceCompanyUpdateRequest(BaseModel):
    """更新外协公司字段（全部可选，只更新传入的非 None 字段）。"""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    contact_name: str | None = Field(default=None, max_length=50)
    contact_phone: str | None = Field(default=None, max_length=50)
    address: str | None = Field(default=None, max_length=200)
    is_active: bool | None = Field(default=None)

    @field_validator("name")
    @classmethod
    def strip(cls, v: str | None) -> str | None:
        return v.strip() if v is not None else None


class SetOutsourceCompanyProcessRequest(BaseModel):
    """整体替换某外协公司的工序能力清单。

    前端保存时把当前勾选的 process_id 列表一次性提交；
    service 端 delete-then-insert 替换原映射。

    雪花 ID 入参用 `str`（CLAUDE.md §3）；service 层 `parse_snowflake_id` 转 int。
    """

    process_ids: list[str] = Field(
        default_factory=list,
        description="该公司可执行的外协工序 id 列表（雪花 ID 字符串，提交顺序即 sort_order）",
    )


# ============================================================
# 列表查询 / 响应
# ============================================================


class OutsourceCompanyListQuery(BaseModel):
    """外协公司列表查询参数。"""

    name_like: str | None = Field(default=None, description="公司名模糊搜索")
    is_active: bool | None = Field(default=None, description="是否启用过滤")
    limit: int = Field(default=100, ge=1, le=500, description="分页大小")
    offset: int = Field(default=0, ge=0, description="分页偏移")


# ============================================================
# 外协对账：发送给某外协公司的所有零件一览（2026-07-28 新增）
# ============================================================


class OutsourceSentPartItem(BaseModel):
    """外协对账端点返回项：一次 SENT_TO_OUTSOURCE 事件 + 当前 part 状态。

    字段说明：
    - sent_at / received_at：TPartEvent 的 created_at（事件时间）
    - received_at 为 NULL 表示该 part 还在该公司手上（status=OUTSOURCE）
    - unit_price / total_price：来自 APPROVED 报价；DIRECT 直发为 None
    - is_billed：对账标记（当前固定 False；未来 POST mark-billed API 触发）
    """

    part_id: IdStrNonNull
    part_serial_no: str | None = None
    part_drawing_no: str | None = None
    part_name: str | None = None
    customer_path: str | None = None
    process_id: IdStrNonNull
    process_name: str | None = None
    quantity: int
    unit_price: Decimal | None = None
    total_price: Decimal | None = None
    sent_at: datetime
    received_at: datetime | None = None
    current_status: PartStatus
    current_location: PartLocation | None = None
    is_billed: bool = False


class OutsourceSentPartListOut(BaseModel):
    items: list[OutsourceSentPartItem]
    total: int
    limit: int
    offset: int


class OutsourceSentPartListQuery(BaseModel):
    """外协对账一览查询参数（2026-07-28 新增）。"""

    keyword: str | None = Field(default=None)
    sent_from: datetime | None = Field(
        default=None, description="发送时间起点（含）",
    )
    sent_to: datetime | None = Field(
        default=None, description="发送时间终点（含）",
    )
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class OutsourceCompanyListOut(BaseModel):
    """外协公司列表响应。"""

    items: list[OutsourceCompanyOut]
    total: int
    limit: int
    offset: int