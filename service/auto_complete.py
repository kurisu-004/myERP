"""DELIVERED → COMPLETED 自动完成后台循环（PR-D 2026-07-10）。

每天扫一次：DELIVERED 状态且最近一次发货 ≥ 7 天前 + 中间无 REPAIR_STARTED
的零件，自动触发 `PartService.complete(part_id)` 完成。

启动方式：在 `core/database.py::lifespan` 里 `asyncio.create_task(auto_complete_loop())`，
挂到 `app.state.auto_complete_task`，shutdown 时 cancel。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from core.config import settings
from core.database import SessionLocal
from core.time import now_naive
from repository.customer import CustomerRepository
from repository.part import PartRepository
from repository.part_event import PartEventRepository
from repository.serial_counter import SerialCounterRepository
from repository.shelf import ShelfRepository
from repository.shelf_process import ShelfProcessRepository
from repository.work_type import WorkTypeRepository
from repository.work_type_process import WorkTypeProcessRepository
from repository.worker import WorkerRepository
from service.part import PartService

logger = logging.getLogger(__name__)


async def auto_complete_loop() -> None:
    """每 AUTO_COMPLETE_INTERVAL_HOURS 小时扫一次；启动时立即跑一轮。"""
    interval_s = settings.auto_complete_interval_hours * 3600
    while True:
        try:
            await _run_once()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — 后台循环吞错不让进程崩
            logger.exception("auto_complete_loop error: %s", e)
        try:
            await asyncio.sleep(interval_s)
        except asyncio.CancelledError:
            raise


async def _run_once() -> None:
    """单次扫描：找出符合条件的零件，逐个调 PartService.complete。"""
    threshold = now_naive() - timedelta(days=settings.auto_complete_threshold_days)
    logger.info(
        "auto_complete: scanning DELIVERED parts older than %s",
        threshold.isoformat(timespec="seconds"),
    )

    async with SessionLocal() as session:
        parts_repo = PartRepository(session)
        threshold_parts = await parts_repo.find_delivered_older_than(threshold=threshold)
        if not threshold_parts:
            logger.info("auto_complete: no parts to complete")
            return

        # 在事务里构造 PartService 并逐个完成
        part_svc = PartService(
            parts=parts_repo,
            customers=CustomerRepository(session),
            workers=WorkerRepository(session),
            events=PartEventRepository(session),
            serial_counters=SerialCounterRepository(session),
            shelves=ShelfRepository(session),
            processes=None,
            work_types=WorkTypeRepository(session),
            work_type_process=WorkTypeProcessRepository(session),
            shelf_process_repo=ShelfProcessRepository(session),
            broadcaster=None,
            event_broadcaster=None,
        )

        completed_count = 0
        for part in threshold_parts:
            try:
                await part_svc.complete(part.id)
                completed_count += 1
                logger.info(
                    "auto_complete: completed part id=%s serial=%s",
                    part.id, part.serial_no,
                )
            except Exception as e:  # noqa: BLE001 — 单个失败不影响其他
                logger.exception(
                    "auto_complete: failed to complete part id=%s: %s",
                    part.id, e,
                )
        await session.commit()
        logger.info("auto_complete: %d part(s) completed this round", completed_count)