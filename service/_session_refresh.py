"""AsyncSession 绑定 ORM 状态机前的显式属性刷新。"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession


async def refresh_for_state_machine(
    session: AsyncSession,
    *models: Any,
    attrs: Iterable[str],
) -> None:
    """在 async 边界显式加载后续同步代码会读取的 ORM 属性。

    状态机回调和响应模型构造都是同步属性访问；如果列在 flush 时因
    server-side ``onupdate`` 被标记为 expired，直接访问会尝试隐式 SELECT，
    从而在 AsyncSession 下触发 ``MissingGreenlet``。这里只刷新调用方声明的
    列，避免 full refresh 覆盖同一事务内尚未 flush 的其他字段。

    非 ``AsyncSession`` 的测试替身不需要刷新，保持现有纯单元测试可用。
    """
    if not isinstance(session, AsyncSession):
        return

    attribute_names = list(dict.fromkeys(attrs))
    if not attribute_names:
        return
    for model in models:
        if model is not None:
            await session.refresh(model, attribute_names=attribute_names)
