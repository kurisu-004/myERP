"""2026-09-19 重构：python 端 IAM 域（auth/user/menu）整体迁出至 backend-rust v2。

活跃 repository：
- `PartRepository` / `PartBatchRepository` / `PartEventRepository` /
  `PartFileRepository` / `AssemblyRepository` / `CustomerRepository` /
  `WorkerRepository` / `ProcessRepository` / `OutsourceCompanyRepository`
  — `McpQueryService` / `PartFileService`（`/api/mcp/*`）

`repository/serial_counter.py` 仍保留模块（被 future-instantiation 测试与 alembic
迁移间接引用），但不再从顶层 package 暴露（2026-09-17 删 auto_complete.py 后
无活跃 service 引用它，import 它走 `from repository.serial_counter import ...`
直连路径即可）。

具体业务 repository（如 applicant/delivery_note/menu/outsource_quote/
pickup_skip_event/user/...）整体移至 `_archive/repository/`，由 backend-rust v2
承接。
"""

from .assembly import AssemblyRepository
from .customer import CustomerRepository
from .outsource_company import OutsourceCompanyRepository
from .part import PartRepository
from .part_batch import PartBatchRepository
from .part_event import PartEventRepository
from .part_file import PartFileRepository
from .process import ProcessRepository
from .shelf import ShelfRepository
from .shelf_process import ShelfProcessRepository
from .work_type import WorkTypeRepository
from .worker import WorkerRepository

__all__ = [
    "AssemblyRepository",
    "CustomerRepository",
    "OutsourceCompanyRepository",
    "PartBatchRepository",
    "PartEventRepository",
    "PartFileRepository",
    "PartRepository",
    "ProcessRepository",
    "ShelfProcessRepository",
    "ShelfRepository",
    "WorkTypeRepository",
    "WorkerRepository",
]
