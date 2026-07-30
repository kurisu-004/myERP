"""outsource shipment + reusable per-(part,process) quotes (2026-07-30)

Revision ID: 000000000022
Revises: 000000000021
Create Date: 2026-07-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from utils.id_gen import new_id

revision: str = "000000000022"
down_revision: Union[str, None] = "000000000021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. 建 t_outsource_shipment
    op.create_table(
        "t_outsource_shipment",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("quote_id", sa.BigInteger(), nullable=False),
        sa.Column("part_id", sa.BigInteger(), nullable=False),
        sa.Column("batch_id", sa.BigInteger(), nullable=True),
        sa.Column("outsource_company_id", sa.BigInteger(), nullable=False),
        sa.Column("process_id", sa.BigInteger(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="OUTSOURCING"),
        sa.Column("sent_at", sa.DateTime(), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=True),
        sa.Column("is_billed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("status IN ('OUTSOURCING','RECEIVED','CANCELLED')", name="ck_t_outsource_shipment_status"),
        sa.CheckConstraint("quantity > 0", name="ck_t_outsource_shipment_quantity_positive"),
    )
    op.create_index("ix_t_outsource_shipment_quote_id", "t_outsource_shipment", ["quote_id"])
    op.create_index("ix_t_outsource_shipment_part_id", "t_outsource_shipment", ["part_id"])
    op.create_index("ix_t_outsource_shipment_batch_id", "t_outsource_shipment", ["batch_id"])
    op.create_index("ix_t_outsource_shipment_outsource_company_id", "t_outsource_shipment", ["outsource_company_id"])
    op.create_index("ix_t_outsource_shipment_process_id", "t_outsource_shipment", ["process_id"])
    op.create_index("ix_t_outsource_shipment_status", "t_outsource_shipment", ["status"])
    op.create_index(
        "uq_t_outsource_shipment_open_batch",
        "t_outsource_shipment",
        ["batch_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL AND status='OUTSOURCING'"),
    )

    # 2. t_outsource_quote 加 is_direct
    op.add_column(
        "t_outsource_quote",
        sa.Column("is_direct", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )

    # 3. 数据迁移
    conn = op.get_bind()

    # 3a. 标记 DIRECT 占位
    conn.execute(sa.text(
        "UPDATE t_outsource_quote SET is_direct = true "
        "WHERE review_note LIKE '系统自动创建%' AND deleted_at IS NULL"
    ))

    # 3b. 存量 OUTSOURCING/RECEIVED/BILLED → 各 INSERT 一行 shipment
    # 用 Python 循环逐行生成雪花 ID，避免 SQL 端 gen_random_uuid()::bigint 转换失败
    rows = conn.execute(sa.text("""
        SELECT
            q.id AS quote_id, q.part_id, q.outsource_company_id, q.process_id,
            COALESCE(q.quantity, p.quantity) AS quantity,
            q.price,
            CASE q.status
                WHEN 'OUTSOURCING' THEN 'OUTSOURCING'
                WHEN 'RECEIVED' THEN 'RECEIVED'
                WHEN 'BILLED' THEN 'RECEIVED'
            END AS status,
            q.sent_at, q.received_at,
            CASE WHEN q.status = 'BILLED' THEN true ELSE q.is_billed END AS is_billed
        FROM t_outsource_quote q
        JOIN t_part p ON p.id = q.part_id
        WHERE q.deleted_at IS NULL
          AND q.status IN ('OUTSOURCING', 'RECEIVED', 'BILLED')
    """)).fetchall()
    for row in rows:
        conn.execute(sa.text("""
            INSERT INTO t_outsource_shipment (
                id, quote_id, part_id, batch_id, outsource_company_id, process_id,
                quantity, unit_price, status, sent_at, received_at, is_billed,
                version, created_at, updated_at
            ) VALUES (
                :id, :quote_id, :part_id, NULL, :outsource_company_id, :process_id,
                :quantity, :price, :status, :sent_at, :received_at, :is_billed,
                0, NOW(), NOW()
            )
        """), {
            "id": new_id(),
            "quote_id": row.quote_id,
            "part_id": row.part_id,
            "outsource_company_id": row.outsource_company_id,
            "process_id": row.process_id,
            "quantity": row.quantity,
            "price": row.price,
            "status": row.status,
            "sent_at": row.sent_at,
            "received_at": row.received_at,
            "is_billed": row.is_billed,
        })

    # 3c. 这些报价 status 归位 APPROVED
    conn.execute(sa.text(
        "UPDATE t_outsource_quote SET status = 'APPROVED' "
        "WHERE deleted_at IS NULL AND status IN ('OUTSOURCING', 'RECEIVED', 'BILLED')"
    ))

    # 3d. 同 (part_id, process_id) 多真实报价去重：保留最新 created_at，其余 REJECTED
    dupes = conn.execute(sa.text("""
        SELECT part_id, process_id
        FROM t_outsource_quote
        WHERE deleted_at IS NULL AND status = 'APPROVED' AND is_direct = false
        GROUP BY part_id, process_id
        HAVING COUNT(*) > 1
    """)).fetchall()
    for part_id, process_id in dupes:
        ids = conn.execute(sa.text("""
            SELECT id FROM t_outsource_quote
            WHERE deleted_at IS NULL AND status = 'APPROVED' AND is_direct = false
              AND part_id = :part_id AND process_id = :process_id
            ORDER BY created_at DESC, id DESC
        """), {"part_id": part_id, "process_id": process_id}).fetchall()
        # 保留第一条（最新），其余置 REJECTED
        for row in ids[1:]:
            conn.execute(sa.text("""
                UPDATE t_outsource_quote
                SET status = 'REJECTED', review_note = '迁移：被更新报价取代',
                    updated_at = NOW()
                WHERE id = :id
            """), {"id": row[0]})

    # 3e. is_direct 行若与同 (part, process) 真实 APPROVED 并存则置 REJECTED
    conn.execute(sa.text("""
        UPDATE t_outsource_quote AS direct
        SET status = 'REJECTED', review_note = '被真实报价取代', updated_at = NOW()
        FROM t_outsource_quote AS real
        WHERE direct.deleted_at IS NULL AND real.deleted_at IS NULL
          AND direct.is_direct = true AND real.is_direct = false
          AND direct.status = 'APPROVED' AND real.status = 'APPROVED'
          AND direct.part_id = real.part_id AND direct.process_id = real.process_id
    """))

    # 4. 唯一索引调整
    op.drop_index("uq_t_outsource_quote_active_tuple", table_name="t_outsource_quote")
    op.create_index(
        "uq_t_outsource_quote_approved_part_process",
        "t_outsource_quote",
        ["part_id", "process_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL AND status='APPROVED' AND is_direct=false"),
    )


def downgrade() -> None:
    # 数据不回迁（docstring 注明）
    op.drop_index("uq_t_outsource_quote_approved_part_process", table_name="t_outsource_quote")
    op.create_index(
        "uq_t_outsource_quote_active_tuple",
        "t_outsource_quote",
        ["part_id", "outsource_company_id", "process_id"],
        unique=True,
        postgresql_where=sa.text(
            "deleted_at IS NULL AND status IN "
            "('DRAFT','SUBMITTED','APPROVED','OUTSOURCING','RECEIVED','BILLED','USED')"
        ),
    )
    op.drop_column("t_outsource_quote", "is_direct")
    op.drop_index("uq_t_outsource_shipment_open_batch", table_name="t_outsource_shipment")
    op.drop_index("ix_t_outsource_shipment_status", table_name="t_outsource_shipment")
    op.drop_index("ix_t_outsource_shipment_process_id", table_name="t_outsource_shipment")
    op.drop_index("ix_t_outsource_shipment_outsource_company_id", table_name="t_outsource_shipment")
    op.drop_index("ix_t_outsource_shipment_batch_id", table_name="t_outsource_shipment")
    op.drop_index("ix_t_outsource_shipment_part_id", table_name="t_outsource_shipment")
    op.drop_index("ix_t_outsource_shipment_quote_id", table_name="t_outsource_shipment")
    op.drop_table("t_outsource_shipment")
