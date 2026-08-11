"""MCP 只读查询 service（2026-08-08 新增）。

面向 AI 的只读聚合查询，供 `api/mcp/*` 端点调用。与 `PartService` 的区别：

- **不注入 `CurrentUser`**（这是免鉴权端点的前提）、不注入 dashboard broadcaster；
- **纯读**：不写任何表、不触发状态机、不发广播；
- 输出面向 LLM 而非前端表格：位置渲染成人话、字段带完整语义。

⚠️ 部署约束：这些端点没有鉴权，靠 nginx / 安全组不暴露 `/api/mcp` 与 `/mcp` 来兜底。
"""
from __future__ import annotations

from datetime import date

from core.exception import BizError
from core.error_code import ErrCode
from model import TPart, TPartBatch
from model.enums import PartFileKind, PartSortKey, PartStatus, SortDir
from repository.assembly import AssemblyRepository
from repository.customer import CustomerRepository
from repository.outsource_company import OutsourceCompanyRepository
from repository.part import PartRepository
from repository.part_batch import PartBatchRepository
from repository.part_event import PartEventRepository
from repository.part_file import PartFileRepository
from repository.process import ProcessRepository
from repository.shelf import ShelfRepository
from repository.worker import WorkerRepository
from schema.mcp import (
    McpBatchItem,
    McpDrawingRef,
    McpDueListOut,
    McpDueQueryEcho,
    McpDueRow,
    McpEventItem,
    McpPartDetail,
)
from service._customer_helpers import (
    make_customer_path_cached,
    preload_customer_cache,
)
from service._holder_display import holder_display_from_maps

# 「未送货」= 还在厂内流转的活跃状态。排除 DELIVERED（已送达）、COMPLETED（已完成）、
# CANCELLED（已取消）—— 这三个都不算「该交没交」。
UNDELIVERED_STATUSES: tuple[PartStatus, ...] = (
    PartStatus.PENDING,
    PartStatus.PROGRAMMING,
    PartStatus.IN_PROCESS,
    PartStatus.OUTSOURCE,
    PartStatus.INSPECTION,
    PartStatus.READY_TO_SHIP,
    PartStatus.REPAIRING,
)

# 批次列表里默认隐藏的终态：它们对「东西在哪」没有信息量。
_TERMINAL_BATCH_STATUSES = frozenset({
    PartStatus.COMPLETED.value,
    PartStatus.CANCELLED.value,
})

_EVENT_LIMIT = 50


def _drawing_download_path(file_id: int) -> str:
    return f"/api/mcp/files/{file_id}/content"


class McpQueryService:
    def __init__(
        self,
        *,
        parts: PartRepository,
        part_batches: PartBatchRepository,
        customers: CustomerRepository,
        workers: WorkerRepository,
        shelves: ShelfRepository,
        processes: ProcessRepository,
        outsource_companies: OutsourceCompanyRepository,
        assemblies: AssemblyRepository,
        files: PartFileRepository,
        events: PartEventRepository,
    ) -> None:
        self.parts = parts
        self.part_batches = part_batches
        self.customers = customers
        self.workers = workers
        self.shelves = shelves
        self.processes = processes
        self.outsource_companies = outsource_companies
        self.assemblies = assemblies
        self.files = files
        self.events = events

    # ===================== 公开查询 =====================

    async def query_due(
        self,
        *,
        due_date: date,
        customer_name: str | None = None,
        is_urgent: bool | None = None,
        statuses: list[str] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> McpDueListOut:
        """查 `system_delivery_date <= due_date` 且仍未送货的零件。"""
        effective_statuses = self._resolve_statuses(statuses)
        customer_ids_in = await self._resolve_customer_ids(customer_name)

        echo = McpDueQueryEcho(
            due_date=due_date,
            customer_name=customer_name,
            is_urgent=is_urgent,
            statuses=[s.value for s in effective_statuses],
        )

        # 客户名给了但一个都没匹配上 → 空结果，别退化成全量查询。
        if customer_name and not customer_ids_in:
            return McpDueListOut(
                items=[], total=0, limit=limit, offset=offset, query=echo,
            )

        common = {
            "statuses": list(effective_statuses),
            "customer_ids_in": customer_ids_in,
            "is_urgent": is_urgent,
            "system_delivery_date_to": due_date,
            # 2026-08-11 Bug 1 修复后区间条件已默认排除 NULL；`not_null=True` 叠加
            # 作为防御性冗余保留（与新谓词 AND），行为不变（仍是严格 `IS NOT NULL`）。
            # 后续可迁移到 `system_delivery_date_is_null=False` 然后移除本字段。
            "system_delivery_date_not_null": True,
        }
        total = await self.parts.count_with_filters(**common)
        rows = await self.parts.list_with_filters(
            **common,
            sort_by=PartSortKey.SYSTEM_DELIVERY_DATE,
            sort_dir=SortDir.ASC,
            limit=limit,
            offset=offset,
        )

        ctx = await self._build_context(rows)
        part_rows = [self._to_row(p, ctx) for p in rows]
        items = await self._group_by_assembly(rows, part_rows)

        return McpDueListOut(
            items=items, total=total, limit=limit, offset=offset, query=echo,
        )

    async def get_by_serial(self, serial_no: str) -> McpPartDetail:
        """按序列号查单个零件工单的完整快照（含全部批次 + 流转历史）。"""
        part = await self.parts.get_by_serial(serial_no)
        if part is None:
            raise BizError(
                code=ErrCode.BIZ_PART_NOT_FOUND,
                message=f"序列号 {serial_no} 对应的零件不存在",
                http_status=404,
            )

        # 先取全部批次（含终态），再让 _build_context 一轮把 holder / process
        # 的 map 覆盖到这些批次上——避免建完 map 再补差集。
        all_batches = await self.part_batches.list_by_part(part.id)
        ctx = await self._build_context([part], batches=all_batches)

        assembly_name: str | None = None
        if part.assembly_id:
            asm = await self.assemblies.get_by_id(int(part.assembly_id))
            assembly_name = asm.name if asm else None

        events = await self.events.list_by_part(part.id, limit=_EVENT_LIMIT)

        return McpPartDetail(
            id=part.id,
            serial_no=part.serial_no,
            drawing_no=part.drawing_no,
            name=part.name,
            quantity=part.quantity,
            status=part.status,
            location_summary=self._holder_display(
                part.location, part.current_holder_id, ctx,
            ),
            system_delivery_date=part.system_delivery_date,
            planned_delivery_date=part.planned_delivery_date,
            actual_delivery_date=part.actual_delivery_date,
            order_no=part.order_no,
            note=part.note,
            customer_path=ctx["customer_path"].get(part.id),
            applicant_name=part.applicant_name,
            is_urgent=bool(part.is_urgent),
            assembly_id=part.assembly_id,
            assembly_name=assembly_name,
            drawing=ctx["drawing"].get(part.id),
            all_batches=[
                self._to_batch(b, ctx, serial_no=part.serial_no)
                for b in all_batches
            ],
            events=[
                McpEventItem(
                    event_type=e.event_type,
                    from_status=e.from_status,
                    to_status=e.to_status,
                    quantity=e.quantity,
                    batch_id=e.batch_id,
                    note=e.note,
                    created_at=e.created_at,
                )
                for e in events
            ],
        )

    # ===================== 过滤条件解析 =====================

    @staticmethod
    def _resolve_statuses(statuses: list[str] | None) -> tuple[PartStatus, ...]:
        """把调用方传的状态白名单与「未送货」状态集取交集。

        取交集而不是直接采用，是为了让 AI 传 `DELIVERED` 也绕不过接口语义。

        无法识别的值一律 400 报错、并把可选值列出来，**不静默丢弃**：
        AI 传错一个（拼写错、传了已送货状态）却拿到 200 的话，它会把
        「按剩下那个状态过滤出来的结果」当成自己问的答案，错得毫无痕迹。
        """
        if not statuses:
            return UNDELIVERED_STATUSES
        allowed = {s.value: s for s in UNDELIVERED_STATUSES}
        unknown = [s for s in statuses if s not in allowed]
        if unknown:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message=(
                    f"statuses 含无效值 {unknown}（已送货的状态本接口不受理）。"
                    f"可选值：{sorted(allowed)}"
                ),
                http_status=400,
            )
        # 去重并保持 UNDELIVERED_STATUSES 的固定顺序，便于回显稳定。
        picked = {allowed[s] for s in statuses}
        return tuple(s for s in UNDELIVERED_STATUSES if s in picked)

    async def _resolve_customer_ids(self, customer_name: str | None) -> list[int] | None:
        """客户名模糊匹配 → 命中节点及其**整棵子树**的 id。

        匹配到父客户（如「法拉电子」）时要能捞到其下各分厂的零件，所以做子树展开。
        全量客户数是几十量级，内存里跑邻接表比递归 SQL 简单可靠。
        """
        if not customer_name:
            return None
        kw = customer_name.strip()
        if not kw:
            return None

        all_customers = await self.customers.list_all()
        matched = {c.id for c in all_customers if kw in (c.name or "")}
        if not matched:
            return []

        children_of: dict[int, list[int]] = {}
        for c in all_customers:
            if c.parent_id is not None:
                children_of.setdefault(int(c.parent_id), []).append(c.id)

        result: set[int] = set()
        stack = list(matched)
        while stack:
            cid = stack.pop()
            if cid in result:
                continue
            result.add(cid)
            stack.extend(children_of.get(cid, []))
        return sorted(result)

    # ===================== 批量预取（防 N+1） =====================

    async def _build_context(
        self, rows: list[TPart], *, batches: list[TPartBatch] | None = None,
    ) -> dict:
        """一次性把渲染这批零件需要的关联数据全部捞进 map。

        沿用 `service/part.py::_to_list_out` 的预取套路，但把 shelf 也做成 map
        （`_to_out` 那里是逐行 `list_by_ids([id])` 的 N+1）。

        `batches` 显式传入时用它，否则只取未软删批次。`get_by_serial` 要展示
        含终态的**全部**批次，就把那份列表传进来——这样 holder / process 的 map
        一轮就覆盖全，不用事后再补一次差集。
        """
        part_ids = [p.id for p in rows]

        if batches is None:
            batches = await self.part_batches.list_active_by_part_ids(part_ids)
        batches_by_part: dict[int, list[TPartBatch]] = {}
        for b in batches:
            batches_by_part.setdefault(int(b.part_id), []).append(b)

        # holder 是多态列，得先按 location 分流才知道该去哪张表查。
        shelf_ids: set[int] = set()
        worker_ids: set[int] = set()
        company_ids: set[int] = set()
        process_ids: set[int] = set()
        for holder in [*rows, *batches]:
            self._collect_holder_id(holder, shelf_ids, worker_ids, company_ids)
            if holder.next_process_id:
                process_ids.add(int(holder.next_process_id))

        shelf_map = {
            s.id: s.code for s in await self.shelves.list_by_ids(sorted(shelf_ids))
        }
        worker_map = {
            w.id: w.name for w in await self.workers.list_by_ids(sorted(worker_ids))
        }
        company_map = {
            c.id: c.name
            for c in await self.outsource_companies.list_by_ids(sorted(company_ids))
        }
        process_map = {
            p.id: p.name for p in await self.processes.list_by_ids(sorted(process_ids))
        }

        customer_path = await self._build_customer_paths(rows)

        drawing_rows = await self.files.list_by_parts(
            part_ids, kind=PartFileKind.DRAWING.value,
        )
        drawing: dict[int, McpDrawingRef | None] = {}
        for pid, f in drawing_rows.items():
            drawing[pid] = (
                None
                if f is None
                else McpDrawingRef(
                    file_id=f.id,
                    filename=f.original_filename,
                    content_type=f.content_type,
                    file_size=f.file_size,
                    download_path=_drawing_download_path(f.id),
                )
            )

        return {
            "batches_by_part": batches_by_part,
            "shelf": shelf_map,
            "worker": worker_map,
            "company": company_map,
            "process": process_map,
            "customer_path": customer_path,
            "drawing": drawing,
        }

    @staticmethod
    def _collect_holder_id(
        holder, shelf_ids: set[int], worker_ids: set[int], company_ids: set[int]
    ) -> None:
        """按 `location` 把多态 `current_holder_id` 归到正确的桶里。"""
        hid = holder.current_holder_id
        if not hid:
            return
        loc = holder.location
        if loc in ("PRODUCTION_SHELF", "INSPECTION_SHELF"):
            shelf_ids.add(int(hid))
        elif loc == "WORKER":
            worker_ids.add(int(hid))
        elif loc == "OUTSOURCE_COMPANY":
            company_ids.add(int(hid))

    async def _build_customer_paths(self, rows: list[TPart]) -> dict[int, str | None]:
        """part_id → `法拉电子 / 母排厂` 形式的客户全路径。

        走共享的 `preload_customer_cache`：它按层 BFS 直到把所有祖先都载完，
        客户树将来加到三层也不会把路径截断（手写「取一轮 parent」会）。
        """
        cust_ids = [int(p.customer_id) for p in rows if p.customer_id]
        if not cust_ids:
            return {}
        cache = await preload_customer_cache(self.customers, cust_ids)
        return {
            p.id: (
                make_customer_path_cached(cache[int(p.customer_id)], cache)
                if p.customer_id and int(p.customer_id) in cache
                else None
            )
            for p in rows
        }

    # ===================== 组装 =====================

    def _holder_display(self, location, holder_id, ctx: dict) -> str | None:
        return holder_display_from_maps(
            location=location,
            current_holder_id=holder_id,
            shelf_map=ctx["shelf"],
            worker_map=ctx["worker"],
            outsource_company_map=ctx["company"],
        )

    def _to_batch(self, b: TPartBatch, ctx: dict, *, serial_no: str | None = None) -> McpBatchItem:
        label = f"{serial_no}B{b.batch_no}" if serial_no else None
        return McpBatchItem(
            batch_id=b.id,
            batch_no=b.batch_no,
            batch_label=label,
            quantity=b.quantity,
            status=b.status,
            location=b.location,
            holder_display=self._holder_display(b.location, b.current_holder_id, ctx),
            next_process_name=(
                ctx["process"].get(int(b.next_process_id)) if b.next_process_id else None
            ),
            has_been_repaired=bool(b.has_been_repaired),
            placed_at=b.placed_at,
        )

    def _to_row(self, p: TPart, ctx: dict) -> McpDueRow:
        batches = [
            self._to_batch(b, ctx, serial_no=p.serial_no)
            for b in ctx["batches_by_part"].get(p.id, [])
            if b.status not in _TERMINAL_BATCH_STATUSES
        ]
        return McpDueRow(
            row_type="PART",
            id=p.id,
            serial_no=p.serial_no,
            drawing_no=p.drawing_no,
            name=p.name,
            quantity=p.quantity,
            status=p.status,
            system_delivery_date=p.system_delivery_date,
            planned_delivery_date=p.planned_delivery_date,
            order_no=p.order_no,
            customer_path=ctx["customer_path"].get(p.id),
            applicant_name=p.applicant_name,
            is_urgent=bool(p.is_urgent),
            location_summary=self._holder_display(p.location, p.current_holder_id, ctx),
            batches=batches,
            drawing=ctx["drawing"].get(p.id),
        )

    async def _group_by_assembly(
        self, rows: list[TPart], part_rows: list[McpDueRow]
    ) -> list[McpDueRow]:
        """把带 `assembly_id` 的子件收进装配体聚合行，保持零件的原始顺序。

        装配体表没有 `system_delivery_date`，所以装配体不直接参与日期筛选——
        是子件命中后反过来把父装配体带出来，因此 `children` 只含命中的子件。
        """
        by_part_id = {p.id: row for p, row in zip(rows, part_rows)}
        assembly_ids = sorted({int(p.assembly_id) for p in rows if p.assembly_id})
        if not assembly_ids:
            return part_rows

        assemblies = {
            a.id: a for a in await self.assemblies.list_by_ids(assembly_ids)
        }

        items: list[McpDueRow] = []
        emitted_assemblies: dict[int, McpDueRow] = {}
        for p in rows:
            child_row = by_part_id[p.id]
            aid = int(p.assembly_id) if p.assembly_id else None
            # 装配体已被软删 → 子件退回顶层独立行，别把它藏进一个查不到的父级里。
            if aid is None or aid not in assemblies:
                items.append(child_row)
                continue
            group = emitted_assemblies.get(aid)
            if group is None:
                asm = assemblies[aid]
                group = McpDueRow(
                    row_type="ASSEMBLY",
                    id=asm.id,
                    serial_no=asm.serial_no,
                    drawing_no=asm.drawing_no,
                    name=asm.name,
                )
                emitted_assemblies[aid] = group
                items.append(group)
            group.children.append(child_row)
        return items
