from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from core.error_code import ErrCode
from core.exception import BizError
from model.worker import TWorker
from repository.worker import WorkerRepository
from schema.worker import (
    WorkerCreateRequest,
    WorkerListQuery,
    WorkerUpdateRequest,
)
from service.worker import WorkerService

pytestmark = pytest.mark.asyncio


@pytest.fixture
def mock_repo() -> WorkerRepository:
    """Create a WorkerRepository with all methods mocked as AsyncMock."""
    repo = WorkerRepository.__new__(WorkerRepository)
    repo.list_with_filters = AsyncMock()
    repo.count_with_filters = AsyncMock()
    repo.get_by_id = AsyncMock()
    repo.get_by_badge_code = AsyncMock()
    repo.create = AsyncMock()
    repo.update = AsyncMock()
    repo.list_by_ids = AsyncMock()
    # 模拟 session（WorkerService.update_worker 用 refresh 更新 updated_at）
    repo.session = AsyncMock()
    repo.session.refresh = AsyncMock()
    return repo


@pytest.fixture
def service(mock_repo: WorkerRepository) -> WorkerService:
    """Create a WorkerService backed by the mock repository."""
    return WorkerService(workers=mock_repo)


def _make_worker(
    id: int = 1,
    badge_code: str = "B001",
    name: str = "张三",
    id_card_no: str | None = "110101199001011234",
    phone: str | None = "13800138000",
    is_active: bool = True,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
    deleted_at: datetime | None = None,
) -> TWorker:
    """Factory helper to construct a TWorker instance without a DB session."""
    return TWorker(
        id=id,
        badge_code=badge_code,
        name=name,
        id_card_no=id_card_no,
        phone=phone,
        is_active=is_active,
        created_at=created_at or datetime(2025, 1, 1, 0, 0, 0),
        updated_at=updated_at or datetime(2025, 1, 1, 0, 0, 0),
        deleted_at=deleted_at,
    )


# ======================================================================
# list_workers
# ======================================================================


class TestListWorkers:
    """Tests for WorkerService.list_workers."""

    async def test_normal_with_filters(
        self,
        service: WorkerService,
        mock_repo: WorkerRepository,
    ) -> None:
        """list_workers returns paginated items with filters applied."""
        # ── arrange ──────────────────────────────────────────────
        w = _make_worker(id=1, name="张三")
        mock_repo.list_with_filters.return_value = [w]
        mock_repo.count_with_filters.return_value = 1

        query = WorkerListQuery(name_like="张", is_active=True, limit=10, offset=0)

        # ── act ──────────────────────────────────────────────────
        result = await service.list_workers(query)

        # ── assert ───────────────────────────────────────────────
        mock_repo.list_with_filters.assert_awaited_once_with(
            name_like="张", is_active=True, limit=10, offset=0
        )
        mock_repo.count_with_filters.assert_awaited_once_with(
            name_like="张", is_active=True
        )
        assert len(result.items) == 1
        assert result.items[0].name == "张三"
        assert result.items[0].id == 1
        assert result.total == 1
        assert result.limit == 10
        assert result.offset == 0


# ======================================================================
# get_worker
# ======================================================================


class TestGetWorker:
    """Tests for WorkerService.get_worker."""

    async def test_exists(
        self,
        service: WorkerService,
        mock_repo: WorkerRepository,
    ) -> None:
        """get_worker returns WorkerOut when the worker exists."""
        # ── arrange ──────────────────────────────────────────────
        w = _make_worker(id=42, name="李四")
        mock_repo.get_by_id.return_value = w

        # ── act ──────────────────────────────────────────────────
        result = await service.get_worker(42)

        # ── assert ───────────────────────────────────────────────
        mock_repo.get_by_id.assert_awaited_once_with(42)
        assert result.id == 42
        assert result.name == "李四"
        assert result.badge_code == "B001"
        assert result.is_active is True

    async def test_not_found(
        self,
        service: WorkerService,
        mock_repo: WorkerRepository,
    ) -> None:
        """get_worker raises 404 when the worker does not exist."""
        # ── arrange ──────────────────────────────────────────────
        mock_repo.get_by_id.return_value = None

        # ── act / assert ─────────────────────────────────────────
        with pytest.raises(BizError) as exc_info:
            await service.get_worker(99)

        mock_repo.get_by_id.assert_awaited_once_with(99)
        assert exc_info.value.code == ErrCode.BIZ_WORKER_NOT_FOUND
        assert "worker 99" in exc_info.value.message
        assert exc_info.value.http_status == 404


# ======================================================================
# verify_badge
# ======================================================================


class TestVerifyBadge:
    """Tests for WorkerService.verify_badge."""

    async def test_normal(
        self,
        service: WorkerService,
        mock_repo: WorkerRepository,
    ) -> None:
        """verify_badge returns WorkerOut when badge_code matches an active worker."""
        # ── arrange ──────────────────────────────────────────────
        w = _make_worker(id=1, badge_code="ABC123", name="王五")
        mock_repo.get_by_badge_code.return_value = w

        # ── act ──────────────────────────────────────────────────
        result = await service.verify_badge("ABC123")

        # ── assert ───────────────────────────────────────────────
        mock_repo.get_by_badge_code.assert_awaited_once_with("ABC123")
        assert result.badge_code == "ABC123"
        assert result.name == "王五"
        assert result.is_active is True

    async def test_badge_code_not_found(
        self,
        service: WorkerService,
        mock_repo: WorkerRepository,
    ) -> None:
        """verify_badge raises 404 when badge_code is not found."""
        # ── arrange ──────────────────────────────────────────────
        mock_repo.get_by_badge_code.return_value = None

        # ── act / assert ─────────────────────────────────────────
        with pytest.raises(BizError) as exc_info:
            await service.verify_badge("NONEXISTENT")

        mock_repo.get_by_badge_code.assert_awaited_once_with("NONEXISTENT")
        assert exc_info.value.code == ErrCode.BIZ_WORKER_NOT_FOUND
        assert exc_info.value.http_status == 404

    async def test_worker_inactive(
        self,
        service: WorkerService,
        mock_repo: WorkerRepository,
    ) -> None:
        """verify_badge raises 400 when the worker is inactive."""
        # ── arrange ──────────────────────────────────────────────
        w = _make_worker(id=1, badge_code="INACTIVE", is_active=False)
        mock_repo.get_by_badge_code.return_value = w

        # ── act / assert ─────────────────────────────────────────
        with pytest.raises(BizError) as exc_info:
            await service.verify_badge("INACTIVE")

        mock_repo.get_by_badge_code.assert_awaited_once_with("INACTIVE")
        assert exc_info.value.code == ErrCode.BIZ_WORKER_INACTIVE
        assert exc_info.value.http_status == 400

    async def test_empty_badge_code(
        self,
        service: WorkerService,
        mock_repo: WorkerRepository,
    ) -> None:
        """verify_badge raises 404 when badge_code is empty (stripped to '')."""
        # ── act / assert ─────────────────────────────────────────
        with pytest.raises(BizError) as exc_info:
            await service.verify_badge("")

        # Repository should never be called since the guard triggers first.
        mock_repo.get_by_badge_code.assert_not_called()
        assert exc_info.value.code == ErrCode.BIZ_WORKER_NOT_FOUND
        assert exc_info.value.http_status == 404


# ======================================================================
# create_worker
# ======================================================================


class TestCreateWorker:
    """Tests for WorkerService.create_worker."""

    async def test_normal(
        self,
        service: WorkerService,
        mock_repo: WorkerRepository,
    ) -> None:
        """create_worker creates a new worker when badge_code is unique."""
        # ── arrange ──────────────────────────────────────────────
        mock_repo.get_by_badge_code.return_value = None  # no conflict

        # Simulate the DB applying server_default for created_at / updated_at
        # after the flush that happens inside repository.create.
        create_fake_now = datetime(2025, 7, 4, 12, 0, 0)

        async def _create_with_defaults(worker: TWorker) -> TWorker:
            worker.created_at = create_fake_now
            worker.updated_at = create_fake_now
            return worker

        mock_repo.create.side_effect = _create_with_defaults

        data = WorkerCreateRequest(
            badge_code="NEW001",
            name="赵六",
            id_card_no="110101199001011234",
            phone="13900139000",
        )

        # ── act ──────────────────────────────────────────────────
        with patch("service.worker.new_id", return_value=1001):
            result = await service.create_worker(data)

        # ── assert ───────────────────────────────────────────────
        mock_repo.get_by_badge_code.assert_awaited_once_with("NEW001")
        mock_repo.create.assert_awaited_once()

        created: TWorker = mock_repo.create.call_args[0][0]
        assert isinstance(created, TWorker)
        assert created.id == 1001
        assert created.badge_code == "NEW001"
        assert created.name == "赵六"
        assert created.id_card_no == "110101199001011234"
        assert created.phone == "13900139000"
        assert created.is_active is True

        # Verify the returned WorkerOut matches
        assert result.id == 1001
        assert result.badge_code == "NEW001"
        assert result.name == "赵六"
        assert result.is_active is True
        # created_at / updated_at come from the simulated DB server_default
        assert result.created_at == create_fake_now
        assert result.updated_at == create_fake_now

    async def test_badge_code_conflict(
        self,
        service: WorkerService,
        mock_repo: WorkerRepository,
    ) -> None:
        """create_worker raises 409 when badge_code already exists."""
        # ── arrange ──────────────────────────────────────────────
        existing = _make_worker(id=1, badge_code="DUP001")
        mock_repo.get_by_badge_code.return_value = existing
        data = WorkerCreateRequest(badge_code="DUP001", name="钱七")

        # ── act / assert ─────────────────────────────────────────
        with pytest.raises(BizError) as exc_info:
            await service.create_worker(data)

        mock_repo.get_by_badge_code.assert_awaited_once_with("DUP001")
        mock_repo.create.assert_not_called()
        assert exc_info.value.code == ErrCode.CONFLICT
        assert exc_info.value.http_status == 409


# ======================================================================
# update_worker
# ======================================================================


class TestUpdateWorker:
    """Tests for WorkerService.update_worker."""

    async def test_name_change(
        self,
        service: WorkerService,
        mock_repo: WorkerRepository,
    ) -> None:
        """update_worker updates only the name field."""
        # ── arrange ──────────────────────────────────────────────
        w = _make_worker(id=1, name="张三")
        mock_repo.get_by_id.return_value = w
        data = WorkerUpdateRequest(name="张三丰")

        # ── act ──────────────────────────────────────────────────
        result = await service.update_worker(1, data)

        # ── assert ───────────────────────────────────────────────
        mock_repo.get_by_id.assert_awaited_once_with(1)
        mock_repo.update.assert_awaited_once_with(w)
        assert w.name == "张三丰"
        assert result.name == "张三丰"
        # Other fields should remain unchanged
        assert result.badge_code == "B001"

    async def test_badge_code_change_no_conflict(
        self,
        service: WorkerService,
        mock_repo: WorkerRepository,
    ) -> None:
        """update_worker changes badge_code when the new code is not taken."""
        # ── arrange ──────────────────────────────────────────────
        w = _make_worker(id=1, badge_code="OLD")
        mock_repo.get_by_id.return_value = w
        mock_repo.get_by_badge_code.return_value = None
        data = WorkerUpdateRequest(badge_code="NEW")

        # ── act ──────────────────────────────────────────────────
        result = await service.update_worker(1, data)

        # ── assert ───────────────────────────────────────────────
        mock_repo.get_by_id.assert_awaited_once_with(1)
        mock_repo.get_by_badge_code.assert_awaited_once_with("NEW")
        mock_repo.update.assert_awaited_once_with(w)
        assert w.badge_code == "NEW"
        assert result.badge_code == "NEW"

    async def test_badge_code_conflict(
        self,
        service: WorkerService,
        mock_repo: WorkerRepository,
    ) -> None:
        """update_worker raises 409 when the new badge_code belongs to another worker."""
        # ── arrange ──────────────────────────────────────────────
        w = _make_worker(id=1, badge_code="OLD")
        other = _make_worker(id=2, badge_code="TAKEN")
        mock_repo.get_by_id.return_value = w
        mock_repo.get_by_badge_code.return_value = other  # other.id != w.id → conflict
        data = WorkerUpdateRequest(badge_code="TAKEN")

        # ── act / assert ─────────────────────────────────────────
        with pytest.raises(BizError) as exc_info:
            await service.update_worker(1, data)

        mock_repo.get_by_id.assert_awaited_once_with(1)
        mock_repo.get_by_badge_code.assert_awaited_once_with("TAKEN")
        mock_repo.update.assert_not_called()
        assert exc_info.value.code == ErrCode.CONFLICT
        assert exc_info.value.http_status == 409

    async def test_worker_not_found(
        self,
        service: WorkerService,
        mock_repo: WorkerRepository,
    ) -> None:
        """update_worker raises 404 when the worker does not exist."""
        # ── arrange ──────────────────────────────────────────────
        mock_repo.get_by_id.return_value = None
        data = WorkerUpdateRequest(name="新名字")

        # ── act / assert ─────────────────────────────────────────
        with pytest.raises(BizError) as exc_info:
            await service.update_worker(999, data)

        mock_repo.get_by_id.assert_awaited_once_with(999)
        mock_repo.update.assert_not_called()
        assert exc_info.value.code == ErrCode.BIZ_WORKER_NOT_FOUND
        assert exc_info.value.http_status == 404


# ======================================================================
# deactivate
# ======================================================================


class TestDeactivate:
    """Tests for WorkerService.deactivate."""

    async def test_normal(
        self,
        service: WorkerService,
        mock_repo: WorkerRepository,
    ) -> None:
        """deactivate sets is_active=False and deleted_at on the worker."""
        # ── arrange ──────────────────────────────────────────────
        w = _make_worker(id=1, is_active=True, deleted_at=None)
        mock_repo.get_by_id.return_value = w

        # ── act ──────────────────────────────────────────────────
        result = await service.deactivate(1)

        # ── assert ───────────────────────────────────────────────
        mock_repo.get_by_id.assert_awaited_once_with(1)
        mock_repo.update.assert_awaited_once_with(w)
        assert w.is_active is False
        assert w.deleted_at is not None  # set by datetime.utcnow()
        assert result.is_active is False

    async def test_worker_not_found(
        self,
        service: WorkerService,
        mock_repo: WorkerRepository,
    ) -> None:
        """deactivate raises 404 when the worker does not exist."""
        # ── arrange ──────────────────────────────────────────────
        mock_repo.get_by_id.return_value = None

        # ── act / assert ─────────────────────────────────────────
        with pytest.raises(BizError) as exc_info:
            await service.deactivate(999)

        mock_repo.get_by_id.assert_awaited_once_with(999)
        mock_repo.update.assert_not_called()
        assert exc_info.value.code == ErrCode.BIZ_WORKER_NOT_FOUND
        assert exc_info.value.http_status == 404


# ======================================================================
# reactivate
# ======================================================================


class TestReactivate:
    """Tests for WorkerService.reactivate."""

    async def test_normal(
        self,
        service: WorkerService,
        mock_repo: WorkerRepository,
    ) -> None:
        """reactivate sets is_active=True and clears deleted_at, using include_deleted."""
        # ── arrange ──────────────────────────────────────────────
        w = _make_worker(
            id=1, is_active=False, deleted_at=datetime(2025, 6, 1, 12, 0, 0)
        )
        mock_repo.get_by_id.return_value = w

        # ── act ──────────────────────────────────────────────────
        result = await service.reactivate(1)

        # ── assert ───────────────────────────────────────────────
        mock_repo.get_by_id.assert_awaited_once_with(1, include_deleted=True)
        mock_repo.update.assert_awaited_once_with(w)
        assert w.is_active is True
        assert w.deleted_at is None
        assert result.is_active is True

    async def test_worker_not_found(
        self,
        service: WorkerService,
        mock_repo: WorkerRepository,
    ) -> None:
        """reactivate raises 404 when the worker does not exist (even with include_deleted)."""
        # ── arrange ──────────────────────────────────────────────
        mock_repo.get_by_id.return_value = None

        # ── act / assert ─────────────────────────────────────────
        with pytest.raises(BizError) as exc_info:
            await service.reactivate(999)

        mock_repo.get_by_id.assert_awaited_once_with(999, include_deleted=True)
        mock_repo.update.assert_not_called()
        assert exc_info.value.code == ErrCode.BIZ_WORKER_NOT_FOUND
        assert exc_info.value.http_status == 404
