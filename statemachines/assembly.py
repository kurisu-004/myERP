"""Assembly 状态机：管理 TAssembly.status 的转换。

2026-08-03 扩展：跟随子件派生态（INSPECTION / READY_TO_SHIP / DELIVERED）。

正向流转由 `PartService._check_parent_assembly` /
`DeliveryNoteService.pickup` 通过 `service/_assembly_rollup.recompute_assembly_status`
触发；该 helper 调用本 SM 的 `recompute(target)` 方法，可任意方向迁移（包含
子件回退导致的父件 backward regression）。
显式动作（`AssemblyService.cancel_assembly`）仍走 named transition `cancel`。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from statemachine import State, StateChart

from core.error_code import ErrCode
from core.exception import BizError
from fastapi import status as http_status
from model.enums import AssemblyStatus

if TYPE_CHECKING:
    from model.assembly import TAssembly


class AssemblyStateMachine(StateChart):
    """TAssembly 状态机（2026-08-03 7 态扩展）。

    Usage::

        assembly = await repo.get_by_id(assembly_id)
        sm = assembly.sm
        sm.start_production(...)  # auto: first child enters production
        sm.enter_inspection(...)  # auto: least-progressed child reaches INSPECTION
        sm.ready_to_ship(...)     # auto: ... READY_TO_SHIP
        sm.deliver(...)           # auto: ... DELIVERED
        sm.complete(...)          # auto: all non-cancelled children COMPLETED
        sm.cancel(...)            # explicit: cancel assembly + cascade
        sm.recompute("INSPECTION")  # rollup path: any-to-any（含回退）
    """

    # ============================================================
    # States (7)
    # ============================================================
    PENDING       = State("PENDING",       initial=True, value="PENDING")
    IN_PROCESS    = State("IN_PROCESS",    value="IN_PROCESS")
    INSPECTION    = State("INSPECTION",    value="INSPECTION")
    READY_TO_SHIP = State("READY_TO_SHIP", value="READY_TO_SHIP")
    DELIVERED     = State("DELIVERED",     value="DELIVERED")
    COMPLETED     = State("COMPLETED",     value="COMPLETED", final=True)
    CANCELLED     = State("CANCELLED",     value="CANCELLED", final=True)

    # ============================================================
    # Named forward transitions（文档/契约；rollup 路径走 recompute）
    # ============================================================
    start_production = PENDING.to(IN_PROCESS)
    enter_inspection = IN_PROCESS.to(INSPECTION)
    ready_to_ship    = INSPECTION.to(READY_TO_SHIP)
    deliver          = READY_TO_SHIP.to(DELIVERED)
    complete         = DELIVERED.to(COMPLETED)

    cancel = (
        PENDING.to(CANCELLED)
        | IN_PROCESS.to(CANCELLED)
        | INSPECTION.to(CANCELLED)
        | READY_TO_SHIP.to(CANCELLED)
        | DELIVERED.to(CANCELLED)
    )

    # ============================================================
    # Init — restore state from model
    # ============================================================
    def __init__(self, model: "TAssembly | None" = None, **kwargs):
        start_value = kwargs.pop("start_value", None)
        if model is not None and start_value is None:
            start_value = getattr(model, "status", None)
        super().__init__(model=model, state_field="status",
                         start_value=start_value, **kwargs)

    # ============================================================
    # State entry callbacks — sync model.status
    # ============================================================
    def on_enter_PENDING(self, **_):
        if self.model:
            self.model.status = "PENDING"

    def on_enter_IN_PROCESS(self, **_):
        if self.model:
            self.model.status = "IN_PROCESS"

    def on_enter_INSPECTION(self, **_):
        if self.model:
            self.model.status = "INSPECTION"

    def on_enter_READY_TO_SHIP(self, **_):
        if self.model:
            self.model.status = "READY_TO_SHIP"

    def on_enter_DELIVERED(self, **_):
        if self.model:
            self.model.status = "DELIVERED"

    def on_enter_COMPLETED(self, **_):
        if self.model:
            self.model.status = "COMPLETED"

    def on_enter_CANCELLED(self, **_):
        if self.model:
            self.model.status = "CANCELLED"

    # ============================================================
    # Rollup helper — any-to-any transition, including backward
    # regression. Called from service/_assembly_rollup.py.
    # ============================================================
    def recompute(self, target_status: str) -> bool:
        """Rollup 路径：直接写装配件到 ``target_status``（任意方向，含回退）。

        返回 ``True`` 表示状态实际改变；``False`` 表示 no-op 或被终态短路。

        行为约定：
        - target 不在 AssemblyStatus 合法集合 → ``BIZ_INVALID_VALUE 400``
        - target == current_state → no-op（返回 False）
        - current_state ∈ {COMPLETED, CANCELLED} → 终态短路（返回 False）
        - 其他情况：直接跳到 target（触发 ``on_enter_<target>`` 回写 model.status）
        """
        if not isinstance(target_status, str):
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message=f"invalid assembly status for rollup: {target_status!r}",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        target = target_status.strip().upper()
        valid = {s.value for s in AssemblyStatus}
        if target not in valid:
            raise BizError(
                code=ErrCode.BIZ_INVALID_VALUE,
                message=f"invalid assembly status for rollup: {target_status!r}",
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )
        current = self.current_state.value
        if current == target:
            return False
        if current in {"COMPLETED", "CANCELLED"}:
            # 终态不接受 rollup 写入；调用方应已过滤（防御性）
            return False
        # python-statemachine 2.x：current_state 是可设置的 property；
        # 直接赋值触发 on_enter_<target> 回写 model.status，跳过显式转移图。
        self.current_state = getattr(self, target)
        return True
