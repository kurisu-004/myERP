"""customer_snowflake_id: t_customer 主键从 BigSerial 改为雪花 ID

Revision ID: 000000000010
Revises: 000000000009
Create Date: 2026-07-07

说明：
- 项目其它业务表（t_part / t_assembly / t_applicant / t_cnc_program 等）的
  主键都是雪花 ID；只有 t_customer 仍用 BigSerial（1-20）。本次统一。
- 雪花 ID 19 位远超 JS `Number.MAX_SAFE_INTEGER`（≈9.007×10¹⁵），因此
  所有 customer_id 入参改为 str；本次迁移是数据切点的关键时刻。
- 列类型 BigInteger 不变（雪花 19 位远在 BIGINT 范围内），无物理 FK
  也无需 ALTER COLUMN TYPE；只需重写 id + parent_id + 所有 FK 引用。
- 同事务保证原子性；事务回滚 → 全部回退，无半完成状态。

⚠️ downgrade 不可逆：雪花 ID 是随机生成的，回退后无法回到原来的 1-20
序列。downgrade 仅重建 sequence 和 partial unique 索引，docstring
明示数据不完整，请勿在生产环境跑 alembic downgrade。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from utils.id_gen import new_id

revision: str = "000000000010"
down_revision: Union[str, None] = "000000000009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    # -----------------------------------------------------------------
    # 1. 读出 t_customer 全部 (id, parent_id)，为每个生成新的雪花 id
    #    同时记下 parent_id 的所有 unique 值，避免重复分配
    # -----------------------------------------------------------------
    rows = bind.execute(sa.text(
        "SELECT id, parent_id FROM t_customer WHERE deleted_at IS NULL ORDER BY id"
    )).fetchall()
    id_map: dict[int, int] = {}
    for old_id, _parent_id in rows:
        if old_id not in id_map:
            id_map[old_id] = new_id()
    parent_ids = {r[1] for r in rows if r[1] is not None}
    for pid in parent_ids:
        if pid not in id_map:
            id_map[pid] = new_id()

    # -----------------------------------------------------------------
    # 2. t_applicant 的 partial unique index `(name, customer_id) WHERE
    #    deleted_at IS NULL` 在 UPDATE 过程中可能瞬时冲突（虽然概率为 0），
    #    先 DROP，UPDATE 完再重建。
    # -----------------------------------------------------------------
    op.execute("DROP INDEX IF EXISTS uq_t_applicant_name_customer_active")

    # -----------------------------------------------------------------
    # 3. UPDATE t_customer.id + t_customer.parent_id
    #    分两步：先 UPDATE 自引用 parent_id，再 UPDATE id 本身（避免级联破坏）。
    # -----------------------------------------------------------------
    # 3a. 先把 parent_id 临时设为 NULL，断开自引用
    bind.execute(sa.text("UPDATE t_customer SET parent_id = NULL"))

    # 3b. UPDATE id 本身
    for old_id, new_id_val in id_map.items():
        bind.execute(
            sa.text("UPDATE t_customer SET id = :new_id WHERE id = :old_id"),
            {"new_id": new_id_val, "old_id": old_id},
        )

    # 3c. 恢复 parent_id（用新 id 映射）
    for old_id, parent_old_id in rows:
        if parent_old_id is not None:
            new_self = id_map[old_id]
            new_parent = id_map[parent_old_id]
            bind.execute(
                sa.text(
                    "UPDATE t_customer SET parent_id = :new_parent WHERE id = :new_self"
                ),
                {"new_parent": new_parent, "new_self": new_self},
            )

    # -----------------------------------------------------------------
    # 4. UPDATE 外键引用
    # -----------------------------------------------------------------
    for old_id, new_id_val in id_map.items():
        bind.execute(
            sa.text("UPDATE t_part SET customer_id = :new_id WHERE customer_id = :old_id"),
            {"new_id": new_id_val, "old_id": old_id},
        )
        bind.execute(
            sa.text("UPDATE t_assembly SET customer_id = :new_id WHERE customer_id = :old_id"),
            {"new_id": new_id_val, "old_id": old_id},
        )
        bind.execute(
            sa.text("UPDATE t_applicant SET customer_id = :new_id WHERE customer_id = :old_id"),
            {"new_id": new_id_val, "old_id": old_id},
        )

    # -----------------------------------------------------------------
    # 5. DROP 自增 sequence（已无用）+ partial unique 重建
    # -----------------------------------------------------------------
    # 先解除 id 列的 DEFAULT 绑定（默认是 nextval(t_customer_id_seq)），
    # 否则 DROP SEQUENCE 会因 dependent objects 失败。
    bind.execute(sa.text("ALTER TABLE t_customer ALTER COLUMN id DROP DEFAULT"))
    op.execute("DROP SEQUENCE IF EXISTS t_customer_id_seq CASCADE")
    op.execute(sa.text("""
        CREATE UNIQUE INDEX uq_t_applicant_name_customer_active
        ON t_applicant (name, customer_id)
        WHERE deleted_at IS NULL
    """))


def downgrade() -> None:
    """⚠️ 不可逆！雪花 ID 不会回退到 1-20；本 downgrade 仅重建 sequence 让
    schema 完整（默认 nextval），数据保留雪花 ID 形态。

    不要在生产环境运行 alembic downgrade 000000000006。
    """
    bind = op.get_bind()
    bind.execute(
        "CREATE SEQUENCE IF NOT EXISTS t_customer_id_seq OWNED BY t_customer.id"
    )
    bind.execute(
        "ALTER TABLE t_customer ALTER COLUMN id SET DEFAULT nextval('t_customer_id_seq')"
    )