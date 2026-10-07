"""2026-09-24 PR-3 重构：dormant repository 全部下线。

本文件仅导出 printing 实际消费的 repository：

- `PartRepository` / `PartBatchRepository` / `PartFileRepository` — 零件 + 批次
  + 多态文件（service/printing 消费）
- `AssemblyRepository` — 装配体
- `CustomerRepository` — 客户（一级客户序列号前缀解析依赖）

2026-10-08：送货单 repository 随送货单打印端口下线一并删除（原仅供渲染服务调
`notes.get_by_id`）。

历史 dormant repository（applicant / process / worker / shelf / shelf_process /
work_type / work_type_process / outsource_company / outsource_quote /
outsource_quote_event / outsource_shipment / outsource_company_process /
part_event / pickup_skip_event / serial_counter / delivery_note）已删除
（2026-09-24 PR-3；delivery_note 于 2026-10-08 随打印端口下线删除）。
"""

from .assembly import AssemblyRepository
from .customer import CustomerRepository
from .part import PartRepository
from .part_batch import PartBatchRepository
from .part_file import PartFileRepository

__all__ = [
    "AssemblyRepository",
    "CustomerRepository",
    "PartBatchRepository",
    "PartFileRepository",
    "PartRepository",
]
