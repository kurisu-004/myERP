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
    OUTSOURCE = State("OUTSOURCE", value="OUTSOURCE")   # 2026-07-15 新增：外协公司
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

    # 2026-07-15 外协流程：
    # - send_to_outsource：PENDING / ON_SHELF / WITH_WORKER → OUTSOURCE
    #   （文员可把待生产或工人加工几道后的零件送给外协）
    # - receive_from_outsource：OUTSOURCE → ON_SHELF
    #   （外协回收，下发回生产货架继续加工）
    send_to_outsource = (
        PENDING.to(OUTSOURCE)
        | ON_SHELF.to(OUTSOURCE)
        | WITH_WORKER.to(OUTSOURCE)
    )
    receive_from_outsource = OUTSOURCE.to(ON_SHELF)
    # 2026-07-16：外协回收「直接进品检」分支（跳过生产货架）。
    inspect_from_outsource = OUTSOURCE.to(INSPECTION)

    cancel = (
        PENDING.to(CANCELLED)
        | PROGRAMMING.to(CANCELLED)
        | ON_SHELF.to(CANCELLED)
        | WITH_WORKER.to(CANCELLED)
        | INSPECTION.to(CANCELLED)
        | READY_TO_SHIP.to(CANCELLED)
        | DELIVERED.to(CANCELLED)
        | REPAIRING.to(CANCELLED)
        | OUTSOURCE.to(CANCELLED)   # 2026-07-15 新增
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
                start_value = status  # INSPECTION, READY_TO_SHIP, OUTSOURCE, etc.
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
            from core.time import now_naive
            self.model.placed_at = now_naive()

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

    def on_enter_OUTSOURCE(self, outsource_company=None, process=None, **_):
        """进入 OUTSOURCE：DB status='OUTSOURCE', location='OUTSOURCE_COMPANY'，
        holder 指向外协公司 id；next_process_id 保持（就是外协工序 id）。
        """
        if self.model:
            self.model.status = "OUTSOURCE"
            self.model.location = "OUTSOURCE_COMPANY"
            if outsource_company is not None:
                self.model.current_holder_id = outsource_company.id
            if process is not None:
                self.model.next_process_id = process.id
            # placed_at 保留陈旧值（前端展示时自行处理语义）；
            # 不在这里清空是为了保留「首次进入流程的时间」参考。

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

    def on_place_on_shelf(
        self,
        shelf=None,
        process=None,
        event_repo=None,
        *,
        created_by: int | None = None,
        **_,
    ):
        if event_repo and self.model:
            shelf_code = shelf.code if shelf and hasattr(shelf, "code") else None
            process_code = process.code if process and hasattr(process, "code") else None
            note_parts = []
            if shelf_code:
                note_parts.append(f"下发货架：{shelf_code}")
            if process_code:
                note_parts.append(f"下一工序：{process_code}")
            event_repo.add(TPartEvent(
                part_id=self.model.id,
                event_type=PartEventType.PLACED_ON_SHELF,
                from_status=PartStatus.PENDING,
                to_status=PartStatus.IN_PROCESS,
                note=" ".join(note_parts) or None,
                created_by=created_by,
            ))

    def on_send_to_programming(
        self,
        event_repo=None,
        *,
        created_by: int | None = None,
        **_,
    ):
        if event_repo and self.model:
            event_repo.add(TPartEvent(
                part_id=self.model.id,
                event_type=PartEventType.SENT_TO_PROGRAMMING,
                from_status=PartStatus.PENDING,
                to_status=PartStatus.PROGRAMMING,
                created_by=created_by,
            ))

    def on_release_from_programming(
        self,
        shelf=None,
        process=None,
        event_repo=None,
        *,
        created_by: int | None = None,
        **_,
    ):
        if event_repo and self.model:
            shelf_code = shelf.code if shelf and hasattr(shelf, "code") else None
            process_code = process.code if process and hasattr(process, "code") else None
            note_parts = []
            if shelf_code:
                note_parts.append(f"下发货架：{shelf_code}")
            if process_code:
                note_parts.append(f"下一工序：{process_code}")
            event_repo.add(TPartEvent(
                part_id=self.model.id,
                event_type=PartEventType.CNC_RELEASED,
                from_status=PartStatus.PROGRAMMING,
                to_status=PartStatus.IN_PROCESS,
                note=" ".join(note_parts) or None,
                created_by=created_by,
            ))

    def on_pick_up(
        self,
        worker=None,
        shelf=None,
        event_repo=None,
        *,
        created_by: int | None = None,
        **_,
    ):
        if event_repo and self.model and worker:
            event_repo.add(TPartEvent(
                part_id=self.model.id,
                event_type=PartEventType.PICKED_UP,
                from_status=PartStatus.IN_PROCESS,
                to_status=PartStatus.IN_PROCESS,
                worker_id=worker.id,
                drawing_code=self.model.serial_no,
                badge_code=worker.badge_code if hasattr(worker, "badge_code") else None,
                created_by=created_by,
            ))

    def on_return_to_shelf(
        self,
        worker=None,
        shelf=None,
        process=None,
        prev_process_code: str | None = None,
        worker_work_type_code: str | None = None,
        event_repo=None,
        *,
        created_by: int | None = None,
        **_,
    ):
        """RETURNED：把当前由工人持有的零件放回货架。

        接收 service 喂入的：
        - shelf: TShelf（工人选定的目标架；note 用 shelf.code）
        - process: TProcess（工人新选的下一道工序；note 用 process.code）

        note 格式（2026-07-17 统一）：「归还货架 <shelf_code> 下一工序 <process_code>」。
        缺值时用「无」兜底，便于历史事件保留可读。
        """
        if event_repo and self.model:
            process_code = process.code if process and hasattr(process, "code") else None
            shelf_code = shelf.code if shelf and hasattr(shelf, "code") else None
            note = (
                f"归还货架 {shelf_code or '无'} "
                f"下一工序 {process_code or '无'}"
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
                created_by=created_by,
            ))

    def on_inspect(
        self,
        worker=None,
        target_shelf=None,
        event_repo=None,
        *,
        created_by: int | None = None,
        **_,
    ):
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
                note=f"送检到货架：{shelf_code}" if shelf_code else None,
                created_by=created_by,
            ))

    def on_pass_inspection(
        self,
        event_repo=None,
        *,
        created_by: int | None = None,
        **_,
    ):
        if event_repo and self.model:
            event_repo.add(TPartEvent(
                part_id=self.model.id,
                event_type=PartEventType.STATUS_CHANGED,
                from_status=PartStatus.INSPECTION,
                to_status=PartStatus.READY_TO_SHIP,
                created_by=created_by,
            ))

    def on_fail_inspection(
        self,
        shelf=None,
        process=None,
        event_repo=None,
        *,
        note: str | None = None,
        created_by: int | None = None,
        **_,
    ):
        """品检不通过：打回生产货架（INSPECTION → ON_SHELF）。

        2026-07-21 改：接受 `process` 与可选 `note`（品检员填的不合格原因）。
        next_process_id 由 service 在调本方法**前**写入
        （不再清空；保留 inspector 指定的下一道工序）。
        `note` 拼接为 `"打回到货架：<code> 下一工序：<code> | 备注：<note>"`，
        受 `t_part_event.note` String(500) 上限保护；旧事件不带 ` | 备注：`
        部分也能正确显示。
        """
        if event_repo and self.model:
            shelf_code = shelf.code if shelf and hasattr(shelf, "code") else ""
            process_code = process.code if process and hasattr(process, "code") else ""
            base = (
                f"打回到货架：{shelf_code} 下一工序：{process_code}".strip()
                or "打回到原货架"
            )
            if note:
                final_note = (f"{base} | 备注：{note}")[:500]
            else:
                final_note = base
            event_repo.add(TPartEvent(
                part_id=self.model.id,
                event_type=PartEventType.INSPECTION_FAILED,
                from_status=PartStatus.INSPECTION,
                to_status=PartStatus.IN_PROCESS,
                note=final_note,
                created_by=created_by,
            ))

    def on_deliver(
        self,
        worker=None,
        event_repo=None,
        *,
        created_by: int | None = None,
        **_,
    ):
        """READY_TO_SHIP → DELIVERED：发货（可由送货司机扫码触发）。

        接收 worker 入参：扫码台调用时由 service 喂入送货司机的 TWorker，
        把 worker_id/badge_code 写入 PartEvent 便于审计追溯；文员手动调用时
        worker=None，仍走通用 STATUS_CHANGED 事件。
        """
        if event_repo and self.model:
            event_repo.add(TPartEvent(
                part_id=self.model.id,
                event_type=PartEventType.STATUS_CHANGED,
                from_status=PartStatus.READY_TO_SHIP,
                to_status=PartStatus.DELIVERED,
                worker_id=worker.id if worker else None,
                drawing_code=self.model.serial_no,
                badge_code=worker.badge_code if worker and hasattr(worker, "badge_code") else None,
                created_by=created_by,
            ))

    def on_complete(
        self,
        event_repo=None,
        *,
        created_by: int | None = None,
        **_,
    ):
        if event_repo and self.model:
            event_repo.add(TPartEvent(
                part_id=self.model.id,
                event_type=PartEventType.COMPLETED,
                from_status=PartStatus.DELIVERED,
                to_status=PartStatus.COMPLETED,
                created_by=created_by,
            ))

    def on_start_repair(
        self,
        event_repo=None,
        *,
        created_by: int | None = None,
        **_,
    ):
        if event_repo and self.model:
            from_status = PartStatus(self._from_status) if self._from_status else None
            event_repo.add(TPartEvent(
                part_id=self.model.id,
                event_type=PartEventType.REPAIR_STARTED,
                from_status=from_status,
                to_status=PartStatus.REPAIRING,
                created_by=created_by,
            ))

    def on_complete_repair(
        self,
        event_repo=None,
        *,
        created_by: int | None = None,
        **_,
    ):
        if event_repo and self.model:
            event_repo.add(TPartEvent(
                part_id=self.model.id,
                event_type=PartEventType.REPAIR_COMPLETED,
                from_status=PartStatus.REPAIRING,
                to_status=PartStatus.IN_PROCESS,
                created_by=created_by,
            ))

    def on_cancel(
        self,
        event_repo=None,
        *,
        created_by: int | None = None,
        **_,
    ):
        if event_repo and self.model:
            from_status = PartStatus(self._from_status) if self._from_status else None
            event_repo.add(TPartEvent(
                part_id=self.model.id,
                event_type=PartEventType.CANCELLED,
                from_status=from_status,
                to_status=PartStatus.CANCELLED,
                created_by=created_by,
            ))

    def on_send_to_outsource(
        self,
        outsource_company=None,
        process=None,
        event_repo=None,
        *,
        created_by: int | None = None,
        direct_send: bool = False,
        outsource_company_id: int | None = None,
        **_,
    ):
        """→ OUTSOURCE：文员把零件发送给外协公司。

        note 模板: "外协公司：{name} 外协工序：{code}"（直接发送时追加 " / 直接发送（无需审批）"）

        `direct_send=True` 由 service/part.py 在 process.requires_approval=False 时传入，
        用于事件 note 区分「免审批直发」vs「正常审批流」（2026-07-28 新增）。

        `outsource_company_id`（2026-07-28 新增）：外协对账审计字段，写入
        TPartEvent.outsource_company_id 供后续按公司聚合发送事件；不传则从
        outsource_company.id 取值。
        """
        if event_repo and self.model:
            parts = []
            if outsource_company is not None and hasattr(outsource_company, "name"):
                parts.append(f"外协公司：{outsource_company.name}")
            if process is not None and hasattr(process, "code"):
                parts.append(f"外协工序：{process.code}")
            if direct_send:
                parts.append("/ 直接发送（无需审批）")
            from_status = (
                PartStatus(self._from_status) if self._from_status else None
            )
            event_repo.add(TPartEvent(
                part_id=self.model.id,
                event_type=PartEventType.SENT_TO_OUTSOURCE,
                from_status=from_status,
                to_status=PartStatus.OUTSOURCE,
                note=" ".join(parts) or None,
                created_by=created_by,
                outsource_company_id=(
                    outsource_company_id
                    if outsource_company_id is not None
                    else (outsource_company.id if outsource_company is not None else None)
                ),
            ))

    def on_receive_from_outsource(
        self,
        shelf=None,
        process=None,
        event_repo=None,
        *,
        created_by: int | None = None,
        outsource_company_id: int | None = None,
        **_,
    ):
        """OUTSOURCE → IN_PROCESS：外协回收，下发到生产货架。

        落到 ON_SHELF 状态（location=PRODUCTION_SHELF, holder=shelf.id, placed_at=now），
        由 on_enter_ON_SHELF 设置；本回调只写事件。

        note 模板: "外协回收 下发货架：{shelf} 下一工序：{process}"

        `outsource_company_id`（2026-07-28 新增）：外协对账审计字段，写入
        TPartEvent.outsource_company_id 标识从哪家公司回收；由 service 层从
        `part_event` 历史中查询最近一次 SENT_TO_OUTSOURCE 的公司 id 后传入。
        """
        if event_repo and self.model:
            parts = ["外协回收"]
            if shelf is not None and hasattr(shelf, "code"):
                parts.append(f"下发货架：{shelf.code}")
            if process is not None and hasattr(process, "code"):
                parts.append(f"下一工序：{process.code}")
            event_repo.add(TPartEvent(
                part_id=self.model.id,
                event_type=PartEventType.RECEIVED_FROM_OUTSOURCE,
                from_status=PartStatus.OUTSOURCE,
                to_status=PartStatus.IN_PROCESS,
                note=" ".join(parts),
                created_by=created_by,
                outsource_company_id=outsource_company_id,
            ))

    def on_inspect_from_outsource(
        self,
        target_shelf=None,
        event_repo=None,
        *,
        created_by: int | None = None,
        outsource_company_id: int | None = None,
        **_,
    ):
        """2026-07-16：OUTSOURCE → INSPECTION：外协件直接送检（跳过生产货架）。

        shelf/process 由 on_enter_INSPECTION 设置（location=INSPECTION_SHELF,
        holder=shelf.id）；本回调只写事件。

        note 模板: "外协回收送检：{shelf}"

        2026-07-28：`outsource_company_id` 透传到 TPartEvent.outsource_company_id
        用于外协对账（INSPECTED 事件 + from_status=OUTSOURCE 时标识从哪家公司送来）。
        """
        if event_repo and self.model:
            shelf_code = (
                target_shelf.code
                if target_shelf and hasattr(target_shelf, "code") else ""
            )
            event_repo.add(TPartEvent(
                part_id=self.model.id,
                # t_part_event.event_type is VARCHAR(30); reuse the existing
                # inspection event and keep the outsource origin in note/from_status.
                event_type=PartEventType.INSPECTED,
                from_status=PartStatus.OUTSOURCE,
                to_status=PartStatus.INSPECTION,
                note=f"外协回收送检：{shelf_code}" if shelf_code else "外协回收送检",
                created_by=created_by,
                outsource_company_id=outsource_company_id,
            ))
