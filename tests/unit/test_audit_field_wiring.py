"""审计字段 created_by / updated_by 注入契约。

策略：service 在构造时接受 `current_user: CurrentUser | None`。
写操作（INSERT / UPDATE / SOFT_DELETE）时在 ORM 模型上显式赋值
`created_by = self._user_id` / `updated_by = self._user_id`，然后
调 `repo.create/update/soft_delete` 把字段写进 DB。

本测试覆盖：
- 核心契约：传 current_user 时 by 字段被正确写入
- 回归保护：不传 current_user 时 by 字段为 None
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from core.permission import CurrentUser
from model.applicant import TApplicant
from model.customer import TCustomer
from model.part import TPart
from model.process import TProcess
from model.shelf import TShelf
from model.worker import TWorker
from model.work_type import TWorkType
from repository.applicant import ApplicantRepository
from repository.customer import CustomerRepository
from repository.part import PartRepository
from repository.process import ProcessRepository
from repository.shelf import ShelfRepository
from repository.user import UserRoleRepository
from repository.work_type import WorkTypeRepository
from repository.worker import WorkerRepository
from schema.applicant import ApplicantCreateRequest
from schema.customer import CustomerCreateRequest
from schema.process import ProcessCreateRequest
from schema.shelf import ShelfCreateRequest, ShelfUpdateRequest
from schema.worker import WorkerCreateRequest
from schema.work_type import WorkTypeCreateRequest
from service.applicant import ApplicantService
from service.customer import CustomerService
from service.process import ProcessService
from service.shelf import ShelfService
from service.worker import WorkerService
from service.work_type import WorkTypeService

pytestmark = pytest.mark.asyncio


def _user(uid: int = 42) -> CurrentUser:
    return CurrentUser(
        id=uid,
        username="tester",
        full_name="Tester",
        is_active=True,
        roles=("MANAGER",),
        shelf_ids=(),
    )


def _patch_to_out(target):
    """绕过 service 的 _to_out 内部 datetime 校验（mock 路径下 created_at 是 None）。"""
    return patch.object(target, "_to_out", new_callable=AsyncMock)


# ============================================================
# 1. CustomerService — 创建/更新/软删都带 by
# ============================================================
class TestCustomerAudit:
    async def test_create_customer_writes_audit_by(self):
        customers = CustomerRepository.__new__(CustomerRepository)
        customers.create = AsyncMock()
        customers.update = AsyncMock()
        customers.soft_delete = AsyncMock()
        customers.get_by_id = AsyncMock(return_value=_customer(1, "C"))
        customers.list_children = AsyncMock(return_value=[])
        parts = PartRepository.__new__(PartRepository)
        parts.count_with_filters = AsyncMock(return_value=0)
        assemblies = PartRepository.__new__(PartRepository)
        assemblies.count_with_filters = AsyncMock(return_value=0)
        svc = CustomerService(
            customers=customers, parts=parts, assemblies=assemblies,
            current_user=_user(100),
        )
        # CustomerService.create_customer 不调 _to_out，直接构造 CustomerOut
        # （无 datetime 字段），不需要 patch。
        # 2026-07-09：root customer 现在要求带 serial_prefix。
        await svc.create_customer(
            CustomerCreateRequest(name="C-A", parent_id=None, serial_prefix="G"),
        )
        created = customers.create.await_args.args[0]
        assert created.created_by == 100
        assert created.updated_by == 100

    async def test_update_customer_writes_updated_by(self):
        customers = CustomerRepository.__new__(CustomerRepository)
        customers.create = AsyncMock()
        customers.update = AsyncMock()
        customers.soft_delete = AsyncMock()
        customers.get_by_id = AsyncMock(return_value=_customer(1, "C"))
        customers.list_children = AsyncMock(return_value=[])
        parts = PartRepository.__new__(PartRepository)
        parts.count_with_filters = AsyncMock(return_value=0)
        assemblies = PartRepository.__new__(PartRepository)
        assemblies.count_with_filters = AsyncMock(return_value=0)
        svc = CustomerService(
            customers=customers, parts=parts, assemblies=assemblies,
            current_user=_user(101),
        )
        from schema.customer import CustomerUpdateRequest
        # update_customer 末尾调 get_customer → 直接构造 CustomerOut（无 datetime）
        await svc.update_customer("1", CustomerUpdateRequest(name="X2"))
        called = customers.update.await_args.args[0]
        assert called.updated_by == 101

    async def test_soft_delete_customer_writes_updated_by(self):
        customers = CustomerRepository.__new__(CustomerRepository)
        customers.create = AsyncMock()
        customers.update = AsyncMock()
        customers.soft_delete = AsyncMock()
        customers.get_by_id = AsyncMock(return_value=_customer(1, "C"))
        customers.list_children = AsyncMock(return_value=[])
        parts = PartRepository.__new__(PartRepository)
        parts.count_with_filters = AsyncMock(return_value=0)
        assemblies = PartRepository.__new__(PartRepository)
        assemblies.count_with_filters = AsyncMock(return_value=0)
        svc = CustomerService(
            customers=customers, parts=parts, assemblies=assemblies,
            current_user=_user(102),
        )
        await svc.soft_delete_customer("1")
        called = customers.soft_delete.await_args.args[0]
        assert called.updated_by == 102


# ============================================================
# 2. ShelfService
# ============================================================
class TestShelfAudit:
    async def test_create_shelf_writes_audit_by(self):
        shelves = ShelfRepository.__new__(ShelfRepository)
        shelves.create = AsyncMock()
        shelves.update = AsyncMock()
        shelves.soft_delete = AsyncMock()
        shelves.get_by_id = AsyncMock(return_value=_shelf(1, "S1"))
        shelves.get_by_code = AsyncMock(return_value=None)
        shelves.list_by_ids = AsyncMock(return_value=[])
        svc = ShelfService(
            shelves=shelves, user_roles=AsyncMock(), current_user=_user(200),
        )
        with patch.object(svc, "_account_count_map", new=AsyncMock(return_value={})):
            with patch.object(svc, "_to_out", new=AsyncMock()):
                await svc.create_shelf(
                    ShelfCreateRequest(code="X1", name="Shelf1", zone="PRODUCTION")
                )
        created = shelves.create.await_args.args[0]
        assert created.created_by == 200
        assert created.updated_by == 200

    async def test_update_shelf_writes_updated_by(self):
        # soft_delete_shelf 内部开 SessionLocal 查 DB，单元测试 mock 不掉；
        # 改测 update_shelf 覆盖 UPDATE 路径的 updated_by 注入。
        shelves = ShelfRepository.__new__(ShelfRepository)
        shelves.create = AsyncMock()
        shelves.update = AsyncMock()
        shelves.soft_delete = AsyncMock()
        shelves.get_by_id = AsyncMock(return_value=_shelf(1, "S1"))
        shelves.get_by_code = AsyncMock(return_value=None)
        shelves.list_by_ids = AsyncMock(return_value=[])
        svc = ShelfService(
            shelves=shelves, user_roles=AsyncMock(), current_user=_user(201),
        )
        with patch.object(svc, "_account_count_map", new=AsyncMock(return_value={})):
            with patch.object(svc, "_to_out", new=AsyncMock()):
                await svc.update_shelf(1, ShelfUpdateRequest(name="X2"))
        called = shelves.update.await_args.args[0]
        assert called.updated_by == 201


# ============================================================
# 3. ProcessService
# ============================================================
class TestProcessAudit:
    async def test_create_process_writes_audit_by(self):
        from model.enums import ProcessCategory
        processes = ProcessRepository.__new__(ProcessRepository)
        processes.create = AsyncMock()
        processes.update = AsyncMock()
        processes.soft_delete = AsyncMock()
        processes.get_by_id = AsyncMock(return_value=_process(1, "P1"))
        processes.get_by_code = AsyncMock(return_value=None)
        svc = ProcessService(processes=processes, current_user=_user(300))
        # 绕过 _process_to_out 里的 datetime 校验
        with patch("service.process._process_to_out", new=lambda p: p):
            await svc.create_process(
                ProcessCreateRequest(
                    code="P1", name="Process1", category=ProcessCategory.INHOUSE,
                    is_inspection=False, sort_order=0,
                )
            )
        created = processes.create.await_args.args[0]
        assert created.created_by == 300
        assert created.updated_by == 300

    async def test_soft_delete_process_writes_updated_by(self):
        processes = ProcessRepository.__new__(ProcessRepository)
        processes.create = AsyncMock()
        processes.update = AsyncMock()
        processes.soft_delete = AsyncMock()
        processes.get_by_id = AsyncMock(return_value=_process(1, "P1"))
        processes.get_by_code = AsyncMock(return_value=None)
        svc = ProcessService(processes=processes, current_user=_user(301))
        await svc.soft_delete_process(1)
        called = processes.soft_delete.await_args.args[0]
        assert called.updated_by == 301


# ============================================================
# 4. WorkTypeService
# ============================================================
class TestWorkTypeAudit:
    async def test_create_work_type_writes_audit_by(self):
        work_types = WorkTypeRepository.__new__(WorkTypeRepository)
        work_types.create = AsyncMock()
        work_types.update = AsyncMock()
        work_types.soft_delete = AsyncMock()
        work_types.get_by_id = AsyncMock(return_value=_work_type(1, "WT1"))
        work_types.get_by_code = AsyncMock(return_value=None)
        svc = WorkTypeService(work_types=work_types, current_user=_user(400))
        # 绕过 _work_type_to_out 里的 datetime 校验
        with patch("service.work_type._work_type_to_out", new=lambda w: w):
            await svc.create_work_type(
                WorkTypeCreateRequest(code="WT1", name="WT1", sort_order=0)
            )
        created = work_types.create.await_args.args[0]
        assert created.created_by == 400
        assert created.updated_by == 400

    async def test_soft_delete_work_type_writes_updated_by(self):
        work_types = WorkTypeRepository.__new__(WorkTypeRepository)
        work_types.create = AsyncMock()
        work_types.update = AsyncMock()
        work_types.soft_delete = AsyncMock()
        work_types.get_by_id = AsyncMock(return_value=_work_type(1, "WT1"))
        work_types.get_by_code = AsyncMock(return_value=None)
        svc = WorkTypeService(work_types=work_types, current_user=_user(401))
        await svc.soft_delete_work_type(1)
        called = work_types.soft_delete.await_args.args[0]
        assert called.updated_by == 401


# ============================================================
# 5. WorkerService — create / update / deactivate / reactivate
# ============================================================
class TestWorkerAudit:
    async def test_create_worker_writes_audit_by(self):
        workers = WorkerRepository.__new__(WorkerRepository)
        workers.create = AsyncMock()
        workers.update = AsyncMock()
        workers.soft_delete = AsyncMock()
        workers.get_by_id = AsyncMock(return_value=_worker(1))
        workers.get_by_badge_code = AsyncMock(return_value=None)
        work_types = WorkTypeRepository.__new__(WorkTypeRepository)
        work_types.get_by_id = AsyncMock(return_value=None)
        svc = WorkerService(
            workers=workers, work_types=work_types, current_user=_user(500),
        )
        # 绕过 _worker_to_out 的 datetime 校验
        with patch("service.worker._worker_to_out", new=lambda w: w):
            await svc.create_worker(
                WorkerCreateRequest(
                    badge_code="B1", name="W1",
                    id_card_no="110101199001011234", phone="13800138000",
                    work_type_id=None,
                )
            )
        created = workers.create.await_args.args[0]
        assert created.created_by == 500
        assert created.updated_by == 500

    async def test_update_worker_writes_updated_by(self):
        workers = WorkerRepository.__new__(WorkerRepository)
        workers.create = AsyncMock()
        workers.update = AsyncMock()
        workers.soft_delete = AsyncMock()
        workers.get_by_id = AsyncMock(return_value=_worker(1))
        workers.get_by_badge_code = AsyncMock(return_value=None)
        workers.session = AsyncMock()
        workers.session.refresh = AsyncMock()
        work_types = WorkTypeRepository.__new__(WorkTypeRepository)
        work_types.get_by_id = AsyncMock(return_value=None)
        svc = WorkerService(
            workers=workers, work_types=work_types, current_user=_user(501),
        )
        from schema.worker import WorkerUpdateRequest
        await svc.update_worker(1, WorkerUpdateRequest(name="X"))
        called = workers.update.await_args.args[0]
        assert called.updated_by == 501

    async def test_deactivate_writes_updated_by(self):
        workers = WorkerRepository.__new__(WorkerRepository)
        workers.create = AsyncMock()
        workers.update = AsyncMock()
        workers.soft_delete = AsyncMock()
        workers.get_by_id = AsyncMock(return_value=_worker(1))
        workers.get_by_badge_code = AsyncMock(return_value=None)
        workers.session = AsyncMock()
        workers.session.refresh = AsyncMock()
        work_types = WorkTypeRepository.__new__(WorkTypeRepository)
        work_types.get_by_id = AsyncMock(return_value=None)
        svc = WorkerService(
            workers=workers, work_types=work_types, current_user=_user(502),
        )
        await svc.deactivate(1)
        called = workers.update.await_args.args[0]
        assert called.updated_by == 502
        assert called.is_active is False
        assert called.deleted_at is not None

    async def test_reactivate_writes_updated_by(self):
        workers = WorkerRepository.__new__(WorkerRepository)
        workers.create = AsyncMock()
        workers.update = AsyncMock()
        workers.soft_delete = AsyncMock()
        workers.get_by_id = AsyncMock(return_value=_worker(1))
        workers.get_by_badge_code = AsyncMock(return_value=None)
        workers.session = AsyncMock()
        workers.session.refresh = AsyncMock()
        work_types = WorkTypeRepository.__new__(WorkTypeRepository)
        work_types.get_by_id = AsyncMock(return_value=None)
        svc = WorkerService(
            workers=workers, work_types=work_types, current_user=_user(503),
        )
        await svc.reactivate(1)
        called = workers.update.await_args.args[0]
        assert called.updated_by == 503
        assert called.is_active is True
        assert called.deleted_at is None


# ============================================================
# 6. ApplicantService — get_or_create
# ============================================================
class TestApplicantAudit:
    async def test_get_or_create_writes_audit_by(self):
        applicants = ApplicantRepository.__new__(ApplicantRepository)
        applicants.create = AsyncMock()
        applicants.update = AsyncMock()
        applicants.soft_delete = AsyncMock()
        applicants.get_by_id = AsyncMock(return_value=None)
        applicants.find_by_name_and_customer = AsyncMock(return_value=None)
        customers = CustomerRepository.__new__(CustomerRepository)
        customers.get_by_id = AsyncMock(return_value=_customer(1, "Parent"))
        svc = ApplicantService(
            applicants=applicants, customers=customers, current_user=_user(600),
        )
        # get_or_create 末尾调 _to_outs([a])，需返回非空 list 才能取 [0]
        with patch.object(svc, "_to_outs", new=AsyncMock(return_value=["dummy"])):
            await svc.get_or_create("张三", 1)
        created = applicants.create.await_args.args[0]
        assert created.created_by == 600
        assert created.updated_by == 600


# ============================================================
# 7. 回归保护：不传 current_user 时所有 by 字段都是 None
# ============================================================
class TestAuditRegressionNoUser:
    async def test_create_customer_no_user_by_is_none(self):
        customers = CustomerRepository.__new__(CustomerRepository)
        customers.create = AsyncMock()
        customers.get_by_id = AsyncMock(return_value=_customer(1, "C"))
        customers.list_children = AsyncMock(return_value=[])
        parts = PartRepository.__new__(PartRepository)
        parts.count_with_filters = AsyncMock(return_value=0)
        assemblies = PartRepository.__new__(PartRepository)
        assemblies.count_with_filters = AsyncMock(return_value=0)
        svc = CustomerService(customers=customers, parts=parts, assemblies=assemblies)
        # CustomerService.create_customer 不调 _to_out
        # 2026-07-09：root customer 现在要求带 serial_prefix；这里传一个字母即可。
        await svc.create_customer(
            CustomerCreateRequest(name="C", parent_id=None, serial_prefix="G"),
        )
        created = customers.create.await_args.args[0]
        assert created.created_by is None
        assert created.updated_by is None

    async def test_create_worker_no_user_by_is_none(self):
        workers = WorkerRepository.__new__(WorkerRepository)
        workers.create = AsyncMock()
        workers.get_by_badge_code = AsyncMock(return_value=None)
        work_types = WorkTypeRepository.__new__(WorkTypeRepository)
        work_types.get_by_id = AsyncMock(return_value=None)
        svc = WorkerService(workers=workers, work_types=work_types)
        with patch("service.worker._worker_to_out", new=lambda w: w):
            await svc.create_worker(
                WorkerCreateRequest(
                    badge_code="B", name="W",
                    id_card_no="110101199001011234", phone="13800138000",
                    work_type_id=None,
                )
            )
        created = workers.create.await_args.args[0]
        assert created.created_by is None
        assert created.updated_by is None

    async def test_create_shelf_no_user_by_is_none(self):
        shelves = ShelfRepository.__new__(ShelfRepository)
        shelves.create = AsyncMock()
        shelves.get_by_code = AsyncMock(return_value=None)
        shelves.list_by_ids = AsyncMock(return_value=[])
        svc = ShelfService(shelves=shelves, user_roles=AsyncMock())
        with patch.object(svc, "_account_count_map", new=AsyncMock(return_value={})):
            with patch.object(svc, "_to_out", new=AsyncMock()):
                await svc.create_shelf(
                    ShelfCreateRequest(code="X", name="X", zone="PRODUCTION")
                )
        created = shelves.create.await_args.args[0]
        assert created.created_by is None
        assert created.updated_by is None

    async def test_deactivate_no_user_by_is_none(self):
        workers = WorkerRepository.__new__(WorkerRepository)
        workers.update = AsyncMock()
        workers.get_by_id = AsyncMock(return_value=_worker(1))
        workers.session = AsyncMock()
        workers.session.refresh = AsyncMock()
        work_types = WorkTypeRepository.__new__(WorkTypeRepository)
        work_types.get_by_id = AsyncMock(return_value=None)
        svc = WorkerService(workers=workers, work_types=work_types)
        await svc.deactivate(1)
        called = workers.update.await_args.args[0]
        assert called.updated_by is None


# ============================================================
# helpers
# ============================================================
def _customer(id: int, name: str, parent_id: int | None = None) -> TCustomer:
    return TCustomer(
        id=id, name=name, parent_id=parent_id,
        created_at=datetime(2025, 1, 1), updated_at=datetime(2025, 1, 1),
    )


def _shelf(id: int, code: str) -> TShelf:
    return TShelf(
        id=id, code=code, name=code, zone="PRODUCTION",
        location=None, is_active=True,
        created_at=datetime(2025, 1, 1), updated_at=datetime(2025, 1, 1),
    )


def _process(id: int, code: str) -> TProcess:
    return TProcess(
        id=id, code=code, name=code, category="INHOUSE",
        is_inspection=False, sort_order=0, description=None,
        created_at=datetime(2025, 1, 1), updated_at=datetime(2025, 1, 1),
    )


def _work_type(id: int, code: str) -> TWorkType:
    return TWorkType(
        id=id, code=code, name=code, description=None, sort_order=0,
        created_at=datetime(2025, 1, 1), updated_at=datetime(2025, 1, 1),
    )


def _worker(id: int) -> TWorker:
    return TWorker(
        id=id, badge_code="B", name="W",
        id_card_no="1", phone="1", work_type_id=None, is_active=True,
        created_at=datetime(2025, 1, 1), updated_at=datetime(2025, 1, 1),
    )
