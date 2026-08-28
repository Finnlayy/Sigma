"""
=========================================================
Datei:      app/tv/script_catalog.py
Zweck:      TradingView "My Scripts" / published scripts entdecken.
            Session-Cookies aus tv_storage_state.json (kein Fake-Katalog).
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Carrera-Engine) / TV-Automation
=========================================================
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote

from app.core import blueprint as bp
from app.core.config import SigmaConfig, load_config

logger = logging.getLogger("app.tv.script_catalog")

PINE_FACADE_BASE = "https://pine-facade.tradingview.com/pine-facade"
HttpTransport = Callable[[str, Dict[str, str]], Any]


def pine_facade_list_urls() -> Tuple[Tuple[str, str], ...]:
    """Saved + published. Reihenfolge: private library zuerst."""
    return (
        (f"{PINE_FACADE_BASE}/list?filter=saved", "saved"),
        (f"{PINE_FACADE_BASE}/list/?filter=saved", "saved"),
        (f"{PINE_FACADE_BASE}/list?filter=published", "published"),
        (f"{PINE_FACADE_BASE}/list/?filter=published", "published"),
    )


def cookies_from_storage_state(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError, TypeError):
        return ""
    parts: List[str] = []
    for cookie in data.get("cookies") or []:
        if not isinstance(cookie, dict):
            continue
        name, value = cookie.get("name"), cookie.get("value")
        domain = str(cookie.get("domain") or "")
        if not name or value is None:
            continue
        if domain and "tradingview" not in domain.lower():
            continue
        parts.append(f"{name}={value}")
    return "; ".join(parts)


def _session_headers(cookie_header: str) -> Dict[str, str]:
    return {
        "Accept": "application/json, text/plain, */*",
        "Cookie": cookie_header,
        "Origin": bp.TV_BASE_URL,
        "Referer": f"{bp.TV_BASE_URL}/",
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
    }


def _default_http(url: str, headers: Dict[str, str]) -> Any:
    import httpx

    resp = httpx.get(url, headers=headers, timeout=20.0, follow_redirects=True)
    resp.raise_for_status()
    ctype = (resp.headers.get("content-type") or "").lower()
    if "json" in ctype:
        return resp.json()
    try:
        return resp.json()
    except Exception:
        return None


def _iter_rows(payload: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                yield item
        return
    if not isinstance(payload, dict):
        return
    for key in ("results", "data", "scripts", "items", "list"):
        nested = payload.get(key)
        if isinstance(nested, list):
            for item in nested:
                if isinstance(item, dict):
                    yield item
            return
    if payload.get("scriptIdPart") or payload.get("scriptName") or payload.get("name"):
        yield payload


def _script_url(script_id: str, href: str = "") -> str:
    if href:
        if href.startswith("http"):
            return href
        return f"{bp.TV_BASE_URL}{href}" if href.startswith("/") else f"{bp.TV_BASE_URL}/{href}"
    if not script_id:
        return ""
    return f"{bp.TV_BASE_URL}/script/{quote(script_id, safe='')}/"


def normalize_script_row(raw: Dict[str, Any], *, origin: str = "") -> Optional[Dict[str, Any]]:
    extra = raw.get("extra") if isinstance(raw.get("extra"), dict) else {}
    href = str(raw.get("href") or raw.get("url") or extra.get("url") or "")
    script_id = str(
        raw.get("tv_script_id")
        or raw.get("scriptIdPart")
        or raw.get("scriptId")
        or raw.get("script_id")
        or extra.get("scriptIdPart")
        or ""
    ).strip()
    if not script_id and href:
        script_id = href.strip()
    name = str(
        raw.get("name")
        or raw.get("scriptName")
        or raw.get("script_name")
        or extra.get("name")
        or ""
    ).strip()
    if not script_id and not name:
        return None
    kind = str(raw.get("type") or extra.get("kind") or extra.get("type") or "strategy").strip() or "strategy"
    version = str(raw.get("version") or extra.get("version") or "").strip()
    symbol = str(raw.get("symbol") or extra.get("symbol") or extra.get("ticker") or "").strip()
    interval = raw.get("interval") or extra.get("interval") or extra.get("timeframe") or ""
    source = raw.get("source") or raw.get("scriptSource") or extra.get("source") or ""
    return {
        "tv_script_id": script_id or name,
        "name": name or script_id,
        "type": kind,
        "version": version,
        "symbol": symbol,
        "interval": interval,
        "url": _script_url(script_id, href),
        "origin": origin or str(raw.get("origin") or ""),
        "has_source": bool(source),
        "pine_source": source if isinstance(source, str) else "",
    }


def normalize_script_rows(payload: Any, *, origin: str = "") -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for raw in _iter_rows(payload):
        row = normalize_script_row(raw, origin=origin)
        if row is None:
            continue
        key = row["tv_script_id"]
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def merge_scripts(*groups: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: set[str] = set()
    out: List[Dict[str, Any]] = []
    for group in groups:
        for row in group:
            key = str(row.get("tv_script_id") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(dict(row))
    return out


def fetch_pine_source(
    script_id: str,
    version: str = "last",
    *,
    cookie_header: str = "",
    http: Optional[HttpTransport] = None,
) -> str:
    if not script_id or not cookie_header:
        return ""
    transport = http or _default_http
    ver = version or "last"
    url = f"{PINE_FACADE_BASE}/get/{quote(script_id, safe='')}/{quote(ver, safe='')}"
    try:
        payload = transport(url, _session_headers(cookie_header))
    except Exception as exc:
        logger.info("pine-facade get failed for %s: %s", script_id, exc)
        return ""
    if isinstance(payload, dict):
        source = payload.get("source") or payload.get("scriptSource") or ""
        if isinstance(source, str):
            return source
    return ""


def list_via_session_http(
    config: Optional[SigmaConfig] = None,
    *,
    http: Optional[HttpTransport] = None,
) -> List[Dict[str, Any]]:
    cfg = config or load_config()
    path = cfg.tv_storage_state_path
    if not os.path.exists(path):
        return []
    cookie_header = cookies_from_storage_state(path)
    if not cookie_header:
        return []
    transport = http or _default_http
    headers = _session_headers(cookie_header)
    groups: List[List[Dict[str, Any]]] = []
    tried: set[str] = set()
    for url, origin in pine_facade_list_urls():
        if url in tried:
            continue
        tried.add(url)
        try:
            payload = transport(url, headers)
        except Exception as exc:
            logger.info("pine-facade list %s failed: %s", origin, exc)
            continue
        rows = normalize_script_rows(payload, origin=origin)
        if rows:
            groups.append(rows)
    return merge_scripts(*groups)


def list_available_scripts(
    *,
    config: Optional[SigmaConfig] = None,
    driver: Any = None,
    http: Optional[HttpTransport] = None,
) -> Dict[str, Any]:
    """Echte TV-Library. Ohne Session: leere Liste (kein Fake-Katalog)."""
    cfg = config or load_config()
    session_present = os.path.exists(cfg.tv_storage_state_path)
    scripts: List[Dict[str, Any]] = []
    source = "none"
    reason = ""
    if driver is not None and hasattr(driver, "list_my_scripts"):
        try:
            scripts = normalize_script_rows(driver.list_my_scripts(), origin="driver")
            source = "driver"
        except Exception as exc:
            reason = str(exc)
            logger.warning("driver list_my_scripts failed: %s", exc)
    elif session_present:
        scripts = list_via_session_http(cfg, http=http)
        if scripts:
            source = "pine-facade"
        else:
            from app.tv.strategy_tester_driver import DriverError, list_my_scripts_with_playwright

            try:
                scripts = normalize_script_rows(
                    list_my_scripts_with_playwright(cfg), origin="playwright")
                source = "playwright"
            except DriverError as exc:
                reason = str(exc)
                source = "session-error"
            except Exception as exc:
                reason = str(exc)
                logger.warning("playwright script list failed: %s", exc)
                source = "session-error"
        if not scripts and not reason:
            reason = "TradingView returned no saved or published scripts"
    else:
        reason = "TV session missing — run bin/sigma-tv-login"
    return {
        "scripts": scripts,
        "source": source,
        "session_present": session_present,
        "driver": "playwright" if session_present else "fake",
        "reason": reason,
        "count": len(scripts),
    }
