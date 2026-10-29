"""
=========================================================
Datei:      app/core/public_url.py
Zweck:      Oeffentliche Webhook-Basis aus ngrok Local-API, Fallback SIGMA_PUBLIC_URL.
=========================================================
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from app.core import blueprint as bp

logger = logging.getLogger("app.core.public_url")

_cache_url = ""
_cache_ts = 0.0
_last_emitted = ""


@dataclass(frozen=True)
class PublicUrl:
    url: str
    source: str
    changed: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {"url": self.url, "source": self.source, "changed": self.changed}


def _ngrok_api_url() -> str:
    return (os.environ.get(bp.NGROK_API_URL_ENV) or bp.NGROK_API_URL).rstrip("/")


def _fetch_ngrok(api_url: str, timeout_s: float = 1.5) -> Optional[Dict[str, Any]]:
    req = urllib.request.Request(api_url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw)


def _https_tunnel(payload: Any) -> str:
    tunnels = payload.get("tunnels") if isinstance(payload, dict) else None
    if not isinstance(tunnels, list):
        return ""
    https = ""
    for row in tunnels:
        if not isinstance(row, dict):
            continue
        public = str(row.get("public_url") or "").rstrip("/")
        proto = str(row.get("proto") or "").lower()
        if public.startswith("https://") or proto == "https":
            return public
        if public.startswith("http://") and not https:
            https = public
    return https


def _fallback_url() -> str:
    return (os.environ.get("SIGMA_PUBLIC_URL") or "http://127.0.0.1:8000").rstrip("/")


def is_loopback(url: str) -> bool:
    blob = (url or "").lower()
    return "127.0.0.1" in blob or "localhost" in blob or blob.startswith("http://0.0.0.0")


def resolve_public_url(
    *,
    now: Optional[float] = None,
    fetch: Optional[Callable[[str], Any]] = None,
    force: bool = False,
) -> PublicUrl:
    """Cached ngrok https public_url, else SIGMA_PUBLIC_URL."""
    global _cache_url, _cache_ts, _last_emitted
    ts = float(now if now is not None else time.time())
    if (not force) and _cache_url and (ts - _cache_ts) < float(bp.PUBLIC_URL_TTL_S):
        changed = _cache_url != _last_emitted and bool(_last_emitted)
        if _cache_url != _last_emitted:
            _last_emitted = _cache_url
        return PublicUrl(_cache_url, "cache", changed)

    source = "env"
    url = _fallback_url()
    api = _ngrok_api_url()
    getter = fetch or _fetch_ngrok
    try:
        payload = getter(api)
        tunnel = _https_tunnel(payload)
        if tunnel:
            url, source = tunnel, "ngrok"
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError) as exc:
        logger.debug("ngrok local api unavailable: %s", exc)

    changed = bool(_last_emitted) and url != _last_emitted
    if changed:
        logger.info("public webhook base changed %s -> %s (%s)", _last_emitted, url, source)
    _cache_url, _cache_ts, _last_emitted = url, ts, url
    return PublicUrl(url, source, changed)


def reset_public_url_cache() -> None:
    global _cache_url, _cache_ts, _last_emitted
    _cache_url, _cache_ts, _last_emitted = "", 0.0, ""
