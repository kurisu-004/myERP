"""Unit test conftest — no PostgreSQL container needed.

Overrides the parent conftest's session-scoped `_postgres_test_lifecycle`
fixture so that unit tests can run without Docker.

共享 fixtures：所有 PartService 单测需要的 repo mock（mock_parts / mock_customers
等）。原 test_part_service_query_crud.py 也在文件内定义同名 fixtures（pytest
fixture scope = function，跨文件不共享），这里提供一组共享版以减少重复。
2026-07-21：把 fixtures 共享到这里，便于 test_part_batch_tree.py 复用。
"""
from unittest.mock import AsyncMock

import pytest

import pytest_asyncio


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _postgres_test_lifecycle():
    """Override parent conftest — unit tests don't need PostgreSQL."""
    yield


@pytest.fixture
def mock_parts() -> AsyncMock:
    mock = AsyncMock()
    mock.list_with_filters = AsyncMock()
    mock.count_with_filters = AsyncMock()
    mock.get_by_id = AsyncMock()
    mock.get_by_serial = AsyncMock()
    mock.create = AsyncMock()
    mock.update = AsyncMock()
    mock.soft_delete = AsyncMock()
    mock.list_children = AsyncMock()
    return mock


@pytest.fixture
def mock_customers() -> AsyncMock:
    mock = AsyncMock()
    mock.get_by_id = AsyncMock()
    mock.list_by_ids = AsyncMock()
    mock.list_children = AsyncMock(return_value=[])
    return mock


@pytest.fixture
def mock_workers() -> AsyncMock:
    mock = AsyncMock()
    mock.list_by_ids = AsyncMock()
    mock.list_with_filters = AsyncMock()
    mock.get_by_badge_code = AsyncMock()
    return mock


@pytest.fixture
def mock_events() -> AsyncMock:
    mock = AsyncMock()
    mock.list_by_part = AsyncMock()
    mock.create = AsyncMock()
    return mock


@pytest.fixture
def mock_serial_counters() -> AsyncMock:
    mock = AsyncMock()
    mock.acquire_serial = AsyncMock()
    return mock


@pytest.fixture
def mock_shelves() -> AsyncMock:
    mock = AsyncMock()
    mock.get_by_id = AsyncMock()
    mock.list_by_ids = AsyncMock()
    return mock


@pytest.fixture
def mock_applicants_repo() -> AsyncMock:
    mock = AsyncMock()
    mock.get_by_id = AsyncMock()
    mock.list_by_customer = AsyncMock()
    return mock


@pytest.fixture
def mock_files() -> AsyncMock:
    mock = AsyncMock()
    mock.list_by_part = AsyncMock()
    mock.list_by_assembly = AsyncMock()
    return mock


@pytest.fixture
def mock_assemblies() -> AsyncMock:
    mock = AsyncMock()
    mock.create = AsyncMock()
    return mock