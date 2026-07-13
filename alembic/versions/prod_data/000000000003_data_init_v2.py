"""data_init_v2: scan_badge 菜单从 MANAGER / CNC_PROGRAMMER 移至 SHELF_ACCOUNT

Revision ID: 000000000003
Revises: 000000000002
Create Date: 2026-07-13

业务背景
========
- 2026-07-13 起 myERP 引入「扫码台仅 SHELF_ACCOUNT 可见」的策略：
  * SHELF_ACCOUNT（货架一体机账号）才看得到侧栏「扫码台」入口
  * MANAGER / CNC_PROGRAMMER 不再挂 scan_badge（编程员改用 /cnc/pending
    走专属流程，MANAGER 通过账号管理 + 货架管理后台处理扫码台相关数据）
- 数据 seed (000000000002) 已经按新映射写入；本迁移为**已上线老库**做
  幂等回填，保证 alembic upgrade head 之后所有 DB 状态一致。
- 不动 DDL（沿用 001）；本文件是 data 类（prod_data/ 目录下），不归 schema/。

幂等保证
========
- 老库（stamped 002 但 t_role_menu 仍是旧数据）：DELETE 删 2 行 + INSERT 1 行
- 新库（fresh install 走 002 已写入新映射）：DELETE 0 affected（行不存在），
  INSERT ON CONFLICT DO NOTHING 跳过 → 无副作用
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from utils.id_gen import new_id

revision: str = "000000000003"
down_revision: Union[str, None] = "000000000002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """把 scan_badge 菜单从 MANAGER / CNC_PROGRAMMER 下架，挂到 SHELF_ACCOUNT。"""
    bind = op.get_bind()

    # 1) 拿 scan_badge 菜单 id
    scan_row = bind.execute(
        sa.text(
            "SELECT id FROM t_menu "
            "WHERE code = 'scan_badge' AND deleted_at IS NULL"
        )
    ).fetchone()
    if scan_row is None:
        # 极端兜底：scan_badge 菜单都没建（不可能，002 必建），直接 return
        return
    scan_id = int(scan_row[0])

    # 2) 把 MANAGER / CNC_PROGRAMMER 之前挂的 scan_badge 行物理删除
    #    走真删（不用 soft_delete）——这是 seed 阶段遗留，不留 deleted_at 假数据
    bind.execute(
        sa.text(
            "DELETE FROM t_role_menu "
            "WHERE menu_id = :mid "
            "  AND role IN ('MANAGER', 'CNC_PROGRAMMER') "
            "  AND deleted_at IS NULL"
        ),
        {"mid": scan_id},
    )

    # 3) 给 SHELF_ACCOUNT 挂上（幂等）
    bind.execute(
        sa.text(
            """
            INSERT INTO t_role_menu (id, role, menu_id, created_at, updated_at)
            VALUES (:id, 'SHELF_ACCOUNT', :mid, now(), now())
            ON CONFLICT (role, menu_id) WHERE deleted_at IS NULL DO NOTHING
            """
        ),
        {"id": new_id(), "mid": scan_id},
    )


def downgrade() -> None:
    """回滚：把 scan_badge 还原给 MANAGER + CNC_PROGRAMMER，删 SHELF_ACCOUNT 那一行。"""
    bind = op.get_bind()

    scan_row = bind.execute(
        sa.text(
            "SELECT id FROM t_menu "
            "WHERE code = 'scan_badge' AND deleted_at IS NULL"
        )
    ).fetchone()
    if scan_row is None:
        return
    scan_id = int(scan_row[0])

    # 删 SHELF_ACCOUNT 那一行（真删）
    bind.execute(
        sa.text(
            "DELETE FROM t_role_menu "
            "WHERE menu_id = :mid AND role = 'SHELF_ACCOUNT' "
            "  AND deleted_at IS NULL"
        ),
        {"mid": scan_id},
    )

    # 还原 MANAGER + CNC_PROGRAMMER
    for role in ("MANAGER", "CNC_PROGRAMMER"):
        bind.execute(
            sa.text(
                """
                INSERT INTO t_role_menu (id, role, menu_id, created_at, updated_at)
                VALUES (:id, :role, :mid, now(), now())
                ON CONFLICT (role, menu_id) WHERE deleted_at IS NULL DO NOTHING
                """
            ),
            {"id": new_id(), "role": role, "mid": scan_id},
        )
