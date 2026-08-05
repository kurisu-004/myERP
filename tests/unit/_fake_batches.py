"""单测用内存假 PartBatchRepository（2026-07-29 批次化）。

workflow / crud 单测全部走 mock 仓储，不接 DB。本假仓储提供批次语义的
最小真实行为：

- `list_by_part`：若该 part 尚无批次记录，**auto-vivify 根批次**（镜像
  part 当前 status/location/holder/next_process_id/placed_at）——让
  存量「单根批次=整单」的测试零改动通过；拆分测试显式 seed 多批次。
- 状态机在批次对象上的字段修改直接落在 store 里的同一对象上，
  rollup / resolve 读到的是同一引用，行为与真实 ORM identity map 一致。
- `get_for_update` 等同 `get_by_id`（无锁语义；并发路径不在单测覆盖）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Awaitable, Callable

from core.time import now_naive
from model import TPart, TPartBatch


class FakePartBatchRepository:
    def __init__(
        self,
        parts_provider: Callable[[int], Awaitable[TPart | None]] | None = None,
    ) -> None:
        self.store: dict[int, TPartBatch] = {}
        self._parts_provider = parts_provider
        self._next_id = 900_000_001

    @staticmethod
    def chain_attrs(repo, *names: str):
        """按属性名在调用时动态解析 part 查找函数（get_by_id / get_by_serial）。

        单测常在 fixture 之后**重绑** ``repo.get_by_id = AsyncMock(...)``；
        若在 fixture 里捕获 bound mock 会拿到未配置的旧引用，所以调用时
        再 ``getattr``。未配置的 AsyncMock 返回的 MagicMock（.id 非 int）
        会被跳过。
        """
        async def _provider(part_id: int):
            for name in names:
                g = getattr(repo, name, None)
                if g is None:
                    continue
                part = await g(part_id)
                if part is not None and isinstance(getattr(part, "id", None), int):
                    return part
            return None
        return _provider

    @staticmethod
    def chain_provider(*getters):
        """多个 part 查找函数串联（get_by_id / get_by_serial 等），首个非 None 生效。

        扫码类 service 方法经 get_by_serial 定位 part，单测里 get_by_id 常未配置；
        vivify 依次尝试，任一返回 part 即 seed 根批次。
        """
        async def _provider(part_id: int):
            for g in getters:
                part = await g(part_id)
                if part is not None and isinstance(getattr(part, "id", None), int):
                    return part
            return None
        return _provider

    # ---- 测试辅助 ----
    def _alloc_id(self) -> int:
        self._next_id += 1
        return self._next_id

    def seed(
        self, part: TPart, *, quantity: int | None = None,
    ) -> TPartBatch:
        """显式登记一个镜像 part 当前状态的根批次（幂等）。

        mock 风格单测（``MagicMock(spec=TPart)`` 且 ``part.sm`` 也是 mock）：
        批次本身用 MagicMock 承载，并**共享 part.sm**——service 调
        ``batch.sm.X(...)`` 与旧版 ``part.sm.X(...)`` 断言逐字兼容；
        mock sm 不改状态，与旧测试「断言调用而非状态」的语义一致。

        真实 TPart fixture：返回真实 TPartBatch（真实状态机就地改批次，
        rollup 回写 part，行为与旧版一致）。
        """
        for b in self.store.values():
            if b.part_id == part.id and b.batch_no == 1:
                return b
        from unittest.mock import MagicMock as _MM

        part_sm = getattr(part, "sm", None)
        if isinstance(part, _MM) and isinstance(part_sm, _MM):
            b = _MM(spec=TPartBatch)
            b.id = self._alloc_id()
            b.part_id = part.id
            b.batch_no = 1
            b.quantity = quantity if quantity is not None else part.quantity
            b.status = part.status
            b.location = part.location
            b.current_holder_id = part.current_holder_id
            b.next_process_id = part.next_process_id
            b.placed_at = part.placed_at
            b.delivery_note_id = None
            b.parent_batch_id = None
            b.deleted_at = None
            b.version = 0
            b.sm = part_sm  # 关键：共享 mock sm
            self.store[b.id] = b
            return b
        b = TPartBatch(
            id=self._alloc_id(),
            part_id=part.id,
            batch_no=1,
            quantity=quantity if quantity is not None else part.quantity,
            status=part.status,
            location=part.location,
            current_holder_id=part.current_holder_id,
            next_process_id=part.next_process_id,
            placed_at=part.placed_at,
        )
        b.version = 0
        self.store[b.id] = b
        return b

    def add_batch(
        self, part: TPart, *, batch_no: int, quantity: int,
        status: str, location: str | None = None,
        current_holder_id: int | None = None,
    ) -> TPartBatch:
        """拆分场景显式追加一个批次。"""
        b = TPartBatch(
            id=self._alloc_id(),
            part_id=part.id,
            batch_no=batch_no,
            quantity=quantity,
            status=status,
            location=location,
            current_holder_id=current_holder_id,
        )
        b.version = 0
        self.store[b.id] = b
        return b

    async def _vivify(self, part_id: int) -> None:
        if any(b.part_id == part_id for b in self.store.values()):
            return
        if self._parts_provider is None:
            return
        part = await self._parts_provider(part_id)
        if part is not None:
            self.seed(part)

    # ---- 仓储接口 ----
    async def create(self, batch: TPartBatch) -> TPartBatch:
        if getattr(batch, "id", None) is None:
            batch.id = self._alloc_id()
        self.store[batch.id] = batch
        return batch

    async def update(self, batch: TPartBatch) -> TPartBatch:
        self.store[batch.id] = batch
        return batch

    async def soft_delete(self, batch: TPartBatch) -> None:
        batch.deleted_at = now_naive()

    async def get_by_id(
        self, batch_id: int, *, include_deleted: bool = False,
    ) -> TPartBatch | None:
        b = self.store.get(batch_id)
        if b is None:
            return None
        if not include_deleted and b.deleted_at is not None:
            return None
        return b

    async def get_for_update(self, batch_id: int) -> TPartBatch | None:
        return await self.get_by_id(batch_id)

    async def list_by_part(
        self, part_id: int, *, include_deleted: bool = False,
    ) -> list[TPartBatch]:
        await self._vivify(part_id)
        out = [
            b for b in self.store.values()
            if b.part_id == part_id
            and (include_deleted or b.deleted_at is None)
        ]
        out.sort(key=lambda b: b.batch_no)
        return out

    async def list_active_by_part_ids(
        self, part_ids: list[int],
    ) -> list[TPartBatch]:
        # 只读路径（_to_out 送货单解析）不 vivify，避免给 mock 增加意外调用
        return [
            b for b in self.store.values()
            if b.part_id in part_ids and b.deleted_at is None
        ]

    async def next_batch_no(self, part_id: int) -> int:
        await self._vivify(part_id)
        nos = [b.batch_no for b in self.store.values() if b.part_id == part_id]
        return (max(nos) + 1) if nos else 1

    # ---- 下列查询在纯 mock 单测里默认空；需要的测试自行 monkeypatch ----
    async def list_for_work_type(self, **_):
        return []

    async def list_for_work_type_all_shelves(self, **_):
        return []

    async def list_held_by_worker(self, **_):
        return []

    async def count_held_by_worker(self, *, worker_id: int) -> int:
        """2026-08-05：工种可领取上限校验用 mock（默认返回 0=无持有/不限）。"""
        return 0

    async def list_batches_with_part(self, **_):
        return []

    async def count_batches_with_part(self, **_):
        return 0

    async def list_by_delivery_note(self, note_id: int):
        return [
            (b, None) for b in self.store.values()
            if b.delivery_note_id == note_id and b.deleted_at is None
        ]

    async def find_delivered_older_than(self, threshold: datetime):
        return []
