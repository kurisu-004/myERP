from sqlalchemy import BigInteger, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from model.audit import EventTimestampMixin
from model.base import Base
from utils.id_gen import new_id


class TPartEvent(Base, EventTimestampMixin):
    """订单全生命周期事件流。

    任何会改变 part 状态或归属的事件都记录到这张表上，不只扫码事件：

    - CREATED / RELEASED / PICKED_UP / RETURNED / INSPECTED
    - STATUS_CHANGED / REPAIR_STARTED / REPAIR_COMPLETED
    - CANCELLED / COMPLETED

    DB 列一律用普通类型：
    - `event_type` / `from_status` / `to_status` 是 `varchar(...)`，
      取值合法性由 Python Enum（`PartEventType` / `PartStatus`）
      在 service 层校验。**不**用 PostgreSQL 原生 ENUM。
    - `part_id` / `worker_id` / `outsource_company_id` 是逻辑外键，无 DB FK 约束。

    事件型 append-only：继承 `EventTimestampMixin`（只要 `created_at`），
    与业务主表的 `AuditMixin` 不共用——后者带 `updated_at` / 操作人 / 软删，
    会污染事件语义。事件一旦写入永不修改、永不删除。
    """

    __tablename__ = "t_part_event"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, default=new_id
    )

    # 逻辑外键 → t_part.id
    part_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # 逻辑外键 → t_worker.id；无工人参与的事件（CREATED / RELEASED 等）为 NULL
    worker_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # 操作者：调用 service 的登录用户 ID（雪花 ID），NULL = 系统 / 历史数据
    # 与 t_user.id 是逻辑外键，无 DB FK 约束（CLAUDE.md §1）
    created_by: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True,
        comment="操作者 t_user.id（NULL = 系统调度/历史数据）",
    )

    # 事件类型（Python: PartEventType）
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    # 事件发生前后状态（Python: PartStatus 或 None）
    from_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # 扫码相关字段
    drawing_code: Mapped[str | None] = mapped_column(
        String(100), nullable=True, comment="扫码事件时传入的图纸码（即 drawing_no）"
    )
    badge_code: Mapped[str | None] = mapped_column(
        String(50), nullable=True, comment="扫码事件时传入的工牌码"
    )

    # 外协对账字段（2026-07-28 新增）：
    # SENT_TO_OUTSOURCE / RECEIVED_FROM_OUTSOURCE 时填入对应外协公司 id；
    # 后续可按此列聚合每个外协公司的发送/接收事件，与对账单核对。
    # 不加 DB FK（CLAUDE.md §1）。
    outsource_company_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True,
        comment="SENT_TO_OUTSOURCE / RECEIVED_FROM_OUTSOURCE 时填入；外协对账按此列聚合",
    )

    note: Mapped[str | None] = mapped_column(
        String(500), nullable=True, comment="可选备注 / 扩展元数据"
    )

    __table_args__ = (
        # 部分索引：仅 outsource_company_id 非 NULL 的事件占索引空间；
        # 99% 的非外协事件不占。t_part_event 是 append-only，无 deleted_at 列。
        Index(
            "ix_t_part_event_outsource_company_id",
            "outsource_company_id",
            postgresql_where=text("outsource_company_id IS NOT NULL"),
        ),
    )
