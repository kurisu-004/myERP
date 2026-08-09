"""MCP 图纸内容代理端点（2026-08-08 新增）。

给 AI / MCP host 一个**不过期、免鉴权**的图纸下载链接，替代 COS 预签名 URL
（后者 900s 就失效，塞进对话上下文里很快就是废链接）。

🔒 安全闸：本端点免鉴权，因此**只放行 `kind=DRAWING`**。G 代码、3D 模型、CAD 源文件、
工艺卡都是核心工艺资产，不能靠遍历 file_id 拿到 —— 非 DRAWING 一律 404（不是 403，
避免泄露「这个 id 存在但不给你」的信息）。

本端点返回二进制，不会被转换成 MCP tool（见 `core/mcp_server.py` 里对
`^/api/mcp/files/` 的 EXCLUDE 规则），只作为 HTTP 链接供 host 自行 GET。
"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, Path
from fastapi.responses import Response
from typing import Annotated

from api.deps import get_mcp_part_file_service
from core.error_code import ErrCode
from core.exception import BizError
from model.enums import PartFileKind
from service._id_parse import parse_snowflake_id
from service.part_file import PartFileService

router = APIRouter(prefix="/files")


def _etag_matches(if_none_match: str | None, sha: str) -> bool:
    """RFC 9110 的 `If-None-Match`：可以是逗号分隔的多个值，可带 `W/` 弱标记。

    只按 `.strip('"')` 比较会漏掉 `W/"abc"` 和多值形式，白白多传一次文件。
    """
    if not if_none_match:
        return False
    for raw in if_none_match.split(","):
        tag = raw.strip()
        if tag == "*":
            return True
        if tag.startswith("W/"):
            tag = tag[2:]
        if tag.strip('"') == sha:
            return True
    return False


@router.get(
    "/{file_id}/content",
    operation_id="get_drawing_content",
    summary="下载图纸文件内容（仅 DRAWING）",
)
async def get_drawing_content(
    file_id: Annotated[
        str,
        Path(description="文件 ID（雪花 ID 数字字符串），取自查询结果的 drawing.file_id"),
    ],
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
    svc: PartFileService = Depends(get_mcp_part_file_service),
):
    """返回图纸文件字节（PDF 或图片）。仅对 `kind=DRAWING` 的文件开放。"""
    # CLAUDE.md §3：雪花 ID 入参一律用 str + parse_snowflake_id，
    # 免得客户端按 OpenAPI 的 integer 类型走 JS Number 丢精度。
    fid = parse_snowflake_id(file_id, field_name="file_id")

    # 304 快路径只查 DB，不下 COS。
    meta = await svc.get_meta_for_304(fid)
    # 不存在 与 非图纸 返回同一个 404：不泄露 id 的存在性。
    if meta is None or meta.kind != PartFileKind.DRAWING.value:
        raise BizError(
            code=ErrCode.BIZ_PART_FILE_NOT_FOUND,
            message=f"drawing {file_id} not found",
            http_status=404,
        )

    if meta.content_sha256 and _etag_matches(if_none_match, meta.content_sha256):
        return Response(
            status_code=304, headers={"ETag": f'"{meta.content_sha256}"'},
        )

    data, content_type, filename = await svc.get_file_content(fid)
    encoded = quote(filename, safe="")
    headers = {
        "Content-Disposition": f"inline; filename*=UTF-8''{encoded}",
        "Cache-Control": "private, max-age=600",
    }
    if meta.content_sha256:
        headers["ETag"] = f'"{meta.content_sha256}"'
    return Response(content=data, media_type=content_type, headers=headers)
