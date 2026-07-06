"""PartService：零件业务逻辑层。

新流程（去掉 READY 后）：
- PENDING → IN_PROCESS (放在生产货架上；holder=shelf_id)
- IN_PROCESS (shelf↔worker 之间切换 holder；状态不变)
- IN_PROCESS → INSPECTION (送检；holder=inspection_shelf_id)
- INSPECTION → READY_TO_SHIP → DELIVERED → COMPLETED
- 任意非终态 → REPAIRING → IN_PROCESS
- 任意非终态 → CANCELLED

货架与 SHELF_ACCOUNT 的耦合已经在 api/v1 层用 `require_shelf_account_from_body`
校验；service 层只关心「传进来的 shelf_id 是否真的存在且生效」。
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime

from fastapi import status as http_status

from core.error_code import ErrCode
from core.exception import BizError
from core.serial import code_for_parent
from model import TCustomer, TPart, TPartEvent, TProcess, TShelf, TWorker, TWorkType
from model.enums import PartEventType, PartStatus, ShelfZone
from repository.applicant import ApplicantRepository
from repository.customer import CustomerRepository
from repository.part import PartRepository
from repository.part_event import PartEventRepository
from repository.process import ProcessRepository
from repository.serial_counter import SerialCounterRepository
from repository.shelf import ShelfRepository
from repository.work_type import WorkTypeRepository
from repository.work_type_process import WorkTypeProcessRepository
from repository.worker import WorkerRepository
from schema.part import (
    PartBatchCreateItemFailure,
    PartBatchCreateRequest,
    PartBatchCreateResult,
    PartCreateRequest,
    PartEventOut,
    PartListOut,
    PartListQuery,
    PartOut,
    PartPickUpRequest,
    PartScanRequest,
    PartUpdateRequest,
    PlaceOnShelfRequest,
)
from utils.id_gen import new_id

Broadcaster = Callable[[], Awaitable[None]]
EventBroadcaster = Callable[[str, dict], Awaitable[None]]


def _parse_status(value: str | PartStatus | None) -> PartStatus | None:
    if value is None:
        return None
    if isinstance(value, PartStatus):
        return value
    try:
        return PartStatus(value)
    except ValueError as e:
        raise BizError(
            code=ErrCode.BIZ_INVALID_VALUE,
            message=f"invalid part status: {value!r}",
            http_status=http_status.HTTP_400_BAD_REQUEST,
        ) from e


def _parse_event_type(value: str | PartEventType) -> PartEventType:
    if isinstance(value, PartEventType):
        return value
    try:
        return PartEventType(value)
    except ValueError as e:
        raise BizError(
            code=ErrCode.BIZ_INVALID_VALUE,
            message=f"invalid event type: {value!r}",
            http_status=http_status.HTTP_400_BAD_REQUEST,
        ) from e


class PartService:
    def __init__(
        self,
        parts: PartRepository,
        customers: CustomerRepository,
        workers: WorkerRepository,
        events: PartEventRepository,
        serial_counters: SerialCounterRepository,
        shelves: ShelfRepository,
        processes: ProcessRepository | None = None,
        work_types: WorkTypeRepository | None = None,
        work_type_process: WorkTypeProcessRepository | None = None,
        applicants: ApplicantRepository | None = None,
        broadcaster: Broadcaster | None = None,
        event_broadcaster: EventBroadcaster | None = None,
    ) -> None:
        self.parts = parts
        self.customers = customers
        self.workers = workers
        self.events = events
        self.serial_counters = serial_counters
        self.shelves = shelves
        self.processes = processes
        self.work_types = work_types
        self.work_type_process = work_type_process
        self.applicants = applicants  # 可选：用于根据 applicant_id 解析 applicant_name
        self.broadcaster = broadcaster
        self.event_broadcaster = event_broadcaster

    # ============================================================
    # 查询
    # ============================================================
    async def list_parts(self, query: PartListQuery) -> PartListOut:
        if query.customer_id is not None:
            cust = await self.customers.get_by_id(query.customer_id)
            if cust is None:
                raise BizError(
                    code=ErrCode.BIZ_CUSTOMER_NOT_FOUND,
                    message=f"customer {query.customer_id} not found",
                    http_status=http_status.HTTP_404_NOT_FOUND,
                )
        rows = await self.parts.list_with_filters(
            customer_id=query.customer_id,
            statuses=query.statuses,
            is_urgent=query.is_urgent,
            keyword=query.keyword,
            sort_by=query.sort_by,
            sort_dir=query.sort_dir,
            limit=query.limit,
            offset=query.offset,
        )
        total = await self.parts.count_with_filters(
            customer_id=query.customer_id,
            statuses=query.statuses,
            is_urgent=query.is_urgent,
            keyword=query.keyword,
        )
        items = await self._to_out(rows)
        return PartListOut(
            items=items, total=total, limit=query.limit, offset=query.offset
        )

    async def get_part(self, part_id: int) -> PartOut:
        part = await self.parts.get_by_id(part_id)
        if part is None:
            raise BizError(
                code=ErrCode.BIZ_PART_NOT_FOUND,
                message=f"part {part_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        items = await self._to_out([part])
        return items[0]

    async def list_events(self, part_id: int) -> list[PartEventOut]:
        part = await self.parts.get_by_id(part_id)
        if part is None:
            raise BizError(
                code=ErrCode.BIZ_PART_NOT_FOUND,
                message=f"part {part_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        events = await self.events.list_by_part(part_id)
        worker_ids = {e.worker_id for e in events if e.worker_id}
        worker_map: dict[int, TWorker] = {}
        if worker_ids:
            workers = await self.workers.list_with_filters(
                is_active=None, limit=max(100, len(worker_ids))
            )
            worker_map = {w.id: w for w in workers if w.id in worker_ids}
        return [
            PartEventOut(
                id=e.id,
                part_id=e.part_id,
                worker_id=e.worker_id,
                worker_name=worker_map[e.worker_id].name
                if e.worker_id in worker_map
                else None,
                event_type=e.event_type,
                from_status=e.from_status,
                to_status=e.to_status,
                drawing_code=e.drawing_code,
                badge_code=e.badge_code,
                note=e.note,
                created_at=e.created_at,
            )
            for e in events
        ]

    async def _write_event(
        self,
        *,
        part: TPart,
        event_type: PartEventType,
        from_status: PartStatus | None,
        to_status: PartStatus | None,
        worker_id: int | None = None,
        drawing_code: str | None = None,
        badge_code: str | None = None,
        note: str | None = None,
    ) -> TPartEvent:
        event = TPartEvent(
            id=new_id(),
            part_id=part.id,
            worker_id=worker_id,
            event_type=event_type.value,
            from_status=from_status.value if from_status else None,
            to_status=to_status.value if to_status else None,
            drawing_code=drawing_code,
            badge_code=badge_code,
            note=note,
        )
        return await self.events.create(event)

    async def _broadcast(self) -> None:
        if self.broadcaster is None:
            return
        try:
            await self.broadcaster()
        except Exception:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).exception("dashboard broadcast failed")

    async def _broadcast_event(self, event_type: str, payload: dict) -> None:
        if self.event_broadcaster is None:
            return
        try:
            await self.event_broadcaster(event_type, payload)
        except Exception:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).exception(
                "dashboard event broadcast failed: %s", event_type
            )

    @staticmethod
    def _banner_payload(
        part: TPart,
        *,
        customer_path: str | None,
        worker_name: str | None = None,
        shelf_code: str | None = None,
    ) -> dict:
        return {
            "serial_no": part.serial_no,
            "drawing_no": part.drawing_no,
            "name": part.name,
            "customer_path": customer_path,
            "is_urgent": bool(part.is_urgent),
            "planned_delivery_date": part.planned_delivery_date.isoformat()
            if part.planned_delivery_date
            else None,
            "worker_name": worker_name,
            "shelf_code": shelf_code,
        }

    # ============================================================
    # 写操作
    # ============================================================
    async def create_part(self, data: PartCreateRequest) -> PartOut:
        cust = await self.customers.get_by_id(data.customer_id)
        if cust is None:
            raise BizError(
                code=ErrCode.BIZ_CUSTOMER_NOT_FOUND,
                message=f"customer {data.customer_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        if cust.parent_id is not None:
            # 二级客户 → 用一级父客户名取序列号代码
            parent = await self.customers.get_by_id(cust.parent_id)
            if parent is None:
                raise BizError(
                    code=ErrCode.BIZ_CUSTOMER_NOT_FOUND,
                    message=f"parent customer {cust.parent_id} not found",
                    http_status=http_status.HTTP_404_NOT_FOUND,
                )
            parent_name = parent.name
            root_customer_id = parent.id
        else:
            # 一级客户自身 → 直接用其名称取序列号代码
            parent_name = cust.name
            root_customer_id = cust.id
        code = code_for_parent(parent_name)
        if code is None:
            raise BizError(
                code=ErrCode.BIZ_CUSTOMER_NOT_FOUND,
                message=f"未配置客户「{parent_name}」的序列号代码",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        # 解析 applicant_id → applicant_name（按姓名快照写入 t_part）
        # 注：applicant_id 在 schema 是 str（雪花 ID 字符串，避免 JS Number 精度丢失），
        # 这里转回 int 再去 repository 查询。
        applicant_name = data.applicant_name
        if data.applicant_id is not None:
            if self.applicants is None:
                raise BizError(
                    code=ErrCode.BIZ_INVALID_VALUE,
                    message="server missing applicant repository",
                    http_status=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
                )
            try:
                applicant_id_int = int(data.applicant_id)
            except (TypeError, ValueError) as e:
                raise BizError(
                    code=ErrCode.BIZ_INVALID_VALUE,
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
                        f"applicant {data.applicant_id} 不属于本零件的一级客户 "
                        f"{root_customer_id}"
                    ),
                    http_status=http_status.HTTP_400_BAD_REQUEST,
                )
            applicant_name = applicant.name
        serial_no = await self.serial_counters.acquire_serial(code)
        total_price = data.total_price
        if total_price is None:
            total_price = data.unit_price * data.quantity

        part = TPart(
            id=new_id(),
            serial_no=serial_no,
            name=data.name,
            drawing_no=data.drawing_no,
            applicant_name=applicant_name,
            quantity=data.quantity,
            unit_price=data.unit_price,
            total_price=total_price,
            request_date=data.request_date,
            planned_delivery_date=data.planned_delivery_date,
            actual_delivery_date=data.actual_delivery_date,
            status=PartStatus.PENDING.value,
            is_urgent=data.is_urgent,
            customer_id=data.customer_id,
        )
        part.location = "OFFICE"
        await self.parts.create(part)
        await self._write_event(
            part=part,
            event_type=PartEventType.CREATED,
            from_status=None,
            to_status=PartStatus.PENDING,
        )
        items = await self._to_out([part])
        return items[0]

    async def create_parts_batch(
        self, payload: PartBatchCreateRequest
    ) -> PartBatchCreateResult:
        customer_cache: dict[int, TCustomer | None] = {}
        parent_cache: dict[int, TCustomer | None] = {}
        code_cache: dict[str, str | None] = {}

        async def get_customer(cid: int) -> TCustomer | None:
            if cid not in customer_cache:
                customer_cache[cid] = await self.customers.get_by_id(cid)
            return customer_cache[cid]

        async def get_parent(pid: int) -> TCustomer | None:
            if pid not in parent_cache:
                parent_cache[pid] = await self.customers.get_by_id(pid)
            return parent_cache[pid]

        def get_code(parent_name: str) -> str | None:
            if parent_name not in code_cache:
                code_cache[parent_name] = code_for_parent(parent_name)
            return code_cache[parent_name]

        failed: list[PartBatchCreateItemFailure] = []
        for idx, item in enumerate(payload.items):
            cust = await get_customer(item.customer_id)
            if cust is None:
                failed.append(PartBatchCreateItemFailure(index=idx, message=f"customer {item.customer_id} not found"))
                continue
            if cust.parent_id is not None:
                parent = await get_parent(cust.parent_id)
                if parent is None:
                    failed.append(PartBatchCreateItemFailure(index=idx, message=f"parent customer {cust.parent_id} not found"))
                    continue
                parent_name = parent.name
            else:
                parent_name = cust.name
            if get_code(parent_name) is None:
                failed.append(PartBatchCreateItemFailure(index=idx, message=f"未配置客户「{parent_name}」的序列号代码"))
                continue

        if failed:
            return PartBatchCreateResult(created=[], failed=failed)

        created: list[PartOut] = []
        for item in payload.items:
            out = await self.create_part(item)
            created.append(out)
        return PartBatchCreateResult(created=created, failed=[])

    async def update_part(
        self, part_id: int, data: PartUpdateRequest
    ) -> PartOut:
        part = await self.parts.get_by_id(part_id)
        if part is None:
            raise BizError(
                code=ErrCode.BIZ_PART_NOT_FOUND,
                message=f"part {part_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        if data.name is not None:
            part.name = data.name.strip()
        if data.drawing_no is not None:
            part.drawing_no = data.drawing_no.strip()
        if data.applicant_name is not None:
            part.applicant_name = data.applicant_name.strip()
        if data.quantity is not None:
            part.quantity = data.quantity
        if data.unit_price is not None:
            part.unit_price = data.unit_price
        if data.total_price is not None:
            part.total_price = data.total_price
        if data.request_date is not None:
            part.request_date = data.request_date
        if data.planned_delivery_date is not None:
            part.planned_delivery_date = data.planned_delivery_date
        if data.actual_delivery_date is not None:
            part.actual_delivery_date = data.actual_delivery_date
        if data.is_urgent is not None:
            part.is_urgent = data.is_urgent
        if data.customer_id is not None:
            cust = await self.customers.get_by_id(data.customer_id)
            if cust is None:
                raise BizError(
                    code=ErrCode.BIZ_CUSTOMER_NOT_FOUND,
                    message=f"customer {data.customer_id} not found",
                    http_status=http_status.HTTP_404_NOT_FOUND,
                )
            part.customer_id = data.customer_id
        await self.parts.update(part)
        items = await self._to_out([part])
        return items[0]

    async def soft_delete_part(self, part_id: int) -> None:
        part = await self.parts.get_by_id(part_id)
        if part is None:
            raise BizError(
                code=ErrCode.BIZ_PART_NOT_FOUND,
                message=f"part {part_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        await self.parts.soft_delete(part)

    # ============================================================
    # 新报工流程（货架 + 工人 + 品检）
    # ============================================================
    async def place_on_shelf(
        self, part_id: int, data: PlaceOnShelfRequest
    ) -> PartOut:
        """PENDING → IN_PROCESS：把零件放到生产货架。

        `shelf_id` 必须在 t_shelf 中存在 / is_active / zone=PRODUCTION。
        `next_process_id` 必填，service 校验 process 存在后喂给状态机。
        """
        part = await self._get_part_or_404(part_id)
        shelf, process = await self._validate_production_shelf_and_process(
            data.shelf_id, data.next_process_id,
        )

        # state machine handles status/location/holder mutation + event creation;
        # 状态机 on_enter_ON_SHELF 会同时把 next_process_id 设到 model 上。
        part.sm.place_on_shelf(
            shelf=shelf, process=process, event_repo=self.events,
        )
        await self.parts.update(part)
        await self._broadcast()
        items = await self._to_out([part])
        await self._broadcast_event(
            "PLACED_ON_SHELF",
            self._banner_payload(
                part,
                customer_path=items[0].customer_path,
                shelf_code=shelf.code,
            ),
        )
        return items[0]

    async def send_to_programming(self, part_id: int) -> PartOut:
        """PENDING → PROGRAMMING：把零件发送至 CNC 编程。

        编程员在「待编程一览」看到这个零件，下载图纸/3D → 写程序 →
        上传 G 代码 → 在编程员端调用 `release_from_programming` 下发到货架。
        """
        part = await self._get_part_or_404(part_id)
        part.sm.send_to_programming(event_repo=self.events)
        await self.parts.update(part)
        await self._broadcast()
        items = await self._to_out([part])
        await self._broadcast_event(
            "SENT_TO_PROGRAMMING",
            self._banner_payload(
                part, customer_path=items[0].customer_path,
            ),
        )
        return items[0]

    async def release_from_programming(
        self, part_id: int, data: PlaceOnShelfRequest
    ) -> PartOut:
        """PROGRAMMING → IN_PROCESS：编程员把零件下发到生产货架。

        与 place_on_shelf 走同一个货架/工序校验，落到 ON_SHELF 状态机入口
        复用同一份 on_enter_ON_SHELF 副作用；事件类型为 CNC_RELEASED
        （见 on_release_from_programming 回调）。
        """
        part = await self._get_part_or_404(part_id)
        shelf, process = await self._validate_production_shelf_and_process(
            data.shelf_id, data.next_process_id,
        )
        part.sm.release_from_programming(
            shelf=shelf, process=process, event_repo=self.events,
        )
        await self.parts.update(part)
        await self._broadcast()
        items = await self._to_out([part])
        await self._broadcast_event(
            "CNC_RELEASED",
            self._banner_payload(
                part,
                customer_path=items[0].customer_path,
                shelf_code=shelf.code,
            ),
        )
        return items[0]

    async def _get_part_or_404(self, part_id: int) -> TPart:
        part = await self.parts.get_by_id(part_id)
        if part is None:
            raise BizError(
                code=ErrCode.BIZ_PART_NOT_FOUND,
                message=f"part {part_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        return part

    async def _validate_production_shelf_and_process(
        self, shelf_id: int | None, next_process_id: int | None,
    ) -> tuple[TShelf, TProcess]:
        """校验 `shelf_id` 是 PRODUCTION 区 active 货架 + `next_process_id` 存在。"""
        if shelf_id is None:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message="shelf_id is required",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        shelf = await self.shelves.get_by_id(shelf_id)
        if shelf is None or shelf.deleted_at is not None:
            raise BizError(
                code=ErrCode.BIZ_SHELF_NOT_FOUND,
                message=f"shelf {shelf_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        if not shelf.is_active:
            raise BizError(
                code=ErrCode.BIZ_SHELF_IN_USE,
                message=f"shelf {shelf.code!r} is inactive",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        if shelf.zone != ShelfZone.PRODUCTION.value:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message=(
                    f"shelf {shelf.code!r} is zone={shelf.zone!r}; "
                    "this operation requires PRODUCTION"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        if next_process_id is None:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message="next_process_id is required",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        process = await self._get_process(next_process_id)
        return shelf, process

    async def _get_process(self, process_id: int) -> TProcess:
        """取工序对象；不存在抛 BIZ_PROCESS_NOT_FOUND。"""
        if self.processes is None:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message="processes repo not configured",
                http_status=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        p = await self.processes.get_by_id(process_id)
        if p is None:
            raise BizError(
                code=ErrCode.BIZ_PROCESS_NOT_FOUND,
                message=f"process {process_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        return p

    async def pick_up_by_scan(self, data: PartPickUpRequest) -> PartOut:
        """工人扫 serial_no 领取：状态仍是 IN_PROCESS；holder 由 shelf → worker。

        不再是状态变更；只是 `current_holder_id` 在「工人↔货架」之间切换。
        加 `with_for_update()` 防两人并发领同一 serial。
        """
        # 货架和工人先取出
        shelf = await self.shelves.get_by_id(data.shelf_id)
        if shelf is None or shelf.deleted_at is not None:
            raise BizError(
                code=ErrCode.BIZ_SHELF_NOT_FOUND,
                message=f"shelf {data.shelf_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        if shelf.zone != ShelfZone.PRODUCTION.value:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message=(
                    f"shelf {shelf.code!r} is zone={shelf.zone!r}; "
                    "pick-up only valid at PRODUCTION shelf"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        worker = await self.workers.get_by_badge_code(data.badge_code)
        if worker is None:
            raise BizError(
                code=ErrCode.BIZ_WORKER_NOT_FOUND,
                message=f"worker with badge_code {data.badge_code!r} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        if not worker.is_active:
            raise BizError(
                code=ErrCode.BIZ_WORKER_INACTIVE,
                message=f"worker {worker.name} is inactive",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )

        part = await self.parts.get_by_serial(data.serial_no)
        if part is None:
            raise BizError(
                code=ErrCode.BIZ_PART_NOT_FOUND,
                message=f"part with serial_no {data.serial_no!r} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )

        if part.status != "IN_PROCESS" or part.location != "PRODUCTION_SHELF":
            raise BizError(
                code=ErrCode.BIZ_INVALID_TRANSITION,
                message=(
                    f"pick-up requires IN_PROCESS on PRODUCTION_SHELF; "
                    f"current status={part.status}, location={part.location}"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        if part.current_holder_id != shelf.id:
            raise BizError(
                code=ErrCode.BIZ_AUTH_SHELF_MISMATCH,
                message=(
                    f"part is currently held by {part.current_holder_id}; "
                    f"expected this shelf {shelf.id}"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )

        # state machine handles holder switch + event creation
        part.sm.pick_up(worker=worker, shelf=shelf, event_repo=self.events)
        await self.parts.update(part)
        await self._broadcast()
        items = await self._to_out([part])
        await self._broadcast_event(
            "PICKED_UP",
            self._banner_payload(
                part,
                customer_path=items[0].customer_path,
                worker_name=worker.name,
                shelf_code=shelf.code,
            ),
        )
        return items[0]

    async def scan_event(self, data: PartScanRequest) -> PartOut:
        """RETURNED：把当前由工人持有的零件放回生产货架（holder = shelf_id）；
        状态不变（仍是 IN_PROCESS）。

        INSPECTED：从工人手送检到品检货架；状态 → INSPECTION；holder → inspection shelf。
        """
        shelf = await self.shelves.get_by_id(data.shelf_id)
        if shelf is None or shelf.deleted_at is not None:
            raise BizError(
                code=ErrCode.BIZ_SHELF_NOT_FOUND,
                message=f"shelf {data.shelf_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        worker = await self.workers.get_by_badge_code(data.badge_code)
        if worker is None:
            raise BizError(
                code=ErrCode.BIZ_WORKER_NOT_FOUND,
                message=f"worker with badge_code {data.badge_code!r} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        if not worker.is_active:
            raise BizError(
                code=ErrCode.BIZ_WORKER_INACTIVE,
                message=f"worker {worker.name} is inactive",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )

        part = await self.parts.get_by_serial(data.serial_no)
        if part is None:
            raise BizError(
                code=ErrCode.BIZ_PART_NOT_FOUND,
                message=f"part with serial_no {data.serial_no!r} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )

        from_status = _parse_status(part.status)
        event_type = _parse_event_type(data.event_type)

        if event_type == PartEventType.RETURNED:
            if part.status != "IN_PROCESS" or part.location != "WORKER":
                raise BizError(
                    code=ErrCode.BIZ_INVALID_TRANSITION,
                    message=(
                        f"return requires IN_PROCESS with WORKER location; "
                        f"current status={part.status}, location={part.location}"
                    ),
                    http_status=http_status.HTTP_400_BAD_REQUEST,
                )
            if shelf.zone != ShelfZone.PRODUCTION.value:
                raise BizError(
                    code=ErrCode.BIZ_INVALID_VALUE,
                    message=(
                        f"shelf {shelf.code!r} is zone={shelf.zone!r}; "
                        "return only valid at PRODUCTION shelf"
                    ),
                    http_status=http_status.HTTP_400_BAD_REQUEST,
                )
            if part.current_holder_id != worker.id:
                raise BizError(
                    code=ErrCode.BIZ_AUTH_SHELF_MISMATCH,
                    message=(
                        f"part is not held by worker {worker.id}; "
                        "only the current holder can return it"
                    ),
                    http_status=http_status.HTTP_400_BAD_REQUEST,
                )
            # RETURNED 必填 next_process_id
            if data.next_process_id is None:
                raise BizError(
                    code=ErrCode.BIZ_INVALID_VALUE,
                    message="RETURNED requires next_process_id",
                    http_status=http_status.HTTP_400_BAD_REQUEST,
                )
            new_process = await self._get_process(data.next_process_id)
            # 解析 prev_process_code 与 worker_work_type_code 给状态机 note 用
            prev_process_code: str | None = None
            if self.processes is not None and part.next_process_id is not None:
                prev = await self.processes.get_by_id(part.next_process_id)
                if prev is not None:
                    prev_process_code = prev.code
            worker_work_type_code: str | None = None
            if (
                self.work_types is not None
                and worker.work_type_id is not None
            ):
                wt = await self.work_types.get_by_id(worker.work_type_id)
                if wt is not None:
                    worker_work_type_code = wt.code

            part.sm.return_to_shelf(
                worker=worker,
                shelf=shelf,
                process=new_process,
                prev_process_code=prev_process_code,
                worker_work_type_code=worker_work_type_code,
                event_repo=self.events,
            )
            await self.parts.update(part)
            await self._broadcast()
            items = await self._to_out([part])
            await self._broadcast_event(
                "RETURNED",
                self._banner_payload(
                    part,
                    customer_path=items[0].customer_path,
                    worker_name=worker.name,
                    shelf_code=shelf.code,
                ),
            )
            return items[0]

        if event_type == PartEventType.INSPECTED:
            if data.target_inspection_shelf_id is None:
                raise BizError(
                    code=ErrCode.BIZ_INVALID_VALUE,
                    message="INSPECTED requires target_inspection_shelf_id",
                    http_status=http_status.HTTP_400_BAD_REQUEST,
                )
            target = await self.shelves.get_by_id(data.target_inspection_shelf_id)
            if target is None or target.deleted_at is not None:
                raise BizError(
                    code=ErrCode.BIZ_SHELF_NOT_FOUND,
                    message=f"inspection shelf {data.target_inspection_shelf_id} not found",
                    http_status=http_status.HTTP_404_NOT_FOUND,
                )
            if target.zone != ShelfZone.INSPECTION.value:
                raise BizError(
                    code=ErrCode.BIZ_INVALID_VALUE,
                    message=(
                        f"target shelf {target.code!r} is zone={target.zone!r}; "
                        "INSPECTED requires INSPECTION zone"
                    ),
                    http_status=http_status.HTTP_400_BAD_REQUEST,
                )
            if not target.is_active:
                raise BizError(
                    code=ErrCode.BIZ_SHELF_IN_USE,
                    message=f"inspection shelf {target.code!r} is inactive",
                    http_status=http_status.HTTP_400_BAD_REQUEST,
                )
            if part.current_holder_id != worker.id:
                raise BizError(
                    code=ErrCode.BIZ_AUTH_SHELF_MISMATCH,
                    message="part is not held by current worker",
                    http_status=http_status.HTTP_400_BAD_REQUEST,
                )

            part.sm.inspect(worker=worker, target_shelf=target, event_repo=self.events)
            await self.parts.update(part)
            await self._broadcast()
            items = await self._to_out([part])
            await self._broadcast_event(
                "INSPECTED",
                self._banner_payload(
                    part,
                    customer_path=items[0].customer_path,
                    worker_name=worker.name,
                    shelf_code=target.code,
                ),
            )
            return items[0]

        raise BizError(
            code=ErrCode.BIZ_INVALID_VALUE,
            message=(
                f"scan endpoint only accepts RETURNED or INSPECTED, got {event_type.value!r}"
            ),
            http_status=http_status.HTTP_400_BAD_REQUEST,
        )

    # ============================================================
    # 扫码台 PICK_UP 列表（按工种 + 货架过滤）
    # ============================================================
    async def list_pickable_parts(
        self, work_type_id: int, shelf_id: int
    ) -> list[PartOut]:
        """列出当前生产货架上、由指定工种可领的零件。

        短路：worker.work_type_id 未传 → []；
              工种未映射任何工序 → []；
              货架不存在 → []。

        返回 [] 时 UI 提示「无可领件 / 工种映射为空 / 请联系管理员」。
        """
        if work_type_id is None:
            return []
        if self.work_type_process is None:
            return []
        process_ids = await self.work_type_process.list_process_ids_by_work_type(
            work_type_id, include_deleted=False,
        )
        if not process_ids:
            return []
        rows = await self.parts.list_for_work_type(
            shelf_id=shelf_id,
            mapped_process_ids=process_ids,
        )
        return await self._to_out(rows)

    async def pass_inspection(self, part_id: int) -> PartOut:
        """INSPECTION -> READY_TO_SHIP：品检合格。"""
        part = await self.parts.get_by_id(part_id)
        if part is None:
            raise BizError(
                code=ErrCode.BIZ_PART_NOT_FOUND,
                message=f"part {part_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        part.sm.pass_inspection(event_repo=self.events)
        await self.parts.update(part)
        await self._broadcast()
        await self._check_parent_assembly(part)
        items = await self._to_out([part])
        return items[0]

    async def deliver(self, part_id: int) -> PartOut:
        """READY_TO_SHIP -> DELIVERED：发货。"""
        part = await self.parts.get_by_id(part_id)
        if part is None:
            raise BizError(
                code=ErrCode.BIZ_PART_NOT_FOUND,
                message=f"part {part_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        part.sm.deliver(event_repo=self.events)
        await self.parts.update(part)
        await self._broadcast()
        await self._check_parent_assembly(part)
        items = await self._to_out([part])
        return items[0]

    async def complete(self, part_id: int) -> PartOut:
        """DELIVERED -> COMPLETED：确认完成，释放流水号。"""
        part = await self.parts.get_by_id(part_id)
        if part is None:
            raise BizError(
                code=ErrCode.BIZ_PART_NOT_FOUND,
                message=f"part {part_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        part.sm.complete(event_repo=self.events)
        await self.parts.update(part)
        await self._broadcast()
        await self._check_parent_assembly(part)
        items = await self._to_out([part])
        return items[0]

    async def start_repair(self, part_id: int) -> PartOut:
        """-> REPAIRING：开始返修（从 INSPECTION / READY_TO_SHIP / DELIVERED 进入）。"""
        part = await self.parts.get_by_id(part_id)
        if part is None:
            raise BizError(
                code=ErrCode.BIZ_PART_NOT_FOUND,
                message=f"part {part_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        part.sm.start_repair(event_repo=self.events)
        await self.parts.update(part)
        await self._broadcast()
        await self._check_parent_assembly(part)
        items = await self._to_out([part])
        return items[0]

    async def complete_repair(self, part_id: int, shelf_id: int) -> PartOut:
        """REPAIRING -> IN_PROCESS：返修完成，放回生产货架。"""
        part = await self.parts.get_by_id(part_id)
        if part is None:
            raise BizError(
                code=ErrCode.BIZ_PART_NOT_FOUND,
                message=f"part {part_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        shelf = await self.shelves.get_by_id(shelf_id)
        if shelf is None or shelf.deleted_at is not None:
            raise BizError(
                code=ErrCode.BIZ_SHELF_NOT_FOUND,
                message=f"shelf {shelf_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        if not shelf.is_active:
            raise BizError(
                code=ErrCode.BIZ_SHELF_IN_USE,
                message=f"shelf {shelf.code!r} is inactive",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        if shelf.zone != ShelfZone.PRODUCTION.value:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message=f"shelf {shelf.code!r} is zone={shelf.zone!r}; complete_repair requires PRODUCTION",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        part.sm.complete_repair(shelf=shelf, event_repo=self.events)
        await self.parts.update(part)
        await self._broadcast()
        await self._check_parent_assembly(part)
        items = await self._to_out([part])
        return items[0]

    async def cancel(self, part_id: int) -> PartOut:
        """-> CANCELLED：取消零件，释放流水号。"""
        part = await self.parts.get_by_id(part_id)
        if part is None:
            raise BizError(
                code=ErrCode.BIZ_PART_NOT_FOUND,
                message=f"part {part_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        part.sm.cancel(event_repo=self.events)
        await self.parts.update(part)
        await self._broadcast()
        await self._check_parent_assembly(part)
        items = await self._to_out([part])
        return items[0]

    async def _check_parent_assembly(self, part: TPart) -> None:
        """Part 状态变更后，检查并自动更新父 Assembly 状态。"""
        if part.assembly_id is None:
            return
        from model.assembly import TAssembly

        session = self.parts.session
        from sqlalchemy import select

        stmt = select(TAssembly).where(
            TAssembly.id == part.assembly_id,
            TAssembly.deleted_at.is_(None),
        )
        result = await session.execute(stmt)
        assembly = result.scalar_one_or_none()
        if assembly is None:
            return

        children = await self.parts.list_children(assembly.id)
        non_cancelled = [p for p in children if p.status != "CANCELLED"]

        # PENDING -> IN_PROCESS: any non-cancelled child is not PENDING
        if assembly.status == "PENDING" and any(
            p.status not in ("PENDING", "CANCELLED") for p in non_cancelled
        ):
            assembly.sm.start_production()
            await session.flush()

        # IN_PROCESS -> COMPLETED: all non-cancelled children are COMPLETED
        if assembly.status == "IN_PROCESS" and non_cancelled and all(
            p.status == "COMPLETED" for p in non_cancelled
        ):
            assembly.sm.complete()
            await session.flush()

    # ============================================================
    # 内部：拼客户路径 + location 字段 + 多态 holder
    # ============================================================
    async def _to_out(self, rows: list[TPart]) -> list[PartOut]:
        if not rows:
            return []
        cust_ids = list({p.customer_id for p in rows})
        cust_list = await self.customers.list_by_ids(cust_ids)
        cust_map: dict[int, TCustomer] = {c.id: c for c in cust_list}
        parent_ids = [c.parent_id for c in cust_list if c.parent_id]
        parents = (
            await self.customers.list_by_ids(parent_ids) if parent_ids else []
        )
        parent_map: dict[int, TCustomer] = {p.id: p for p in parents}

        # 批查 worker name（location=WORKER 的行）
        worker_ids = [
            int(p.current_holder_id)
            for p in rows
            if p.location == "WORKER" and p.current_holder_id
        ]
        worker_map: dict[int, str] = {}
        if worker_ids:
            workers = await self.workers.list_by_ids(worker_ids)
            worker_map = {w.id: w.name for w in workers}

        # 批查下一道工序名称（避免前端为「下一道工序列」再发一次 /processes 请求）
        process_ids = {
            int(p.next_process_id)
            for p in rows
            if p.next_process_id
        }
        process_map: dict[int, str] = {}
        if process_ids and self.processes is not None:
            procs = await self.processes.list_by_ids(list(process_ids))
            process_map = {pr.id: pr.name for pr in procs}

        out: list[PartOut] = []
        for p in rows:
            cust = cust_map.get(p.customer_id)
            parent = (
                parent_map.get(cust.parent_id)
                if cust and cust.parent_id
                else None
            )
            parent_name = parent.name if parent else None
            child_name = cust.name if cust else None
            path: str | None = None
            if parent_name and child_name:
                path = f"{parent_name} / {child_name}"
            elif child_name:
                path = child_name
            elif parent_name:
                path = parent_name

            # Use location field instead of dual-table holder lookup
            holder_kind: str | None = None
            shelf_code: str | None = None
            worker_name: str | None = None
            if p.location in ("PRODUCTION_SHELF", "INSPECTION_SHELF") and p.current_holder_id:
                holder_kind = "shelf"
                shelf_id = int(p.current_holder_id)
                shelves_for_holder = await self.shelves.list_by_ids([shelf_id])
                if shelves_for_holder:
                    shelf_code = shelves_for_holder[0].code
            elif p.location == "WORKER" and p.current_holder_id:
                holder_kind = "worker"
                worker_name = worker_map.get(int(p.current_holder_id))

            out.append(
                PartOut(
                    id=p.id,
                    serial_no=p.serial_no,
                    name=p.name,
                    drawing_no=p.drawing_no,
                    quantity=p.quantity,
                    planned_delivery_date=p.planned_delivery_date,
                    actual_delivery_date=p.actual_delivery_date,
                    is_urgent=p.is_urgent,
                    status=_parse_status(p.status) or PartStatus.PENDING,
                    customer_name=child_name,
                    parent_customer_name=parent_name,
                    customer_path=path,
                    assembly_id=p.assembly_id,
                    current_holder_id=p.current_holder_id,
                    current_holder_kind=holder_kind,
                    shelf_code=shelf_code,
                    placed_at=getattr(p, "placed_at", None),
                    location=p.location,
                    worker_name=worker_name,
                    next_process_id=p.next_process_id,
                    next_process_name=process_map.get(int(p.next_process_id))
                    if p.next_process_id
                    else None,
                )
            )
        return out
