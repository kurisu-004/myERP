"""2026-09-24 重构：MCP 域整体下线，本仓 service package 仅剩：

- ``StsService``              — STS 凭证端口薄层（``api/v1/sts.py``）
- ``PrintingServiceFacade``   — 2026-09-24 PR-2 新增：零件标签 PDF 打印 facade
  （``api/v1/printing.py``）
- ``DeliveryNotePrintService`` — 2026-09-24 PR-2 新增：送货单 / 标签 Excel 打印
  （``api/v1/delivery_note_print.py``）

历史 service（mcp / part_file / dashboard 等）整体迁至 ``_archive/service/``
或已删除，业务由 backend-rust v2 的 ``/api/v2/*`` 承接。

2026-09-24 PR-3：dormant service helper（_assembly_rollup / _batch_ops /
_delivery_note_events / _session_refresh）已删；保留活跃 helper：
``_id_parse``（str→int 雪花 ID 转换）、``_print_back_page`` / ``_print_front_cache``
（打印图纸 / 送货单 / 标签 PDF 正面 + 背面排版）。
"""

from .delivery_note_print import DeliveryNotePrintService
from .printing import PrintingServiceFacade
from .sts import StsService

__all__ = [
    "DeliveryNotePrintService",
    "PrintingServiceFacade",
    "StsService",
]
