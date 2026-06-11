"""复杂查询测试.

1. 查询 order 下所有正在加工的 part 和其对应的 worker
2. 查询 worker 正在加工的 part 和对应的 order
"""
import pytest

from model.enums import PartStatus
from repository.order import OrderRepository
from repository.worker import WorkerRepository


@pytest.mark.integration
async def test_order_in_process_parts_with_workers(
    order_repo: OrderRepository,
    user_factory,
    order_factory,
    part_factory,
    worker_factory,
):
    # 准备: 1 user, 2 orders
    user = await user_factory("u1")
    order_a = await order_factory(user.id)
    order_b = await order_factory(user.id)

    # 3 个 workers
    w1 = await worker_factory("w1")
    w2 = await worker_factory("w2")
    w3 = await worker_factory("w3")

    # order_a: 4 parts，覆盖各种状态
    await part_factory(order_a.id, name="p_a1", status=PartStatus.PENDING)
    await part_factory(order_a.id, name="p_a2", status=PartStatus.IN_PROCESS, worker_id=w1.id)
    await part_factory(order_a.id, name="p_a3", status=PartStatus.IN_PROCESS, worker_id=w2.id)
    await part_factory(order_a.id, name="p_a4", status=PartStatus.DONE, worker_id=w3.id)

    # order_b: 与本次查询无关
    await part_factory(order_b.id, name="p_b1", status=PartStatus.IN_PROCESS, worker_id=w1.id)

    # ---- 执行复杂查询 ----
    rows = await order_repo.list_in_process_parts_with_workers(order_a.id)

    # 断言
    assert len(rows) == 2
    part_names = [p.name for p, _ in rows]
    worker_names = [w.name for _, w in rows]
    assert part_names == ["p_a2", "p_a3"]
    assert set(worker_names) == {"w1", "w2"}

    # 验证没混入 order_b 的 part
    order_ids = {p.order_id for p, _ in rows}
    assert order_ids == {order_a.id}


@pytest.mark.integration
async def test_order_in_process_returns_empty_when_none(
    order_repo: OrderRepository,
    user_factory,
    order_factory,
    part_factory,
    worker_factory,
):
    user = await user_factory("u_empty")
    order = await order_factory(user.id)
    w = await worker_factory("w_x")

    await part_factory(order.id, name="p", status=PartStatus.PENDING)
    await part_factory(order.id, name="p_done", status=PartStatus.DONE, worker_id=w.id)

    rows = await order_repo.list_in_process_parts_with_workers(order.id)
    assert rows == []


@pytest.mark.integration
async def test_worker_in_process_parts_with_orders(
    worker_repo: WorkerRepository,
    user_factory,
    order_factory,
    part_factory,
    worker_factory,
):
    # 准备: 1 user, 2 orders, 2 workers
    user = await user_factory("u_w2")
    order_a = await order_factory(user.id)
    order_b = await order_factory(user.id)

    w_target = await worker_factory("w_target")
    w_other = await worker_factory("w_other")

    # w_target 加工: 3 parts, 跨 2 orders
    await part_factory(order_a.id, name="t1", status=PartStatus.IN_PROCESS, worker_id=w_target.id)
    await part_factory(order_a.id, name="t2", status=PartStatus.IN_PROCESS, worker_id=w_target.id)
    await part_factory(order_b.id, name="t3", status=PartStatus.IN_PROCESS, worker_id=w_target.id)

    # w_target 的已完成 part: 不应返回
    await part_factory(order_a.id, name="t4", status=PartStatus.DONE, worker_id=w_target.id)

    # w_other 加工: 不应返回
    await part_factory(order_a.id, name="o1", status=PartStatus.IN_PROCESS, worker_id=w_other.id)

    # ---- 执行复杂查询 ----
    rows = await worker_repo.list_in_process_parts_with_orders(w_target.id)

    assert len(rows) == 3
    part_names = [p.name for p, _ in rows]
    assert part_names == ["t1", "t2", "t3"]
    order_ids = {o.id for _, o in rows}
    assert order_ids == {order_a.id, order_b.id}

    # 验证只返回 target worker
    worker_ids = {p.worker_id for p, _ in rows}
    assert worker_ids == {w_target.id}


@pytest.mark.integration
async def test_worker_in_process_returns_empty_when_idle(
    worker_repo: WorkerRepository,
    user_factory,
    order_factory,
    part_factory,
    worker_factory,
):
    user = await user_factory("u_idle")
    order = await order_factory(user.id)
    w_busy = await worker_factory("w_busy")
    w_idle = await worker_factory("w_idle")

    await part_factory(order.id, name="b1", status=PartStatus.IN_PROCESS, worker_id=w_busy.id)

    rows = await worker_repo.list_in_process_parts_with_orders(w_idle.id)
    assert rows == []
