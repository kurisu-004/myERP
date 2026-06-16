"""用户权限内存缓存 (替代 Redis, 简化实现)."""
import asyncio
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class CacheEntry:
    value: Any
    expires_at: float


class PermissionCache:
    """进程内权限缓存, TTL 控制.

    生产环境应当接入 Redis; 这里为了零依赖,使用内存 dict + asyncio.Lock.
    """

    def __init__(self, default_ttl: int = 1800) -> None:
        self._store: dict[str, CacheEntry] = {}
        self._lock = asyncio.Lock()
        self._default_ttl = default_ttl

    async def get(self, key: str) -> Any | None:
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if entry.expires_at < time.time():
                del self._store[key]
                return None
            return entry.value

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        async with self._lock:
            self._store[key] = CacheEntry(
                value=value,
                expires_at=time.time() + (ttl or self._default_ttl),
            )

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._store.pop(key, None)

    async def clear(self) -> None:
        async with self._lock:
            self._store.clear()


permission_cache = PermissionCache()
