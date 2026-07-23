"""送货单（DeliveryNote）状态机（2026-07-22 新增）。

形态对齐 Part / OutsourceQuote 状态机：

- `model` 由 `__init__(model=note)` 注入；`start_value` 从 note.status 恢复。
- 状态转换通过 callbacks 写 event_repo（同步 add，不 flush）。
- 不在状态机内部查 DB；service 层校验 target 存在 / 业务规则。
- 不写 `state_field`，与 OutsourceQuoteStateMachine 一致；model.status 由
  on_enter_* 直接改写。
- 终态只有 ARCHIVED；PICKED_UP 不 final，以便衔接 archive 一并完成 pickup 事务
  内的「state migration + part.deliver + delivery_note_id 清空」。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from statemachine import State, StateChart

from model.delivery_note_event import TDeliveryNoteEvent
from model.enums import DeliveryNoteEventType, DeliveryNoteStatus

if TYPE_CHECKING:
    from model.delivery_note import TDeliveryNote


class DeliveryNoteStateMachine(StateChart):
    """送货单状态机。

    Usage::

        note = await repo.get_by_id(note_id)
        note.sm.submit(event_repo=events, created_by=user_id)
        # note.status 已是 SUBMITTED；note.submitted_at 已被 on_enter 设置
    """

    # States

    DRAFT = State("DRAFT", initial=True, value="DRAFT")
    SUBMITTED = State("SUBMITTED", value="SUBMITTED")
    PICKED_UP = State("PICKED_UP", value="PICKED_UP")
    ARCHIVED = State("ARCHIVED", value="ARCHIVED", final=True)

    # Transitions

    submit = DRAFT.to(SUBMITTED)
    recall = SUBMITTED.to(DRAFT)
    pickup = SUBMITTED.to(PICKED_UP)
    archive = PICKED_UP.to(ARCHIVED)

    # Init — restore from model.status

    def __init__(self, model: "TDeliveryNote | None" = None, **kwargs):
        start_value = kwargs.pop("start_value", None)
        if model is not None and start_value is None:
            status = getattr(model, "status", None)
            if status:
                start_value = status
        super().__init__(
            model=model, start_value=start_value, **kwargs,
        )

    # on_enter_* — sync model.status + 时间戳

    def on_enter_DRAFT(self, **_):
        # 2026-07-23 修复撤回 bug：库默认 state_field='state'，动态 state 不落到
        # mapped `status` 列；缺 on_enter_DRAFT 时 recall(SUBMITTED→DRAFT) 只 append
        # RECALLED 事件而 DB status 仍 SUBMITTED。此 hook 显式写回 DRAFT，并清空
        # 提交痕迹（re-submit 时 on_enter_SUBMITTED 会重置 submitted_at）。
        # 对照 statemachines/outsource_quote.py::on_enter_DRAFT。
        if self.model:
            self.model.status = DeliveryNoteStatus.DRAFT.value
            self.model.submitted_at = None
            self.model.submitted_by = None

    def on_enter_SUBMITTED(self, **_):
        if self.model:
            from core.time import now_naive
            self.model.status = DeliveryNoteStatus.SUBMITTED.value
            self.model.submitted_at = now_naive()

    def on_enter_PICKED_UP(self, **_):
        if self.model:
            from core.time import now_naive
            self.model.status = DeliveryNoteStatus.PICKED_UP.value
            self.model.picked_up_at = now_naive()

    def on_enter_ARCHIVED(self, **_):
        if self.model:
            # 仅同步 status；归档动作由 service 在同一事务内一并处理
            self.model.status = DeliveryNoteStatus.ARCHIVED.value

    # on_<transition_name> — append TDeliveryNoteEvent

    def _write_event(
        self,
        event_repo,
        event_type: DeliveryNoteEventType,
        *,
        from_status: str | None,
        to_status: str | None,
        note: str | None = None,
        created_by: int | None = None,
    ) -> None:
        if event_repo is None or self.model is None:
            return
        event_repo.add(TDeliveryNoteEvent(
            delivery_note_id=self.model.id,
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
            event_repo, DeliveryNoteEventType.SUBMITTED,
            from_status=DeliveryNoteStatus.DRAFT.value,
            to_status=DeliveryNoteStatus.SUBMITTED.value,
            created_by=created_by,
        )

    def on_recall(
        self,
        event_repo=None,
        *,
        created_by: int | None = None,
        **_,
    ):
        # 2026-07-23 改：写 WITHDRAWN 而非 RECALLED（语义无变化：仍 SUBMITTED → DRAFT；
        # 仅事件类型名换成中文友好值）。RECALLED enum 保留只为读老数据。
        self._write_event(
            event_repo, DeliveryNoteEventType.WITHDRAWN,
            from_status=DeliveryNoteStatus.SUBMITTED.value,
            to_status=DeliveryNoteStatus.DRAFT.value,
            created_by=created_by,
        )

    def on_pickup(
        self,
        event_repo=None,
        *,
        created_by: int | None = None,
        **_,
    ):
        self._write_event(
            event_repo, DeliveryNoteEventType.PICKED_UP,
            from_status=DeliveryNoteStatus.SUBMITTED.value,
            to_status=DeliveryNoteStatus.PICKED_UP.value,
            created_by=created_by,
        )

    def on_archive(
        self,
        event_repo=None,
        *,
        created_by: int | None = None,
        **_,
    ):
        self._write_event(
            event_repo, DeliveryNoteEventType.ARCHIVED,
            from_status=DeliveryNoteStatus.PICKED_UP.value,
            to_status=DeliveryNoteStatus.ARCHIVED.value,
            created_by=created_by,
        )

    # before_transition — capture old status for event note

    def before_transition(self, **_):
        self._from_status: str | None = (
            self.model.status if self.model else None
        )

    @property
    def is_terminal(self) -> bool:
        """是否终态（ARCHIVED）。"""
        return self.current_state == self.ARCHIVED
