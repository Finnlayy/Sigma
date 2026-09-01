"""
=========================================================
Datei:      app/tv/tv_driver.py
Zweck:      §17.5 — produktiver TradingView-Alert-Treiber
            (Playwright, echte Session aus bin/sigma-tv-login).
            upsert / enable / disable / delete / list — DOM-getrieben
            über den SelectorManager, thread-affin gekapselt, damit
            der Sync-Playwright-Client nie auf dem asyncio-Loop läuft.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Carrera-Engine) / TV-Automation
=========================================================

Vertrag (vom AlertProvisioner konsumiert):
    upsert_alert(name, symbol, interval, webhook_url, message) -> {"tv_alert_id": str}
    enable_alert(alert_ref)   -> {"ok": bool}
    disable_alert(alert_ref)  -> {"ok": bool}
    delete_alert(alert_ref)   -> {"ok": bool}
    list_alerts()             -> [{"tv_alert_id", "name", "enabled"}]
    close()                   -> None

Es gibt bewusst KEINEN Fake-Transport: ohne gültige TV-Session wirft die
Factory ``TvDriverUnavailable`` und der Provisioner bleibt fail-closed
(lokaler Zustand bleibt konsistent, `last_reason` dokumentiert den Grund).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

from app.core import blueprint as bp
from app.core.config import SigmaConfig, load_config
from app.tv.interval_map import to_tv_interval
from app.tv.selector_manager import SelectorManager, get_selector_manager
from app.tv.strategy_tester_driver import DriverError
from app.tv.symbol_map import to_tradingview

logger = logging.getLogger("app.tv.tv_driver")

ALERT_LIST_URL_PATH = "/chart/"
SESSION_COOKIES = ("sessionid", "sessionid_sign")


class TvDriverUnavailable(DriverError):
    """Keine nutzbare TradingView-Session / kein Playwright."""

    def __init__(self, message: str, code: str = "TV_DRIVER_UNAVAILABLE"):
        super().__init__(message, code)


# ---------------------------------------------------------------- session ---

def session_state_path(config: Optional[SigmaConfig] = None) -> str:
    cfg = config or load_config()
    return os.path.abspath(os.path.expanduser(cfg.tv_storage_state_path))


def session_status(config: Optional[SigmaConfig] = None) -> Dict[str, Any]:
    """Bewertet ``tv_storage_state.json`` ohne Browser-Start."""
    path = session_state_path(config)
    out: Dict[str, Any] = {
        "path": path, "present": False, "valid": False, "cookies": 0,
        "auth_cookies": [], "age_h": None, "mode": None, "error": None,
        "driver": "unavailable",
    }
    if not os.path.exists(path):
        out["error"] = "no_session_file"
        return out
    out["present"] = True
    try:
        with open(path, "r", encoding="utf-8") as fh:
            state = json.load(fh)
    except Exception as exc:
        out["error"] = f"corrupt_session:{exc}"
        return out
    cookies = state.get("cookies") or []
    names = {c.get("name") for c in cookies if isinstance(c, dict)}
    have = [n for n in SESSION_COOKIES if n in names]
    out["cookies"] = len(cookies)
    out["auth_cookies"] = have
    out["age_h"] = round((time.time() - os.path.getmtime(path)) / 3600.0, 2)
    out["mode"] = oct(os.stat(path).st_mode & 0o777)
    if not have:
        out["error"] = "no_auth_cookie"
        return out
    out["valid"] = True
    out["driver"] = "playwright"
    return out


# ------------------------------------------------------------ the driver ---

class PlaywrightTvAlertDriver:
    """Ein Playwright-Kontext, eigener Worker-Thread (Sync-API ist thread-affin)."""

    def __init__(self, config: Optional[SigmaConfig] = None,
                 selectors: Optional[SelectorManager] = None,
                 headless: bool = True):
        self.config = config or load_config()
        self.selectors = selectors or get_selector_manager()
        self.headless = headless
        self._lock = threading.RLock()
        self._pw = None
        self._browser = None
        self._context = None
        self.page = None

    # ---------------------------------------------------------- lifecycle
    def start(self) -> None:
        with self._lock:
            if self.page is not None:
                return
            status = session_status(self.config)
            if not status["valid"]:
                raise TvDriverUnavailable(
                    f"TradingView session unusable ({status['error']}) — "
                    f"run bin/sigma-tv-login")
            try:
                from playwright.sync_api import sync_playwright  # type: ignore
            except ImportError as exc:
                raise TvDriverUnavailable(
                    "playwright not installed — `pip install playwright && "
                    "playwright install chromium`", "PLAYWRIGHT_MISSING") from exc
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=self.headless)
            self._context = self._browser.new_context(
                storage_state=status["path"],
                viewport={"width": 1920, "height": 1080})
            self._context.set_default_navigation_timeout(
                self.config.tv_navigation_timeout_ms)
            self.page = self._context.new_page()
            logger.info("TV alert driver started (session %s)", status["path"])

    def close(self) -> None:
        with self._lock:
            for closer in (self._context, self._browser):
                try:
                    if closer is not None:
                        closer.close()
                except Exception:  # pragma: no cover
                    pass
            try:
                if self._pw is not None:
                    self._pw.stop()
            except Exception:  # pragma: no cover
                pass
            self._pw = self._browser = self._context = self.page = None

    def restart(self) -> None:
        self.close()
        self.start()

    def __enter__(self) -> "PlaywrightTvAlertDriver":
        self.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ------------------------------------------------------------ helpers
    def _click(self, category: str, name: str, **fmt: Any):
        self.selectors.maybe_hot_reload()
        for attempt in range(bp.SELECTOR_RETRY_AFTER_REMOTE_REFRESH + 1):
            for selector in self.selectors.get(category, name, **fmt):
                try:
                    locator = self.page.locator(selector).first
                    locator.wait_for(state="visible", timeout=4000)
                    locator.click()
                    return locator
                except Exception:
                    continue
            if attempt < bp.SELECTOR_RETRY_AFTER_REMOTE_REFRESH:
                self.selectors.download_remote_selectors()
        raise DriverError(f"{bp.SELECTOR_ERROR_CODE}: {category}.{name}",
                          bp.SELECTOR_ERROR_CODE)

    def _fill(self, category: str, name: str, value: str, **fmt: Any) -> None:
        for selector in self.selectors.get(category, name, **fmt):
            try:
                node = self.page.locator(selector).first
                node.wait_for(state="visible", timeout=4000)
                node.fill(value)
                return
            except Exception:
                continue
        raise DriverError(f"{bp.SELECTOR_ERROR_CODE}: {category}.{name}",
                          bp.SELECTOR_ERROR_CODE)

    def open_chart(self, symbol: str, interval: Any) -> None:
        self.start()
        url = (f"{self.config.tv_base_url}/chart/?symbol={to_tradingview(symbol)}"
               f"&interval={to_tv_interval(interval)}")
        self.page.goto(url, wait_until="domcontentloaded")
        self.page.wait_for_timeout(2000)

    def _alert_rows(self) -> List[Any]:
        rows: List[Any] = []
        for selector in self.selectors.get("alerts", "alert_row"):
            try:
                found = self.page.locator(selector).all()
            except Exception:
                continue
            if found:
                rows.extend(found)
                break
        return rows

    def _find_row(self, ref: str):
        for row in self._alert_rows():
            try:
                text = (row.inner_text() or "").strip()
            except Exception:
                continue
            if ref and ref in text:
                return row
        return None

    @staticmethod
    def _row_enabled(row) -> Optional[bool]:
        try:
            box = row.locator('input[type="checkbox"]').first
            if box.count() == 0:
                return None
            return bool(box.is_checked())
        except Exception:
            return None

    # ------------------------------------------------------------- actions
    def list_alerts(self) -> List[Dict[str, Any]]:
        self.start()
        out: List[Dict[str, Any]] = []
        for row in self._alert_rows():
            try:
                name = (row.inner_text() or "").strip().splitlines()[0]
            except Exception:
                continue
            if not name:
                continue
            out.append({"tv_alert_id": name, "name": name,
                        "enabled": self._row_enabled(row)})
        return out

    def upsert_alert(self, *, name: str, symbol: str, interval: Any,
                     webhook_url: str, message: str) -> Dict[str, Any]:
        """Idempotent über den Alert-Namen: existiert er, wird er aktualisiert."""
        self.open_chart(symbol, interval)
        existing = self._find_row(name)
        if existing is not None:
            try:
                existing.dblclick()
            except Exception:
                self._click("alerts", "create_button")
        else:
            self._click("alerts", "create_button")
        self.page.wait_for_timeout(1200)
        self._fill("alerts", "message_field", message)
        try:
            self._fill("alerts", "webhook_url_field", webhook_url)
        except DriverError:
            logger.warning("webhook field not found — alert %s saved without URL", name)
        self._click("alerts", "submit_button")
        self.page.wait_for_timeout(1500)
        return {"tv_alert_id": name, "name": name, "updated": existing is not None}

    def _toggle(self, alert_ref: str, desired: bool) -> Dict[str, Any]:
        self.start()
        row = self._find_row(alert_ref)
        if row is None:
            raise DriverError(f"alert {alert_ref!r} not found on TradingView",
                              "TV_ALERT_NOT_FOUND")
        current = self._row_enabled(row)
        if current is None:
            raise DriverError(f"alert {alert_ref!r} has no toggle",
                              "TV_ALERT_TOGGLE_MISSING")
        if current != desired:
            row.locator('input[type="checkbox"]').first.click()
            self.page.wait_for_timeout(600)
        return {"ok": True, "tv_alert_id": alert_ref, "enabled": desired}

    def enable_alert(self, alert_ref: str) -> Dict[str, Any]:
        return self._toggle(alert_ref, True)

    def disable_alert(self, alert_ref: str) -> Dict[str, Any]:
        return self._toggle(alert_ref, False)

    def delete_alert(self, alert_ref: str) -> Dict[str, Any]:
        self.start()
        row = self._find_row(alert_ref)
        if row is None:
            return {"ok": True, "tv_alert_id": alert_ref, "deleted": False,
                    "reason": "not_present"}
        try:
            row.click(button="right")
            self.page.wait_for_timeout(400)
            self.page.get_by_text("Delete", exact=False).first.click()
            self.page.wait_for_timeout(600)
        except Exception as exc:
            raise DriverError(f"delete_alert failed for {alert_ref}: {exc}",
                              "TV_ALERT_DELETE_FAILED") from exc
        return {"ok": True, "tv_alert_id": alert_ref, "deleted": True}


# ------------------------------------------------- thread-affine facade ---

class ThreadedTvAlertDriver:
    """Führt alle Driver-Aufrufe auf EINEM dedizierten Thread aus.

    Playwrights Sync-API darf weder auf einem laufenden asyncio-Loop noch
    aus wechselnden Threads bedient werden. FastAPI-Routen und der M8-
    Lifecycle rufen den Provisioner aus beliebigen Kontexten — deshalb
    diese Serialisierung.
    """

    _METHODS = ("upsert_alert", "enable_alert", "disable_alert",
                "delete_alert", "list_alerts", "open_chart", "restart")

    def __init__(self, inner: PlaywrightTvAlertDriver):
        self._inner = inner
        self._lock = threading.RLock()
        from concurrent.futures import ThreadPoolExecutor

        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tv-alerts")

    def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        with self._lock:
            future = self._pool.submit(getattr(self._inner, method), *args, **kwargs)
            return future.result()

    def __getattr__(self, item: str):
        if item in self._METHODS:
            def _proxy(*args: Any, **kwargs: Any) -> Any:
                return self._call(item, *args, **kwargs)
            return _proxy
        raise AttributeError(item)

    def close(self) -> None:
        try:
            self._call("close")
        finally:
            self._pool.shutdown(wait=False)

    @property
    def inner(self) -> PlaywrightTvAlertDriver:
        return self._inner


# ----------------------------------------------------------------- factory --

_driver: Optional[ThreadedTvAlertDriver] = None
_driver_lock = threading.RLock()


def get_tv_alert_driver(config: Optional[SigmaConfig] = None, *,
                        required: bool = True
                        ) -> Optional[ThreadedTvAlertDriver]:
    """Liefert den Prozess-Singleton-Treiber.

    ``required=False`` gibt ``None`` zurück, wenn keine Session vorliegt
    (Provisioner arbeitet dann rein lokal + fail-closed). ``required=True``
    wirft ``TvDriverUnavailable`` — es gibt keinen Fake-Transport.
    """
    global _driver
    cfg = config or load_config()
    status = session_status(cfg)
    if not status["valid"]:
        if required:
            raise TvDriverUnavailable(
                f"no usable TradingView session at {status['path']} "
                f"({status['error']}) — run bin/sigma-tv-login")
        return None
    with _driver_lock:
        if _driver is None:
            _driver = ThreadedTvAlertDriver(PlaywrightTvAlertDriver(cfg))
        return _driver


def set_tv_alert_driver(driver: Optional[Any]) -> None:
    """Test-/DI-Seam: injizierten Treiber setzen oder Singleton verwerfen."""
    global _driver
    with _driver_lock:
        if _driver is not None and driver is not _driver:
            try:
                _driver.close()
            except Exception:  # pragma: no cover
                pass
        _driver = driver


def driver_snapshot(config: Optional[SigmaConfig] = None) -> Dict[str, Any]:
    """Statusbericht für /api/v1/tv/* und bin/sigma-tv-login --check."""
    status = session_status(config)
    playwright_available = True
    try:  # pragma: no cover - Importprobe
        import playwright  # noqa: F401
    except Exception:
        playwright_available = False
    status["playwright_installed"] = playwright_available
    status["driver_started"] = _driver is not None
    if not playwright_available:
        status["driver"] = "unavailable"
        status["error"] = status["error"] or "playwright_not_installed"
    return status
