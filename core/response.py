from typing import Any, Generic, TypeVar

from pydantic import BaseModel

from core.error_code import ErrCode

T = TypeVar("T")


class R(BaseModel, Generic[T]):
    code: int = ErrCode.SUCCESS
    message: str = "ok"
    data: T | None = None

    @classmethod
    def ok(cls, data: T | None = None, message: str = "ok") -> "R[T]":
        return cls(code=ErrCode.SUCCESS, message=message, data=data)

    @classmethod
    def fail(
        cls,
        code: ErrCode,
        message: str,
        data: Any = None,
    ) -> "R[Any]":
        return cls(code=code, message=message, data=data)