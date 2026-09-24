"""2026-09-24 PR-3 重构：dormant repository 全部下线。

本文件仅导出 printing / delivery_note_print 实际消费的 6 个 repository：

- `PartRepository` / `PartBatchRepository` / `PartFileRepository` — 零件 + 批次
  + 多态文件（service/printing 消费）
- `AssemblyRepository` — 装配体
- `CustomerRepository` — 客户（一级客户序列号前缀解析依赖）
- `DeliveryNoteRepository` — 2026-09-24 PR-2 从 git 785df37^ 恢复，专供
  service.delivery_note_print 调 `notes.get_by_id(note.id)`

历史 dormant repository（applicant / process / worker / shelf / shelf_process /
work_type / work_type_process / outsource_company / outsource_quote /
outsource_quote_event / outsource_shipment / outsource_company_process /
part_event / pickup_skip_event / serial_counter）已删除（2026-09-24 PR-3）。
"""

from .assembly import AssemblyRepository
from .customer import CustomerRepository
from .delivery_note import DeliveryNoteRepository
from .part import PartRepository
from .part_batch import PartBatchRepository
from .part_file import PartFileRepository

__all__ = [
    "AssemblyRepository",
    "CustomerRepository",
    "DeliveryNoteRepository",
    "PartBatchRepository",
    "PartFileRepository",
    "PartRepository",
]
