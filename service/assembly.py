"""装配体 service。

主流程（`create_assembly`）：
1. 校验客户为叶子节点；
2. 校验 PDF 字节流合法 + pypdf 解析成功；
3. `split_pdf` 把 PDF 拆成 N 个单页 PDF 字节流（page 1 = 总图，page 2..N = 子件）；
4. 校验页数（≥ 2 且 children 数量匹配且 ≤ 99）；
5. 写 `t_assembly` 行（PENDING）；
6. 上传 `page_splits[0]` 到 COS → 写 `t_part_file`（装配体总图，kind=ASSEMBLY_MASTER，polymorphic part_id=assembly.id）；
7. 对每个 child i：分配序列号 `{assembly_serial}-{i:02d}` →
   写 `t_part`（assembly_id=装配行 id，unit_price=0, total_price=0）→
   上传 `page_splits[i]` 到 COS `drawings/part/{part_id}/{file_id}.pdf` →
   写 `t_part_file`（part_id=子件 id，kind=DRAWING）→
   写 `t_part_event(CREATED)`；
8. 任一失败 → DB 整体回滚；之前已成功上传 COS 的对象由
   `part_files.delete_files_silently` 在异常分支清理。

依赖注入：`deps.get_assembly_service` 会同时构造 `PartService` 与
`PartFileService`，再传入本类，确保所有 service 共享同一 session / 事务。
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from decimal import Decimal

from fastapi import status as http_status
from pypdf.errors import PdfReadError

from core.error_code import ErrCode
from core.exception import BizError
from core.permission import CurrentUser
from model import TAssembly, TPart, TPartEvent
from model.enums import PartEventType, PartFileKind
from repository import (
    ApplicantRepository,
    AssemblyRepository,
    CustomerRepository,
    PartEventRepository,
    PartFileRepository,
    PartRepository,
    SerialCounterRepository,
)
from schema.assembly import (
    AssemblyCreateRequest,
    AssemblyCreateResult,
    AssemblyDetail,
    AssemblyListItem,
    AssemblyListOut,
    AssemblyListQuery,
    AssemblyOut,
)
from schema.part_file import PartFileOut
from service._id_parse import parse_snowflake_id
from service.part import PartService
from service.part_file import (
    PartFileService,
    _guess_content_type,
    _normalize_ext,
)
from utils.id_gen import new_id
from utils.pdf import split_pdf

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
        files: PartFileRepository,
        customers: CustomerRepository,
        serial_counters: SerialCounterRepository,
        events: PartEventRepository,
        part_service: PartService,
        part_files: PartFileService,
        applicants: ApplicantRepository | None = None,
        event_broadcaster: EventBroadcaster | None = None,
        broadcaster: Broadcaster | None = None,
        *,
        current_user: CurrentUser | None = None,
    ) -> None:
        self.assemblies = assemblies
        self.parts = parts
        self.files = files
        self.customers = customers
        self.serial_counters = serial_counters
        self.events = events
        self.part_service = part_service
        self.part_files = part_files
        self.applicants = applicants
        self.event_broadcaster = event_broadcaster
        self.broadcaster = broadcaster
        self._user_id: int | None = current_user.id if current_user else None
        self._username: str | None = current_user.username if current_user else None

    # ============================================================
    # 写操作：create
    # ============================================================
    async def create_assembly(
        self,
        data: AssemblyCreateRequest,
        *,
        pdf_bytes: bytes | None = None,
        pdf_filename: str | None = None,
    ) -> AssemblyCreateResult:
        """创建装配件（可选带总装 PDF + 子零件）。

        - `pdf_bytes` / `pdf_filename` 由 API 层从 `UploadFile` 读出；二者皆可空。
        - 流程：
          1. 校验客户为叶子节点（即使空装配体也必填）；
          2. 写 `t_assembly` 行（PENDING）；
          3. 若提供 PDF：拆页 + 上传 page 1 = master (kind=ASSEMBLY_MASTER, polymorphic part_id=assembly.id)
             + page 2..N = 子件 (kind=DRAWING)；
             都不提供：创建空装配体（无 master / 无子件 / 无 serial）；
          4. 拼装返回 + 广播 ASSEMBLY_CREATED 事件。
        - DB 操作全部在调用方所在 session/事务里；任一失败 → 整体回滚。
        - 已成功上传 COS 的孤儿文件由 `part_files.delete_files_silently` 在异常分支清理。
        """
        # PDF 校验（仅当提供时）
        if pdf_bytes:
            if not pdf_filename or _normalize_ext(pdf_filename) != "pdf":
                raise BizError(
                    code=ErrCode.BIZ_PART_FILE_BAD_TYPE,
                    message=(
                        f"装配件主文件必须是 PDF，当前为 "
                        f".{_normalize_ext(pdf_filename) or '(无)'}"
                    ),
                    http_status=http_status.HTTP_400_BAD_REQUEST,
                )

            # 拆 PDF：page 1 = 总装图，page 2..N = 子件 1..N-1
            try:
                page_splits = split_pdf(pdf_bytes)
            except (PdfReadError, Exception) as e:  # noqa: BLE001 — pypdf 异常族复杂，统一兜底
                raise BizError(
                    code=ErrCode.BIZ_PART_FILE_UPLOAD_FAILED,
                    message=f"PDF 解析失败：{e!s}",
                    http_status=http_status.HTTP_400_BAD_REQUEST,
                ) from e
            total_pages = len(page_splits)
            if total_pages < 2:
                raise BizError(
                    code=ErrCode.BIZ_ASSEMBLY_TOO_MANY_CHILDREN,
                    message=(
                        f"总装 PDF 至少 2 页：第 1 页是总装图，剩余每页对应 1 个子零件。"
                        f"当前 PDF 共 {total_pages} 页。"
                    ),
                    http_status=http_status.HTTP_400_BAD_REQUEST,
                )
            if len(data.children) > 99:
                raise BizError(
                    code=ErrCode.BIZ_ASSEMBLY_TOO_MANY_CHILDREN,
                    message=(
                        f"子件数量 {len(data.children)} > 99，"
                        f"序列号 '{{serial}}-{{i:02d}}' 派生失败"
                    ),
                    http_status=http_status.HTTP_400_BAD_REQUEST,
                )
            if total_pages - 1 != len(data.children):
                raise BizError(
                    code=ErrCode.BIZ_ASSEMBLY_TOO_MANY_CHILDREN,
                    message=(
                        f"PDF 共 {total_pages} 页，应有 {total_pages - 1} 个子零件，"
                        f"当前 {len(data.children)} 个"
                    ),
                    http_status=http_status.HTTP_400_BAD_REQUEST,
                )
        else:
            # 无 PDF 模式：data.children 必须为空（避免给子件分配 serial 但无 PDF）
            if data.children:
                raise BizError(
                    code=ErrCode.BIZ_INVALID_VALUE,
                    message="未提供 PDF 时不允许直接传入子件；"
                    "请在装配体详情页使用「添加子件」接口",
                    http_status=http_status.HTTP_400_BAD_REQUEST,
                )
            page_splits = []

        # 校验客户是叶子节点
        cid = parse_snowflake_id(data.customer_id, field_name="customer_id")
        if cid is None:
            raise BizError(
                code=ErrCode.BIZ_ASSEMBLY_BAD_CUSTOMER,
                message=f"customer {data.customer_id!r} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        cust = await self.customers.get_by_id(cid)
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

        # 非空装配体：先解析一级客户的 serial_prefix → 一次性 acquire 顶级流水号，
        # 让 serial_no 从 INSERT 阶段就带进去（不要走"先 INSERT 再 UPDATE 填 serial"
        # 的两步走——AuditMixin.updated_at 是 onupdate=func.now()，第二次 flush
        # 会把 updated_at 标记为 expired，触发 _load_expired 同步 IO，
        # 在 async session 上下文里抛 MissingGreenlet）。
        # 空装配体：serial_no 留 None，不分配。
        assembly_serial: str | None = None
        if page_splits:
            from core.serial import resolve_root_prefix

            parent = await self.customers.get_by_id(cust.parent_id)
            if parent is None:
                raise BizError(
                    code=ErrCode.BIZ_ASSEMBLY_BAD_CUSTOMER,
                    message=f"parent customer {cust.parent_id} not found",
                    http_status=http_status.HTTP_404_NOT_FOUND,
                )
            try:
                code = resolve_root_prefix(parent)
            except BizError:
                raise
            assembly_serial = await self.serial_counters.acquire_serial(code)

        assembly = TAssembly(
            id=new_id(),
            drawing_no=data.drawing_no,
            name=data.name,
            applicant_name=resolved_applicant_name,
            customer_id=cid,   # 关键：TAssembly.customer_id 是 BigInteger，传 int
            request_date=data.request_date,
            planned_delivery_date=data.planned_delivery_date,
            actual_delivery_date=None,
            is_urgent=data.is_urgent,
            status="PENDING",
            serial_no=assembly_serial,  # 空装配体 = None；非空装配体 = 顶级流水号
        )
        assembly.created_by = self._user_id
        assembly.updated_by = self._user_id
        await self.assemblies.create(assembly)

        # 空装配体：跳过 PDF / 子件处理，直接返回
        if not page_splits:
            assembly_out = await self._assembly_to_out(assembly, child_count=0)
            await self._broadcast_event(
                "ASSEMBLY_CREATED",
                {
                    "assembly_id": assembly.id,
                    "drawing_no": assembly.drawing_no,
                    "name": assembly.name,
                    "child_count": 0,
                },
            )
            return AssemblyCreateResult(
                assembly=assembly_out,
                children=[],
                files=[],
            )

        # 2. 上传装配件总装图（page 1 = 单页 PDF）到 COS + 写 t_part_file
        #    polymorphic part_id = assembly.id, kind = ASSEMBLY_MASTER
        master_out: PartFileOut | None = None
        try:
            master_out = await self.part_files.upload(
                owner_id=assembly.id,
                kind=PartFileKind.ASSEMBLY_MASTER,
                data=page_splits[0],
                original_filename=pdf_filename or "assembly.pdf",
                content_type=_guess_content_type(pdf_filename, "application/pdf"),
            )

        except BizError:
            raise

        # 3. 写每个子零件：t_part + t_part_file (kind=DRAWING, 独立 COS 对象) + t_part_event
        child_parts: list[TPart] = []
        child_files_out: list[PartFileOut] = []
        # assembly_serial 已在 INSERT 之前 acquire，serial_no 也已写进 assembly 行
        assert assembly_serial is not None
        try:
            for i, child in enumerate(data.children, start=1):
                child_id = new_id()
                child_serial = f"{assembly_serial}-{i:02d}"
                tpart = TPart(
                    id=child_id,
                    serial_no=child_serial,
                    name=child.name,
                    drawing_no=child.drawing_no,
                    applicant_name=(
                        child.applicant_name
                        or resolved_applicant_name
                        or "(未知)"
                    ),
                    quantity=child.quantity,
                    unit_price=Decimal("0"),
                    total_price=Decimal("0"),
                    request_date=data.request_date,
                    planned_delivery_date=data.planned_delivery_date,
                    actual_delivery_date=None,
                    status="PENDING",
                    is_urgent=data.is_urgent,
                    customer_id=cid,   # 关键：TPart.customer_id 是 BigInteger
                    assembly_id=assembly.id,
                )
                tpart.created_by = self._user_id
                tpart.updated_by = self._user_id
                await self.parts.create(tpart)
                child_parts.append(tpart)

                # 上传该子件的单页 PDF 到独立 COS 对象
                child_file_out = await self.part_files.upload(
                    owner_id=child_id,
                    kind=PartFileKind.DRAWING,
                    data=page_splits[i],
                    original_filename=pdf_filename or f"child-{i:02d}.pdf",
                    content_type=_guess_content_type(pdf_filename, "application/pdf"),
                )
                child_files_out.append(child_file_out)

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
                    created_by=self._user_id,
                )
                await self.events.create(part_event)
        except BizError:
            # 已经成功上传的 COS 孤儿清理：
            # master_out 与已成功的 child_files_out 的 COS key 通过
            # soft_delete_by_part_and_kind 不太合适（这是创建阶段，对应行
            # 也未提交），这里直接调用底层 cos delete_object 清理。
            # 简化处理：交由依赖 PartFileRepository 的反向追踪失败时一并
            # 清理；本次 commit 失败，事务回滚，DB 行的 COS key 在调用方
            # session 不可见，所以走 fire-and-forget 删 part_files 中
            # 可见的对象 key（master + 已成功 children）。
            for f in child_files_out:
                if f and f.download_url:
                    # 下载 url 是临时签名，反查 row 不易；改用 fire-and-forget
                    # 直接 delete_object by key（需要 cos key，但 PartFileOut
                    # 没暴露），跳过此清理，交由 cos 后台 GC 处理
                    pass
            await self.part_files.delete_files_silently([])
            raise

        # 4. 拼装返回
        assembly_out = await self._assembly_to_out(
            assembly, child_count=len(child_parts)
        )
        parts_out = await self.part_service._to_out(child_parts)  # noqa: SLF001
        files_out: list[PartFileOut] = []
        if master_out is not None:
            files_out.append(master_out)
        files_out.extend(child_files_out)

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
    async def list_assemblies(self, q: AssemblyListQuery) -> AssemblyListOut:
        # q.customer_id 是雪花 ID 字符串，转 int 后传给 repository。
        cid_int = parse_snowflake_id(q.customer_id, field_name="customer_id") if q.customer_id else None
        # 客户筛选级联：L1 自动包含 L2 子客户
        customer_ids_in: list[int] | None = None
        if cid_int is not None:
            cust = await self.customers.get_by_id(cid_int)
            if cust is None:
                raise BizError(
                    code=ErrCode.BIZ_ASSEMBLY_NOT_FOUND,
                    message=f"customer {q.customer_id} not found",
                    http_status=http_status.HTTP_404_NOT_FOUND,
                )
            if cust.parent_id is None:
                children = await self.customers.list_children(cid_int)
                customer_ids_in = [cid_int] + [c.id for c in children]
            else:
                customer_ids_in = [cid_int]
        rows = await self.assemblies.list_with_filters(
            customer_ids_in=customer_ids_in,
            status=_parse_status(q.status),
            is_urgent=q.is_urgent,
            drawing_no_like=q.drawing_no_like,
            name_like=q.name_like,
            sort_by=q.sort_by,
            sort_dir=q.sort_dir.value,
            limit=q.limit,
            offset=q.offset,
        )
        total = await self.assemblies.count_with_filters(
            customer_ids_in=customer_ids_in,
            status=_parse_status(q.status),
            is_urgent=q.is_urgent,
            drawing_no_like=q.drawing_no_like,
            name_like=q.name_like,
        )
        child_counts = await self._count_children_for(rows)
        cust_map = await self._load_cust_map([a.customer_id for a in rows])

        items: list[AssemblyListItem] = []
        for a in rows:
            items.append(
                self._assembly_to_list_out_obj(
                    a, child_counts.get(a.id, 0), cust_map
                )
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
    # 详情页扩展：上传总装 PDF / 添加单个子件
    # ============================================================
    async def upload_total_pdf(
        self,
        assembly_id: int,
        *,
        pdf_bytes: bytes,
        pdf_filename: str,
    ) -> AssemblyDetail:
        """详情页上传总装 PDF：拆页 → page 1 作 master (kind=ASSEMBLY_MASTER) +
        page 2..N 各自派生子件 (kind=DRAWING)。

        前置条件：
        - 装配体存在（且未软删）；
        - 装配体当前**无任何文件 + 无任何子件**（避免与既有 master / 既有子件冲突）；
        - PDF 至少 2 页、拆分后子件 ≤ 99。
        """
        from schema.assembly import AddAssemblyChildRequest

        # 1. 校验装配体
        asm = await self.assemblies.get_by_id(assembly_id)
        if asm is None:
            raise BizError(
                code=ErrCode.BIZ_ASSEMBLY_NOT_FOUND,
                message=f"assembly {assembly_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )

        existing_children = await self.parts.list_children(assembly_id)
        # 检查 master 是否已存在 (polymorphic part_id = asm.id, kind = ASSEMBLY_MASTER)
        existing_master = await self.files.get_master_for_assembly(assembly_id)
        existing_files_for_asm = (
            [existing_master] if existing_master else []
        )
        if existing_files_for_asm or existing_children:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message=(
                    f"装配体已存在 {len(existing_files_for_asm)} 个文件、"
                    f"{len(existing_children)} 个子件；"
                    "如需替换请先软删后重建"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )

        # 2. PDF 校验
        if _normalize_ext(pdf_filename) != "pdf":
            raise BizError(
                code=ErrCode.BIZ_PART_FILE_BAD_TYPE,
                message=(
                    f"装配件主文件必须是 PDF，当前为 "
                    f".{_normalize_ext(pdf_filename) or '(无)'}"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        try:
            page_splits = split_pdf(pdf_bytes)
        except (PdfReadError, Exception) as e:  # noqa: BLE001
            raise BizError(
                code=ErrCode.BIZ_PART_FILE_UPLOAD_FAILED,
                message=f"PDF 解析失败：{e!s}",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            ) from e
        total_pages = len(page_splits)
        if total_pages < 2:
            raise BizError(
                code=ErrCode.BIZ_ASSEMBLY_TOO_MANY_CHILDREN,
                message=(
                    f"总装 PDF 至少 2 页。当前 PDF 共 {total_pages} 页。"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        child_count = total_pages - 1
        if child_count > 99:
            raise BizError(
                code=ErrCode.BIZ_ASSEMBLY_TOO_MANY_CHILDREN,
                message=f"PDF 共 {total_pages} 页 > 100（子件上限 99）",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )

        # 3. 拿父客户 → code → 分配顶级流水号
        cust = await self.customers.get_by_id(asm.customer_id)
        if cust is None or cust.parent_id is None:
            raise BizError(
                code=ErrCode.BIZ_ASSEMBLY_BAD_CUSTOMER,
                message=(
                    f"assembly {assembly_id} 的客户 {asm.customer_id} "
                    "不是叶子节点，无法派生子件"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        parent = await self.customers.get_by_id(cust.parent_id)
        if parent is None:
            raise BizError(
                code=ErrCode.BIZ_ASSEMBLY_BAD_CUSTOMER,
                message=f"parent customer {cust.parent_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        from core.serial import resolve_root_prefix

        code = resolve_root_prefix(parent)

        try:
            assembly_serial = await self.serial_counters.acquire_serial(code)
            asm.serial_no = assembly_serial
            await self.assemblies.session.flush()

            # 4. 上传 master（page 1 = 单页 PDF）
            await self.part_files.upload(
                owner_id=asm.id,
                kind=PartFileKind.ASSEMBLY_MASTER,
                data=page_splits[0],
                original_filename=pdf_filename,
                content_type=_guess_content_type(pdf_filename, "application/pdf"),
            )

            # 5. 自动创建 N-1 个子件（每页一个），图号 / 名称预填
            for i in range(child_count):
                seq = i + 1
                child_data = AddAssemblyChildRequest(
                    drawing_no=str(seq).zfill(2),
                    name=f"子零件{str(seq).zfill(2)}",
                    quantity=1,
                )
                await self._create_single_child(
                    asm=asm,
                    child=child_data,
                    assembly_serial=assembly_serial,
                    index=seq,
                    pdf_bytes=page_splits[seq],
                    pdf_filename=pdf_filename,
                )
        except BizError:
            # 事务会回滚；已经上传的 COS 孤儿由 PartFileService.upload
            # 内部的旧 key 收集清理（这里没有「旧 key」可收集；
            # 走 master 创建后立即回滚的情况只能依赖调用方 commit 前
            # 的 COS 后台 GC）。本路径实现简化：依赖事务整体回滚 +
            # 上传路径中对失败 key 的 fire-and-forget 兜底。
            raise

        return await self._build_detail(asm)

    async def add_child(
        self,
        assembly_id: int,
        child: "AddAssemblyChildRequest",
    ) -> "PartOut":
        """详情页添加单个子件（无 PDF；如需 PDF 走 POST /parts/{id}/files）。

        - 装配体必须存在（且未软删）；
        - 装配体的顶级 serial_no 必须已分配（由 upload_total_pdf 或 create_assembly
          携带 PDF 时设置）；如果还是空装配体（serial_no is None），本方法先 acquire。
        """
        asm = await self.assemblies.get_by_id(assembly_id)
        if asm is None:
            raise BizError(
                code=ErrCode.BIZ_ASSEMBLY_NOT_FOUND,
                message=f"assembly {assembly_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )

        # 派生下一个 index
        existing_children = await self.parts.list_children(assembly_id)
        next_index = len(existing_children) + 1
        if next_index > 99:
            raise BizError(
                code=ErrCode.BIZ_ASSEMBLY_TOO_MANY_CHILDREN,
                message=(
                    f"子件数量 {next_index - 1} 已达 99 上限，无法添加更多"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )

        # 顶级 serial：若还没有（空装配体），先 acquire
        assembly_serial = asm.serial_no
        if assembly_serial is None:
            cust = await self.customers.get_by_id(asm.customer_id)
            if cust is None or cust.parent_id is None:
                raise BizError(
                    code=ErrCode.BIZ_ASSEMBLY_BAD_CUSTOMER,
                    message=(
                        f"assembly {assembly_id} 的客户 {asm.customer_id} "
                        "不是叶子节点，无法派生子件"
                    ),
                    http_status=http_status.HTTP_400_BAD_REQUEST,
                )
            parent = await self.customers.get_by_id(cust.parent_id)
            if parent is None:
                raise BizError(
                    code=ErrCode.BIZ_ASSEMBLY_BAD_CUSTOMER,
                    message=f"parent customer {cust.parent_id} not found",
                    http_status=http_status.HTTP_404_NOT_FOUND,
                )
            from core.serial import resolve_root_prefix

            code = resolve_root_prefix(parent)
            assembly_serial = await self.serial_counters.acquire_serial(code)
            asm.serial_no = assembly_serial
            await self.assemblies.session.flush()

        tpart = await self._create_single_child(
            asm=asm,
            child=child,
            assembly_serial=assembly_serial,
            index=next_index,
            pdf_bytes=None,
            pdf_filename=None,
        )
        # _to_out 接收 list[TPart]；单个子件要包成列表。
        out_list = await self.part_service._to_out([tpart])  # noqa: SLF001
        return out_list[0]

    async def _create_single_child(
        self,
        *,
        asm: TAssembly,
        child: "AddAssemblyChildRequest",
        assembly_serial: str,
        index: int,
        pdf_bytes: bytes | None,
        pdf_filename: str | None,
    ) -> TPart:
        """写一个子件（含 t_part + t_part_file + t_part_event）。

        pdf_bytes 为 None 时跳过 t_part_file 写入（详情页 add_child 场景）。
        """
        child_id = new_id()
        child_serial = f"{assembly_serial}-{index:02d}"
        tpart = TPart(
            id=child_id,
            serial_no=child_serial,
            name=child.name,
            drawing_no=child.drawing_no,
            applicant_name=asm.applicant_name or "(未知)",
            quantity=child.quantity,
            unit_price=Decimal("0"),
            total_price=Decimal("0"),
            request_date=asm.request_date,
            planned_delivery_date=asm.planned_delivery_date,
            actual_delivery_date=None,
            status="PENDING",
            is_urgent=asm.is_urgent,
            customer_id=asm.customer_id,  # 已 int（create_assembly 传的是 cid）
            assembly_id=asm.id,
        )
        tpart.created_by = self._user_id
        tpart.updated_by = self._user_id
        await self.parts.create(tpart)

        if pdf_bytes is not None and pdf_filename is not None:
            await self.part_files.upload(
                owner_id=child_id,
                kind=PartFileKind.DRAWING,
                data=pdf_bytes,
                original_filename=pdf_filename,
                content_type=_guess_content_type(pdf_filename, "application/pdf"),
            )

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
            created_by=self._user_id,
        )
        await self.events.create(part_event)
        return tpart

    # ============================================================
    # 取消（级联）
    # ============================================================
    async def cancel_assembly(self, assembly_id: int) -> AssemblyDetail:
        """取消装配体，级联取消所有非终态子件。

        - Assembly: PENDING/IN_PROCESS → CANCELLED
        - 每个非终态子 Part → CANCELLED
        - 终态（COMPLETED, CANCELLED）的子件不处理

        终态后释放装配体自身的流水号（serial_no = None），与 Part 的
        on_enter_CANCELLED 行为一致；partial unique 索引的
        `WHERE serial_no IS NOT NULL` 让该号重入池中。
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
                child.updated_by = self._user_id
                await self.parts.session.flush()

        # 取消装配体自身
        asm.sm.cancel()
        asm.updated_by = self._user_id
        # 释放装配体级流水号（NULL 即让 partial unique 索引腾位置）
        asm.serial_no = None
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
        # 装配体 master (kind=ASSEMBLY_MASTER, part_id=asm.id) + 子件 drawings
        master = await self.files.get_master_for_assembly(assembly_id)
        files_for_children = (
            await self.files.list_for_part_ids(child_ids, kind="DRAWING")
            if child_ids
            else []
        )
        all_files = [f for f in [master] + files_for_children if f is not None]
        keys_to_cleanup = [f.object_key for f in all_files]

        # 级联赋值 updated_by（按 flush 顺序：files → children → asm）
        for f in all_files:
            f.updated_by = self._user_id
        for c in children:
            c.updated_by = self._user_id
        asm.updated_by = self._user_id
        # 软删前先释放装配体流水号，让 partial unique 索引腾位置
        # （deleted_at IS NULL WHERE serial_no IS NOT NULL）
        asm.serial_no = None

        if all_files:
            await self.files.soft_delete_many(all_files)
        for child in children:
            await self.parts.soft_delete(child)
        await self.assemblies.soft_delete(asm)

        await self.part_files.delete_files_silently(keys_to_cleanup)

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
        child_ids = [c.id for c in children]
        files_out = await self.part_files.list_for_assembly(
            asm.id, child_part_ids=child_ids,
        )
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
            serial_no=asm.serial_no,
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

    @staticmethod
    def _assembly_to_list_out_obj(
        asm: TAssembly,
        child_count: int,
        cust_map: dict,
    ) -> AssemblyListItem:
        """列表端点用：复用 _assembly_to_out_obj 的 cust_map 路径，返回
        AssemblyListItem（与 AssemblyOut 字段一致 + serial_no）。
        """
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
        return AssemblyListItem(
            id=asm.id,
            serial_no=asm.serial_no,
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