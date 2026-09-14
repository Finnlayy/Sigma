"""
=========================================================
Datei:      app/services/tv_library_service.py
Zweck:      TV-Strategien als First-Class-Einträge in die Sigma-Library.
            Fail-closed: immer paper + inactive, nie live.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Carrera-Engine) / Services
=========================================================
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional

from app.core.config import SigmaConfig, load_config
from app.tv.alert_provisioner import get_alert_provisioner
from app.tv.interval_map import to_minutes
from app.tv.script_catalog import (
    fetch_pine_source,
    cookies_from_storage_state,
    list_available_scripts,
)

logger = logging.getLogger("app.services.tv_library")

PAPER_MODE = "paper"
INACTIVE = "inactive"

_driver_factory: Optional[Callable[[], Any]] = None
_http_transport: Optional[Callable[[str, Dict[str, str]], Any]] = None
_service: Optional["TvLibraryService"] = None


def set_tv_library_driver_factory(factory: Optional[Callable[[], Any]]) -> None:
    """Test-Seam — injiziert list_my_scripts ohne Playwright."""
    global _driver_factory, _service
    _driver_factory = factory
    _service = None


def set_tv_library_http(transport: Optional[Callable[[str, Dict[str, str]], Any]]) -> None:
    global _http_transport, _service
    _http_transport = transport
    _service = None


def library_id_for(tv_script_id: str) -> str:
    digest = hashlib.sha1((tv_script_id or "").encode("utf-8")).hexdigest()[:12]
    return f"tv_{digest}"


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _escape_pine_title(name: str) -> str:
    return (name or "Imported TV Strategy").replace("\\", "\\\\").replace('"', '\\"')[:80]


def placeholder_pine(name: str, tv_script_id: str, webhook_url: str) -> str:
    title = _escape_pine_title(name)
    return (
        f'//@version=6\n'
        f'strategy("{title}", overlay=true, initial_capital=1000, pyramiding=0)\n'
        f'// Sigma library import of TradingView script {tv_script_id}\n'
        f'// Pine is NOT compiled locally. Attach Loop A via webhook alerts.\n'
        f'// webhook: {webhook_url or "configure in Settings"}\n'
        f'if false\n'
        f'    strategy.entry("L", strategy.long)\n'
    )


def coerce_interval(value: Any, default: int = 15) -> int:
    try:
        minutes = to_minutes(value) if value not in (None, "") else default
        return int(minutes or default)
    except (TypeError, ValueError):
        return default


class TvLibraryService:
    """Discover TV scripts and upsert them into the DuckDB strategy library."""

    def __init__(
        self,
        *,
        config: Optional[SigmaConfig] = None,
        driver_factory: Optional[Callable[[], Any]] = None,
        http: Optional[Callable[[str, Dict[str, str]], Any]] = None,
        provisioner: Any = None,
    ):
        self.config = config or load_config()
        self.driver_factory = driver_factory
        self.http = http
        self.provisioner = provisioner

    def _open_driver(self) -> Any:
        if self.driver_factory is None:
            return None
        return self.driver_factory()

    def discover(self, store: Any) -> Dict[str, Any]:
        drv = self._open_driver()
        try:
            catalog = list_available_scripts(
                config=self.config, driver=drv, http=self.http,
            )
        finally:
            if drv is not None:
                try:
                    drv.close()
                except Exception:
                    pass
        existing = _index_by_tv_script_id(store)
        scripts = []
        for row in catalog.get("scripts") or []:
            item = dict(row)
            sid = str(item.get("tv_script_id") or "")
            match = existing.get(sid)
            item["already_imported"] = match is not None
            item["library_id"] = match["id"] if match else ""
            item["library_execution_mode"] = (match or {}).get("executionMode") or ""
            scripts.append(item)
        catalog["scripts"] = scripts
        catalog["imported_count"] = sum(1 for s in scripts if s.get("already_imported"))
        return catalog

    def sync(
        self,
        store: Any,
        *,
        script_ids: Optional[Iterable[str]] = None,
        symbol: str = "BTC/USD",
        interval: Any = 15,
        fetch_source: bool = True,
    ) -> Dict[str, Any]:
        catalog = self.discover(store)
        available = {str(s.get("tv_script_id") or ""): s for s in catalog.get("scripts") or []}
        wanted = [str(x).strip() for x in (script_ids or []) if str(x).strip()]
        if wanted:
            missing = [sid for sid in wanted if sid not in available]
            selected = [available[sid] for sid in wanted if sid in available]
        else:
            missing = []
            selected = list(available.values())

        cookie_header = ""
        if fetch_source and os.path.exists(self.config.tv_storage_state_path):
            cookie_header = cookies_from_storage_state(self.config.tv_storage_state_path)

        imported: List[Dict[str, Any]] = []
        skipped: List[Dict[str, Any]] = []
        strategies: List[Dict[str, Any]] = []
        default_interval = coerce_interval(interval, 15)
        default_symbol = (symbol or "BTC/USD").strip() or "BTC/USD"

        for script in selected:
            tv_id = str(script.get("tv_script_id") or "")
            existing = store.find_strategy_by_tv_script_id(tv_id) if hasattr(store, "find_strategy_by_tv_script_id") else None
            if existing is None:
                existing = _match_existing(store, tv_id)
            if existing is not None:
                skipped.append({
                    "tv_script_id": tv_id,
                    "name": script.get("name"),
                    "library_id": existing.get("id"),
                    "reason": "already_imported",
                })
                strategies.append(existing)
                continue
            row = self._build_library_row(
                script,
                default_symbol=default_symbol,
                default_interval=default_interval,
                cookie_header=cookie_header if fetch_source else "",
            )
            store.upsert_strategy(row)
            saved = store.get_strategy(row["id"]) or row
            alert = self._bind_alert(saved)
            if alert:
                params = dict(saved.get("parameters") or {})
                params["tv_alert_id"] = alert.get("tv_alert_id") or ""
                params["alert_status"] = alert.get("status") or "DISABLED"
                params["webhook_url"] = alert.get("webhook_url") or params.get("webhook_url")
                saved["parameters"] = params
                saved["tv_alert_id"] = params["tv_alert_id"]
                saved["alert_status"] = params["alert_status"]
                store.upsert_strategy(saved)
                saved = store.get_strategy(saved["id"]) or saved
            imported.append({
                "tv_script_id": tv_id,
                "name": saved.get("name"),
                "library_id": saved.get("id"),
                "executionMode": saved.get("executionMode"),
                "status": saved.get("status"),
            })
            strategies.append(saved)
            logger.info("TV library imported %s as %s (paper/inactive)", tv_id, saved.get("id"))

        return {
            "ok": True,
            "source": catalog.get("source"),
            "session_present": catalog.get("session_present"),
            "driver": catalog.get("driver"),
            "reason": catalog.get("reason") or "",
            "execution_mode": PAPER_MODE,
            "live_trading": False,
            "requested": wanted,
            "missing": missing,
            "imported": imported,
            "skipped": skipped,
            "strategies": strategies,
            "imported_count": len(imported),
            "skipped_count": len(skipped),
        }

    def _build_library_row(
        self,
        script: Dict[str, Any],
        *,
        default_symbol: str,
        default_interval: int,
        cookie_header: str,
    ) -> Dict[str, Any]:
        tv_id = str(script.get("tv_script_id") or "")
        name = str(script.get("name") or tv_id or "Imported TV Strategy")
        pair = str(script.get("symbol") or "").strip() or default_symbol
        interval = coerce_interval(script.get("interval"), default_interval)
        provisioner = self.provisioner or get_alert_provisioner()
        webhook_url = provisioner.webhook_url() if hasattr(provisioner, "webhook_url") else ""
        pine = str(script.get("pine_source") or "")
        if not pine and cookie_header:
            pine = fetch_pine_source(
                tv_id,
                version=str(script.get("version") or "last"),
                cookie_header=cookie_header,
                http=self.http,
            )
        if not pine:
            pine = placeholder_pine(name, tv_id, webhook_url)
        sid = library_id_for(tv_id)
        now = _iso(time.time())
        params: Dict[str, Any] = {
            "source": "tradingview",
            "tv_script_id": tv_id,
            "tv_script_url": script.get("url") or "",
            "tv_script_type": script.get("type") or "strategy",
            "tv_origin": script.get("origin") or "",
            "tv_version": script.get("version") or "",
            "webhook_url": webhook_url,
            "imported": True,
            "pine_compiled_locally": False,
        }
        return {
            "id": sid,
            "name": name,
            "description": (
                f"Imported from TradingView ({script.get('origin') or 'library'}). "
                f"tv_script_id={tv_id}. Paper/sim only until you start a lifecycle run."
            ),
            "code": pine,
            "status": INACTIVE,
            "assetPair": pair,
            "interval": interval,
            "executionMode": PAPER_MODE,
            "parameters": params,
            "hardStopEnabled": True,
            "hardStopPercent": 5.0,
            "createdAt": now,
            "version": 1,
            "tv_script_id": tv_id,
            "seededFromId": tv_id,
            "seededFromName": "tradingview",
        }

    def _bind_alert(self, strategy: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        provisioner = self.provisioner or get_alert_provisioner()
        try:
            rec = provisioner.upsert(
                strategy["id"],
                strategy.get("assetPair") or "BTC/USD",
                strategy.get("interval") or 15,
                enable=False,
            )
            return rec if isinstance(rec, dict) else rec.to_dict()
        except Exception as exc:
            logger.warning("alert bind skipped for %s: %s", strategy.get("id"), exc)
            return None


def _index_by_tv_script_id(store: Any) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    rows = store.list_strategies() if store is not None else []
    for row in rows:
        tv_id = str(row.get("tv_script_id") or (row.get("parameters") or {}).get("tv_script_id") or "")
        if tv_id:
            out[tv_id] = row
    return out


def _match_existing(store: Any, tv_script_id: str) -> Optional[Dict[str, Any]]:
    if not tv_script_id or store is None:
        return None
    return _index_by_tv_script_id(store).get(tv_script_id)


def get_tv_library_service() -> TvLibraryService:
    global _service
    if _service is None:
        _service = TvLibraryService(
            driver_factory=_driver_factory,
            http=_http_transport,
        )
    return _service
