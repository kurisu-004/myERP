"""装配体 API。

路径前缀：`/assemblies`
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status as http_status

from api.deps import get_assembly_service
from core.error_code import ErrCode
from core.exception import BizError
from core.permission import require_role, require_roles
from model.enums import UserRole
from schema.assembly import (
    AddAssemblyChildRequest,
    AssemblyCreateRequest,
    AssemblyCreateResult,
    AssemblyDetail,
    AssemblyListOut,
    AssemblyListQuery,
    AssemblyUpdateRequest,
)
from schema.part_file import PartFileOut
from service._id_parse import parse_snowflake_id
from service.assembly import AssemblyService

# 装配体自身的路由：去掉 router 级 deps，改成 per-route 声明，
# 让 INSPECTOR（品检员）能只读访问 list / detail / files。
# 写端点（POST）仍保持 MANAGER + CLERK。

router = APIRouter(
    prefix="/assemblies",
    tags=["装配体管理"],
)

# 读端点共用：MANAGER + CLERK + INSPECTOR（PR-I 2026-07-20）
_assembly_read_dep = [
    Depends(require_roles(
        UserRole.MANAGER, UserRole.CLERK, UserRole.INSPECTOR,
    ))
]

# 写端点共用：MANAGER + CLERK
_assembly_write_dep = [
    Depends(require_roles(UserRole.MANAGER, UserRole.CLERK))
]


@router.get(
    "",
    response_model=AssemblyListOut,
    summary="分页查询装配体列表（MANAGER / CLERK / INSPECTOR 只读）",
    dependencies=_assembly_read_dep,
)
async def list_assemblies(
    customer_id: str | None = Query(default=None, description="客户 id（雪花 ID 字符串）"),
    status: str | None = Query(default=None),
    is_urgent: bool | None = Query(default=None),
    drawing_no_like: str | None = Query(default=None),
    name_like: str | None = Query(default=None),
    sort_by: str = Query(
        default="PLANNED_DELIVERY_DATE",
        description="排序字段：PLANNED_DELIVERY_DATE / REQUEST_DATE / CREATED_AT / SERIAL_NO / DRAWING_NO / NAME",
    ),
    sort_dir: str = Query(default="ASC", description="排序方向：ASC / DESC"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    svc: AssemblyService = Depends(get_assembly_service),
) -> AssemblyListOut:
    from repository.assembly import AssemblySortKey
    from model.enums import SortDir

    return await svc.list_assemblies(
        AssemblyListQuery(
            customer_id=customer_id,
            status=status,
            is_urgent=is_urgent,
            drawing_no_like=drawing_no_like,
            name_like=name_like,
            sort_by=AssemblySortKey(sort_by),
            sort_dir=SortDir(sort_dir),
            limit=limit,
            offset=offset,
        )
    )


@router.get(
    "/{assembly_id}",
    response_model=AssemblyDetail,
    summary="装配件详情（自身 + 子件 + 文件）（MANAGER / CLERK / INSPECTOR 只读）",
    dependencies=_assembly_read_dep,
)
async def get_assembly(
    assembly_id: int,
    svc: AssemblyService = Depends(get_assembly_service),
) -> AssemblyDetail:
    return await svc.get_assembly_detail(assembly_id)


@router.post(
    "/{assembly_id}/soft-delete",
    summary="级联软删装配件 + 子件 + 文件（COS 文件异步清理，MANAGER-only）",
    dependencies=[Depends(require_role(UserRole.MANAGER))],
)
async def soft_delete_assembly(
    assembly_id: int,
    svc: AssemblyService = Depends(get_assembly_service),
) -> dict:
    await svc.soft_delete_assembly(assembly_id)
    return {"ok": True}


@router.post(
    "/{assembly_id}/cancel",
    response_model=AssemblyDetail,
    summary="取消装配体，级联取消所有非终态子件",
    dependencies=_assembly_write_dep,
)
async def cancel_assembly(
    assembly_id: int,
    svc: AssemblyService = Depends(get_assembly_service),
) -> AssemblyDetail:
    return await svc.cancel_assembly(assembly_id)


@router.post(
    "/{assembly_id}/update",
    response_model=AssemblyDetail,
    summary="编辑装配体元数据（MANAGER + CLERK，field-level partial update）",
    dependencies=_assembly_write_dep,
)
async def update_assembly(
    assembly_id: int,
    payload: AssemblyUpdateRequest,
    svc: AssemblyService = Depends(get_assembly_service),
) -> AssemblyDetail:
    return await svc.update_assembly(assembly_id, payload)


@router.post(
    "/{assembly_id}/upload-pdf",
    response_model=AssemblyDetail,
    status_code=http_status.HTTP_201_CREATED,
    summary="详情页上传总装 PDF：拆页 → page 1 = master + page 2..N = 自动创建子件",
    dependencies=_assembly_write_dep,
)
async def upload_assembly_pdf(
    assembly_id: int,
    file: UploadFile = File(..., description="总装 PDF（必填，第一页是总装图）"),
    svc: AssemblyService = Depends(get_assembly_service),
) -> AssemblyDetail:
    pdf_bytes = await file.read()
    return await svc.upload_total_pdf(
        assembly_id,
        pdf_bytes=pdf_bytes,
        pdf_filename=file.filename or "assembly.pdf",
    )


@router.post(
    "/{assembly_id}/children",
    response_model=dict,  # PartOut（避免循环引用；前端用 PartListItem 接收）
    status_code=http_status.HTTP_201_CREATED,
    summary="详情页添加单个子件（无 PDF；如需 PDF 走 POST /parts/{id}/files）",
    dependencies=_assembly_write_dep,
)
async def add_assembly_child(
    assembly_id: int,
    payload: AddAssemblyChildRequest,
    svc: AssemblyService = Depends(get_assembly_service),
) -> dict:
    child_out = await svc.add_child(assembly_id, payload)
    return child_out.model_dump(mode="json")


# ---------- 子件反查（MANAGER + CLERK + CNC_PROGRAMMER + INSPECTOR） ----------
# （INSPECTOR 在 PR-I 2026-07-20 加入：PartDetail 页对装配件子件反查父装配件
#  时会调此端点；INSPECTOR 已经能访问装配体 list/detail/files）
child_router = APIRouter(
    prefix="/parts",
    tags=["零件管理"],
    dependencies=[
        Depends(require_roles(
            UserRole.MANAGER, UserRole.CLERK, UserRole.CNC_PROGRAMMER,
            UserRole.INSPECTOR,
        ))
    ],
)


@child_router.get(
    "/{part_id}/assembly",
    response_model=AssemblyDetail,
    summary="从任意子零件反查所属装配件（装配件自身/兄弟/文件）",
)
async def get_assembly_for_child(
    part_id: str,
    svc: AssemblyService = Depends(get_assembly_service),
) -> AssemblyDetail:
    # path 参数 part_id 是雪花 ID 字符串（避免 JS Number.MAX_SAFE_INTEGER 丢精度），
    # 在 service 边界 parse 转 int；失败抛 BIZ_INVALID_VALUE 400。
    # 见 CLAUDE.md §3 「雪花 ID 入参必须用 str 类型」。
    part_id_int = parse_snowflake_id(part_id, field_name="part_id")
    if part_id_int is None:
        raise BizError(
            code=ErrCode.BIZ_INVALID_VALUE,
            message=f"invalid part id: {part_id!r}",
            http_status=http_status.HTTP_400_BAD_REQUEST,
        )
    return await svc.get_assembly_for_child(part_id_int)


# 装配件文件：list（聚合 master + 子件 drawings），
# 上传已合并到 create_assembly / upload_total_pdf 流程，不再有独立 POST 端点。
# INSPECTOR 加进读端点（PR-I 2026-07-20）。
file_router = APIRouter(
    prefix="/assemblies",
    tags=["装配体管理"],
    dependencies=[
        Depends(require_roles(
            UserRole.MANAGER, UserRole.CLERK, UserRole.CNC_PROGRAMMER,
            UserRole.INSPECTOR,
        ))
    ],
)


@file_router.get(
    "/{assembly_id}/files",
    response_model=list[PartFileOut],
    summary="装配件下的所有文件（master + 子件 drawings）",
)
async def list_assembly_files(
    assembly_id: int,
    svc: AssemblyService = Depends(get_assembly_service),
) -> list[PartFileOut]:
    asm = await svc.assemblies.get_by_id(assembly_id)
    if asm is None:
        from core.error_code import ErrCode
        from core.exception import BizError
        from fastapi import status as http_status
        raise BizError(
            code=ErrCode.BIZ_ASSEMBLY_NOT_FOUND,
            message=f"assembly {assembly_id} not found",
            http_status=http_status.HTTP_404_NOT_FOUND,
        )
    return await svc.part_files.list_for_assembly(
        assembly_id, child_part_ids=[c.id for c in await svc.parts.list_children(assembly_id)],
    )