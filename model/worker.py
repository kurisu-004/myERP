from sqlalchemy import BigInteger, Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from model.audit import AuditMixin
from model.base import Base
from utils.id_gen import new_id


class TWorker(Base, AuditMixin):
    """工人/操作员。

    - `badge_code` 是工牌上的条码/二维码值，扫码端用它定位工人。
    - `id_card_no` 身份证号（18 位），与主键 `id` 联合唯一：
      等价于 "id_card_no 在非空范围内全局唯一"，同一身份证号只能对应一名工人。
    - 项目约定 **不在 DB 层加物理外键**：被 `t_part.current_worker_id`
      和 `t_part_event.worker_id` 逻辑引用，是否在职由 service 层校验。
    - 审计字段由 `AuditMixin` 提供。
    """

    __tablename__ = "t_worker"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, default=new_id
    )

    badge_code: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="工牌扫码值（车间扫码端以此定位工人）",
    )
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    id_card_no: Mapped[str | None] = mapped_column(
        String(18),
        nullable=True,
        comment="身份证号；与 id 组成联合唯一索引（同一身份证号只能对应一名工人）",
    )
    phone: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        comment="手机号（11 位手机号或带国际区号）",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
        comment="是否在职；停用后不能再扫码领取",
    )

    # 工种：每个工人对应一个工种（NULL 表示暂未分配）。
    # 逻辑外键 → t_work_type.id；service 层校验存在性。
    work_type_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        index=True,
        comment="逻辑外键 → t_work_type.id；NULL = 未分配工种",
    )