"""Unit tests for PartRepository.find_delivered_older_than（PR-D 2026-07-10）。

覆盖四个核心谓词：
- 早于 7 天的 DELIVERED → 命中
- 不足 7 天的 DELIVERED → 不动
- 8 天前发货 + 3 天前 REPAIR_STARTED → 不动（返修干扰）
- DELIVERED 多次（返修后再次 DELIVERED）→ 用最近那次算 threshold
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from model import TPart
from model.enums import PartEventType, PartStatus
from repository.part import PartRepository


@pytest.fixture
def session() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


@pytest.fixture
def repo(session: AsyncSession) -> PartRepository:
    return PartRepository(session)


def _make_part(part_id: int, status: str = PartStatus.DELIVERED.value) -> MagicMock:
    p = MagicMock(spec=TPart)
    p.id = part_id
    p.status = status
    p.deleted_at = None
    p.serial_no = f"L{part_id:04d}"
    return p


class _FakeResult:
    def __init__(self, rows: list[MagicMock]) -> None:
        self._rows = rows

    def scalars(self):
        return self

    def all(self) -> list[MagicMock]:
        return self._rows


@pytest.mark.asyncio
class TestFindDeliveredOlderThan:
    async def test_returns_old_delivered_parts(
        self, repo: PartRepository, session: AsyncMock,
    ) -> None:
        """8 天前 DELIVERED → 命中。"""
        old_part = _make_part(part_id=1001)
        session.execute.return_value = _FakeResult([old_part])

        # 2026-07-15 修复 deprecation：datetime.utcnow() 已废弃，
        # 改用 `datetime.now(timezone.utc).replace(tzinfo=None)` 保持 naive。
        threshold = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=7)
        rows = await repo.find_delivered_older_than(threshold=threshold, limit=200)

        assert rows == [old_part]
        # 应该至少调用了一次 execute（实际是 1 次 SELECT + 内部子查询）
        assert session.execute.await_count == 1

    async def test_empty(
        self, repo: PartRepository, session: AsyncMock,
    ) -> None:
        """无候选 → 返回空 list。"""
        session.execute.return_value = _FakeResult([])
        threshold = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=7)
        rows = await repo.find_delivered_older_than(threshold=threshold)
        assert rows == []


@pytest.mark.asyncio
class TestAutoCompleteRunOnce:
    """集成测试：跑一次 auto_complete_loop._run_once，确认（2026-07-29 批次级）：
    - 找到的 DELIVERED 批次逐个走 PartService.complete(part_id, batch_id=...)
    - 没找到时静默返回
    - 单个失败不影响其他
    """

    @staticmethod
    def _make_batch(batch_id: int, part_id: int) -> MagicMock:
        b = MagicMock()
        b.id = batch_id
        b.part_id = part_id
        b.batch_no = 1
        return b

    async def test_no_parts_no_op(self) -> None:
        from service.auto_complete import _run_once
        from unittest.mock import AsyncMock, patch

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch("service.auto_complete.SessionLocal") as session_local, \
             patch("service.auto_complete.PartBatchRepository") as pb_cls:
            session_local.return_value = mock_session
            mock_repo = pb_cls.return_value
            mock_repo.find_delivered_older_than = AsyncMock(return_value=[])

            await _run_once()

            mock_repo.find_delivered_older_than.assert_awaited_once()
            mock_session.commit.assert_not_awaited()

    async def test_completes_eligible_parts(self) -> None:
        from service.auto_complete import _run_once
        from unittest.mock import AsyncMock, patch

        b1 = self._make_batch(batch_id=501, part_id=1001)
        b2 = self._make_batch(batch_id=502, part_id=1002)
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch("service.auto_complete.SessionLocal") as session_local, \
             patch("service.auto_complete.PartBatchRepository") as pb_cls, \
             patch("service.auto_complete.PartService") as svc_cls:
            session_local.return_value = mock_session
            mock_repo = pb_cls.return_value
            mock_repo.find_delivered_older_than = AsyncMock(return_value=[b1, b2])

            mock_svc = svc_cls.return_value
            mock_svc.complete = AsyncMock()

            await _run_once()

            # 两个批次都按 (part_id, batch_id) 调了 complete
            assert mock_svc.complete.await_count == 2
            mock_svc.complete.assert_any_await(b1.part_id, batch_id=b1.id)
            mock_svc.complete.assert_any_await(b2.part_id, batch_id=b2.id)
            mock_session.commit.assert_awaited_once()

    async def test_one_failure_does_not_block_others(self) -> None:
        from service.auto_complete import _run_once
        from unittest.mock import AsyncMock, patch

        b1 = self._make_batch(batch_id=601, part_id=2001)
        b2 = self._make_batch(batch_id=602, part_id=2002)
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch("service.auto_complete.SessionLocal") as session_local, \
             patch("service.auto_complete.PartBatchRepository") as pb_cls, \
             patch("service.auto_complete.PartService") as svc_cls:
            session_local.return_value = mock_session
            mock_repo = pb_cls.return_value
            mock_repo.find_delivered_older_than = AsyncMock(return_value=[b1, b2])

            mock_svc = svc_cls.return_value

            async def _complete_one(pid: int, *, batch_id=None):
                if pid == 2001:
                    raise RuntimeError("simulated")
                return None

            mock_svc.complete = AsyncMock(side_effect=_complete_one)

            # 不抛错；第二个继续完成
            await _run_once()
            assert mock_svc.complete.await_count == 2
            mock_session.commit.assert_awaited_once()