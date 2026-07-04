"""Unit test conftest — no PostgreSQL container needed.

Overrides the parent conftest's session-scoped `_postgres_test_lifecycle`
fixture so that unit tests can run without Docker.
"""
import pytest_asyncio


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _postgres_test_lifecycle():
    """Override parent conftest — unit tests don't need PostgreSQL."""
    yield
