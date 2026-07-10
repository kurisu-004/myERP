"""账号主表（t_user）。

只承载「账号」语义——username + 密码哈希 + 基本资料 + is_active。
业务身份（工人 / 员工）继续在 `t_worker`。`t_user` 与 `t_worker` 未来可
通过 account ↔ worker 多对多关联（**本轮不做**）。

权限角色（MANAGER / SHELF_ACCOUNT 等）放在 `t_user_role` 里，一个用户可多角色。
"""
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from model.audit import AuditMixin
from model.base import Base
from utils.id_gen import new_id


class TUser(Base, AuditMixin):
    """账号实体。

    - `username` 全局唯一（同 `t_worker.badge_code` 的部分唯一索引风格：
      `WHERE deleted_at IS NULL`），service 层小写比对。
    - `password_hash` bcrypt（passlib）。
    - `full_name` 显示用（header / 列表）。
    - `last_login_at` 仅用于审计展示，不参与鉴权。
    """

    __tablename__ = "t_user"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, default=new_id
    )
    username: Mapped[str] = mapped_column(String(50), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(50), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    # refresh token 轮转计数器：每次成功 POST /auth/refresh 后 +1；
    # 旧的 refresh token（payload.ver 落后当前值）立即失效。
    refresh_token_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0",
    )
