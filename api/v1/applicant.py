"""申请人 (Applicant) 管理 API。"""
from fastapi import APIRouter, Depends, Query, status as http_status

from api.deps import get_applicant_service
from core.permission import require_roles
from model.enums import UserRole
from schema.applicant import (
    ApplicantCreateRequest,
    ApplicantListOut,
    ApplicantListQuery,
    ApplicantOut,
    ApplicantUpdateRequest,
)
from service import ApplicantService

# 客户管理目录：MANAGER + CLERK 都可读写。
router = APIRouter(prefix="/applicants", tags=["申请人管理"])
_role_dep = [
    Depends(require_roles(UserRole.MANAGER, UserRole.CLERK))
]


@router.get(
    "",
    response_model=ApplicantListOut,
    summary="申请人列表（MANAGER / CLERK）",
    dependencies=_role_dep,
)
async def list_applicants(
    customer_id: str | None = Query(default=None, description="所属一级客户 id（雪花 ID 字符串）"),
    name_like: str | None = Query(default=None, description="姓名模糊匹配"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    svc: ApplicantService = Depends(get_applicant_service),
) -> ApplicantListOut:
    return await svc.list_applicants(
        ApplicantListQuery(
            customer_id=customer_id, name_like=name_like,
            limit=limit, offset=offset,
        )
    )


@router.get(
    "/search",
    response_model=list[ApplicantOut],
    summary="申请人前序查询（零件对话框自动补全）",
    dependencies=_role_dep,
)
async def search_applicants(
    customer_id: str = Query(..., description="一级客户 id（雪花 ID 字符串）"),
    name_prefix: str | None = Query(default=None, max_length=50),
    limit: int = Query(default=20, ge=1, le=100),
    svc: ApplicantService = Depends(get_applicant_service),
) -> list[ApplicantOut]:
    return await svc.search_for_customer(
        customer_id=customer_id, name_prefix=name_prefix, limit=limit,
    )


@router.post(
    "",
    response_model=ApplicantOut,
    status_code=http_status.HTTP_201_CREATED,
    summary="新增申请人",
    dependencies=_role_dep,
)
async def create_applicant(
    payload: ApplicantCreateRequest,
    svc: ApplicantService = Depends(get_applicant_service),
) -> ApplicantOut:
    return await svc.create_applicant(payload)


@router.get(
    "/{applicant_id}",
    response_model=ApplicantOut,
    summary="申请人详情",
    dependencies=_role_dep,
)
async def get_applicant(
    applicant_id: str,
    svc: ApplicantService = Depends(get_applicant_service),
) -> ApplicantOut:
    return await svc.get_applicant(applicant_id)


@router.post(
    "/{applicant_id}/update",
    response_model=ApplicantOut,
    summary="更新申请人字段",
    dependencies=_role_dep,
)
async def update_applicant(
    applicant_id: str,
    payload: ApplicantUpdateRequest,
    svc: ApplicantService = Depends(get_applicant_service),
) -> ApplicantOut:
    return await svc.update_applicant(applicant_id, payload)


@router.post(
    "/{applicant_id}/soft-delete",
    summary="软删申请人（被零件引用时拒绝）",
    dependencies=_role_dep,
)
async def soft_delete_applicant(
    applicant_id: str,
    svc: ApplicantService = Depends(get_applicant_service),
) -> dict:
    await svc.soft_delete_applicant(applicant_id)
    return {"ok": True}