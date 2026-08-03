"""装配件 rollup helper（2026-08-03 新增）：从子件状态派生父装配件状态。

被 `PartService._check_parent_assembly` 和 `DeliveryNoteService.pickup` 共用，
避免两份漂移的派生逻辑（与 `service/_batch_ops.py::rollup_part_status` 同构）。

事务边界：本模块只 flush；commit 由调用方负责。

派生规则：
  1. 父件终态（CANCELLED / COMPLETED）⇒ 短路返回 False，**不**改写。
  2. 取所有非终态（COMPLETED / CANCELLED）子件；按 ROLLUP_PROGRESS 取
     「最落后」者的进度。
  3. 所有子件都处于终态 ⇒ 全 CANCELLED ⇒ CANCELLED；否则 ⇒ COMPLETED。
  4. 进度 → 父件目标状态映射见 ``ASSEMBLY_ROLLUP_TARGET``。
  5. 目标 == 当前状态 ⇒ no-op。

防 MissingGreenlet：派生前对父件做 ``refresh_for_state_machine``，
避免异步 session 上的过期字段被同步读取。
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from model.assembly import TAssembly
from model.enums import AssemblyStatus, PartStatus
from repository.part import PartRepository
from service._batch_ops import ROLLUP_PROGRESS
from service._session_refresh import refresh_for_state_machine

# 进度序 → 父装配体状态。
# 0  (PENDING)              → PENDING
# 1  (PROGRAMMING)          → IN_PROCESS
# 2  (IN_PROCESS/REPAIRING) → IN_PROCESS
# 3  (OUTSOURCE)            → IN_PROCESS
# 4  (INSPECTION)           → INSPECTION
# 5  (READY_TO_SHIP)        → READY_TO_SHIP
# 6  (DELIVERED)            → DELIVERED
#
# PartStatus.COMPLETED（progress=7）由 batch rollup 已经收敛：active 批次全
# 终态 ⇒ part.status 进入 COMPLETED ⇒ 父件 COMPLETED（在规则 3 中处理）。
ASSEMBLY_ROLLUP_TARGET: dict[int, str] = {
    0: AssemblyStatus.PENDING.value,
    1: AssemblyStatus.IN_PROCESS.value,
    2: AssemblyStatus.IN_PROCESS.value,
    3: AssemblyStatus.IN_PROCESS.value,
    4: AssemblyStatus.INSPECTION.value,
    5: AssemblyStatus.READY_TO_SHIP.value,
    6: AssemblyStatus.DELIVERED.value,
}

# 父件终态：拒绝被 rollup 改写。
_ASSEMBLY_TERMINAL: frozenset[str] = frozenset({
    AssemblyStatus.COMPLETED.value,
    AssemblyStatus.CANCELLED.value,
})

# 子件终态：rollup 时不计入「最落后」集合。
_PART_TERMINAL: frozenset[str] = frozenset({
    PartStatus.COMPLETED.value,
    PartStatus.CANCELLED.value,
})


async def recompute_assembly_status(
    *,
    session: AsyncSession,
    assembly: TAssembly,
    parts: PartRepository,
    user_id: int | None,
) -> bool:
    """根据子件派生父装配件状态；返回是否实际写盘。

    - assembly 终态 ⇒ 返回 False（拒绝改写）。
    - 没有子件 ⇒ 保持当前状态（空装配件；保持现状不动）。
    - 所有子件都终态 + 全 CANCELLED ⇒ 父件 → CANCELLED
    - 所有子件都终态 + 否则 ⇒ 父件 → COMPLETED
    - 否则 ⇒ 父件 = 最落后非终态子件进度对应的 AssemblyStatus

    实际写盘由 ``AssemblyStateMachine.recompute(target)`` 完成；
    本函数负责 flush + updated_by。
    """
    # 1. 终态短路
    if assembly.status in _ASSEMBLY_TERMINAL:
        return False

    # 2. 拉子件（list_children 默认 filtered by deleted_at IS NULL）
    children = await parts.list_children(assembly.id)
    if not children:
        # 空装配件：保持现状（不自动 CANCELLED/PENDING）
        return False

    # 3. 计算目标状态
    non_terminal = [c for c in children if c.status not in _PART_TERMINAL]
    if not non_terminal:
        all_cancelled = all(
            c.status == PartStatus.CANCELLED.value for c in children
        )
        target = (
            AssemblyStatus.CANCELLED.value
            if all_cancelled
            else AssemblyStatus.COMPLETED.value
        )
    else:
        least_progress = min(
            ROLLUP_PROGRESS.get(c.status, 0) for c in non_terminal
        )
        target = ASSEMBLY_ROLLUP_TARGET[least_progress]

    # 4. no-op
    if target == assembly.status:
        return False

    # 5. 防 MissingGreenlet：刷父件的关键字段
    await refresh_for_state_machine(
        session, assembly, attrs=("status", "version", "updated_at"),
    )

    # 6. 走 SM.recompute（任意方向，含回退；同步方法）
    if not assembly.sm.recompute(target):
        # 终态短路或非法 target → SM 拒绝；本函数已确保不会出现，
        # 但防御性兜底：返回 False
        return False

    # 7. 写 updated_by + flush
    assembly.updated_by = user_id
    await session.flush()
    return True
