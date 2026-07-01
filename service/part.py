from collections.abc import Awaitable, Callable
from datetime import datetime

from fastapi import status as http_status

from core.error_code import ErrCode
from core.exception import BizError
from core.serial import SERIAL_RELEASE_STATUSES, code_for_parent
from model import PART_TRANSITIONS, TCustomer, TPart, TPartEvent, TWorker
from model.enums import PartEventType, PartStatus
from repository.customer import CustomerRepository
from repository.part import PartRepository
from repository.part_event import PartEventRepository
from repository.serial_counter import SerialCounterRepository
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
    PartStatusChangeRequest,
)
from utils.id_gen import new_id


# CANCELLED 允许从任意非终态进入（任意 → CANCELLED）
ALL_NON_TERMINAL_STATUSES: frozenset[PartStatus] = frozenset(
    {
        PartStatus.PENDING,
        PartStatus.READY,
        PartStatus.IN_PROCESS,
        PartStatus.INSPECTION,
        PartStatus.READY_TO_SHIP,
        PartStatus.DELIVERED,
        PartStatus.REPAIRING,
    }
)

# 触发 dashboard 广播的回调签名：(no args) -> awaitable[None]
Broadcaster = Callable[[], Awaitable[None]]
# 触发单条业务事件推送的回调签名：(event_type, payload) -> awaitable[None]
EventBroadcaster = Callable[[str, dict], Awaitable[None]]


def _parse_status(value: str | PartStatus | None) -> PartStatus | None:
    """字符串/枚举统一成 PartStatus；非法值抛 BizError(BIZ_INVALID_VALUE)。"""
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
    """字符串/枚举统一成 PartEventType。"""
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
    """零件业务逻辑层。

    负责：
    - 调用 PartRepository / WorkerRepository / PartEventRepository 做数据访问；
    - 拼装客户全路径（一级 / 二级）以避免 N+1；
    - **状态机校验**：所有 PartStatus 转换都在 service 层校验，
      DB 不加任何约束。详见 `model.enums.PART_TRANSITIONS`。
    - **事件写入**：每次成功的状态变更都要写一条 `t_part_event`，
      与状态变更在同一个事务里。
    - **仪表盘广播**：状态变更成功后调用 `broadcaster()` 回调，
      由 deps.py 注入，避免 service → ws 循环依赖。
    """

    def __init__(
        self,
        parts: PartRepository,
        customers: CustomerRepository,
        workers: WorkerRepository,
        events: PartEventRepository,
        serial_counters: SerialCounterRepository,
        broadcaster: Broadcaster | None = None,
        event_broadcaster: EventBroadcaster | None = None,
    ) -> None:
        self.parts = parts
        self.customers = customers
        self.workers = workers
        self.events = events
        self.serial_counters = serial_counters
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
            status=_parse_status(query.status),
            is_urgent=query.is_urgent,
            drawing_no_like=query.drawing_no_like,
            name_like=query.name_like,
            sort_by=query.sort_by,
            sort_dir=query.sort_dir,
            limit=query.limit,
            offset=query.offset,
        )
        total = await self.parts.count_with_filters(
            customer_id=query.customer_id,
            status=_parse_status(query.status),
            is_urgent=query.is_urgent,
            drawing_no_like=query.drawing_no_like,
            name_like=query.name_like,
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
        """按 part_id 拉取该零件的全生命周期事件流（按 created_at 升序）。"""
        part = await self.parts.get_by_id(part_id)
        if part is None:
            raise BizError(
                code=ErrCode.BIZ_PART_NOT_FOUND,
                message=f"part {part_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        events = await self.events.list_by_part(part_id)
        # 拼 worker_name
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

    # ============================================================
    # 状态机核心
    # ============================================================
    def _assert_transition(
        self, from_status: PartStatus, to_status: PartStatus
    ) -> None:
        """校验 from → to 合法。CANCELLED 允许从任意非终态进入；否则查表。"""
        if to_status == PartStatus.CANCELLED:
            if from_status in ALL_NON_TERMINAL_STATUSES:
                return
            # COMPLETED → CANCELLED 不允许
            raise BizError(
                code=ErrCode.BIZ_INVALID_TRANSITION,
                message=f"cannot cancel from terminal status {from_status.value}",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        if (from_status, to_status) not in PART_TRANSITIONS:
            raise BizError(
                code=ErrCode.BIZ_INVALID_TRANSITION,
                message=f"invalid transition {from_status.value} -> {to_status.value}",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )

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
            # 推送失败不影响主业务
            import logging

            logging.getLogger(__name__).exception("dashboard broadcast failed")

    async def _broadcast_event(self, event_type: str, payload: dict) -> None:
        """触发单条业务事件推送（如 PICKED_UP / RELEASED）。

        失败不影响主业务；调用方无需 try/except。
        payload 仅含 UI 横幅所需最小集，**不要传全 PartOut**。
        """
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
        part: TPart, *, customer_path: str | None, worker_name: str | None = None
    ) -> dict:
        """拼横幅事件 payload（最小字段集）。

        `customer_path` 由调用方从 `_to_out(...)` 拿，避免重复查 customer。
        """
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
        }

    # ============================================================
    # 写操作
    # ============================================================
    async def create_part(self, data: PartCreateRequest) -> PartOut:
        """新增 PENDING 零件。系统按客户代码自动分配序列号。"""
        cust = await self.customers.get_by_id(data.customer_id)
        if cust is None:
            raise BizError(
                code=ErrCode.BIZ_CUSTOMER_NOT_FOUND,
                message=f"customer {data.customer_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        if cust.parent_id is None:
            raise BizError(
                code=ErrCode.BIZ_CUSTOMER_NOT_FOUND,
                message="序列号只能分配给二级客户节点（一级集团不允许挂零件）",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        parent = await self.customers.get_by_id(cust.parent_id)
        if parent is None:
            raise BizError(
                code=ErrCode.BIZ_CUSTOMER_NOT_FOUND,
                message=f"parent customer {cust.parent_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        code = code_for_parent(parent.name)
        if code is None:
            raise BizError(
                code=ErrCode.BIZ_CUSTOMER_NOT_FOUND,
                message=f"未配置一级客户「{parent.name}」的序列号代码",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        serial_no = await self.serial_counters.acquire_serial(code)
        total_price = data.total_price
        if total_price is None:
            total_price = data.unit_price * data.quantity

        part = TPart(
            id=new_id(),
            serial_no=serial_no,
            name=data.name,
            drawing_no=data.drawing_no,
            applicant_name=data.applicant_name,
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
        """批量新增零件（Excel 导入用）。

        行为：
        - **校验阶段**：仅 SELECT（customer 存在 / 是叶子节点 / parent 存在 / 一级客户有序列号代码），
          失败行写入 `failed`，整体不下任何写入。
        - **写入阶段**：校验全部通过后，逐行调用 `create_part()`，共享同一请求级 session
          同一个事务；任一 DB 写失败 → session 回滚（由 `get_session` 处理），整个 batch 失败。
        - 校验阶段对 customer / parent 做内存级缓存，避免 N 行重复查同一节点。
        """
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
                failed.append(
                    PartBatchCreateItemFailure(
                        index=idx,
                        message=f"customer {item.customer_id} not found",
                    )
                )
                continue
            if cust.parent_id is None:
                failed.append(
                    PartBatchCreateItemFailure(
                        index=idx,
                        message="序列号只能分配给二级客户节点（一级集团不允许挂零件）",
                    )
                )
                continue
            parent = await get_parent(cust.parent_id)
            if parent is None:
                failed.append(
                    PartBatchCreateItemFailure(
                        index=idx,
                        message=f"parent customer {cust.parent_id} not found",
                    )
                )
                continue
            if get_code(parent.name) is None:
                failed.append(
                    PartBatchCreateItemFailure(
                        index=idx,
                        message=f"未配置一级客户「{parent.name}」的序列号代码",
                    )
                )
                continue

        if failed:
            return PartBatchCreateResult(created=[], failed=failed)

        # 全部校验通过；逐行写入，共用同一事务。
        # 任一 raise → session 上下文管理器回滚，batch 整体失败由 FastAPI 错误处理统一包装。
        created: list[PartOut] = []
        for item in payload.items:
            out = await self.create_part(item)
            created.append(out)
        return PartBatchCreateResult(created=created, failed=[])

    # ===== 通用状态变更（兼容 change-status 端点） =====
    async def change_status(
        self, part_id: int, payload: PartStatusChangeRequest
    ) -> PartOut:
        """通用状态变更。

        根据 from→to 自动选 `PartEventType`：
        - → COMPLETED    → COMPLETED
        - → CANCELLED    → CANCELLED
        - → REPAIRING    → REPAIR_STARTED
        - REPAIRING → IN_PROCESS → REPAIR_COMPLETED
        - 其他           → STATUS_CHANGED
        """
        part = await self.parts.get_by_id(part_id)
        if part is None:
            raise BizError(
                code=ErrCode.BIZ_PART_NOT_FOUND,
                message=f"part {part_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        from_status = _parse_status(part.status)
        to_status = _parse_status(payload.status)
        if to_status is None:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message="status is required",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        if to_status == from_status:
            # 同状态不写事件
            items = await self._to_out([part])
            return items[0]
        self._assert_transition(from_status, to_status)

        part.status = to_status.value
        if to_status in SERIAL_RELEASE_STATUSES:
            part.serial_no = None
        await self.parts.update(part)

        # 选 event_type
        if to_status == PartStatus.COMPLETED:
            event_type = PartEventType.COMPLETED
        elif to_status == PartStatus.CANCELLED:
            event_type = PartEventType.CANCELLED
        elif to_status == PartStatus.REPAIRING:
            event_type = PartEventType.REPAIR_STARTED
        elif from_status == PartStatus.REPAIRING and to_status == PartStatus.IN_PROCESS:
            event_type = PartEventType.REPAIR_COMPLETED
        else:
            event_type = PartEventType.STATUS_CHANGED

        await self._write_event(
            part=part,
            event_type=event_type,
            from_status=from_status,
            to_status=to_status,
        )
        await self._broadcast()
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
    # 报工流程动作
    # ============================================================
    async def release_to_floor(self, part_id: int) -> PartOut:
        """文员点击"开始生产"：PENDING → READY。"""
        part = await self.parts.get_by_id(part_id)
        if part is None:
            raise BizError(
                code=ErrCode.BIZ_PART_NOT_FOUND,
                message=f"part {part_id} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        from_status = _parse_status(part.status)
        to_status = PartStatus.READY
        self._assert_transition(from_status, to_status)
        part.status = to_status.value
        part.released_at = datetime.utcnow()
        await self.parts.update(part)
        await self._write_event(
            part=part,
            event_type=PartEventType.RELEASED,
            from_status=from_status,
            to_status=to_status,
        )
        await self._broadcast()
        items = await self._to_out([part])
        await self._broadcast_event(
            "RELEASED",
            self._banner_payload(part, customer_path=items[0].customer_path),
        )
        return items[0]

    async def pick_up_by_scan(self, data: PartPickUpRequest) -> PartOut:
        """工人扫 serial_no + 工牌：READY → IN_PROCESS，记录工人。"""
        part = await self.parts.get_by_serial(data.serial_no)
        if part is None:
            raise BizError(
                code=ErrCode.BIZ_PART_NOT_FOUND,
                message=f"part with serial_no {data.serial_no!r} not found",
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

        from_status = _parse_status(part.status)
        to_status = PartStatus.IN_PROCESS
        self._assert_transition(from_status, to_status)
        part.status = to_status.value
        part.current_worker_id = worker.id
        await self.parts.update(part)
        await self._write_event(
            part=part,
            event_type=PartEventType.PICKED_UP,
            from_status=from_status,
            to_status=to_status,
            worker_id=worker.id,
            drawing_code=data.serial_no,
            badge_code=data.badge_code,
        )
        await self._broadcast()
        items = await self._to_out([part])
        await self._broadcast_event(
            "PICKED_UP",
            self._banner_payload(
                part,
                customer_path=items[0].customer_path,
                worker_name=worker.name,
            ),
        )
        return items[0]

    async def scan_event(self, data: PartScanRequest) -> PartOut:
        """工人扫 serial_no（无工牌）触发归还 / 送检。"""
        part = await self.parts.get_by_serial(data.serial_no)
        if part is None:
            raise BizError(
                code=ErrCode.BIZ_PART_NOT_FOUND,
                message=f"part with serial_no {data.serial_no!r} not found",
                http_status=http_status.HTTP_404_NOT_FOUND,
            )
        event_type = _parse_event_type(data.event_type)
        from_status = _parse_status(part.status)

        if event_type == PartEventType.RETURNED:
            to_status = PartStatus.READY
            worker_id_for_event = part.current_worker_id
            drawing_code_for_event = data.serial_no
            badge_code_for_event: str | None = None
        elif event_type == PartEventType.INSPECTED:
            to_status = PartStatus.INSPECTION
            worker_id_for_event = part.current_worker_id
            drawing_code_for_event = data.serial_no
            badge_code_for_event = None
        else:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message=(
                    f"scan endpoint only accepts RETURNED or INSPECTED, "
                    f"got {event_type.value!r}"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )

        self._assert_transition(from_status, to_status)
        part.status = to_status.value
        part.current_worker_id = None
        await self.parts.update(part)
        await self._write_event(
            part=part,
            event_type=event_type,
            from_status=from_status,
            to_status=to_status,
            worker_id=worker_id_for_event,
            drawing_code=drawing_code_for_event,
            badge_code=badge_code_for_event,
        )
        await self._broadcast()
        items = await self._to_out([part])
        return items[0]

    # ============================================================
    # 内部：拼客户路径
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
        parent_map: dict[int, TCustomer] = {c.id: c for c in parents}

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
                )
            )
        return out