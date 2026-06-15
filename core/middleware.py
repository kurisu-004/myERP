import json
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

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