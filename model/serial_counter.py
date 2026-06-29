"""单字母客户代码的序列号计数器。

每个一级客户 (L / F / H) 持有一行 counter；序列号 = 1000 + counter % 5000，
范围 [1000, 5999]，单 prefix 同时活跃工单上限 = 5000。

设计要点：
- PK 是单字母字符串；服务层用 `SELECT ... FOR UPDATE` 串行化同 prefix
  的并发分配，不同 prefix 拿不同行锁互不阻塞。
- counter 是 `BigInteger`（永远只递增，wrap 通过 mod 5000 实现），
  正常情况下每个 prefix 在生命周期内用不完 2^63。
- 不加额外索引——`acquire_serial` 唯一查询路径是
  `WHERE prefix = ? FOR UPDATE`，主键已覆盖。
- 不放物理外键，与 CLAUDE.md 约定一致。
"""
from __future__ import annotations

from sqlalchemy import BigInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from model.audit import AuditMixin
from model.base import Base


class TSerialCounter(Base, AuditMixin):
    """序列号计数器表。"""

    __tablename__ = "t_serial_counter"

    # 为什么 String(1) 不是 CHAR(1)：
    # - 项目其它 PK 用 String/VARCHAR，保持一致；
    # - CHAR(N) 在 PostgreSQL 上对单字节 ASCII 不会 padding，但 alembic
    #   反射时容易跟 VARCHAR 触发 type 差异告警；
    # - String(1) 在 PG 上就是 VARCHAR(1)，零开销。
    prefix: Mapped[str] = mapped_column(String(1), primary_key=True)

    counter: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
