"""可选 Redis 缓存层：数据库是唯一事实来源，任何 Redis 异常都降级为空操作。"""

from __future__ import annotations

import json
import os
from typing import Any, Protocol


class CacheClient(Protocol):
    def get_json(self, key: str) -> Any | None: ...

    def set_json(self, key: str, value: Any, ttl_seconds: int) -> None: ...

    def delete(self, *keys: str) -> None: ...

    def delete_prefix(self, prefix: str) -> None: ...

    def set_if_absent(self, key: str, value: str, ttl_seconds: int) -> bool: ...


class NullCache:
    """无 Redis 时的空实现；set_if_absent 恒成功，语义等于"未启用加速"。"""

    def get_json(self, key: str) -> Any | None:
        return None

    def set_json(self, key: str, value: Any, ttl_seconds: int) -> None:
        return None

    def delete(self, *keys: str) -> None:
        return None

    def delete_prefix(self, prefix: str) -> None:
        return None

    def set_if_absent(self, key: str, value: str, ttl_seconds: int) -> bool:
        return True


class RedisCache:
    def __init__(self, url: str) -> None:
        import redis

        # 缓存层必须快速失败：连接超时 0.3s，拒连/网络异常时按降级语义走数据库
        self._client = redis.Redis.from_url(url, decode_responses=True, socket_connect_timeout=0.3)

    def get_json(self, key: str) -> Any | None:
        try:
            raw = self._client.get(key)
            return json.loads(raw) if raw is not None else None
        except Exception:
            return None

    def set_json(self, key: str, value: Any, ttl_seconds: int) -> None:
        try:
            self._client.set(key, json.dumps(value, ensure_ascii=False), ex=ttl_seconds)
        except Exception:
            return None

    def delete(self, *keys: str) -> None:
        try:
            if keys:
                self._client.delete(*keys)
        except Exception:
            return None

    def delete_prefix(self, prefix: str) -> None:
        try:
            batch: list[str] = []
            for key in self._client.scan_iter(match=f"{prefix}*"):
                batch.append(key)
                if len(batch) >= 200:
                    self._client.delete(*batch)
                    batch = []
            if batch:
                self._client.delete(*batch)
        except Exception:
            return None

    def set_if_absent(self, key: str, value: str, ttl_seconds: int) -> bool:
        try:
            return bool(self._client.set(key, value, ex=ttl_seconds, nx=True))
        except Exception:
            return True


def build_cache_from_env() -> CacheClient:
    """SHOP_REDIS_URL 为空时禁用 Redis；连接失败仅告警并降级为 NullCache。"""
    url = os.getenv("SHOP_REDIS_URL", "").strip()
    if not url:
        return NullCache()
    try:
        return RedisCache(url)
    except Exception as error:
        print(f"[shop] Redis 初始化失败，已降级为无缓存模式: {error}")
        return NullCache()
