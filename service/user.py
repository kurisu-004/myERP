from fastapi import status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from core.error_code import ErrCode
from core.exception import BizError
from model.user import TUser
from repository.unit_of_work import UnitOfWork
from schema.order import OrderOut
from schema.user import UserCreate, UserOut, UserPage, UserWithOrdersOut
from utils.id_gen import new_id


class UserService:
    def __init__(self, uow: UnitOfWork):
        self.uow = uow

    async def list_users(self, page: int, size: int) -> UserPage:
        items, total = await self.uow.users.list_paginated(page, size)
        return UserPage(
            items=[UserOut.model_validate(u) for u in items],
            total=total,
            page=page,
            size=size,
            pages=(total + size - 1) // size if size else 0,
        )

    async def create_user(self, data: UserCreate) -> UserOut:
        async with self.uow.session.begin():
            if await self.uow.users.get_by_username(data.username) is not None:
                raise BizError(
                    code=ErrCode.BIZ_USER_DUPLICATE,
                    message="username already exists",
                    http_status=status.HTTP_409_CONFLICT,
                )
            user = TUser(id=new_id(), username=data.username)
            await self.uow.users.create(user)
        return UserOut.model_validate(user)

    async def get_user_with_orders(self, user_id: int) -> UserWithOrdersOut:
        stmt = (
            select(TUser)
            .where(TUser.id == user_id)
            .options(selectinload(TUser.orders))
        )
        user = (await self.uow.session.execute(stmt)).scalar_one_or_none()
        if user is None:
            raise BizError(
                code=ErrCode.BIZ_USER_NOT_FOUND,
                message=f"user {user_id} not found",
                http_status=status.HTTP_404_NOT_FOUND,
            )
        return UserWithOrdersOut(
            id=user.id,
            username=user.username,
            orders=[OrderOut.model_validate(o) for o in user.orders],
        )
