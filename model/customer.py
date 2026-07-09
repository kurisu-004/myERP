from sqlalchemy import BigInteger, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from model.audit import AuditMixin
from model.base import Base
from utils.id_gen import new_id


class TCustomer(Base, AuditMixin):
    """客户树（邻接表）。

    典型结构：
        法拉电子 (parent_id=NULL)
        ├── 母排厂
        │   └── 一组
        └── 二厂
        路达 (parent_id=NULL)
        └── 开发一部

    注意：
    - 项目约定 **不在 DB 层加物理外键**，parent_id 的合法性（包括
      防自引用）由 service 层校验。
    - 审计字段（created_at / created_by / updated_at / updated_by / deleted_at）
      由 `AuditMixin` 提供，本类不重复声明。
    - 因为 DB 层无外键，自引用的 `parent` / `children` 关系如果用 ORM 表达
      会引入 join 推断复杂性。本类只保留 `parts`（跨表 1:N），parent/children
      通过 repository 显式查询（如 `WHERE id = :parent_id` 或递归 CTE）。

    2026-07-07 起：id 改雪花 ID（default=new_id），与其它业务表一致；
    原 BigSerial 序列已 DROP（迁移 000000000006）。所有 customer_id 入参
    在 schema 层统一为 str（CLAUDE.md §3「雪花 ID 入参必须用 str 类型」），
    service 层 parse_snowflake_id 转回 int。

    2026-07-09 起：一级客户（parent_id IS NULL）新增 `serial_prefix` 列
    （A-Z 单字母），用于派生该客户旗下零件/装配体的流水号前缀。
    叶子客户（parent_id 非空）继承所属一级客户的 prefix，本列写 NULL。
    DB 层约束（check constraint + 部分唯一索引）在迁移 000000000013 里建。
    """

    __tablename__ = "t_customer"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, default=new_id,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    # 逻辑外键 -> t_customer.id（无 DB 约束）。取值校验在 service 层完成。
    # 雪花 ID 入参在 schema 层是 str；DB 列保持 BigInteger，service 转换。
    parent_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, index=True
    )
    # 一级客户的序列号前缀（单字符 A-Z）。叶子客户 NULL，由所属一级客户派生。
    # service 层校验：一级客户创建必填、清空拒绝；DB 部分唯一索引兜底冲突。
    serial_prefix: Mapped[str | None] = mapped_column(
        String(1), nullable=True, index=True,
    )

    parts: Mapped[list["TPart"]] = relationship(
        "TPart",
        primaryjoin="TCustomer.id == TPart.customer_id",
        foreign_keys="[TPart.customer_id]",
        back_populates="customer",
        lazy="raise",
    )