"""验证统一响应结构与全局异常拦截器."""
import pytest
from httpx import ASGITransport, AsyncClient

from main import app


@pytest.mark.asyncio
async def test_validation_error_returns_unified_shape():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post("/api/v1/users", json={"username": ""})
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == 40001
    assert body["message"] == "request validation failed"
    assert body["data"]


@pytest.mark.asyncio
async def test_not_found_returns_unified_shape():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/api/v1/users/9999999999/with-orders")
    assert r.status_code == 404
    body = r.json()
    assert body["code"] == 20001
    assert "9999999999" in body["message"]


@pytest.mark.asyncio
async def test_docs_unaffected():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/openapi.json")
    assert r.status_code == 200
    assert "openapi" in r.json()