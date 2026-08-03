from fastapi import APIRouter

from . import (
    applicant,
    assembly,
    auth,
    cnc_program,
    customer,
    delivery_note,
    drawing,
    outsource_company,
    outsource_quote,
    outsource_shipment,
    part,
    process,
    shelf,
    statistics,
    user,
    work_type,
    worker,
    ws,
)

api_router = APIRouter(prefix="/v1")
api_router.include_router(auth.router)
api_router.include_router(user.router)
# 共享 HMI picker 路由（任意已登录，含 SHELF_ACCOUNT 共享账号；2026-07-10）。
# ⚠️ 必须在 shelf.read_router **之前** 注册：read_router 含 /{shelf_id:int}
# catch-all，若先注册会把 GET /shelves/processes（2026-07-17 新增批量端点）以及
# /shelves/for-return（2026-07-13）/shelves/for-inspection（2026-07-13）三个字面
# 子路径截胡到 get_shelf 并因 "processes"/"for-return"/"for-inspection" → int
# 转换失败返回 40001 VALIDATION_ERROR（HTTP 422）；而前端 useShelfProcessFilter
# 静默 catch 后 loaded=false 退回全集、shelf↔process 下拉无联动。（2026-07-17 修）
api_router.include_router(shelf.picker_router)
# 货架：读（MANAGER+CLERK+CNC_PROGRAMMER）+ 写（MANAGER-only）两个并列 router
api_router.include_router(shelf.read_router)
api_router.include_router(shelf.write_router)
# 客户管理：读（MANAGER+CLERK+CNC_PROGRAMMER）+ 写（MANAGER+CLERK）两个并列 router
api_router.include_router(customer.read_router)
api_router.include_router(customer.write_router)
api_router.include_router(applicant.router)
api_router.include_router(worker.router)
api_router.include_router(part.router)
api_router.include_router(ws.router)
# 送货单 Excel 导出（PR-B 2026-07-10；MANAGER + CLERK）
api_router.include_router(delivery_note.router)
# 装配体（多个 router 共享同一组路径前缀，避免相互覆盖）
api_router.include_router(assembly.router)
# 子件反查 /parts/{part_id}/assembly —— 由装配 router 提供，已自带 MANAGER 守卫
api_router.include_router(assembly.child_router)
# 装配件级文件管理（MANAGER+CLERK+CNC_PROGRAMMER，仅 list，master 由 create 流创建）
api_router.include_router(assembly.file_router)
# 零件文件：/parts/{id}/drawings + /parts/{id}/3d-models + /parts/{id}/files
api_router.include_router(drawing.part_file_router)
# 通用文件：/files/{id}/download-url + /content + /delete
api_router.include_router(drawing.file_router)
# CNC 文件：/parts/{id}/cnc-programs + /parts/{id}/setup-sheets
api_router.include_router(cnc_program.child_cnc_router)
# CNC 文件级：/cnc-programs/{id}/... (别名 → /files/{id}/...)
api_router.include_router(cnc_program.program_router)
# 工种：读（MANAGER+CLERK+CNC_PROGRAMMER+SHELF_ACCOUNT）+ 写（MANAGER-only）
api_router.include_router(work_type.read_router)
api_router.include_router(work_type.write_router)
# 工序：读（MANAGER+CLERK+CNC_PROGRAMMER+SHELF_ACCOUNT）+ 写（MANAGER-only）
api_router.include_router(process.read_router)
api_router.include_router(process.write_router)
# 外协公司：读（MANAGER+CLERK+CNC_PROGRAMMER）+ 写（MANAGER+CLERK）两个并列 router（2026-07-15）
api_router.include_router(outsource_company.read_router)
api_router.include_router(outsource_company.write_router)
# 外协报价：读（MANAGER+CLERK）+ 写（MANAGER+CLERK；approve/reject 是 MANAGER-only）两个并列 router（2026-07-16）
api_router.include_router(outsource_quote.read_router)
api_router.include_router(outsource_quote.write_router)
# 外协发货记录：对账页编辑（2026-07-30 新增）
api_router.include_router(outsource_shipment.router)
# 生产统计（MANAGER-only；2026-08-03 新增）
api_router.include_router(statistics.router)