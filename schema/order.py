from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from schema._types import IdStrNonNull


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: IdStrNonNull
    user_id: IdStrNonNull
    price: Decimal


class OrderCreate(BaseModel):
    price: Decimal = Field(..., gt=0, examples=[99.90])
