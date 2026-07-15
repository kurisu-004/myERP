"""外协报价单（OutsourceQuote）状态机（2026-07-16 新增）。

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
    """外协报价单状态机。

    Usage::

        quote = await repo.get_by_id(quote_id)
        sm = quote.sm
        sm.submit(event_repo=events, created_by=user_id)
        # quote.status / quote.submitted_at 已设置

    自动从 `model.status` 恢复当前状态。
    """

    # ============================================================
    # States
    # ============================================================

    DRAFT = State("DRAFT", initial=True, value="DRAFT")
    SUBMITTED = State("SUBMITTED", value="SUBMITTED")
    APPROVED = State("APPROVED", value="APPROVED")
    REJECTED = State("REJECTED", value="REJECTED", final=True)
    USED = State("USED", value="USED", final=True)

    # ============================================================
    # Transitions
    # ============================================================

    submit = DRAFT.to(SUBMITTED)
    approve = SUBMITTED.to(APPROVED)
    reject = SUBMITTED.to(REJECTED)
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

    def on_mark_used(
        self,
        event_repo=None,
        *,
        created_by: int | None = None,
        **_,
    ):
        """APPROVED → USED：外协发送成功后由 PartService 自动调用。"""
        self._write_event(
            event_repo,
            OutsourceQuoteEventType.USED,
            from_status=OutsourceQuoteStatus.APPROVED.value,
            to_status=OutsourceQuoteStatus.USED.value,
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
        """是否终态（REJECTED / USED）。"""
        return self.current_state in (self.REJECTED, self.USED)
