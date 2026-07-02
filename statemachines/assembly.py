"""Assembly 状态机：管理 TAssembly.status 的转换。

4 态：PENDING → IN_PROCESS → COMPLETED，可从 PENDING/IN_PROCESS → CANCELLED。
IN_PROCESS/COMPLETED 由 Part 状态变更自动触发（非用户显式操作）。
CANCELLED 由显式 cancel 端点触发。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from statemachine import State, StateChart

from model.enums import AssemblyStatus

if TYPE_CHECKING:
    from model.assembly import TAssembly


class AssemblyStateMachine(StateChart):
    """TAssembly 状态机。

    Usage::

        assembly = await repo.get_by_id(assembly_id)
        sm = assembly.sm
        sm.start_production(...)  # auto: first child enters production
        sm.complete(...)          # auto: all children completed
        sm.cancel(...)            # explicit: cancel assembly + cascade
    """

    PENDING = State("PENDING", initial=True, value="PENDING")
    IN_PROCESS = State("IN_PROCESS", value="IN_PROCESS")
    COMPLETED = State("COMPLETED", value="COMPLETED", final=True)
    CANCELLED = State("CANCELLED", value="CANCELLED", final=True)

    start_production = PENDING.to(IN_PROCESS)
    complete = IN_PROCESS.to(COMPLETED)
    cancel = (
        PENDING.to(CANCELLED)
        | IN_PROCESS.to(CANCELLED)
    )

    # ============================================================
    # Init — restore state from model
    # ============================================================

    def __init__(self, model: TAssembly | None = None, **kwargs):
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

    def on_enter_COMPLETED(self, **_):
        if self.model:
            self.model.status = "COMPLETED"

    def on_enter_CANCELLED(self, **_):
        if self.model:
            self.model.status = "CANCELLED"
