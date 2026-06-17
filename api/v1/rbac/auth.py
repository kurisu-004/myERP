"""Auth API: login + me."""
from __future__ import annotations

from fastapi import APIRouter, Depends, status

from api.rbac_deps import get_current_user
from model import TUser
from repository.unit_of_work import UnitOfWork
from schema.rbac import LoginIn, MeOut, TokenOut
from service.rbac.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["认证"])


def get_auth_service(uow: UnitOfWork = Depends(get_uow)) -> AuthService:
    return AuthService(uow)


@router.post("/login", response_model=TokenOut, summary="登录")
async def login(
    payload: LoginIn,
    svc: AuthService = Depends(get_auth_service),
) -> TokenOut:
    return await svc.login(payload.username, payload.password)


@router.get("/me", response_model=MeOut, summary="获取当前登录用户 + 权限 + 菜单")
async def me(
    user: TUser = Depends(get_current_user),
    svc: AuthService = Depends(get_auth_service),
) -> MeOut:
    return await svc.load_me(user.id)
