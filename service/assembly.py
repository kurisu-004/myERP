"""装配体 service。

主流程（`create_assembly`）：
1. 校验客户为叶子节点；
2. 校验 PDF 字节流合法；
3. 写 `t_assembly` 行（PENDING）；
4. 上传 PDF 到 COS；
5. 写 `t_drawing_file` 装配件挂的总图（page_index=NULL）；
6. 对每个 child：分配序列号 → 写 `t_part`（assembly_id=装配行 id）→
   写 `t_part_event(CREATED)` → 写 `t_drawing_file`（part_id=子件 id，
   page_index=child.page_index 引用同一 PDF）；
7. 任一失败 → DB 整体回滚；之前已成功上传 COS 的对象由
   `drawing.delete_files_silently` 在异常分支清理。

依赖注入：`deps.get_assembly_service` 会同时构造 `PartService` 与
`DrawingService`，再传入本类，确保所有 service 共享同一 session / 事务。
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from fastapi import status as http_status

from core import cos as cos_mod
from core.error_code import ErrCode
from core.exception import BizError
from model import TAssembly, TDrawingFile, TPart, TPartEvent
from model.enums import PartEventType
from repository import (
    ApplicantRepository,
    AssemblyRepository,
    CustomerRepository,
    DrawingFileRepository,
    PartEventRepository,
    PartRepository,
    SerialCounterRepository,
)
from schema.assembly import (
    AssemblyCreateRequest,
    AssemblyCreateResult,
    AssemblyDetail,
    AssemblyListOut,
    AssemblyListQuery,
    AssemblyOut,
)
from service.drawing import DrawingService, _guess_content_type, _normalize_ext
from service.part import PartService
from utils.id_gen import new_id

_logger = logging.getLogger(__name__)


# 事件广播回调签名（与 PartService.event_broadcaster 一致）。
Broadcaster = Callable[[], Awaitable[None]]
EventBroadcaster = Callable[[str, dict], Awaitable[None]]


def _parse_status(value: str | None) -> str | None:
    if value is None:
        return None
    v = value.strip().upper()
    if v not in {"PENDING", "IN_PROCESS", "COMPLETED", "CANCELLED"}:
        raise BizError(
            code=ErrCode.BIZ_INVALID_VALUE,
            message=f"invalid assembly status: {value!r}",
            http_status=http_status.HTTP_400_BAD_REQUEST,
        )
    return v


class AssemblyService:
    """装配体的创建 / 查询 / 删除。"""

    def __init__(
        self,
        assemblies: AssemblyRepository,
        parts: PartRepository,
        files: DrawingFileRepository,
        customers: CustomerRepository,
        serial_counters: SerialCounterRepository,
        events: PartEventRepository,
        part_service: PartService,
        drawings: DrawingService,
        applicants: ApplicantRepository | None = None,
        event_broadcaster: EventBroadcaster | None = None,
        broadcaster: Broadcaster | None = None,
    ) -> None:
        self.assemblies = assemblies
        self.parts = parts
        self.files = files
        self.customers = customers
        self.serial_counters = serial_counters
        self.events = events
        self.part_service = part_service
        self.drawings = drawings
        self.applicants = applicants
        self.event_broadcaster = event_broadcaster
        self.broadcaster = broadcaster

    # ============================================================
    # 写操作：create
    # ============================================================
    async def create_assembly(
        self, data: AssemblyCreateRequest, *, pdf_bytes: bytes, pdf_filename: str
    ) -> AssemblyCreateResult:
        """创建装配件 + 子零件 + 上传 PDF。

        - `pdf_bytes` / `pdf_filename` 由 API 层从 `UploadFile` 读出。
        - DB 操作全部在调用方所在 session/事务里；任一失败 → 整体回滚。
        - 已成功上传 COS 的孤儿文件由 `drawings.delete_files_silently`
          在异常分支清理。
        """
        if not pdf_bytes:
            raise BizError(
                code=ErrCode.BIZ_DRAWING_FILE_TOO_LARGE,
                message="empty PDF",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        if _normalize_ext(pdf_filename) != "pdf":
            raise BizError(
                code=ErrCode.BIZ_DRAWING_FILE_BAD_TYPE,
                message=(
                    f"装配件主文件必须是 PDF，当前为 "
                    f".{_normalize_ext(pdf_filename) or '(无)'}"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )

        # 校验客户是叶子节点
        cust = await self.customers.get_by_id(data.customer_id)
        if cust is None:
            raise BizError(
                code=ErrCode.BIZ_ASSEMBLY_BAD_CUSTOMER,
                message=f"customer {data.customer_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )

        # 1. 写 t_assembly（先拿到 id，后面所有 COS key / 外键都依赖它）
        # 解析 applicant_id → applicant_name（顶层装配体的申请人）
        # 注：applicant_id 在 schema 是 str（雪花 ID 字符串），转回 int 再查。
        resolved_applicant_name = data.applicant_name
        if data.applicant_id is not None:
            if self.applicants is None:
                raise BizError(
                    code=ErrCode.BIZ_INVALID_VALUE,
                    message="server missing applicant repository",
                    http_status=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
                )
            # 一级客户需要 customer_id 是根才能拿到（叶子装配体的叶子客户）
            # 通过装配体的 customer_id 解析到 root
            if cust.parent_id is None:
                root_customer_id = cust.id
            else:
                # cust.parent_id 必定存在（二级）；上面已校验叶子节点存在
                root_customer_id = cust.parent_id
            try:
                applicant_id_int = int(data.applicant_id)
            except (TypeError, ValueError) as e:
                raise BizError(
                    code=ErrCode.BIZ_APPLICANT_BAD_CUSTOMER,
                    message=f"applicant_id 必须是数字字符串：{data.applicant_id!r}",
                    http_status=http_status.HTTP_400_BAD_REQUEST,
                ) from e
            applicant = await self.applicants.get_by_id(applicant_id_int)
            if applicant is None:
                raise BizError(
                    code=ErrCode.BIZ_APPLICANT_NOT_FOUND,
                    message=f"applicant {data.applicant_id} not found",
                    http_status=http_status.HTTP_404_NOT_FOUND,
                )
            if applicant.customer_id != root_customer_id:
                raise BizError(
                    code=ErrCode.BIZ_APPLICANT_BAD_CUSTOMER,
                    message=(
                        f"applicant {data.applicant_id} 不属于本装配体的一级客户 "
                        f"{root_customer_id}"
                    ),
                    http_status=http_status.HTTP_400_BAD_REQUEST,
                )
            resolved_applicant_name = applicant.name

        assembly = TAssembly(
            id=new_id(),
            drawing_no=data.drawing_no,
            name=data.name,
            applicant_name=resolved_applicant_name,
            customer_id=data.customer_id,
            request_date=data.request_date,
            planned_delivery_date=data.planned_delivery_date,
            actual_delivery_date=None,
            is_urgent=data.is_urgent,
            status="PENDING",
        )
        await self.assemblies.create(assembly)

        # 2. 上传装配件主 PDF 到 COS + 写 t_drawing_file（装配件挂总图）
        master_pdf_id = new_id()
        master_key = f"drawings/assembly/{assembly.id}/{master_pdf_id}.pdf"
        cos_keys_to_cleanup: list[str] = []
        try:
            await cos_mod.upload_object(
                master_key,
                pdf_bytes,
                _guess_content_type(pdf_filename, "application/pdf"),
            )
            cos_keys_to_cleanup.append(master_key)
        except BizError:
            raise

        master_file = TDrawingFile(
            id=master_pdf_id,
            assembly_id=assembly.id,
            part_id=None,
            file_type="PDF",
            object_key=master_key,
            original_filename=pdf_filename,
            file_size=len(pdf_bytes),
            content_type=_guess_content_type(
                pdf_filename, "application/pdf"
            ),
            page_index=None,
            upload_status="READY",
        )
        await self.files.create(master_file)

        # 3. 写每个子零件：t_part + t_drawing_file (page_index) + t_part_event
        child_parts: list[TPart] = []
        child_files: list[TDrawingFile] = []
        try:
            parent = await self.customers.get_by_id(cust.parent_id)
            if parent is None:
                raise BizError(
                    code=ErrCode.BIZ_ASSEMBLY_BAD_CUSTOMER,
                    message=f"parent customer {cust.parent_id} not found",
                    http_status=http_status.HTTP_404_NOT_FOUND,
                )
            from core.serial import code_for_parent

            code = code_for_parent(parent.name)
            if code is None:
                raise BizError(
                    code=ErrCode.BIZ_ASSEMBLY_BAD_CUSTOMER,
                    message=f"未配置一级客户「{parent.name}」的序列号代码",
                    http_status=http_status.HTTP_400_BAD_REQUEST,
                )

            for child in data.children:
                child_id = new_id()
                child_total = (
                    child.total_price
                    if child.total_price is not None
                    else child.unit_price * child.quantity
                )
                serial_no = await self.serial_counters.acquire_serial(code)
                tpart = TPart(
                    id=child_id,
                    serial_no=serial_no,
                    name=child.name,
                    drawing_no=child.drawing_no,
                    applicant_name=(
                        child.applicant_name
                        or resolved_applicant_name
                        or "(未知)"
                    ),
                    quantity=child.quantity,
                    unit_price=child.unit_price,
                    total_price=child_total,
                    request_date=data.request_date,
                    planned_delivery_date=data.planned_delivery_date,
                    actual_delivery_date=None,
                    status="PENDING",
                    is_urgent=data.is_urgent,
                    customer_id=data.customer_id,
                    assembly_id=assembly.id,
                )
                await self.parts.create(tpart)
                child_parts.append(tpart)

                # page_index 引用同一份 PDF 的对应页
                ref_file = TDrawingFile(
                    id=new_id(),
                    part_id=tpart.id,
                    assembly_id=None,
                    file_type="PDF",
                    object_key=master_key,
                    original_filename=pdf_filename,
                    file_size=len(pdf_bytes),
                    content_type=_guess_content_type(
                        pdf_filename, "application/pdf"
                    ),
                    page_index=child.page_index,
                    upload_status="READY",
                )
                await self.files.create(ref_file)
                child_files.append(ref_file)

                # 写 CREATED 事件
                part_event = TPartEvent(
                    id=new_id(),
                    part_id=tpart.id,
                    worker_id=None,
                    event_type=PartEventType.CREATED.value,
                    from_status=None,
                    to_status="PENDING",
                    drawing_code=None,
                    badge_code=None,
                    note=None,
                )
                await self.events.create(part_event)
        except BizError:
            await self.drawings.delete_files_silently(cos_keys_to_cleanup)
            raise

        # 4. 拼装返回
        assembly_out = await self._assembly_to_out(
            assembly, child_count=len(child_parts)
        )
        parts_out = await self.part_service._to_out(child_parts)  # noqa: SLF001
        files_out: list = []
        for f in [master_file, *child_files]:
            files_out.append(await self.drawings._to_out(f))  # noqa: SLF001

        # 5. 广播事件
        await self._broadcast_event(
            "ASSEMBLY_CREATED",
            {
                "assembly_id": assembly.id,
                "drawing_no": assembly.drawing_no,
                "name": assembly.name,
                "child_count": len(child_parts),
            },
        )

        return AssemblyCreateResult(
            assembly=assembly_out,
            children=parts_out,
            files=files_out,
        )

    # ============================================================
    # 查询
    # ============================================================
    async def list_assemblies(self, q: AssemblyListQuery) -> AssemblyListOut:
        rows = await self.assemblies.list_with_filters(
            customer_id=q.customer_id,
            status=_parse_status(q.status),
            is_urgent=q.is_urgent,
            drawing_no_like=q.drawing_no_like,
            name_like=q.name_like,
            limit=q.limit,
            offset=q.offset,
        )
        total = await self.assemblies.count_with_filters(
            customer_id=q.customer_id,
            status=_parse_status(q.status),
            is_urgent=q.is_urgent,
            drawing_no_like=q.drawing_no_like,
            name_like=q.name_like,
        )
        child_counts = await self._count_children_for(rows)
        cust_map = await self._load_cust_map([a.customer_id for a in rows])

        items: list[AssemblyOut] = []
        for a in rows:
            items.append(
                self._assembly_to_out_obj(a, child_counts.get(a.id, 0), cust_map)
            )
        return AssemblyListOut(
            items=items, total=total, limit=q.limit, offset=q.offset
        )

    async def get_assembly_detail(self, assembly_id: int) -> AssemblyDetail:
        asm = await self.assemblies.get_by_id(assembly_id)
        if asm is None:
            raise BizError(
                code=ErrCode.BIZ_ASSEMBLY_NOT_FOUND,
                message=f"assembly {assembly_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        return await self._build_detail(asm)

    async def get_assembly_for_child(self, child_part_id: int) -> AssemblyDetail:
        """从任意子零件反查所属装配件 + 全部兄弟 + 文件。"""
        part = await self.parts.get_by_id(child_part_id)
        if part is None:
            raise BizError(
                code=ErrCode.BIZ_PART_NOT_FOUND,
                message=f"part {child_part_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        if part.assembly_id is None:
            raise BizError(
                code=ErrCode.BIZ_ASSEMBLY_NOT_FOUND,
                message=(
                    f"part {child_part_id} 不属于任何装配件（assembly_id IS NULL）"
                ),
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        asm = await self.assemblies.get_by_id(part.assembly_id)
        if asm is None:
            raise BizError(
                code=ErrCode.BIZ_ASSEMBLY_NOT_FOUND,
                message=(
                    f"part {child_part_id} 的 assembly {part.assembly_id} "
                    "不存在或已软删"
                ),
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        return await self._build_detail(asm)

    # ============================================================
    # 取消（级联）
    # ============================================================
    async def cancel_assembly(self, assembly_id: int) -> AssemblyDetail:
        """取消装配体，级联取消所有非终态子件。

        - Assembly: PENDING/IN_PROCESS → CANCELLED
        - 每个非终态子 Part → CANCELLED
        - 终态（COMPLETED, CANCELLED）的子件不处理
        """
        asm = await self.assemblies.get_by_id(assembly_id)
        if asm is None:
            raise BizError(
                code=ErrCode.BIZ_ASSEMBLY_NOT_FOUND,
                message=f"assembly {assembly_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )

        # 级联取消所有非终态子件
        children = await self.parts.list_children(assembly_id)
        for child in children:
            if child.status not in ("COMPLETED", "CANCELLED"):
                child.sm.cancel(event_repo=self.events)
                await self.parts.session.flush()

        # 取消装配体自身
        asm.sm.cancel()
        await self.assemblies.session.flush()

        # dashboard 卡片立刻消失 + 通知横幅（不走 PartService.cancel,
        # 所以必须自己推）
        await self._broadcast()
        await self._broadcast_event(
            "ASSEMBLY_CANCELLED",
            {
                "assembly_id": asm.id,
                "drawing_no": asm.drawing_no,
                "name": asm.name,
            },
        )

        return await self._build_detail(asm)

    # ============================================================
    # 软删（级联）
    # ============================================================
    async def soft_delete_assembly(self, assembly_id: int) -> None:
        asm = await self.assemblies.get_by_id(assembly_id)
        if asm is None:
            raise BizError(
                code=ErrCode.BIZ_ASSEMBLY_NOT_FOUND,
                message=f"assembly {assembly_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        children = await self.parts.list_children(assembly_id)
        child_ids = [c.id for c in children]
        files_for_children = (
            await self.files.list_for_part_ids(child_ids) if child_ids else []
        )
        files_for_asm = await self.files.list_by_assembly(assembly_id)
        all_files = {f.id: f for f in files_for_children + files_for_asm}
        keys_to_cleanup = [f.object_key for f in all_files.values()]

        if all_files:
            await self.files.soft_delete_many(list(all_files.values()))
        for child in children:
            await self.parts.soft_delete(child)
        await self.assemblies.soft_delete(asm)

        await self.drawings.delete_files_silently(keys_to_cleanup)

        # dashboard 卡片 + asm 立刻消失 + 通知横幅
        await self._broadcast()
        await self._broadcast_event(
            "ASSEMBLY_DELETED",
            {
                "assembly_id": asm.id,
                "drawing_no": asm.drawing_no,
                "name": asm.name,
            },
        )

    # ============================================================
    # 内部
    # ============================================================
    async def _build_detail(self, asm: TAssembly) -> AssemblyDetail:
        children = await self.parts.list_children(asm.id)
        files_out = await self.drawings.list_for_assembly(asm.id)
        asm_out = await self._assembly_to_out(asm, child_count=len(children))
        parts_out = await self.part_service._to_out(children)  # noqa: SLF001
        return AssemblyDetail(
            assembly=asm_out, children=parts_out, files=files_out
        )

    async def _assembly_to_out(
        self, asm: TAssembly, *, child_count: int
    ) -> AssemblyOut:
        cust_map = await self._load_cust_map([asm.customer_id])
        return self._assembly_to_out_obj(asm, child_count, cust_map)

    @staticmethod
    def _assembly_to_out_obj(
        asm: TAssembly,
        child_count: int,
        cust_map: dict,
    ) -> AssemblyOut:
        cust = cust_map.get(asm.customer_id)
        parent = cust_map.get(cust.parent_id) if cust and cust.parent_id else None
        customer_name = cust.name if cust else None
        parent_name = parent.name if parent else None
        path: str | None = None
        if parent_name and customer_name:
            path = f"{parent_name} / {customer_name}"
        elif customer_name:
            path = customer_name
        elif parent_name:
            path = parent_name
        return AssemblyOut(
            id=asm.id,
            drawing_no=asm.drawing_no,
            name=asm.name,
            applicant_name=asm.applicant_name,
            customer_id=asm.customer_id,
            customer_name=customer_name,
            parent_customer_name=parent_name,
            customer_path=path,
            request_date=asm.request_date,
            planned_delivery_date=asm.planned_delivery_date,
            actual_delivery_date=asm.actual_delivery_date,
            is_urgent=asm.is_urgent,
            status=asm.status,
            child_count=child_count,
            created_at=asm.created_at,
            updated_at=asm.updated_at,
        )

    async def _load_cust_map(self, cust_ids: list[int]) -> dict:
        if not cust_ids:
            return {}
        rows = await self.customers.list_by_ids(list(set(cust_ids)))
        m = {c.id: c for c in rows}
        parent_ids = list({c.parent_id for c in rows if c.parent_id})
        if parent_ids:
            parents = await self.customers.list_by_ids(parent_ids)
            for p in parents:
                m[p.id] = p
        return m

    async def _count_children_for(
        self, assemblies: list[TAssembly]
    ) -> dict[int, int]:
        """批量算每个装配件的子件数量。"""
        from sqlalchemy import func, select

        from model import TPart

        if not assemblies:
            return {}
        ids = [a.id for a in assemblies]
        stmt = (
            select(TPart.assembly_id, func.count(TPart.id))
            .where(
                TPart.assembly_id.in_(ids),
                TPart.deleted_at.is_(None),
            )
            .group_by(TPart.assembly_id)
        )
        result = await self.parts.session.execute(stmt)
        return {row[0]: int(row[1]) for row in result.all()}

    async def _broadcast_event(self, event_type: str, payload: dict) -> None:
        if self.event_broadcaster is None:
            return
        try:
            await self.event_broadcaster(event_type, payload)
        except Exception:  # noqa: BLE001
            _logger.exception("assembly event broadcast failed: %s", event_type)

    async def _broadcast(self) -> None:
        """触发整张 dashboard snapshot 重推（与 PartService._broadcast 一致）。

        装配体的 cancel / soft_delete 走级联路径，不经过 PartService 状态机
        入口，所以它们自己负责推送：子件从 snapshot 移除需要这次刷新。
        """
        if self.broadcaster is None:
            return
        try:
            await self.broadcaster()
        except Exception:  # noqa: BLE001
            _logger.exception("assembly snapshot broadcast failed")
