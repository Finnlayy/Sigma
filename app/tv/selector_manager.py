"""
=========================================================
Datei:      app/tv/selector_manager.py
Zweck:      §16 — Self-Healing Selector-Engine.
            Stufe 1 lokal -> Stufe 2 remote (atomic write) -> Stufe 3 builtin.
            Circuit-Breaker: max 3 Downloads / 5 min, exponential backoff.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Noir (Resilienz) / Jaune
=========================================================
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
from typing import Any, Dict, List, Optional

from app.core import blueprint as bp

logger = logging.getLogger("app.tv.selector_manager")


class SelectorNotFound(RuntimeError):
    """Alle Fallback-Ketten erschöpft (§16 `ELEMENT_NOT_FOUND`)."""

    code = bp.SELECTOR_ERROR_CODE


# Stufe 3: minimal, aber funktionsfähig — nie leer ausliefern.
BUILTIN_DEFAULT_SELECTORS: Dict[str, Any] = {
    "version": bp.BLUEPRINT_VERSION,
    "chart": {
        "symbol_search_button": ['[data-name="legend-source-title"]',
                                 "button#header-toolbar-symbol-search"],
        "symbol_search_input": ['input[data-role="search"]'],
        "interval_button": ["#header-toolbar-intervals button"],
    },
    "strategy_tester": {
        "tab": ['button[data-name="backtesting"]', 'button:has-text("Strategy Tester")'],
        "performance_tab": ['button[data-name="performance"]'],
        "trades_tab": ['button[data-name="trades"]'],
        "export_button": ['[data-name="export-data"]', 'button:has-text("Export")'],
        "export_confirm": ['button[data-name="submit-button"]'],
    },
    "properties": {
        "settings_button": ['[data-name="legend-settings-action"]'],
        "inputs_tab": ['button[data-name="inputs"]'],
        "input_field": ['div[data-name="{param}"] input'],
        "ok_button": ['button[data-name="submit-button"]'],
    },
    "pine_editor": {
        "tab": ['button[data-name="scripteditor"]'],
        "code_area": ["textarea.inputarea"],
        "save_button": ['[data-name="save"]'],
        "add_to_chart_button": ['[data-name="add-script-to-chart"]'],
    },
    "alerts": {
        "create_button": ['[data-name="alerts-create-button"]'],
        "alert_row": ['[data-name="alert-item"]'],
        "alert_toggle": ['[data-name="alert-item"] input[type="checkbox"]'],
        "message_field": ['textarea[data-name="alert-message"]'],
        "webhook_url_field": ['input[data-name="webhook-url"]'],
        "submit_button": ['button[data-name="submit"]'],
    },
    "account": {
        "my_scripts_link": ['a[href*="/scripts/"]'],
        "script_row": ['.tv-feed__item', '[data-widget-type="user-script"]'],
    },
}


class SelectorManager:
    """Playwright darf an einer fehlenden YAML nicht sterben."""

    def __init__(self, local_path: Optional[str] = None, remote_url: Optional[str] = None,
                 sha256: Optional[str] = None, fetcher=None):
        self.local_path = local_path or os.environ.get(
            bp.SELECTORS_LOCAL_PATH_ENV, bp.PATH_SELECTORS_YAML)
        self.remote_url = remote_url if remote_url is not None else os.environ.get(
            bp.SELECTORS_REMOTE_URL_ENV, "")
        self.expected_sha256 = sha256 if sha256 is not None else os.environ.get(
            bp.SELECTORS_SHA256_ENV, "")
        self._fetcher = fetcher                     # Test-Seam: callable(url) -> str
        self._data: Dict[str, Any] = {}
        self._source = ""
        self._mtime = 0.0
        self._download_times: List[float] = []
        self._backoff_until = 0.0
        self.load()

    # ------------------------------------------------------------------ load
    def load(self) -> str:
        """Stufe 1 -> 2 -> 3. Gibt die verwendete Stufe zurück."""
        data = self._load_local()
        if data:
            self._data, self._source = data, "local_yaml"
            return self._source
        logger.warning("selectors.yaml missing/invalid at %s — self-healing", self.local_path)
        data = self.download_remote_selectors()
        if data:
            self._data, self._source = data, "remote_fetch"
            return self._source
        self._data = dict(BUILTIN_DEFAULT_SELECTORS)
        self._source = "builtin_default"
        self._persist(self._data)      # optional lokal materialisieren
        return self._source

    def _load_local(self) -> Optional[Dict[str, Any]]:
        try:
            import yaml  # type: ignore

            with open(self.local_path, "r", encoding="utf-8") as fh:
                raw = fh.read()
            if self.expected_sha256:
                digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
                if digest != self.expected_sha256:
                    logger.error("selectors.yaml sha256 mismatch (%s)", digest)
                    return None
            data = yaml.safe_load(raw)
            self._mtime = os.path.getmtime(self.local_path)
            return data if self._valid(data) else None
        except FileNotFoundError:
            return None
        except Exception as exc:
            logger.error("selectors.yaml unreadable: %s", exc)
            return None

    @staticmethod
    def _valid(data: Any) -> bool:
        """Schema-Check: `version` + mindestens ein Kategorie-Dict (§16.1)."""
        return (isinstance(data, dict) and "version" in data
                and any(isinstance(v, dict) for k, v in data.items() if k != "version"))

    # ---------------------------------------------------------------- remote
    def download_remote_selectors(self) -> Optional[Dict[str, Any]]:
        if not self.remote_url:
            return None
        now = time.time()
        if now < self._backoff_until:
            logger.info("selector download in backoff for %.0fs", self._backoff_until - now)
            return None
        self._download_times = [t for t in self._download_times
                                if now - t < bp.SELECTOR_DOWNLOAD_WINDOW_SECONDS]
        if len(self._download_times) >= bp.SELECTOR_MAX_DOWNLOADS:
            self._backoff_until = now + bp.SELECTOR_DOWNLOAD_WINDOW_SECONDS
            logger.error("selector circuit breaker open (%d downloads / %ds)",
                         len(self._download_times), bp.SELECTOR_DOWNLOAD_WINDOW_SECONDS)
            return None
        self._download_times.append(now)
        try:
            raw = self._fetch(self.remote_url)
            import yaml  # type: ignore

            data = yaml.safe_load(raw)
            if not self._valid(data):
                raise ValueError("remote selectors failed schema validation")
            self._persist(data, raw=raw)
            logger.info("selectors self-healed from %s", self.remote_url)
            return data
        except Exception as exc:
            self._backoff_until = now + min(60 * (2 ** (len(self._download_times) - 1)), 900)
            logger.error("remote selector fetch failed: %s", exc)
            return None

    def _fetch(self, url: str) -> str:
        if self._fetcher is not None:
            return self._fetcher(url)
        import httpx  # type: ignore

        resp = httpx.get(url, timeout=20)
        resp.raise_for_status()
        return resp.text

    def _persist(self, data: Dict[str, Any], raw: Optional[str] = None) -> None:
        """Atomic `.tmp` -> replace (§16.1)."""
        try:
            os.makedirs(os.path.dirname(self.local_path) or ".", exist_ok=True)
            tmp = f"{self.local_path}.tmp"
            payload = raw
            if payload is None:
                try:
                    import yaml  # type: ignore

                    payload = yaml.safe_dump(data, sort_keys=False)
                except Exception:
                    return
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(tmp, self.local_path)
            self._mtime = os.path.getmtime(self.local_path)
        except OSError as exc:  # pragma: no cover
            logger.warning("selector persist failed: %s", exc)

    # ------------------------------------------------------------------- api
    def maybe_hot_reload(self) -> bool:
        """Datei geändert -> neu einlesen (Hot-Reload im Driver, §16)."""
        try:
            mtime = os.path.getmtime(self.local_path)
        except OSError:
            return False
        if mtime > self._mtime:
            self.load()
            return True
        return False

    def get(self, category: str, element: str, **fmt: Any) -> List[str]:
        """Fallback-Kette für ein Element; leer -> einmal heilen, dann raise."""
        chain = self._chain(category, element)
        if not chain:
            healed = self.download_remote_selectors()
            if healed:
                self._data, self._source = healed, "remote_fetch"
                chain = self._chain(category, element)
        if not chain:
            chain = self._chain(category, element, source=BUILTIN_DEFAULT_SELECTORS)
        if not chain:
            raise SelectorNotFound(f"{bp.SELECTOR_ERROR_CODE}: {category}.{element}")
        return [sel.format(**fmt) if fmt else sel for sel in chain]

    def _chain(self, category: str, element: str,
               source: Optional[Dict[str, Any]] = None) -> List[str]:
        node = (source or self._data).get(category)
        if not isinstance(node, dict):
            return []
        value = node.get(element)
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [str(v) for v in value]
        return []

    @property
    def source(self) -> str:
        return self._source

    def snapshot(self) -> Dict[str, Any]:
        return {
            "source": self._source,
            "local_path": self.local_path,
            "remote_url": self.remote_url or None,
            "categories": sorted(k for k, v in self._data.items() if isinstance(v, dict)),
            "downloads_in_window": len(self._download_times),
            "circuit_open": time.time() < self._backoff_until,
            "version": self._data.get("version"),
        }


_manager: Optional[SelectorManager] = None


def get_selector_manager(**kwargs) -> SelectorManager:
    global _manager
    if _manager is None:
        _manager = SelectorManager(**kwargs)
    return _manager
