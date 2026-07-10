"""通用账号登录 / 会话端点。

- POST /auth/login    登录返回 JWT（含 access + refresh）
- GET  /auth/me       当前账号上下文
- POST /auth/logout   no-op（客户端丢弃 token 即可）
- POST /auth/refresh  用 refresh token 换新一对 token（2026-07-10 新增）
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from api.deps import get_auth_service
from core.permission import CurrentUser, get_current_user
from schema.user import CurrentUserOut, LoginRequest, LoginResponse
from service.auth import AuthService


router = APIRouter(prefix="/auth", tags=["账号登录"])


@router.post("/login", response_model=LoginResponse, summary="账号登录")
async def login(
    payload: LoginRequest,
    svc: AuthService = Depends(get_auth_service),
) -> LoginResponse:
    return await svc.login(payload)


@router.get("/me", response_model=CurrentUserOut, summary="当前账号")
async def me(
    user: CurrentUser = Depends(get_current_user),
    svc: AuthService = Depends(get_auth_service),
) -> CurrentUserOut:
    return await svc.me(
        user_id=user.id,
        roles=list(user.roles),
        shelf_ids=list(user.shelf_ids),
    )


@router.post("/logout", summary="登出（no-op，客户端丢 token 即可）")
async def logout() -> dict:
    return {"ok": True}


class RefreshRequest(BaseModel):
    """POST /auth/refresh 请求体。"""
    refresh_token: str = Field(min_length=1, max_length=4096)


@router.post(
    "/refresh",
    response_model=LoginResponse,
    summary="用 refresh token 换新一对 token（旧 refresh 失效，轮转）",
)
async def refresh_session(
    payload: RefreshRequest,
    svc: AuthService = Depends(get_auth_service),
) -> LoginResponse:
    """公开端点（不挂 require_auth），靠 refresh_token 自身证明身份。

    失败：401 BIZ_AUTH_REFRESH_INVALID（code=40103）。
    """
    return await svc.refresh(payload.refresh_token)
