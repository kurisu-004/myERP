from fastapi import APIRouter

from . import order, user

api_router = APIRouter(prefix="/v1")
api_router.include_router(user.router)
api_router.include_router(order.router)
