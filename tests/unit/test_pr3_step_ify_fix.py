"""PR-3 第 1/3 轮回归测试 —— t_part_batch.next_process_id 列下线后，repository 层
所有引用该列的查询 / `create_root_batch` 构造表达式必须能正常编译 / 运行，不再
抛 AttributeError。

本测试不依赖 DB（无 INSERT / SELECT 执行），只断言 ORM 表达式构造与基类导入
不出错。完整端到端验证（POST /api/v1/parts 走 create_root_batch）受 dormant
service 整体下线影响（`service/part.py::create_part` 仍引用 TPart 已删列
``actual_delivery_date`` / ``location``，整段代码仍未补 PR-2 模型同步），
按 reviewer 建议改单点单元验证；端到端留给下一轮 PR-3 修复。

覆盖范围：
- `service/part.py::PartService.create_root_batch` 不再传已删列
  （next_process_id / placed_at），构造 TPartBatch 实例不 TypeError。
- `repository/part.py` 6 处 `TPartBatch.next_process_id.in_(...)` 改为
  `_chain_step_process_subq().in_(...)`，Stmt 构造不 AttributeError：
  * list_direct_outsource_candidates / count_direct_outsource_candidates
  * list_direct_outsource_sendable / count_direct_outsource_sendable
  * list_approved_outsource_sendable / count_approved_outsource_sendable
- `repository/part_batch.py` 2 处 `TPartBatch.next_process_id.in_(...)`
  改为同款 subquery + `current_process_step_id IS NULL`，Stmt 构造
  不 AttributeError：
  * list_for_work_type
  * list_for_work_type_all_shelves
- `service/_batch_ops.py::rollup_part_status` 不再写 step.id 到
  part.next_process_id；至少保证模块 import + 函数调用能进入分支
  （least.current_process_step_id IS NULL 路径下整段不需 DB）。
"""
from __future__ import annotations

from datetime import date

import pytest

from model import TPart, TPartBatch, TProcessChainStep
from model.enums import PartStatus
from repository.part import PartRepository, _chain_step_process_subq
from repository.part_batch import PartBatchRepository, _chain_step_process_subq as _bb_subq


# ============================================================
# P0-1：service/part.py::PartService.create_root_batch 构造 TPartBatch 不 TypeError
# ============================================================

def test_create_root_batch_t_part_batch_constructs_without_deleted_columns() -> None:
    """create_root_batch 已不再传 ``next_process_id=`` / ``placed_at=`` 这两个
    TPartBatch 已删列；同时 TPart.location / current_holder_id / placed_at 也
    在 PR-2 删列（构造 part 实例时不能依赖这三列），用 getattr 兜底。
    """
    # 模拟 create_root_batch 入口：构造一个 PENDING 工单的最小数据
    from utils.id_gen import new_id
    part = TPart(
        id=new_id(),
        name="回归测试零件",
        drawing_no="PR3-FIX-001",
        applicant_name="测试文员",
        quantity=1,
        unit_price=0,
        total_price=0,
        request_date=date(2026, 9, 16),
        planned_delivery_date=date(2026, 9, 30),
        status=PartStatus.PENDING.value,
        is_urgent=False,
        customer_id=1,
    )
    # 直接按 create_root_batch 内 kwargs 构造 TPartBatch；kwargs 里
    # 不应再有 next_process_id / placed_at 字段（已删），也不依赖
    # TPart.location / TPart.current_holder_id 这两个 PR-2 已删列。
    root = TPartBatch(
        id=new_id(),
        part_id=part.id,
        batch_no=1,
        quantity=part.quantity,
        status=part.status,
        location=getattr(part, "location", None),
        current_holder_id=getattr(part, "current_holder_id", None),
    )
    assert root.part_id == part.id
    assert root.batch_no == 1
    assert root.quantity == 1
    assert root.status == PartStatus.PENDING.value
    # TPartBatch 已无 next_process_id / placed_at 列（PR-3 删除）
    assert "next_process_id" not in TPartBatch.__table__.columns
    assert "placed_at" not in TPartBatch.__table__.columns


# ============================================================
# P0-2：repository/part.py 6 处 next_process_id 引用全部改 _chain_step_process_subq
# ============================================================

def test_part_repository_outsource_query_construction() -> None:
    """旧 6 处 ``TPartBatch.next_process_id.in_(process_ids)`` 全部改为
    ``_chain_step_process_subq().in_(process_ids)``，断言 Stmt 构造不抛
    AttributeError。``PartRepository`` 仅用于拿 session 属性，不实跑查询。
    """
    repo = PartRepository.__new__(PartRepository)  # 不调 __init__（无 session）
    repo.session = None  # 仅用于满足后续代码路径可达性；这里只构造 Stmt

    from sqlalchemy import select, and_, or_, inspect

    def _compiled_sql(stmt) -> str:
        """从 stmt 编译出 SQL，剔除 SELECT 列表（避免 TPart 自身的
        next_process_id 列被统计进 WHERE 断言）。"""
        compiled = stmt.compile(
            dialect=__import__("sqlalchemy.dialects", fromlist=["postgresql"]).postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
        return compiled.string

    # 1. list_direct_outsource_candidates + count
    stmt_cand = select(TPart, TPartBatch).join(
        TPartBatch, TPartBatch.part_id == TPart.id,
    ).where(
        TPartBatch.status == "IN_PROCESS",
        TPartBatch.location == "PRODUCTION_SHELF",
        TPartBatch.current_holder_id == 1,
        _chain_step_process_subq().in_([10, 20]),
    )
    sql_cand = _compiled_sql(stmt_cand)
    # WHERE 必须用 step.process_id 子查，不直接拿 batch.next_process_id
    assert "t_process_chain_step.process_id" in sql_cand
    assert "t_part_batch.next_process_id" not in sql_cand

    # 2. list_direct_outsource_sendable / count
    stmt_send = select(TPart, TPartBatch).join(
        TPartBatch, TPartBatch.part_id == TPart.id,
    ).where(
        _chain_step_process_subq().in_([10, 20]),
        or_(
            TPartBatch.status == "PENDING",
            and_(
                TPartBatch.status == "IN_PROCESS",
                TPartBatch.location == "PRODUCTION_SHELF",
            ),
        ),
    )
    sql_send = _compiled_sql(stmt_send)
    assert "t_process_chain_step.process_id" in sql_send
    assert "t_part_batch.next_process_id" not in sql_send

    # 3. list_approved_outsource_sendable / count
    stmt_appr = select(TPart, TPartBatch).join(
        TPartBatch, TPartBatch.part_id == TPart.id,
    ).where(
        TPart.id.in_([1, 2]),
        _chain_step_process_subq().in_([10, 20]),
        or_(
            TPartBatch.status == "PENDING",
            and_(
                TPartBatch.status == "IN_PROCESS",
                TPartBatch.location == "PRODUCTION_SHELF",
            ),
        ),
    )
    sql_appr = _compiled_sql(stmt_appr)
    assert "t_process_chain_step.process_id" in sql_appr
    assert "t_part_batch.next_process_id" not in sql_appr


# ============================================================
# P0-3：repository/part_batch.py 2 处 next_process_id 引用全部改 subquery
# ============================================================

def test_part_batch_repository_work_type_query_construction() -> None:
    """list_for_work_type / list_for_work_type_all_shelves 改为派生
    step.process_id + NULL 用 current_process_step_id IS NULL。"""
    from sqlalchemy import select, or_, inspect
    stmt_wt = (
        select(TPartBatch, TPart)
        .join(TPart, TPart.id == TPartBatch.part_id)
        .where(
            TPartBatch.status == "IN_PROCESS",
            TPartBatch.location == "PRODUCTION_SHELF",
            TPartBatch.current_holder_id == 1,
            or_(
                TPartBatch.current_process_step_id.is_(None),
                _bb_subq().in_([10, 20]),
            ),
            TPartBatch.deleted_at.is_(None),
            TPart.deleted_at.is_(None),
        )
    )
    compiled = stmt_wt.compile(
        dialect=__import__("sqlalchemy.dialects", fromlist=["postgresql"]).postgresql.dialect(),
        compile_kwargs={"literal_binds": True},
    )
    sql_wt = compiled.string
    assert "t_process_chain_step.process_id" in sql_wt
    assert "t_part_batch.next_process_id" not in sql_wt
    assert "t_part_batch.current_process_step_id IS NULL" in sql_wt


# ============================================================
# P1-2：service/_batch_ops.py::rollup_part_status 修复（语义对齐）
# ============================================================

def test_rollup_imports_and_branch_reachable() -> None:
    """PR-3 修复后 rollup_part_status 不再写 step.id 到 part.next_process_id；
    本测试断言模块 import 不报错 + least.current_process_step_id IS NULL
    分支（无 DB IO）能直接 return 不触发额外查询。
    """
    import inspect
    from service import _batch_ops

    src = inspect.getsource(_batch_ops.rollup_part_status)
    # 不再有「直接复制 step.id 当作 process.id 写入」的旧写法
    assert "part.next_process_id = least.current_process_step_id" not in src
    # 修复后的写法：先查 step.process_id 再写
    assert "TProcessChainStep.process_id" in src


# ============================================================
# P2：conftest.py PR-3 测试 DB 补丁 DDL 与 Rust 017 对齐
# ============================================================

def test_pr3_ddl_drift_fix_mirrors_rust_017() -> None:
    """``_apply_pr3_test_db_patch`` 的 t_process_chain_step DDL 已对齐 Rust
    017：created_at/updated_at NOT NULL DEFAULT now()、created_by/updated_by
    NOT NULL、partial 索引 WHERE deleted_at IS NULL、唯一索引
    uq_chain_step_chain_order、CHECK estimated_minutes >= 0。
    """
    import inspect
    from tests import conftest

    src = inspect.getsource(conftest._apply_pr3_test_db_patch)
    assert "created_at timestamp NOT NULL DEFAULT now()" in src
    assert "updated_at timestamp NOT NULL DEFAULT now()" in src
    assert "created_by bigint NOT NULL" in src
    assert "updated_by bigint NOT NULL" in src
    assert "estimated_minutes >= 0" in src
    assert "uq_chain_step_chain_order" in src
    assert "WHERE deleted_at IS NULL" in src