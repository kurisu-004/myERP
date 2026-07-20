"""送货单 Excel 模板导出（PR-F 2026-07-17 重设计；2026-07-20 切到 template/ 新模板）。

POST /api/v1/delivery-notes/generate
- body: { part_ids: list[str], template?: "F" | "L" }（雪花 ID 字符串）
- response: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet
- permission: MANAGER + CLERK（与「生成送货单」前端入口对齐）

模板分发规则：
- 若 `template` 给定（"F" / "L"）：走显式选择；service 层仍校验
  「所选零件所属 L1 root 必须与 template 一致」，否则 400
  BIZ_DELIVERY_TEMPLATE_NOT_CONFIGURED。
- 若 `template` 缺省：按所选零件所属 L1 root 的 `serial_prefix` 自动分发
  （F=法拉 / L=路达）。
- prefix 未配置模板 → 400 BIZ_DELIVERY_TEMPLATE_NOT_CONFIGURED。
- 数据行数超出模板容量 → 400 BIZ_DELIVERY_TEMPLATE_TOO_MANY_PARTS。

状态要求：所有 part.status 必须 == READY_TO_SHIP。
跨客户校验：所有 part 必须同属一个 L1 root。
"""
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Response, status as http_status
from pydantic import BaseModel, Field

from api.deps import get_delivery_note_service
from core.permission import require_roles
from model.enums import UserRole
from service._id_parse import parse_snowflake_id
from service.delivery_note import DeliveryNoteService

router = APIRouter(prefix="/delivery-notes", tags=["送货单"])


class DeliveryNoteGenerateRequest(BaseModel):
    part_ids: list[str] = Field(
        min_length=1,
        description="雪花 ID 字符串列表（service 层 int() 转换）",
    )
    template: Literal["F", "L"] | None = Field(
        default=None,
        description=(
            "显式指定送货单模板（F=法拉 / L=路达）；"
            "None=按所选零件所属 L1 root 的 serial_prefix 自动分发"
        ),
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
    # 雪花 ID 字符串 → int（parse_snowflake_id 内部已抛 BizError）
    ids = [parse_snowflake_id(s, field_name="part_ids") for s in payload.part_ids]

    blob, prefix = await svc.build_xlsx(ids, template=payload.template)
    today = datetime.now().strftime("%Y%m%d")
    return Response(
        content=blob,
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={
            "Content-Disposition": (
                f'attachment; filename="delivery_note_{prefix}_{today}.xlsx"'
            ),
        },
        status_code=http_status.HTTP_200_OK,
    )