"""Part 状态机：管理 TPart.status 和 TPart.location 的转换。

扁平化设计（9 个顶级状态），ON_SHELF / WITH_WORKER 映射到同一个 DB status="IN_PROCESS"。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from statemachine import State, StateChart

from model.enums import PartEventType, PartStatus
from model.part_event import TPartEvent

if TYPE_CHECKING:
    from model.part import TPart


class PartStateMachine(StateChart):
    """TPart 状态机。

    Usage::

        part = await repo.get_by_id(part_id)
        sm = part.sm
        sm.place_on_shelf(shelf=shelf, event_repo=events)
        # part.status / part.location / part.current_holder_id 已设置
    """

    # ============================================================
    # States (9 flat states)
    # ============================================================

    PENDING = State("PENDING", initial=True, value="PENDING")
    PROGRAMMING = State("PROGRAMMING", value="PROGRAMMING")
    ON_SHELF = State("ON_SHELF", value="ON_SHELF")
    WITH_WORKER = State("WITH_WORKER", value="WITH_WORKER")
    INSPECTION = State("INSPECTION", value="INSPECTION")
    READY_TO_SHIP = State("READY_TO_SHIP", value="READY_TO_SHIP")
    DELIVERED = State("DELIVERED", value="DELIVERED")
    REPAIRING = State("REPAIRING", value="REPAIRING")
    COMPLETED = State("COMPLETED", value="COMPLETED", final=True)
    CANCELLED = State("CANCELLED", value="CANCELLED", final=True)

    # ============================================================
    # Transitions
    # ============================================================

    place_on_shelf = PENDING.to(ON_SHELF)
    send_to_programming = PENDING.to(PROGRAMMING)
    release_from_programming = PROGRAMMING.to(ON_SHELF)
    pick_up = ON_SHELF.to(WITH_WORKER)
    return_to_shelf = WITH_WORKER.to(ON_SHELF)
    inspect = WITH_WORKER.to(INSPECTION)
    pass_inspection = INSPECTION.to(READY_TO_SHIP)
    fail_inspection = INSPECTION.to(ON_SHELF)
    deliver = READY_TO_SHIP.to(DELIVERED)
    complete = DELIVERED.to(COMPLETED)
    start_repair = (
        INSPECTION.to(REPAIRING)
        | READY_TO_SHIP.to(REPAIRING)
        | DELIVERED.to(REPAIRING)
    )
    complete_repair = REPAIRING.to(ON_SHELF)
    cancel = (
        PENDING.to(CANCELLED)
        | PROGRAMMING.to(CANCELLED)
        | ON_SHELF.to(CANCELLED)
        | WITH_WORKER.to(CANCELLED)
        | INSPECTION.to(CANCELLED)
        | READY_TO_SHIP.to(CANCELLED)
        | DELIVERED.to(CANCELLED)
        | REPAIRING.to(CANCELLED)
    )

    # ============================================================
    # Init — restore state from model.status + model.location
    # ============================================================

    def __init__(self, model: TPart | None = None, **kwargs):
        start_value = kwargs.pop("start_value", None)
        if model is not None and start_value is None:
            status = getattr(model, "status", None)
            location = getattr(model, "location", None)
            if status == "IN_PROCESS":
                start_value = "WITH_WORKER" if location == "WORKER" else "ON_SHELF"
            elif status == "PENDING":
                start_value = "PENDING"
            elif status == "PROGRAMMING":
                start_value = "PROGRAMMING"
            else:
                start_value = status  # INSPECTION, READY_TO_SHIP, etc.
        super().__init__(
            model=model, start_value=start_value, **kwargs,
        )

    # ============================================================
    # before_transition — capture old status
    # ============================================================

    def before_transition(self, **_):
        self._from_status: str | None = (
            self.model.status if self.model else None
        )

    # ============================================================
    # State entry callbacks — sync model fields
    # ============================================================

    def on_enter_PENDING(self, **_):
        if self.model:
            self.model.status = "PENDING"
            self.model.location = "OFFICE"
            self.model.current_holder_id = None

    def on_enter_PROGRAMMING(self, **_):
        if self.model:
            self.model.status = "PROGRAMMING"
            # 编程中不占货架，逻辑上仍在办公室 / 编程员处。
            self.model.location = "OFFICE"
            self.model.current_holder_id = None

    def on_enter_ON_SHELF(self, shelf=None, process=None, **_):
        if self.model:
            self.model.status = "IN_PROCESS"
            self.model.location = "PRODUCTION_SHELF"
            if shelf:
                self.model.current_holder_id = shelf.id
            # next_process_id 由调用方 service 喂入（保持状态机零 DB 依赖）。
            # place_on_shelf 时设；RETURNED 时由工人新选。
            if process is not None:
                self.model.next_process_id = process.id
            from datetime import datetime as _dt
            self.model.placed_at = _dt.utcnow()

    def on_enter_WITH_WORKER(self, worker=None, **_):
        if self.model:
            self.model.status = "IN_PROCESS"
            self.model.location = "WORKER"
            if worker:
                self.model.current_holder_id = worker.id

    def on_enter_INSPECTION(self, target_shelf=None, **_):
        if self.model:
            self.model.status = "INSPECTION"
            self.model.location = "INSPECTION_SHELF"
            if target_shelf:
                self.model.current_holder_id = target_shelf.id

    def on_enter_READY_TO_SHIP(self, **_):
        if self.model:
            self.model.status = "READY_TO_SHIP"
            self.model.location = None

    def on_enter_DELIVERED(self, **_):
        if self.model:
            self.model.status = "DELIVERED"
            self.model.location = None

    def on_enter_REPAIRING(self, **_):
        if self.model:
            self.model.status = "REPAIRING"
            self.model.location = None
            self.model.current_holder_id = None

    def on_enter_COMPLETED(self, **_):
        if self.model:
            self.model.status = "COMPLETED"
            self.model.location = None
            self.model.serial_no = None

    def on_enter_CANCELLED(self, **_):
        if self.model:
            self.model.status = "CANCELLED"
            self.model.location = None
            self.model.serial_no = None

    # ============================================================
    # Event callbacks — create PartEvent records
    # ============================================================

    def on_place_on_shelf(self, shelf=None, process=None, event_repo=None, **_):
        if event_repo and self.model:
            shelf_code = shelf.code if shelf and hasattr(shelf, "code") else None
            process_code = process.code if process and hasattr(process, "code") else None
            note_parts = []
            if shelf_code:
                note_parts.append(f"shelf={shelf_code}")
            if process_code:
                note_parts.append(f"next_process={process_code}")
            event_repo.add(TPartEvent(
                part_id=self.model.id,
                event_type=PartEventType.PLACED_ON_SHELF,
                from_status=PartStatus.PENDING,
                to_status=PartStatus.IN_PROCESS,
                note="; ".join(note_parts) or None,
            ))

    def on_send_to_programming(self, event_repo=None, **_):
        if event_repo and self.model:
            event_repo.add(TPartEvent(
                part_id=self.model.id,
                event_type=PartEventType.SENT_TO_PROGRAMMING,
                from_status=PartStatus.PENDING,
                to_status=PartStatus.PROGRAMMING,
            ))

    def on_release_from_programming(self, shelf=None, process=None, event_repo=None, **_):
        if event_repo and self.model:
            shelf_code = shelf.code if shelf and hasattr(shelf, "code") else None
            process_code = process.code if process and hasattr(process, "code") else None
            note_parts = []
            if shelf_code:
                note_parts.append(f"shelf={shelf_code}")
            if process_code:
                note_parts.append(f"next_process={process_code}")
            event_repo.add(TPartEvent(
                part_id=self.model.id,
                event_type=PartEventType.CNC_RELEASED,
                from_status=PartStatus.PROGRAMMING,
                to_status=PartStatus.IN_PROCESS,
                note="; ".join(note_parts) or None,
            ))

    def on_pick_up(self, worker=None, shelf=None, event_repo=None, **_):
        if event_repo and self.model and worker:
            event_repo.add(TPartEvent(
                part_id=self.model.id,
                event_type=PartEventType.PICKED_UP,
                from_status=PartStatus.IN_PROCESS,
                to_status=PartStatus.IN_PROCESS,
                worker_id=worker.id,
                drawing_code=self.model.serial_no,
                badge_code=worker.badge_code if hasattr(worker, "badge_code") else None,
            ))

    def on_return_to_shelf(
        self,
        worker=None,
        shelf=None,
        process=None,
        prev_process_code: str | None = None,
        worker_work_type_code: str | None = None,
        event_repo=None,
        **_,
    ):
        """RETURNED：把当前由工人持有的零件放回货架。

        接收 service 喂入的：
        - process: TProcess（工人新选的下一道工序；可空）
        - prev_process_code: str（零件改前的 next_process 的 code；可能 None）
        - worker_work_type_code: str（工人的工种 code；用于 note 记录）

        note 格式: "from=<prev_code>; to=<new_code>; by=<work_type_code>"
        """
        if event_repo and self.model:
            process_code = process.code if process and hasattr(process, "code") else None
            note = (
                f"from={prev_process_code or 'none'}; "
                f"to={process_code or 'none'}; "
                f"by={worker_work_type_code or 'none'}"
            )
            event_repo.add(TPartEvent(
                part_id=self.model.id,
                event_type=PartEventType.RETURNED,
                from_status=PartStatus.IN_PROCESS,
                to_status=PartStatus.IN_PROCESS,
                worker_id=worker.id if worker else None,
                drawing_code=self.model.serial_no,
                badge_code=worker.badge_code if worker and hasattr(worker, "badge_code") else None,
                note=note,
            ))

    def on_inspect(self, worker=None, target_shelf=None, event_repo=None, **_):
        if event_repo and self.model:
            shelf_code = target_shelf.code if target_shelf and hasattr(target_shelf, "code") else ""
            event_repo.add(TPartEvent(
                part_id=self.model.id,
                event_type=PartEventType.INSPECTED,
                from_status=PartStatus.IN_PROCESS,
                to_status=PartStatus.INSPECTION,
                worker_id=worker.id if worker else None,
                drawing_code=self.model.serial_no,
                badge_code=worker.badge_code if worker and hasattr(worker, "badge_code") else None,
                note=f"to inspection shelf {shelf_code}",
            ))

    def on_pass_inspection(self, event_repo=None, **_):
        if event_repo and self.model:
            event_repo.add(TPartEvent(
                part_id=self.model.id,
                event_type=PartEventType.STATUS_CHANGED,
                from_status=PartStatus.INSPECTION,
                to_status=PartStatus.READY_TO_SHIP,
            ))

    def on_fail_inspection(self, shelf=None, event_repo=None, **_):
        """品检不通过：打回生产货架（INSPECTION → ON_SHELF）。

        与 on_complete_repair 区别：本路径不经过 REPAIRING 状态，直接回到 ON_SHELF
        （DB status='IN_PROCESS', location='PRODUCTION_SHELF'）；next_process_id
        由 service 在调本方法前清空，文员重新下发时再选工序。
        """
        if event_repo and self.model:
            shelf_code = shelf.code if shelf and hasattr(shelf, "code") else ""
            event_repo.add(TPartEvent(
                part_id=self.model.id,
                event_type=PartEventType.INSPECTION_FAILED,
                from_status=PartStatus.INSPECTION,
                to_status=PartStatus.IN_PROCESS,
                note=f"back to shelf {shelf_code}" if shelf_code else None,
            ))

    def on_deliver(self, event_repo=None, **_):
        if event_repo and self.model:
            event_repo.add(TPartEvent(
                part_id=self.model.id,
                event_type=PartEventType.STATUS_CHANGED,
                from_status=PartStatus.READY_TO_SHIP,
                to_status=PartStatus.DELIVERED,
            ))

    def on_complete(self, event_repo=None, **_):
        if event_repo and self.model:
            event_repo.add(TPartEvent(
                part_id=self.model.id,
                event_type=PartEventType.COMPLETED,
                from_status=PartStatus.DELIVERED,
                to_status=PartStatus.COMPLETED,
            ))

    def on_start_repair(self, event_repo=None, **_):
        if event_repo and self.model:
            from_status = PartStatus(self._from_status) if self._from_status else None
            event_repo.add(TPartEvent(
                part_id=self.model.id,
                event_type=PartEventType.REPAIR_STARTED,
                from_status=from_status,
                to_status=PartStatus.REPAIRING,
            ))

    def on_complete_repair(self, event_repo=None, **_):
        if event_repo and self.model:
            event_repo.add(TPartEvent(
                part_id=self.model.id,
                event_type=PartEventType.REPAIR_COMPLETED,
                from_status=PartStatus.REPAIRING,
                to_status=PartStatus.IN_PROCESS,
            ))

    def on_cancel(self, event_repo=None, **_):
        if event_repo and self.model:
            from_status = PartStatus(self._from_status) if self._from_status else None
            event_repo.add(TPartEvent(
                part_id=self.model.id,
                event_type=PartEventType.CANCELLED,
                from_status=from_status,
                to_status=PartStatus.CANCELLED,
            ))
