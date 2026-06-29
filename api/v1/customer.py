"""客户管理 API。"""

from fastapi import APIRouter, Depends

from api.deps import get_customer_service
from schema.customer import CustomerOut
from service import CustomerService

router = APIRouter(prefix="/customers", tags=["客户管理"])


@router.get(
    "",
    response_model=list[CustomerOut],
    summary="客户列表（含 parent_name，前端按 parent_name/child_name 唯一定位）",
)
async def list_customers(
    svc: CustomerService = Depends(get_customer_service),
) -> list[CustomerOut]:
    return await svc.list_customers()