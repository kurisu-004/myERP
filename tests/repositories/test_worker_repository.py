"""测试 WorkerRepository 基础 CRUD."""
import pytest

from repository.worker import WorkerRepository


@pytest.mark.integration
async def test_create_and_get_by_id(worker_repo: WorkerRepository, worker_factory):
    w = await worker_factory("alice")
    fetched = await worker_repo.get_by_id(w.id)
    assert fetched is not None
    assert fetched.name == "alice"


@pytest.mark.integration
async def test_get_by_name(worker_repo: WorkerRepository, worker_factory):
    await worker_factory("bob")
    w = await worker_repo.get_by_name("bob")
    assert w is not None
    assert w.name == "bob"
    assert (await worker_repo.get_by_name("nobody")) is None


@pytest.mark.integration
async def test_list_all(worker_repo: WorkerRepository, worker_factory):
    await worker_factory("w_a")
    await worker_factory("w_b")
    await worker_factory("w_c")
    workers = await worker_repo.list_all()
    assert len(workers) == 3
    assert {w.name for w in workers} == {"w_a", "w_b", "w_c"}
