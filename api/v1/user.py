from fastapi import APIRouter, Depends, Query, status

from api.deps import get_user_service
from schema.user import UserCreate, UserOut, UserPage, UserWithOrdersOut
from service.user import UserService


router = APIRouter(prefix="/users", tags=["用户管理"])


@router.get("", response_model=UserPage)
async def list_users(
    page: int = Query(1, ge=1, description="Page number, starts from 1"),
    size: int = Query(20, ge=1, le=100, description="Items per page, max 100"),
    svc: UserService = Depends(get_user_service),
) -> UserPage:
    return await svc.list_users(page=page, size=size)


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreate,
    svc: UserService = Depends(get_user_service),
) -> UserOut:
    return await svc.create_user(payload)


@router.get(
    "/{user_id}/with-orders",
    response_model=UserWithOrdersOut,
    summary="查询用户及其所有订单",
)
async def get_user_with_orders(
    user_id: int,
    svc: UserService = Depends(get_user_service),
) -> UserWithOrdersOut:
    return await svc.get_user_with_orders(user_id)
