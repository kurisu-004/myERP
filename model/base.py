from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """统一 declarative 注册器。

    不放任何字段——审计字段由 `model.audit.AuditMixin` 提供，
    每个 model 按需继承：

    - 业务主表：`class TPart(Base, AuditMixin): ...`
    - 事件/日志表：`class TPartEvent(Base, EventTimestampMixin): ...`

    `__abstract__ = True` 保留以防有人误继承 `Base` 直接建表。
    """

    __abstract__ = True
