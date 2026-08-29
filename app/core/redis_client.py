"""
=========================================================
Datei:      app/core/redis_client.py
Zweck:      Async Redis (AOF) Verbindung mit fakeredis-Fallback
Knoten:     Jaune (Carrera-Engine) / Core
=========================================================
Redis ist der Herzschlag der M8-Fast-Path-Architektur:
  m8:state:{strategy_id}  -> Hash, kein TTL, write-through
  halt:symbol:{symbol}    -> String, TTL 300s
  strategies:wake_up      -> Pub/Sub Quarantäne-Benachrichtigung
  signals:proposed/verdict-> Fast Path (~300 B JSON)
"""
from __future__ import annotations

import logging
from typing import Optional

from app.core.config import AlphaConfig

logger = logging.getLogger("app.core.redis")

_global_client = None
_is_fake = False


async def get_redis(config: AlphaConfig, force_reconnect: bool = False):
    """Return a shared async Redis client (fakeredis fallback when no server)."""
    global _global_client, _is_fake
    if _global_client is not None and not force_reconnect:
        return _global_client

    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(
            config.redis_url, decode_responses=True, socket_connect_timeout=2
        )
        await client.ping()
        _global_client = client
        _is_fake = False
        logger.info("Redis connected (real) at %s", config.redis_url)
        return client
    except Exception as exc:
        if not config.allow_fakeredis:
            _global_client = None
            raise
        import fakeredis.aioredis

        client = fakeredis.aioredis.FakeRedis(decode_responses=True)
        _global_client = client
        _is_fake = True
        logger.warning(
            "Redis server unreachable (%s) — using in-memory fakeredis "
            "(durable AOF state disabled in this run).", exc
        )
        return client


def is_fake_redis(client=None) -> bool:
    """True when get_redis() fell back, or when *client* is a FakeRedis instance.

    fakeredis does not implement SCRIPT LOAD / EVALSHA. Callers must use the
    local Python fallback instead of loading Lua.
    """
    if client is not None:
        return "fakeredis" in getattr(type(client), "__module__", "")
    return _is_fake


async def close_redis() -> None:
    global _global_client
    if _global_client is not None:
        try:
            await _global_client.aclose()
        except Exception:
            try:
                await _global_client.close()
            except Exception:
                pass
        _global_client = None
