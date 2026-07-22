import json
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from core.error_code import ErrCode
from core.response import R

_WRAP_PREFIXES = ("/api",)


class UnifiedResponseMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.url.path
        if not any(path == p or path.startswith(p + "/") for p in _WRAP_PREFIXES):
            return await call_next(request)

        response = await call_next(request)

        if response.status_code >= 400:
            return response

        ctype = response.headers.get("content-type", "")
        if "application/json" not in ctype:
            return response

        body = b""
        async for chunk in response.body_iterator:
            body += chunk if isinstance(chunk, bytes) else chunk.encode()

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return Response(content=body, status_code=response.status_code,
                            headers=dict(response.headers))

        wrapped = R.ok(data=payload).model_dump()
        new_body = json.dumps(wrapped, ensure_ascii=False).encode()
        headers = dict(response.headers)
        headers["content-length"] = str(len(new_body))
        return Response(
            content=new_body,
            status_code=response.status_code,
            headers=headers,
            media_type="application/json",
        )


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """请求体大小早期守卫（2026-07-22 新增）。

    批量 PDF 上传（POST /parts/batch-with-pdfs）单次可达 300 MB；
    路由层 `await f.read()` 会把整个 multipart 全量读进进程内存，OOM 风险高。
    在 routing 之前的 middleware 检查 Content-Length，超阈值直接返回 413 信封，
    避免 Starlette MultiPartParser 把全部 body 加载进内存。

    与 frontend/nginx.conf 的 `client_max_body_size` 配对：两层任一拦截都返回 413，
    阈值统一读 `settings.max_request_body_size_bytes`（默认 300 MB）。
    """

    def __init__(self, app, max_bytes: int) -> None:
        super().__init__(app)
        self.max_bytes = max_bytes

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        # 仅对 /api/* 路径生效；静态资源 / SPA fallback 无大 body，过滤省一次 header 读取。
        path = request.url.path
        if not any(path == p or path.startswith(p + "/") for p in _WRAP_PREFIXES):
            return await call_next(request)

        cl = request.headers.get("content-length")
        if cl is not None:
            try:
                if int(cl) > self.max_bytes:
                    # 与 BizError 抛出的信封同结构：`UnifiedResponseMiddleware` 对 4xx
                    # 不再二次包装，前端 `http.ts` 直接按 `{code, message, data}` 解。
                    return JSONResponse(
                        status_code=413,
                        content={
                            "code": int(ErrCode.BIZ_REQUEST_TOO_LARGE),
                            "message": (
                                f"request body {cl} bytes exceeds limit "
                                f"{self.max_bytes} bytes"
                            ),
                            "data": None,
                        },
                    )
            except ValueError:
                # 非整数 Content-Length 让 nginx / FastAPI 后续处理，本层不拦截。
                pass
        return await call_next(request)