"""测试 PartRepository 基础 CRUD + Worker 关联."""
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from model.enums import PartStatus
from repository.part import PartRepository


@pytest.mark.integration
async def test_create_part(
    part_repo: PartRepository, order_factory, user_factory
):
    from model import TPart

    user = await user_factory("alice")
    order = await order_factory(user.id)

    p = await part_repo.create(
        TPart(order_id=order.id, name="gear-A", status=PartStatus.PENDING)
    )

    assert p.id is not None
    assert p.name == "gear-A"
    assert p.status == PartStatus.PENDING
    assert p.worker_id is None


@pytest.mark.integration
async def test_list_by_order(part_repo: PartRepository, part_factory, order_factory, user_factory):
    user = await user_factory("u_list")
    order = await order_factory(user.id)
    await part_factory(order.id, name="p1")
    await part_factory(order.id, name="p2")
    await part_factory(order.id, name="p3")

    parts = await part_repo.list_by_order(order.id)
    assert len(parts) == 3
    assert [p.name for p in parts] == ["p1", "p2", "p3"]


@pytest.mark.integration
async def test_list_in_process_by_order(
    part_repo: PartRepository, part_factory, order_factory, user_factory, worker_factory
):
    user = await user_factory("u_inp")
    order = await order_factory(user.id)
    w1 = await worker_factory("w1")
    w2 = await worker_factory("w2")

    await part_factory(order.id, name="pending-1", status=PartStatus.PENDING)
    await part_factory(order.id, name="inproc-1", status=PartStatus.IN_PROCESS, worker_id=w1.id)
    await part_factory(order.id, name="inproc-2", status=PartStatus.IN_PROCESS, worker_id=w2.id)
    await part_factory(order.id, name="done-1", status=PartStatus.DONE, worker_id=w1.id)

    in_process = await part_repo.list_in_process_by_order(order.id)
    assert len(in_process) == 2
    assert {p.name for p in in_process} == {"inproc-1", "inproc-2"}


@pytest.mark.integration
async def test_list_in_process_by_worker(
    part_repo: PartRepository, part_factory, order_factory, user_factory, worker_factory
):
    user = await user_factory("u_w")
    o1 = await order_factory(user.id)
    o2 = await order_factory(user.id)
    w = await worker_factory("workerA")

    await part_factory(o1.id, name="p1", status=PartStatus.IN_PROCESS, worker_id=w.id)
    await part_factory(o1.id, name="p2", status=PartStatus.IN_PROCESS, worker_id=w.id)
    await part_factory(o2.id, name="p3", status=PartStatus.IN_PROCESS, worker_id=w.id)
    await part_factory(o1.id, name="p4", status=PartStatus.DONE, worker_id=w.id)  # 不算

    parts = await part_repo.list_in_process_by_worker(w.id)
    assert len(parts) == 3


@pytest.mark.integration
async def test_update_part_status(
    part_repo: PartRepository, part_factory, order_factory, user_factory
):
    user = await user_factory("u_upd")
    order = await order_factory(user.id)
    p = await part_factory(order.id, name="p", status=PartStatus.PENDING)

    p.status = PartStatus.IN_PROCESS
    await part_repo.update(p)

    fetched = await part_repo.get_by_id(p.id)
    assert fetched is not None
    assert fetched.status == PartStatus.IN_PROCESS


@pytest.mark.integration
async def test_delete_part(
    part_repo: PartRepository, part_factory, order_factory, user_factory
):
    user = await user_factory("u_del")
    order = await order_factory(user.id)
    p = await part_factory(order.id, name="p")

    await part_repo.delete(p)

    assert await part_repo.get_by_id(p.id) is None
