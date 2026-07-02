"""货架实体（t_shelf）。

不带账号字段——账号与货架的关联走 `t_user_role` 的
`(role='SHELF_ACCOUNT', scope_type='shelf', scope_id=<shelf.id>)`。
一个货架可有多个 SHELF_ACCOUNT 账号。

`zone` ∈ {PRODUCTION, INSPECTION}（ShelfZone enum），service 层校验。
"""
from sqlalchemy import BigInteger, Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from model.audit import AuditMixin
from model.base import Base
from utils.id_gen import new_id


class TShelf(Base, AuditMixin):
    """货架实体。

    - `code` 全局唯一（`WHERE deleted_at IS NULL`），例如 `PROD-A1` / `INSP-I1`。
    - `zone` 由 service 层强校验（`ShelfZone` enum）。
    - `is_active=False` 时不入 login 时的 scope 解析，新工人不能在新机器上
      登入；已有零件仍按历史 holder 留存。
    """

    __tablename__ = "t_shelf"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, default=new_id
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    zone: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    location: Mapped[str | None] = mapped_column(String(200), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
