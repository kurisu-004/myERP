from fastapi import status as http_status

from core.error_code import ErrCode


class BizError(Exception):
    """业务异常.

    - code: 业务错误码 (ErrCode)
    - message: 业务提示
    - http_status: 对外 HTTP 状态码 (默认 400,便于网关/前端判断)
    """

    http_status: int = http_status.HTTP_400_BAD_REQUEST

    def __init__(
        self,
        code: ErrCode = ErrCode.BIZ_USER_NOT_FOUND,
        message: str = "business error",
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        if http_status is not None:
            self.http_status = http_status