"""序列号计数器仓储。

核心方法 `acquire_serial(prefix)` 是序列号分配的唯一入口，
必须在 service 层进入写事务时调用（`get_session` 已经把这个包成请求级事务）。
"""
from __future__ import annotations

from fastapi import status as http_status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.error_code import ErrCode
from core.exception import BizError
from core.serial import (
    SERIAL_ALLOC_MAX_ATTEMPTS,
    SERIAL_MIN,
    SERIAL_POOL_SIZE,
    SERIAL_RELEASE_STATUSES,
)
from model import TPart
from model.serial_counter import TSerialCounter


class SerialCounterRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def acquire_serial(self, prefix: str) -> str:
        """为指定 prefix 分配一个未被活跃工单占用的序列号。

        算法：
        1. `SELECT ... FOR UPDATE` 锁住 prefix 对应的 counter 行；
           找不到该 prefix → `BIZ_SERIAL_PREFIX_UNKNOWN`（不自动建）。
        2. 循环最多 `SERIAL_ALLOC_MAX_ATTEMPTS` (=5000) 次：
           a. candidate = `SERIAL_MIN + counter % SERIAL_POOL_SIZE`
              （counter 在 Python 局部变量里累加，不再每轮 round-trip DB）
           b. 在 `t_part` 里查是否存在未软删且状态非 COMPLETED/CANCELLED
              的同 serial_no 行。
           c. 若不存在 → `row.counter = counter + 1`, flush, return。
           d. 若存在 → `counter += 1`，进入下一轮。
        3. 5000 次都撞 → `BIZ_PART_SERIAL_EXHAUSTED`。

        并发模型：
        - 不同 prefix 的事务拿不同行锁，**互不阻塞**。
        - 同 prefix 的事务在第 1 步的行锁上排队；后者读到的是前者
          commit 后的 counter 值。
        - `t_part.serial_no` 上的 `uk_t_part_serial_no` 部分唯一索引
          仍然作为防御性兜底（覆盖未来不经此方法直接写 serial_no 的路径）。
        """
        # 1. 锁 counter 行
        stmt = (
            select(TSerialCounter)
            .where(TSerialCounter.prefix == prefix)
            .with_for_update()
        )
        result = await self.session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            raise BizError(
                code=ErrCode.BIZ_SERIAL_PREFIX_UNKNOWN,
                message=(
                    f"serial counter for prefix {prefix!r} not seeded; "
                    "add it to t_serial_counter before creating parts"
                ),
                http_status=http_status.HTTP_400_BAD_REQUEST,
            )

        # 2. 把 counter 复制到 Python 局部变量，避免每轮 round-trip DB。
        # 后续修改只动局部 + ORM 对象属性，DB 在成功/EXHAUSTED 时一次性 flush。
        counter = row.counter

        for _ in range(SERIAL_ALLOC_MAX_ATTEMPTS):
            candidate_int = SERIAL_MIN + (counter % SERIAL_POOL_SIZE)
            candidate = f"{prefix}{candidate_int}"

            # 3. 查 t_part 是否有人持有这个号（活跃 = 未软删 && 非释放态）
            active_stmt = (
                select(1)
                .select_from(TPart)
                .where(
                    TPart.serial_no == candidate,
                    TPart.deleted_at.is_(None),
                    TPart.status.notin_(
                        [s.value for s in SERIAL_RELEASE_STATUSES]
                    ),
                )
                .limit(1)
            )
            taken = (
                await self.session.execute(active_stmt)
            ).scalar_one_or_none()
            if taken is None:
                # 4. 找到空号：把 counter 写回（in-place 修改 ORM 对象）
                row.counter = counter + 1
                await self.session.flush()
                return candidate

            # 5. 撞号：counter +1，重试
            counter += 1

        # 6. 走完一整圈都撞 → pool 真的满了
        row.counter = counter
        await self.session.flush()
        raise BizError(
            code=ErrCode.BIZ_PART_SERIAL_EXHAUSTED,
            message=(
                f"serial pool for prefix {prefix!r} exhausted "
                f"(>{SERIAL_POOL_SIZE} active orders)"
            ),
            http_status=http_status.HTTP_409_CONFLICT,
        )
