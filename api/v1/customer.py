"""客户管理 API。

路由结构：
- 读端点 (`GET /customers`)：MANAGER + CLERK + CNC_PROGRAMMER 共享，
  文员/编程员要拉客户列表做筛选。
- CRUD 端点（POST / 单条 GET / update / soft-delete）：MANAGER + CLERK。
  与申请人目录一致（user 拍板"MANAGER + CLERK 都可写"）。

两个 router 共用 `/customers` 前缀；由于 FastAPI 的依赖是按路由粒度生效，
两者并列注册即可。
"""
from fastapi import APIRouter, Depends, status as http_status

from api.deps import get_customer_service
from core.permission import require_role, require_roles
from model.enums import UserRole
from schema.customer import (
    CustomerCreateRequest,
    CustomerOut,
    CustomerUpdateRequest,
)
from service import CustomerService

# ============================================================
# 读路由：开放给 MANAGER + CLERK + CNC_PROGRAMMER
# ============================================================
read_router = APIRouter(
    prefix="/customers",
    tags=["客户管理"],
    dependencies=[
        Depends(require_roles(
            UserRole.MANAGER, UserRole.CLERK, UserRole.CNC_PROGRAMMER,
        ))
    ],
)


@read_router.get(
    "",
    response_model=list[CustomerOut],
    summary="客户列表（含 parent_name，前端按 parent_name/child_name 唯一定位）",
)
async def list_customers(
    svc: CustomerService = Depends(get_customer_service),
) -> list[CustomerOut]:
    return await svc.list_customers()


@read_router.get(
    "/{customer_id}",
    response_model=CustomerOut,
    summary="客户详情",
)
async def get_customer(
    customer_id: str,
    svc: CustomerService = Depends(get_customer_service),
) -> CustomerOut:
    return await svc.get_customer(customer_id)


# ============================================================
# 写路由：仅 MANAGER + CLERK
# ============================================================
write_router = APIRouter(
    prefix="/customers",
    tags=["客户管理(写)"],
    dependencies=[
        Depends(require_roles(UserRole.MANAGER, UserRole.CLERK))
    ],
)


@write_router.post(
    "",
    response_model=CustomerOut,
    status_code=http_status.HTTP_201_CREATED,
    summary="新增客户（parent_id 留空=一级客户）",
)
async def create_customer(
    payload: CustomerCreateRequest,
    svc: CustomerService = Depends(get_customer_service),
) -> CustomerOut:
    return await svc.create_customer(payload)


@write_router.post(
    "/{customer_id}/update",
    response_model=CustomerOut,
    summary="更新客户字段",
)
async def update_customer(
    customer_id: str,
    payload: CustomerUpdateRequest,
    svc: CustomerService = Depends(get_customer_service),
) -> CustomerOut:
    return await svc.update_customer(customer_id, payload)


@write_router.post(
    "/{customer_id}/soft-delete",
    summary="软删客户（有子节点或被引用时拒绝）",
)
async def soft_delete_customer(
    customer_id: str,
    svc: CustomerService = Depends(get_customer_service),
) -> dict:
    await svc.soft_delete_customer(customer_id)
    return {"ok": True}