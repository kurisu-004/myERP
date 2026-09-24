"""2026-09-24 PR-3 重构：dormant ORM 全部下线。

本文件仅导出 printing / delivery_note_print / sts 实际消费的 6 个 ORM：

- `Base` / `AuditMixin` / `EventTimestampMixin` — ORM 基类 / 审计字段 mixin
- `TPart` / `TPartBatch` / `TPartFile` — 零件 + 批次 + 多态文件（基表 + 批次
  rollup 关系由 alembic / rust v2 维持；本仓仅持有 ORM 抽象供 STS / 打印端口
  间接消费）
- `TAssembly` / `TCustomer` / `TDeliveryNote` — 装配体 / 客户 / 送货单

历史 IAM ORM（TUser / TUserRole / TMenu / TRoleMenu）已删除（2026-09-19
auth 域迁出）；其它 dormant ORM（applicant / process / worker / shelf /
work_type / outsource_company / outsource_quote / part_event / pickup_skip_event
/ serial_counter / drawing_file / cnc_program / delivery_note_event /
delivery_note_counter / process_chain_step / outsource_shipment /
outsource_quote_event / outsource_company_process / shelf_process /
work_type_process）已删除（2026-09-24 PR-3）。

错误码 `BIZ_USER_*` / `BIZ_AUTH_*` 仍保留（历史 unit 测试 + delivery_note_print
集成测试引用），详见 `core/error_code.py`。
"""

from .assembly import TAssembly
from .audit import AuditMixin, EventTimestampMixin
from .base import Base
from .customer import TCustomer
from .delivery_note import TDeliveryNote
from .enums import (
    AssemblyStatus,
    DeliveryNoteEventType,
    DeliveryNoteSortKey,
    DeliveryNoteStatus,
    OutsourceQuoteEventType,
    OutsourceQuoteSortKey,
    OutsourceQuoteStatus,
    PartEventType,
    PartFileKind,
    PartLocation,
    PartSortKey,
    PartStatus,
    ProcessCategory,
    SCAN_EVENT_TYPES,
    ShelfZone,
    SortDir,
    UserRole,
)
from .part import TPart
from .part_batch import TPartBatch
from .part_file import TPartFile

__all__ = [
    "AuditMixin",
    "Base",
    "EventTimestampMixin",
    "TAssembly",
    "TCustomer",
    "TDeliveryNote",
    "TPart",
    "TPartBatch",
    "TPartFile",
    "AssemblyStatus",
    "DeliveryNoteEventType",
    "DeliveryNoteSortKey",
    "DeliveryNoteStatus",
    "OutsourceQuoteEventType",
    "OutsourceQuoteSortKey",
    "OutsourceQuoteStatus",
    "PartEventType",
    "PartFileKind",
    "PartLocation",
    "PartSortKey",
    "PartStatus",
    "ProcessCategory",
    "SCAN_EVENT_TYPES",
    "ShelfZone",
    "SortDir",
    "UserRole",
]
