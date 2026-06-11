from fastapi import HTTPException, status

class BizError(HTTPException):
    code: str = "BIZ_ERROR"
    message: str = "business error"

