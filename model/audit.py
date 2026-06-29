"""审计字段 mixin。

把原 `model/base.py` 中硬编码的 5 个审计字段抽离到这里，便于 model 按需继承：

- `AuditMixin`            业务主表继承：5 个字段全要。
- `EventTimestampMixin`   事件 / 日志型表继承：只要 `created_at`，
                          不要 `updated_at` / 操作人 / 软删（事件型只追加）。

字段顺序与现有 DB 列序精确一致（`created_at → created_by → updated_at →
updated_by → deleted_at`），alembic 1.13+ 默认 `compare_column_order=True`
也不会触发列重排迁移。
"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column


class AuditMixin:
    """业务主表继承：5 个审计字段全包。

    - `created_at` / `updated_at`：由 DB 默认 `now()` 维护，应用层不必赋值。
    - `created_by` / `updated_by`：由调用方（service / middleware）在写入前
      显式赋值；当前无用户体系时一律 NULL。
    - `deleted_at`：软删标记。默认查询条件 `deleted_at IS NULL` 由
      repository 拼；统一走 `repository.soft_delete(model)`，不要直接
      `session.delete()`。
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    updated_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, index=True
    )


class EventTimestampMixin:
    """事件 / 日志型表继承：只要 `created_at`。

    事件型 append-only，不要 `updated_at` / 操作人 / 软删，与 `AuditMixin`
    的更新 / 删除语义冲突。事件一旦写入永不修改、永不删除。
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
