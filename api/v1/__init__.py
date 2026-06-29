from fastapi import APIRouter

from . import customer, part, worker, ws

api_router = APIRouter(prefix="/v1")
api_router.include_router(customer.router)
api_router.include_router(part.router)
api_router.include_router(worker.router)
api_router.include_router(ws.router)