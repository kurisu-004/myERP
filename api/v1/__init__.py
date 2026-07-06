from fastapi import APIRouter

from . import (
    assembly,
    auth,
    cnc_program,
    customer,
    drawing,
    part,
    process,
    shelf,
    user,
    work_type,
    worker,
    ws,
)

api_router = APIRouter(prefix="/v1")
api_router.include_router(auth.router)
api_router.include_router(user.router)
api_router.include_router(shelf.router)
api_router.include_router(customer.router)
api_router.include_router(worker.router)
api_router.include_router(part.router)
api_router.include_router(ws.router)
# 装配体（多个 router 共享同一组路径前缀，避免相互覆盖）
api_router.include_router(assembly.router)
# 子件反查 /parts/{part_id}/assembly —— 由装配 router 提供，已自带 MANAGER 守卫
api_router.include_router(assembly.child_router)
# 装配件级文件管理（MANAGER+CLERK+CNC_PROGRAMMER）
api_router.include_router(assembly.file_router)
# 子件文件 /parts/{part_id}/files —— MANAGER+CLERK
api_router.include_router(drawing.child_file_router)
# 文件级管理 /drawings/{file_id}/... —— MANAGER+CLERK+CNC_PROGRAMMER
api_router.include_router(drawing.file_router)
# CNC 程序：/parts/{part_id}/cnc-programs + /cnc-programs/{file_id}/...
api_router.include_router(cnc_program.child_program_router)
api_router.include_router(cnc_program.program_router)
# 工种 / 工序 / 映射（写 MANAGER-only；读 MANAGER+CLERK+CNC_PROGRAMMER）
api_router.include_router(work_type.router)
api_router.include_router(process.router)