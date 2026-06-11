from fastapi import APIRouter, Depends, status

from api.deps import get_order_service
from schema.order import OrderCreate, OrderOut
from service.order import OrderService


router = APIRouter(prefix="/orders", tags=["订单管理"])


@router.post(
    "/users/{user_id}",
    response_model=OrderOut,
    status_code=status.HTTP_201_CREATED,
    summary="为指定用户创建订单",
)
async def create_order_for_user(
    user_id: int,
    payload: OrderCreate,
    svc: OrderService = Depends(get_order_service),
) -> OrderOut:
    return await svc.create_order_for_user(user_id, payload)
