from fastapi import APIRouter

from . import assembly, customer, drawing, part, worker, ws

api_router = APIRouter(prefix="/v1")
api_router.include_router(customer.router)
api_router.include_router(part.router)
api_router.include_router(worker.router)
api_router.include_router(ws.router)
# 装配体（多个 router 共享同一组路径，避免相互覆盖）
api_router.include_router(assembly.router)
api_router.include_router(assembly.file_router)
api_router.include_router(drawing.child_router)
api_router.include_router(drawing.child_file_router)
api_router.include_router(drawing.file_router)