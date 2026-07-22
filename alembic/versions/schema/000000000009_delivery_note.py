"""add t_delivery_note + t_delivery_note_event + t_delivery_note_counter
+ t_part.delivery_note_id（PR-G 送货单管理 2026-07-22）

把原先无状态的 `/delivery-notes/generate` XLSX 导出升级为完整状态化模块：
DRAFT → SUBMITTED → PICKED_UP → ARCHIVED，零件↔送货单多对一，文员/司机分角色
（沿用工牌识别，不新增 UserRole）。

新表：
- t_delivery_note：送货单聚合根（AuditMixin，乐观锁）
- t_delivery_note_event：状态转换事件流（EventTimestampMixin，append-only）
- t_delivery_note_counter：每日单号计数器（DN-YYYYMMDD-NNNN）

修改表：
- t_part：加 delivery_note_id BigInteger NULL 逻辑 FK；非唯一索引供查询。

菜单迁移：
- 旧 `delivery_notes_new`（指向 /delivery-notes/new，PR-F 2026-07-17 老 XLSX 导出页）
  重命名为 `delivery_notes_manage`（指向 /delivery-notes，新管理页面）；
  MANAGER + CLERK 的 role_menu 绑定通过更新 t_menu.code 自动跟随。

Revision ID: 000000000009
Revises: 000000000008
Create Date: 2026-07-22
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "000000000009"
# 链在 008 之后（任意一个 008 即可；当前仓两个 008 都是 head，二选一合并时再处理）；
# 008 已经在 prod_data/000000000008_remove_assemblies_new_menu.py 占用，schema
# 与 prod_data 共享 revision id 编号规范，本仓本次先接 008 即可。
down_revision: Union[str, None] = "000000000008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _version_col() -> sa.Column:
    """乐观锁 version 列工厂（与 schema/000000000003 一致）。"""
    return sa.Column(
        "version", sa.Integer(), nullable=False,
        server_default=sa.text("0"),
        comment="乐观锁版本号；每次 UPDATE 自增；冲突抛 BIZ_VERSION_CONFLICT 409",
    )


def upgrade() -> None:
    # ============================================================
    # 1) t_delivery_note_counter：每日单号计数器（PK = 日期字符串）
    #    用于发放 `DN-YYYYMMDD-NNNN` 单号；锁行原子递增。
    # ============================================================
    op.create_table(
        "t_delivery_note_counter",
        sa.Column(
            "date_ymd", sa.String(length=8), primary_key=True,
            comment="自然日，格式 YYYYMMDD",
        ),
        sa.Column(
            "last_value", sa.Integer(), nullable=False,
            server_default=sa.text("0"),
            comment="当日已发放的最大序列号（0 起；NNN 段从 1 开始）",
        ),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # ============================================================
    # 2) t_delivery_note：送货单聚合根（业务主表 + AuditMixin + OCC）
    # ============================================================
    op.create_table(
        "t_delivery_note",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "delivery_note_no", sa.String(length=16), nullable=False,
            comment="单号，格式 DN-YYYYMMDD-NNNN（4 位数）；"
                    "唯一约束见 uq_t_delivery_note_no_active",
        ),
        sa.Column(
            "customer_id", sa.BigInteger(), nullable=False,
            comment="逻辑外键 → t_customer.id；送货单的客户（叶子二级）",
        ),
        sa.Column(
            "status", sa.String(length=16), nullable=False,
            server_default=sa.text("'DRAFT'"),
            comment="DRAFT / SUBMITTED / PICKED_UP / ARCHIVED",
        ),
        # —— 时间戳 ——
        sa.Column("submitted_at", sa.DateTime(), nullable=True),
        sa.Column("picked_up_at", sa.DateTime(), nullable=True),
        # —— 操作人/司机 ——
        sa.Column(
            "submitted_by", sa.BigInteger(), nullable=True,
            comment="提交人 t_user.id（MANAGER / CLERK）",
        ),
        sa.Column(
            "picked_up_by", sa.BigInteger(), nullable=True,
            comment="领取时登入账号 t_user.id（一般是 SHELF_ACCOUNT 等扫码台账号）",
        ),
        sa.Column(
            "driver_worker_id", sa.BigInteger(), nullable=True,
            comment="司机 t_worker.id；必须是 work_type.code='送货司机' 的活跃工人",
        ),
        sa.Column(
            "note", sa.String(length=500), nullable=True,
            comment="备注",
        ),
        # —— 乐观锁 version ——
        _version_col(),
        # —— 审计字段（顺序与 AuditMixin 对齐）——
        sa.Column(
            "created_at", sa.DateTime(), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "status IN ('DRAFT','SUBMITTED','PICKED_UP','ARCHIVED')",
            name="ck_t_delivery_note_status",
        ),
    )
    op.create_index(
        "uq_t_delivery_note_no_active",
        "t_delivery_note",
        ["delivery_note_no"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_t_delivery_note_status",
        "t_delivery_note", ["status"],
    )
    op.create_index(
        "ix_t_delivery_note_customer_id",
        "t_delivery_note", ["customer_id"],
    )
    op.create_index(
        "ix_t_delivery_note_submitted_at",
        "t_delivery_note", ["submitted_at"],
    )
    op.create_index(
        "ix_t_delivery_note_deleted_at",
        "t_delivery_note", ["deleted_at"],
    )

    # ============================================================
    # 3) t_delivery_note_event：状态机事件流（append-only，无 OCC）
    # ============================================================
    op.create_table(
        "t_delivery_note_event",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "delivery_note_id", sa.BigInteger(), nullable=False,
            comment="逻辑外键 → t_delivery_note.id",
        ),
        sa.Column(
            "event_type", sa.String(length=32), nullable=False,
            comment="CREATED / EDITED / ITEM_ADDED / ITEM_REMOVED / "
                    "SUBMITTED / RECALLED / PICKUP_SCANNED / PICKED_UP / ARCHIVED",
        ),
        sa.Column("from_status", sa.String(length=16), nullable=True),
        sa.Column("to_status", sa.String(length=16), nullable=True),
        sa.Column(
            "drawing_code", sa.String(length=100), nullable=True,
            comment="扫码事件时传入的图纸码（serial_no）",
        ),
        sa.Column(
            "badge_code", sa.String(length=50), nullable=True,
            comment="扫码事件时传入的工牌码",
        ),
        sa.Column(
            "note", sa.String(length=500), nullable=True,
            comment="事件备注 / 扩展元数据",
        ),
        sa.Column(
            "scanned_count", sa.Integer(), nullable=True,
            comment="扫码累积时填写（仅 PICKUP_SCANNED 事件）",
        ),
        sa.Column(
            "expected_count", sa.Integer(), nullable=True,
            comment="本单的零件总数（仅 PICKUP_SCANNED 事件）",
        ),
        sa.Column(
            "created_by", sa.BigInteger(), nullable=True,
            comment="操作用户 t_user.id",
        ),
        # EventTimestampMixin：append-only，只要 created_at
        sa.Column(
            "created_at", sa.DateTime(), nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_t_delivery_note_event_note_id",
        "t_delivery_note_event", ["delivery_note_id"],
    )
    op.create_index(
        "ix_t_delivery_note_event_note_created",
        "t_delivery_note_event",
        ["delivery_note_id", "created_at"],
    )
    op.create_index(
        "ix_t_delivery_note_event_created_at",
        "t_delivery_note_event", ["created_at"],
    )

    # ============================================================
    # 4) t_part：加 delivery_note_id 逻辑 FK + 查询索引
    #    一个零件在非终态下最多被关联到一张送货单，由 service 层
    #    `add_parts` 校验（`if part.delivery_note_id is not None: reject`）；
    #    DB 层不做 partial unique（语法上等于"同 note 至多 1 part"，与语义相反）。
    #    PICKED_UP 时由 service 主动把 delivery_note_id 置 NULL，让 part 进入
    #    终态后自然失联；前端 PartDetail「所属送货单」卡片随之消失。
    # ============================================================
    op.add_column(
        "t_part",
        sa.Column(
            "delivery_note_id", sa.BigInteger(), nullable=True,
            comment="逻辑外键 → t_delivery_note.id（active 状态下最多 1 张）",
        ),
    )
    op.create_index(
        "ix_t_part_delivery_note_id",
        "t_part", ["delivery_note_id"],
    )

    # ============================================================
    # 5) 菜单迁移：delivery_notes_new → delivery_notes_manage
    #    原 prod_data/000000000002 在 t_menu 上 INSERT code='delivery_notes_new'
    #    并关联 MANAGER + CLERK 的 t_role_menu（menu_id FK 不动 → 自动跟随）。
    #    司机扫码台不新增 menu（用现有 /scan 子链 + allowRoles=['SHELF_ACCOUNT']）。
    # ============================================================
    op.execute(
        sa.text(
            """
            UPDATE t_menu
               SET code    = 'delivery_notes_manage',
                   path    = '/delivery-notes',
                   title   = '送货单',
                   updated_at = now()
             WHERE code = 'delivery_notes_new'
               AND deleted_at IS NULL
            """
        )
    )


def downgrade() -> None:
    # 5) 菜单还原（保留旧 code 便于回退后老页面仍可访问）
    op.execute(
        sa.text(
            """
            UPDATE t_menu
               SET code    = 'delivery_notes_new',
                   path    = '/delivery-notes/new',
                   title   = '生成送货单',
                   updated_at = now()
             WHERE code = 'delivery_notes_manage'
               AND deleted_at IS NULL
            """
        )
    )

    # 4) t_part.delivery_note_id 还原
    op.drop_index("ix_t_part_delivery_note_id", table_name="t_part")
    op.drop_column("t_part", "delivery_note_id")

    # 3) t_delivery_note_event 还原
    op.drop_index("ix_t_delivery_note_event_created_at", table_name="t_delivery_note_event")
    op.drop_index("ix_t_delivery_note_event_note_created", table_name="t_delivery_note_event")
    op.drop_index("ix_t_delivery_note_event_note_id", table_name="t_delivery_note_event")
    op.drop_table("t_delivery_note_event")

    # 2) t_delivery_note 还原
    op.drop_index("ix_t_delivery_note_deleted_at", table_name="t_delivery_note")
    op.drop_index("ix_t_delivery_note_submitted_at", table_name="t_delivery_note")
    op.drop_index("ix_t_delivery_note_customer_id", table_name="t_delivery_note")
    op.drop_index("ix_t_delivery_note_status", table_name="t_delivery_note")
    op.drop_index("uq_t_delivery_note_no_active", table_name="t_delivery_note")
    op.drop_table("t_delivery_note")

    # 1) t_delivery_note_counter 还原
    op.drop_table("t_delivery_note_counter")
