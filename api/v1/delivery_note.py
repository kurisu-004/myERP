"""送货单 Excel 模板导出（PR-B 2026-07-10）。

POST /api/v1/delivery-notes/generate
- body: { part_ids: list[str] }（雪花 ID 字符串）
- response: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet
- permission: MANAGER + CLERK（与「生成送货单」前端入口对齐）
"""
from datetime import datetime

from fastapi import APIRouter, Depends, Response, status as http_status
from pydantic import BaseModel, Field

from api.deps import get_delivery_note_service
from core.permission import require_roles
from model.enums import UserRole
from service.delivery_note import DeliveryNoteService
from service._id_parse import parse_snowflake_id
from core.error_code import ErrCode
from core.exception import BizError

router = APIRouter(prefix="/delivery-notes", tags=["送货单"])


class DeliveryNoteGenerateRequest(BaseModel):
    part_ids: list[str] = Field(
        min_length=1,
        description="雪花 ID 字符串列表（service 层 int() 转换）",
    )


@router.post(
    "/generate",
    summary="按 part_ids 列表生成送货单 xlsx（MANAGER / CLERK）",
    response_class=Response,
    dependencies=[Depends(require_roles(UserRole.MANAGER, UserRole.CLERK))],
)
async def generate_delivery_note(
    payload: DeliveryNoteGenerateRequest,
    svc: DeliveryNoteService = Depends(get_delivery_note_service),
) -> Response:
    try:
        ids = [parse_snowflake_id(s, field_name="part_ids") for s in payload.part_ids]
    except BizError as e:
        # parse_snowflake_id 已经抛了 BIZ_INVALID_VALUE；这里再抛一次让框架接管
        raise

    blob = await svc.build_delivery_note_xlsx([i for i in ids if i is not None])
    today = datetime.now().strftime("%Y%m%d")
    return Response(
        content=blob,
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={
            "Content-Disposition": (
                f'attachment; filename="delivery_note_{today}.xlsx"'
            ),
        },
        status_code=http_status.HTTP_200_OK,
    )