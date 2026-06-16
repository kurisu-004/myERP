from fastapi import APIRouter

from .auth import router as auth_router
from .rbac import router as rbac_router

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(rbac_router)
