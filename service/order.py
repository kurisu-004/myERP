from fastapi import status

from core.error_code import ErrCode
from core.exception import BizError
from model.order import TOrder
from repository.unit_of_work import UnitOfWork
from schema.order import OrderCreate, OrderOut
from utils.id_gen import new_id


class OrderService:
    def __init__(self, uow: UnitOfWork):
        self.uow = uow

    async def create_order_for_user(self, user_id: int, data: OrderCreate) -> OrderOut:
        async with self.uow.session.begin():
            user = await self.uow.users.get_by_id(user_id)
            if user is None:
                raise BizError(
                    code=ErrCode.BIZ_USER_NOT_FOUND,
                    message=f"user {user_id} not found",
                    http_status=status.HTTP_404_NOT_FOUND,
                )
            order = TOrder(
                id=new_id(),
                price=data.price,
                user=user,
            )
            await self.uow.orders.create(order)
        return OrderOut.model_validate(order)
