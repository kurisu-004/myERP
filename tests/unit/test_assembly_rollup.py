"""``service/_assembly_rollup.recompute_assembly_status`` 单元测试（2026-08-03 新增）。

直接覆盖 helper 的所有派生规则（与 PartService / DeliveryNoteService 解耦）：
- 各 ROLLUP_PROGRESS 桶 → 父件目标状态
- 全 CANCELLED / 全 COMPLETED 终态收敛
- 终态父件短路
- 空子件 no-op
- SM recompute 失败兜底
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.exception import BizError
from model.enums import AssemblyStatus, PartStatus
from service._assembly_rollup import (
    ASSEMBLY_ROLLUP_TARGET,
    ROLLUP_PROGRESS,
    recompute_assembly_status,
)


def _mock_assembly(status: str) -> MagicMock:
    """构造 mock TAssembly（sm.recompute 是同步方法，返回 bool）。"""
    asm = MagicMock()
    asm.id = 123
    asm.status = status
    asm.deleted_at = None
    asm.version = 0
    asm.updated_at = None
    asm.sm = MagicMock()
    asm.sm.recompute = MagicMock(return_value=True)
    return asm


def _mock_child(status: str, part_id: int = 2000) -> MagicMock:
    p = MagicMock()
    p.id = part_id
    p.status = status
    return p


def _mock_session() -> AsyncMock:
    """AsyncMock session；refresh_for_state_machine 走 AsyncMock。"""
    return AsyncMock()


@pytest.fixture
def fake_parts() -> MagicMock:
    p = MagicMock()
    p.list_children = AsyncMock(return_value=[])
    return p


# ============================================================
# 进度 → 父件状态映射（静态）
# ============================================================


class TestRollupTargetMapping:
    """ASSEMBLY_ROLLUP_TARGET 字典值正确性。"""

    def test_pending_maps_to_pending(self):
        assert ASSEMBLY_ROLLUP_TARGET[0] == AssemblyStatus.PENDING.value

    def test_programming_maps_to_in_process(self):
        assert ASSEMBLY_ROLLUP_TARGET[1] == AssemblyStatus.IN_PROCESS.value

    def test_in_process_and_repairing_map_to_in_process(self):
        # ROLLUP_PROGRESS 用同一值（2）映射，IN_PROCESS/REPAIRING 都 → IN_PROCESS
        assert ROLLUP_PROGRESS["IN_PROCESS"] == ROLLUP_PROGRESS["REPAIRING"] == 2
        assert ASSEMBLY_ROLLUP_TARGET[2] == AssemblyStatus.IN_PROCESS.value

    def test_outsource_maps_to_in_process(self):
        assert ROLLUP_PROGRESS["OUTSOURCE"] == 3
        assert ASSEMBLY_ROLLUP_TARGET[3] == AssemblyStatus.IN_PROCESS.value

    def test_inspection_maps_to_inspection(self):
        assert ASSEMBLY_ROLLUP_TARGET[4] == AssemblyStatus.INSPECTION.value

    def test_ready_to_ship_maps_to_ready_to_ship(self):
        assert ASSEMBLY_ROLLUP_TARGET[5] == AssemblyStatus.READY_TO_SHIP.value

    def test_delivered_maps_to_delivered(self):
        assert ASSEMBLY_ROLLUP_TARGET[6] == AssemblyStatus.DELIVERED.value


# ============================================================
# recompute_assembly_status 行为
# ============================================================


class TestRecomputeAssemblyStatus:
    """``recompute_assembly_status`` 行为级测试。"""

    async def test_terminal_completed_no_change(
        self, fake_parts: MagicMock,
    ) -> None:
        asm = _mock_assembly(AssemblyStatus.COMPLETED.value)
        ok = await recompute_assembly_status(
            session=_mock_session(), assembly=asm,
            parts=fake_parts, user_id=None,
        )
        assert ok is False
        asm.sm.recompute.assert_not_called()
        # list_children 不该被调用（终态短路）
        fake_parts.list_children.assert_not_called()

    async def test_terminal_cancelled_no_change(
        self, fake_parts: MagicMock,
    ) -> None:
        asm = _mock_assembly(AssemblyStatus.CANCELLED.value)
        ok = await recompute_assembly_status(
            session=_mock_session(), assembly=asm,
            parts=fake_parts, user_id=None,
        )
        assert ok is False
        asm.sm.recompute.assert_not_called()

    async def test_empty_children_noop(
        self, fake_parts: MagicMock,
    ) -> None:
        """空装配件（无子件）→ 保持现状不动。"""
        asm = _mock_assembly(AssemblyStatus.PENDING.value)
        fake_parts.list_children = AsyncMock(return_value=[])
        ok = await recompute_assembly_status(
            session=_mock_session(), assembly=asm,
            parts=fake_parts, user_id=None,
        )
        assert ok is False
        asm.sm.recompute.assert_not_called()

    async def test_least_pending_noop(
        self, fake_parts: MagicMock,
    ) -> None:
        """最落后子件为 PENDING + 父件 PENDING → no-op。"""
        asm = _mock_assembly(AssemblyStatus.PENDING.value)
        fake_parts.list_children = AsyncMock(return_value=[
            _mock_child(PartStatus.PENDING.value, 2001),
            _mock_child(PartStatus.PENDING.value, 2002),
        ])
        ok = await recompute_assembly_status(
            session=_mock_session(), assembly=asm,
            parts=fake_parts, user_id=None,
        )
        assert ok is False
        asm.sm.recompute.assert_not_called()

    async def test_least_in_process_to_in_process(
        self, fake_parts: MagicMock,
    ) -> None:
        asm = _mock_assembly(AssemblyStatus.PENDING.value)
        fake_parts.list_children = AsyncMock(return_value=[
            _mock_child(PartStatus.IN_PROCESS.value, 2001),
            _mock_child(PartStatus.READY_TO_SHIP.value, 2002),
        ])
        ok = await recompute_assembly_status(
            session=_mock_session(), assembly=asm,
            parts=fake_parts, user_id=42,
        )
        assert ok is True
        asm.sm.recompute.assert_called_once_with(AssemblyStatus.IN_PROCESS.value)
        assert asm.updated_by == 42

    async def test_least_inspection_to_inspection(
        self, fake_parts: MagicMock,
    ) -> None:
        asm = _mock_assembly(AssemblyStatus.IN_PROCESS.value)
        fake_parts.list_children = AsyncMock(return_value=[
            _mock_child(PartStatus.INSPECTION.value, 2001),
            _mock_child(PartStatus.DELIVERED.value, 2002),
        ])
        ok = await recompute_assembly_status(
            session=_mock_session(), assembly=asm,
            parts=fake_parts, user_id=None,
        )
        assert ok is True
        asm.sm.recompute.assert_called_once_with(
            AssemblyStatus.INSPECTION.value,
        )

    async def test_least_ready_to_ship_to_ready_to_ship(
        self, fake_parts: MagicMock,
    ) -> None:
        asm = _mock_assembly(AssemblyStatus.INSPECTION.value)
        fake_parts.list_children = AsyncMock(return_value=[
            _mock_child(PartStatus.READY_TO_SHIP.value, 2001),
            _mock_child(PartStatus.READY_TO_SHIP.value, 2002),
        ])
        ok = await recompute_assembly_status(
            session=_mock_session(), assembly=asm,
            parts=fake_parts, user_id=None,
        )
        assert ok is True
        asm.sm.recompute.assert_called_once_with(
            AssemblyStatus.READY_TO_SHIP.value,
        )

    async def test_least_delivered_to_delivered(
        self, fake_parts: MagicMock,
    ) -> None:
        asm = _mock_assembly(AssemblyStatus.READY_TO_SHIP.value)
        fake_parts.list_children = AsyncMock(return_value=[
            _mock_child(PartStatus.DELIVERED.value, 2001),
            _mock_child(PartStatus.DELIVERED.value, 2002),
        ])
        ok = await recompute_assembly_status(
            session=_mock_session(), assembly=asm,
            parts=fake_parts, user_id=None,
        )
        assert ok is True
        asm.sm.recompute.assert_called_once_with(
            AssemblyStatus.DELIVERED.value,
        )

    async def test_all_completed_to_completed(
        self, fake_parts: MagicMock,
    ) -> None:
        asm = _mock_assembly(AssemblyStatus.DELIVERED.value)
        fake_parts.list_children = AsyncMock(return_value=[
            _mock_child(PartStatus.COMPLETED.value, 2001),
            _mock_child(PartStatus.COMPLETED.value, 2002),
        ])
        ok = await recompute_assembly_status(
            session=_mock_session(), assembly=asm,
            parts=fake_parts, user_id=None,
        )
        assert ok is True
        asm.sm.recompute.assert_called_once_with(
            AssemblyStatus.COMPLETED.value,
        )

    async def test_all_cancelled_to_cancelled(
        self, fake_parts: MagicMock,
    ) -> None:
        asm = _mock_assembly(AssemblyStatus.INSPECTION.value)
        fake_parts.list_children = AsyncMock(return_value=[
            _mock_child(PartStatus.CANCELLED.value, 2001),
            _mock_child(PartStatus.CANCELLED.value, 2002),
        ])
        ok = await recompute_assembly_status(
            session=_mock_session(), assembly=asm,
            parts=fake_parts, user_id=None,
        )
        assert ok is True
        asm.sm.recompute.assert_called_once_with(
            AssemblyStatus.CANCELLED.value,
        )

    async def test_cancelled_child_excluded_from_least(
        self, fake_parts: MagicMock,
    ) -> None:
        """CANCELLED 子件不计入「最落后」，由其余子件决定。"""
        asm = _mock_assembly(AssemblyStatus.READY_TO_SHIP.value)
        fake_parts.list_children = AsyncMock(return_value=[
            _mock_child(PartStatus.DELIVERED.value, 2001),
            _mock_child(PartStatus.CANCELLED.value, 2002),
        ])
        ok = await recompute_assembly_status(
            session=_mock_session(), assembly=asm,
            parts=fake_parts, user_id=None,
        )
        assert ok is True
        asm.sm.recompute.assert_called_once_with(
            AssemblyStatus.DELIVERED.value,
        )

    async def test_sm_recompute_returns_false_returns_false(
        self, fake_parts: MagicMock,
    ) -> None:
        """SM 拒绝（终态短路等情况）→ helper 返回 False，不写盘。"""
        asm = _mock_assembly(AssemblyStatus.INSPECTION.value)
        asm.sm.recompute.return_value = False
        fake_parts.list_children = AsyncMock(return_value=[
            _mock_child(PartStatus.DELIVERED.value, 2001),
        ])
        ok = await recompute_assembly_status(
            session=_mock_session(), assembly=asm,
            parts=fake_parts, user_id=None,
        )
        assert ok is False


# ============================================================
# AssemblyStateMachine.recompute() 自身
# ============================================================


class TestStateMachineRecompute:
    """``statemachines.assembly.AssemblyStateMachine.recompute`` 方法。"""

    def test_invalid_target_raises(self) -> None:
        from statemachines.assembly import AssemblyStateMachine
        sm = AssemblyStateMachine()
        with pytest.raises(BizError):
            sm.recompute("BOGUS")

    def test_non_string_raises(self) -> None:
        from statemachines.assembly import AssemblyStateMachine
        sm = AssemblyStateMachine()
        with pytest.raises(BizError):
            sm.recompute(123)  # type: ignore[arg-type]

    def test_terminal_target_reachable_from_non_terminal(self) -> None:
        """非终态 → CANCELLED 也允许（因为它是合法的 AssemblyStatus）。"""
        from statemachines.assembly import AssemblyStateMachine
        sm = AssemblyStateMachine(start_value="INSPECTION")
        # CANCELLED 在我们的实现里被 SM 拦了（终态短路），所以应该返回 False
        result = sm.recompute("CANCELLED")
        # CANCELLED 是终态且 current 不是 CANCELLED 本身
        # 但 current_state 赋值会进入 on_enter_CANCELLED
        # 我们的实现里「current_state in {COMPLETED, CANCELLED}」是检查源不是目标
        assert result is True
        assert sm.current_state_value == "CANCELLED"

    def test_terminal_source_refuses_any_target(self) -> None:
        from statemachines.assembly import AssemblyStateMachine
        sm = AssemblyStateMachine(start_value="COMPLETED")
        result = sm.recompute("INSPECTION")
        assert result is False
        assert sm.current_state_value == "COMPLETED"

    def test_noop_when_target_equals_current(self) -> None:
        from statemachines.assembly import AssemblyStateMachine
        sm = AssemblyStateMachine(start_value="INSPECTION")
        result = sm.recompute("INSPECTION")
        assert result is False

    def test_backward_regression_inspection_to_in_process(self) -> None:
        from statemachines.assembly import AssemblyStateMachine
        sm = AssemblyStateMachine(start_value="INSPECTION")
        result = sm.recompute("IN_PROCESS")
        assert result is True
        assert sm.current_state_value == "IN_PROCESS"

    def test_forward_inspection_to_ready_to_ship(self) -> None:
        from statemachines.assembly import AssemblyStateMachine
        sm = AssemblyStateMachine(start_value="INSPECTION")
        result = sm.recompute("READY_TO_SHIP")
        assert result is True
        assert sm.current_state_value == "READY_TO_SHIP"
