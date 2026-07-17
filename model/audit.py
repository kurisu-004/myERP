"""审计字段 mixin。

把原 `model/base.py` 中硬编码的 5 个审计字段抽离到这里，便于 model 按需继承：

- `AuditMixin`            业务主表继承：version + 5 个审计字段。
- `EventTimestampMixin`   事件 / 日志型表继承：只要 `created_at`，
                          不要 version / `updated_at` / 操作人 / 软删（事件型只追加）。

字段顺序与现有 DB 列序精确一致
（`version → created_at → created_by → updated_at → updated_by → deleted_at`），
alembic 1.13+ 默认 `compare_column_order=True` 也不会触发列重排迁移。

乐观锁（OCC）
-------------
`version` 是 SQLAlchemy 的 `version_id_col`：每次 ORM dirty UPDATE 自动
追加 `WHERE id=? AND version=?` 并 `SET version=version+1`；0 行更新（被
其他事务抢先修改）→ flush 时抛 `sqlalchemy.orm.exc.StaleDataError`，由
`core/exception_handler.py` 注册的全局处理器转 `BizError(BIZ_VERSION_CONFLICT, 409)`。

- `version_id_generator` 保持默认 True：SQLAlchemy 在 Python 端同步
  `model.version += 1` 并保留其它字段值，flush 后**不触发 expire / refresh**
  → 不会触发 SQLAlchemy 2.0 async session 的同步 IO → 不会重现
  CLAUDE.md §13 / §15 警示的 MissingGreenlet。
- `onupdate=func.now()` 的 `updated_at` 同样走 RETURNING 回填，行为不变。
- 业务调用方（service / state machine callback）**不需要**手动管理 version：
  仍然保持「fetch → 就地改 ORM 字段 → repo.update() / session.flush()」的模式。

实现细节：
- 使用 `declared_attr.directive` 在每个继承 AuditMixin 的子类创建时自动注入
  `__mapper_args__ = {"version_id_col": "version"}`。
- SQLAlchemy 2.0 `Mapped` 风格下 `default=0` **不会**在 ORM 实例构造时
  自动填充（仅影响 INSERT SQL 表达式），所以必须靠 `__init__` 兜底给未传
  `version` 的实例默认填 0，兼容现有测试 fixture 不显式传 version 的情况。
"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, func, text
from sqlalchemy.orm import Mapped, declared_attr, mapped_column


class AuditMixin:
    """业务主表继承：version + 5 个审计字段。

    - `version`：SQLAlchemy version_id_col，乐观锁版本号；`server_default=0`，
      每次 UPDATE 自增；冲突时 StaleDataError → 409。
    - `created_at` / `updated_at`：由 DB 默认 `now()` 维护，应用层不必赋值。
    - `created_by` / `updated_by`：由调用方（service / middleware）在写入前
      显式赋值；当前无用户体系时一律 NULL。
    - `deleted_at`：软删标记。默认查询条件 `deleted_at IS NULL` 由
      repository 拼；统一走 `repository.soft_delete(model)`，不要直接
      `session.delete()`。
    """

    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
        comment="乐观锁版本号；每次 UPDATE 自增；冲突抛 BIZ_VERSION_CONFLICT 409",
    )

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

    @declared_attr.directive
    def __mapper_args__(cls) -> dict:
        """把 `version` 列注册为 ORM version_id_col。

        `declared_attr.directive` 让每个继承 AuditMixin 的 ORM 自动获得
        该 mapper option。返回 cls.version（Mapped 描述符），SQLAlchemy
        在 mapper 构建时会从 class descriptor 解析成对应的 Column 对象。
        """
        return {"version_id_col": cls.version}


class EventTimestampMixin:
    """事件 / 日志型表继承：只要 `created_at`。

    事件型 append-only，不要 `version` / `updated_at` / 操作人 / 软删，
    与 `AuditMixin` 的更新 / 删除语义冲突。事件一旦写入永不修改、永不删除。
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


# 在所有 AuditMixin 子类构造时自动给 `version` 默认填 0（兜底）。
# SQLAlchemy 2.0 的 `mapped_column(default=...)` 仅影响 INSERT SQL 表达式，
# 不会在 ORM 实例构造时自动填充。用 SQLAlchemy 的 `init` 事件统一处理。
# `propagate=True` 让注册到 Base 的监听器被子类继承——所有继承
# AuditMixin 的 ORM 在 create(__init__) 时触发。
from sqlalchemy import event  # noqa: E402
from sqlalchemy.orm import DeclarativeBase  # noqa: E402


@event.listens_for(DeclarativeBase, "init", propagate=True)
def _audit_mixin_init_version(target, args, kwargs) -> None:
    """AuditMixin 子类的 ORM 实例若未传 `version`，自动填 0。

    防止测试 fixture / 业务代码构造 ORM 时忘记传 version 而 Pydantic
    schema 校验失败（version 是非空 int）。
    """
    if isinstance(target, AuditMixin) and "version" not in kwargs:
        kwargs["version"] = 0
