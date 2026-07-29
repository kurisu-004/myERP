"""外协报价单（OutsourceQuote）状态机（2026-07-16 新增；2026-07-30 重构）。

2026-07-30 重构：报价回归纯审批对象。
- 保留 DRAFT / SUBMITTED / APPROVED / REJECTED 流转。
- 删除 mark_outsourcing / mark_received / mark_billed 转换及对应 on_* 回调。
- 状态类定义（OUTSOURCING / RECEIVED / BILLED / USED）保留以兼容存量行。
- 事件枚举 OutsourceQuoteEventType 的历史值保留不动。

形态对齐 Part 状态机（statemachines/part.py）：
- `model` 由 `__init__(model=quote)` 注入；`start_value` 从 quote.status 恢复。
- 状态转换通过 callbacks 写 event_repo（同步 add，不 flush）。
- 不在状态机内部查 DB；service 层校验 target 存在 / 业务规则。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from statemachine import State, StateChart

from core.time import now_naive
from model.enums import OutsourceQuoteEventType, OutsourceQuoteStatus
from model.outsource_quote_event import TOutsourceQuoteEvent

if TYPE_CHECKING:
    from model.outsource_quote import TOutsourceQuote


class OutsourceQuoteStateMachine(StateChart):
    """外协报价单状态机（2026-07-30 重构）。

    Usage::

        quote = await repo.get_by_id(quote_id)
        sm = quote.sm
        sm.submit(event_repo=events, created_by=user_id)
        # quote.status / quote.submitted_at 已设置

    自动从 `model.status` 恢复当前状态。

    流转：
        DRAFT ─submit→ SUBMITTED ─approve→ APPROVED
                                              ╰─reject→ REJECTED（终态）

    旧 mark_outsourcing / mark_received / mark_billed / mark_used 转换已删除；
    状态类 OUTSOURCING / RECEIVED / BILLED / USED 保留兼容存量行。
    """

    # ============================================================
    # States
    # ============================================================

    DRAFT = State("DRAFT", initial=True, value="DRAFT")
    SUBMITTED = State("SUBMITTED", value="SUBMITTED")
    APPROVED = State("APPROVED", value="APPROVED")
    REJECTED = State("REJECTED", value="REJECTED", final=True)
    # 2026-07-30：状态类保留兼容存量行，但新流程不再产生这些状态
    OUTSOURCING = State("OUTSOURCING", value="OUTSOURCING")
    RECEIVED = State("RECEIVED", value="RECEIVED")
    BILLED = State("BILLED", value="BILLED", final=True)
    USED = State("USED", value="USED", final=True)

    # ============================================================
    # Transitions
    # ============================================================

    submit = DRAFT.to(SUBMITTED)
    approve = SUBMITTED.to(APPROVED)
    reject = SUBMITTED.to(REJECTED)
    # 2026-07-30：mark_outsourcing / mark_received / mark_billed / mark_used 的 on_* 回调已删除，
    # 新流程不再调用这些转换。但因 python-statemachine 库要求「所有状态可达 + 非终态必须有出向
    # 转换」，保留以下空转换以满足库约束；调用它们不会触发副作用（事件写入等）。
    mark_outsourcing = APPROVED.to(OUTSOURCING)
    mark_received = OUTSOURCING.to(RECEIVED)
    mark_billed = RECEIVED.to(BILLED)
    mark_used = APPROVED.to(USED)

    # ============================================================
    # Init — restore from model.status
    # ============================================================

    def __init__(self, model: "TOutsourceQuote | None" = None, **kwargs):
        start_value = kwargs.pop("start_value", None)
        if model is not None and start_value is None:
            status = getattr(model, "status", None)
            if status:
                start_value = status
        super().__init__(
            model=model, start_value=start_value, **kwargs,
        )

    # ============================================================
    # State-entry hooks — sync model fields
    # ============================================================

    def on_enter_DRAFT(self, **_):
        if self.model:
            self.model.status = "DRAFT"

    def on_enter_SUBMITTED(self, **_):
        if self.model:
            self.model.status = "SUBMITTED"
            self.model.submitted_at = now_naive()

    def on_enter_APPROVED(self, review_note=None, **_):
        if self.model:
            self.model.status = "APPROVED"
            self.model.reviewed_at = now_naive()
            if review_note is not None:
                self.model.review_note = review_note

    def on_enter_REJECTED(self, review_note=None, **_):
        if self.model:
            self.model.status = "REJECTED"
            self.model.reviewed_at = now_naive()
            if review_note is not None:
                self.model.review_note = review_note

    def on_enter_OUTSOURCING(self, **_):
        # 2026-07-30：兼容存量行
        if self.model:
            self.model.status = "OUTSOURCING"

    def on_enter_RECEIVED(self, **_):
        # 2026-07-30：兼容存量行
        if self.model:
            self.model.status = "RECEIVED"

    def on_enter_BILLED(self, **_):
        if self.model:
            self.model.status = "BILLED"

    def on_enter_USED(self, **_):
        if self.model:
            self.model.status = "USED"

    # ============================================================
    # Event callbacks — append TOutsourceQuoteEvent rows
    # ============================================================

    def _write_event(
        self,
        event_repo,
        event_type: OutsourceQuoteEventType,
        *,
        from_status: str | None,
        to_status: str | None,
        note: str | None = None,
        created_by: int | None = None,
    ) -> None:
        if event_repo is None or self.model is None:
            return
        event_repo.add(TOutsourceQuoteEvent(
            quote_id=self.model.id,
            event_type=event_type.value,
            from_status=from_status,
            to_status=to_status,
            note=note,
            created_by=created_by,
        ))

    def on_submit(
        self,
        event_repo=None,
        *,
        created_by: int | None = None,
        **_,
    ):
        self._write_event(
            event_repo,
            OutsourceQuoteEventType.SUBMITTED,
            from_status=OutsourceQuoteStatus.DRAFT.value,
            to_status=OutsourceQuoteStatus.SUBMITTED.value,
            created_by=created_by,
        )

    def on_approve(
        self,
        review_note: str | None = None,
        event_repo=None,
        *,
        created_by: int | None = None,
        **_,
    ):
        self._write_event(
            event_repo,
            OutsourceQuoteEventType.APPROVED,
            from_status=OutsourceQuoteStatus.SUBMITTED.value,
            to_status=OutsourceQuoteStatus.APPROVED.value,
            note=review_note,
            created_by=created_by,
        )

    def on_reject(
        self,
        review_note: str | None = None,
        event_repo=None,
        *,
        created_by: int | None = None,
        **_,
    ):
        self._write_event(
            event_repo,
            OutsourceQuoteEventType.REJECTED,
            from_status=OutsourceQuoteStatus.SUBMITTED.value,
            to_status=OutsourceQuoteStatus.REJECTED.value,
            note=review_note,
            created_by=created_by,
        )

    # ============================================================
    # before_transition — capture old status for event note
    # ============================================================

    def before_transition(self, **_):
        self._from_status: str | None = (
            self.model.status if self.model else None
        )

    # ============================================================
    # Property helpers for ORM-side ergonomics
    # ============================================================

    @property
    def is_terminal(self) -> bool:
        """是否终态（REJECTED / USED / BILLED）。"""
        return self.current_state in (
            self.REJECTED, self.USED, self.BILLED,
        )
