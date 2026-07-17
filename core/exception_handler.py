import logging
import traceback

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm.exc import StaleDataError

from core.error_code import ErrCode
from core.exception import BizError
from core.response import R

_logger = logging.getLogger("exception_handler")


def _json(code: ErrCode, message: str, http_status: int, data=None) -> JSONResponse:
    return JSONResponse(
        status_code=http_status,
        content=R.fail(code=code, message=message, data=data).model_dump(),
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(BizError)
    async def biz_error_handler(_: Request, exc: BizError):
        return _json(exc.code, exc.message, exc.http_status)

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

    @app.exception_handler(StaleDataError)
    async def stale_data_error_handler(_: Request, exc: StaleDataError):
        """乐观锁冲突：把 SQLAlchemy 的 StaleDataError 转 409 + BIZ_VERSION_CONFLICT。

        触发场景：任意通过 ORM dirty tracking 的 UPDATE（`repo.update()`、
        `repo.soft_delete()`、直接 `session.flush()`）发现行的 `version` 列
        已被其他事务改过 → 0 行更新 → StaleDataError。

        业务提示统一为「该记录已被其他用户修改，请刷新后重试」，由前端根据
        409 + 错误码弹窗提示用户重新拉取详情再操作。详细 traceback 走
        WARNING 级别日志，方便排查但不出现在响应里。
        """
        _logger.warning("StaleDataError (optimistic lock conflict): %s", exc)
        return _json(
            ErrCode.BIZ_VERSION_CONFLICT,
            "该记录已被其他用户修改，请刷新后重试",
            status.HTTP_409_CONFLICT,
        )

    @app.exception_handler(SQLAlchemyError)
    async def sqlalchemy_error_handler(_: Request, exc: SQLAlchemyError):
        _logger.exception("SQLAlchemyError")
        return _json(
            ErrCode.DATABASE_ERROR,
            "database error",
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(_: Request, exc: Exception):
        _logger.exception("Unhandled exception")
        return _json(
            ErrCode.INTERNAL_ERROR,
            str(exc) or "internal server error",
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        )