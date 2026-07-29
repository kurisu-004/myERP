"""外协发货记录 (OutsourceShipment) API（2026-07-30 新增）。

路由：
- POST /outsource-shipments/{shipment_id}/reconcile-update
  对账页双击编辑 shipment（unit_price / quantity / is_billed）。
"""
from fastapi import APIRouter, Depends, status as http_status

from api.deps import get_outsource_quote_service
from core.permission import require_roles
from model.enums import UserRole
from schema.outsource_quote import (
    OutsourceShipmentOut,
    OutsourceShipmentReconcileUpdateRequest,
)
from service import OutsourceQuoteService


router = APIRouter(
    prefix="/outsource-shipments",
    tags=["外协发货记录"],
    dependencies=[
        Depends(require_roles(UserRole.MANAGER, UserRole.CLERK)),
    ],
)


@router.post(
    "/{shipment_id}/reconcile-update",
    response_model=OutsourceShipmentOut,
    summary="对账页更新 shipment（CLERK + MANAGER）：unit_price / quantity / is_billed",
)
async def reconcile_update_shipment(
    shipment_id: str,
    payload: OutsourceShipmentReconcileUpdateRequest,
    svc: OutsourceQuoteService = Depends(get_outsource_quote_service),
) -> OutsourceShipmentOut:
    return await svc.reconcile_update_shipment(shipment_id, payload)
