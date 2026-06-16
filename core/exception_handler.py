from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from core.error_code import ErrCode
from core.exception import BizError
from core.response import R


def _json(code: ErrCode, message: str, http_status: int, data=None) -> JSONResponse:
    return JSONResponse(
        status_code=http_status,
        content=R.fail(code=code, message=message, data=data).model_dump(),
    )


def _err_code_for_http_status(http_status: int) -> ErrCode:
    if http_status == 401:
        return ErrCode.UNAUTHORIZED
    if http_status == 403:
        return ErrCode.FORBIDDEN
    if http_status == 404:
        return ErrCode.NOT_FOUND
    if http_status == 409:
        return ErrCode.CONFLICT
    return ErrCode.BAD_REQUEST


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(BizError)
    async def biz_error_handler(_: Request, exc: BizError):
        return _json(exc.code, exc.message, exc.http_status)

    @app.exception_handler(HTTPException)
    async def http_exception_handler(_: Request, exc: HTTPException):
        # 如果 detail 已经是结构化的 {code, message, data} 透传
        if isinstance(exc.detail, dict) and "code" in exc.detail and "message" in exc.detail:
            payload = R.fail(
                code=exc.detail["code"],
                message=exc.detail["message"],
                data=exc.detail.get("data"),
            ).model_dump()
        else:
            payload = R.fail(
                code=_err_code_for_http_status(exc.status_code),
                message=str(exc.detail),
            ).model_dump()
        return JSONResponse(
            status_code=exc.status_code,
            content=payload,
            headers=exc.headers or {},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_: Request, exc: RequestValidationError):
        http_422 = getattr(
            status, "HTTP_422_UNPROCESSABLE_CONTENT", status.HTTP_422_UNPROCESSABLE_ENTITY
        )
        return _json(
            ErrCode.VALIDATION_ERROR,
            "request validation failed",
            http_422,
            data=exc.errors(),
        )

    @app.exception_handler(SQLAlchemyError)
    async def sqlalchemy_error_handler(_: Request, exc: SQLAlchemyError):
        return _json(
            ErrCode.DATABASE_ERROR,
            "database error",
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(_: Request, exc: Exception):
        return _json(
            ErrCode.INTERNAL_ERROR,
            str(exc) or "internal server error",
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        )