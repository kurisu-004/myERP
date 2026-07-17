"""外协公司（t_outsource_company）。

- 与 t_customer 形态对称：雪花 ID + AuditMixin + 业务字段（name / 联系人 / 电话 / 地址 / is_active）。
- 软删支持：默认查询 `deleted_at IS NULL`，由 repository 强制。
- 同一公司名在未软删行内不允许重复（DB partial unique 索引 uk_t_outsource_company_name 兜底）。
- 不在 DB 层加物理外键：外协公司 ↔ 工序 映射走独立 junction 表（t_outsource_company_process）。
"""
from sqlalchemy import BigInteger, Boolean, String, text
from sqlalchemy.orm import Mapped, mapped_column

from model.audit import AuditMixin
from model.base import Base
from utils.id_gen import new_id


class TOutsourceCompany(Base, AuditMixin):
    """外协公司。

    Columns:
        id (BigInteger PK): 雪花 ID
        name (String(100)): 公司名（业务字段；DB partial unique `WHERE deleted_at IS NULL`）
        contact_name (String(50) NULL): 联系人
        contact_phone (String(50) NULL): 联系电话
        address (String(200) NULL): 地址
        is_active (Boolean): 是否启用（停用后下拉不再展示，但历史 part 仍可读）
        (audit 5 fields via AuditMixin)
    """

    __tablename__ = "t_outsource_company"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, default=new_id,
    )
    name: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True,
        comment="外协公司名",
    )
    contact_name: Mapped[str | None] = mapped_column(
        String(50), nullable=True, comment="联系人",
    )
    contact_phone: Mapped[str | None] = mapped_column(
        String(50), nullable=True, comment="联系电话",
    )
    address: Mapped[str | None] = mapped_column(
        String(200), nullable=True, comment="地址",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True,
        server_default=text("true"),
        comment="是否启用（停用后下拉不再展示）",
    )