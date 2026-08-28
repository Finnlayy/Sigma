"""
=========================================================
Datei:      app/tv/strategy_tester_driver.py
Zweck:      §5 Loop B — TradingView Strategy Tester Driver.
            `export_parameters`, `apply_parameters`, `run_backtest`,
            `push_pine_code` — Playwright echt, FakeDriver für Tests/P2.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Carrera-Engine) / TV-Automation
=========================================================

Vertrag (beide Driver identisch):
    open_chart(symbol, interval)            -> None
    push_pine_code(code)                    -> {"compiled": bool, "errors": [...]}
    export_parameters()                     -> parameter CSV (str)
    apply_parameters(params)                -> applied dict
    run_backtest(window)                    -> {"trades_csv": str, "performance_csv": str}
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol, TypeVar

from app.backtest.tv_csv import params_to_csv, synthesize_result_csv
from app.core import blueprint as bp
from app.core.config import SigmaConfig, load_config
from app.tv.interval_map import to_tv_interval
from app.tv.selector_manager import SelectorManager, SelectorNotFound, get_selector_manager
from app.tv.symbol_map import to_tradingview

logger = logging.getLogger("app.tv.driver")


class DriverError(RuntimeError):
    def __init__(self, message: str, code: str = "TV_DRIVER_ERROR"):
        super().__init__(message)
        self.code = code


class StrategyTesterDriver(Protocol):  # pragma: no cover - reine Typ-Deklaration
    def open_chart(self, symbol: str, interval: Any) -> None: ...
    def push_pine_code(self, code: str) -> Dict[str, Any]: ...
    def export_parameters(self) -> str: ...
    def apply_parameters(self, params: Mapping[str, Any]) -> Dict[str, Any]: ...
    def run_backtest(self, window: Optional[Mapping[str, Any]] = None) -> Dict[str, str]: ...
    def close(self) -> None: ...


# =============================================================================
# FakeDriver (P2) — deterministische CSVs ohne Browser
# =============================================================================

DEFAULT_FAKE_PARAMS: Dict[str, Any] = {
    "trendFastEma": 12,
    "trendSlowEma": 60,
    "atrPeriod": bp.ATR_PERIOD,
    "atrStopMultiplier": bp.ATR_STOP_MULTIPLIER,
    "takeProfitMultiplier": bp.ATR_TAKE_PROFIT_MULTIPLIER,
    "rsiPeriod": 14,
}


@dataclass
class FakeStrategyTesterDriver:
    """Erfüllt den Driver-Vertrag ohne TradingView. Kein Prod-Backtest-Pfad."""

    params: Dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_FAKE_PARAMS))
    symbol: str = "BTC/USD"
    interval: Any = 15
    code: str = ""
    calls: List[str] = field(default_factory=list)

    def open_chart(self, symbol: str, interval: Any) -> None:
        self.symbol, self.interval = symbol, interval
        self.calls.append(f"open_chart:{to_tradingview(symbol)}:{to_tv_interval(interval)}")

    def push_pine_code(self, code: str) -> Dict[str, Any]:
        self.code = code
        self.calls.append("push_pine_code")
        compiled = "strategy(" in code or "indicator(" in code or not code
        return {"compiled": compiled, "errors": [] if compiled else ["missing strategy() declaration"]}

    def export_parameters(self) -> str:
        self.calls.append("export_parameters")
        return params_to_csv(self.params)

    def apply_parameters(self, params: Mapping[str, Any]) -> Dict[str, Any]:
        self.calls.append("apply_parameters")
        self.params.update(dict(params))
        return dict(self.params)

    def run_backtest(self, window: Optional[Mapping[str, Any]] = None) -> Dict[str, str]:
        self.calls.append("run_backtest")
        seed = f"{self.symbol}|{self.interval}|{(window or {}).get('from', '')}"
        trades_csv = synthesize_result_csv(self.params, seed=seed)
        perf_csv = "Metric,Value\nNet Profit,0\nProfit Factor,1.0\n"
        return {"trades_csv": trades_csv, "performance_csv": perf_csv, "source": "fake"}

    def list_my_scripts(self) -> List[Dict[str, str]]:
        return [
            {
                "tv_script_id": "PUB;fake1",
                "name": "CISD Momentum v6",
                "type": "strategy",
                "symbol": "BTC/USD",
                "interval": 15,
                "origin": "saved",
            },
            {
                "tv_script_id": "USER;fake2",
                "name": "RSI Reversion TV",
                "type": "strategy",
                "symbol": "ETH/USD",
                "interval": 15,
                "origin": "published",
            },
        ]

    def close(self) -> None:
        self.calls.append("close")


# =============================================================================
# Playwright-Driver (P3) — echte TV-Session
# =============================================================================

class PlaywrightStrategyTesterDriver:
    """Headless Chromium gegen TradingView mit gespeichertem Login-State."""

    def __init__(self, config: Optional[SigmaConfig] = None,
                 selectors: Optional[SelectorManager] = None, headless: bool = True):
        self.config = config or load_config()
        self.selectors = selectors or get_selector_manager()
        self.headless = headless
        self._pw = None
        self._browser = None
        self._context = None
        self.page = None
        self.symbol = ""
        self.interval: Any = 15

    # ----------------------------------------------------------- lifecycle
    def start(self) -> None:
        if self.page is not None:
            return
        try:
            from playwright.sync_api import sync_playwright  # type: ignore
        except ImportError as exc:
            raise DriverError("playwright not installed — run `playwright install chromium`",
                              "PLAYWRIGHT_MISSING") from exc
        state_path = self.config.tv_storage_state_path
        if not os.path.exists(state_path):
            raise DriverError(f"TV session missing ({state_path}) — run bin/sigma-tv-login",
                              "TV_SESSION_MISSING")
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self.headless)
        self._context = self._browser.new_context(
            storage_state=state_path, accept_downloads=True,
            viewport={"width": 1920, "height": 1080})
        self._context.set_default_navigation_timeout(self.config.tv_navigation_timeout_ms)
        self.page = self._context.new_page()

    def close(self) -> None:
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

    def __enter__(self) -> "PlaywrightStrategyTesterDriver":
        self.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ------------------------------------------------- selector interaction
    def click_element_with_fallback(self, category: str, name: str, **fmt: Any):
        """§16.2 — Kette durchgehen, bei Total-Miss einmal Remote-Refresh."""
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
        raise DriverError(f"{bp.SELECTOR_ERROR_CODE}: {category}.{name}", bp.SELECTOR_ERROR_CODE)

    # ------------------------------------------------------------- actions
    def open_chart(self, symbol: str, interval: Any) -> None:
        self.start()
        self.symbol, self.interval = symbol, interval
        ticker = to_tradingview(symbol)
        url = f"{self.config.tv_base_url}/chart/?symbol={ticker}&interval={to_tv_interval(interval)}"
        self.page.goto(url, wait_until="domcontentloaded")
        self.page.wait_for_timeout(2500)

    def push_pine_code(self, code: str) -> Dict[str, Any]:
        self.start()
        self.click_element_with_fallback("pine_editor", "tab")
        area = self.page.locator(self.selectors.get("pine_editor", "code_area")[0]).first
        area.click()
        self.page.keyboard.press("Control+A")
        self.page.keyboard.type(code, delay=0)
        self.click_element_with_fallback("pine_editor", "save_button")
        self.page.wait_for_timeout(1500)
        errors = self._collect_compile_errors()
        if not errors:
            self.click_element_with_fallback("pine_editor", "add_to_chart_button")
            self.page.wait_for_timeout(2000)
        return {"compiled": not errors, "errors": errors}

    def _collect_compile_errors(self) -> List[str]:
        out: List[str] = []
        for selector in self.selectors.get("pine_editor", "compile_error"):
            try:
                for node in self.page.locator(selector).all():
                    text = (node.inner_text() or "").strip()
                    if text:
                        out.append(text)
            except Exception:
                continue
        return out

    def export_parameters(self) -> str:
        """Strategy-Properties -> Parameter-CSV (Genraum für die GA)."""
        self.start()
        self.click_element_with_fallback("properties", "settings_button")
        self.click_element_with_fallback("properties", "inputs_tab")
        params: Dict[str, Any] = {}
        try:
            for row in self.page.locator('[data-name="inputs"] [data-name]').all():
                name = row.get_attribute("data-name") or ""
                value_node = row.locator("input").first
                if not name or value_node.count() == 0:
                    continue
                params[name] = value_node.input_value()
        except Exception as exc:
            logger.warning("parameter scrape degraded: %s", exc)
        self.click_element_with_fallback("properties", "ok_button")
        if not params:
            raise DriverError("no Pine inputs found", "NO_PINE_INPUTS")
        return params_to_csv(params)

    def apply_parameters(self, params: Mapping[str, Any]) -> Dict[str, Any]:
        self.start()
        self.click_element_with_fallback("properties", "settings_button")
        self.click_element_with_fallback("properties", "inputs_tab")
        applied: Dict[str, Any] = {}
        for key, value in params.items():
            for selector in self.selectors.get("properties", "input_field", param=key):
                try:
                    field_node = self.page.locator(selector).first
                    field_node.fill(str(value))
                    applied[key] = value
                    break
                except Exception:
                    continue
        self.click_element_with_fallback("properties", "ok_button")
        self.page.wait_for_timeout(1200)
        return applied

    def run_backtest(self, window: Optional[Mapping[str, Any]] = None) -> Dict[str, str]:
        self.start()
        self.click_element_with_fallback("strategy_tester", "tab")
        deadline = time.time() + self.config.tv_tester_run_timeout_ms / 1000.0
        while time.time() < deadline and self._tester_running():
            self.page.wait_for_timeout(1000)
        trades = self._export_csv("trades_tab")
        performance = self._export_csv("performance_tab")
        return {"trades_csv": trades, "performance_csv": performance, "source": "tradingview"}

    def _tester_running(self) -> bool:
        for selector in self.selectors.get("strategy_tester", "running_indicator"):
            try:
                if self.page.locator(selector).first.is_visible():
                    return True
            except Exception:
                continue
        return False

    def _export_csv(self, tab: str) -> str:
        self.click_element_with_fallback("strategy_tester", tab)
        with self.page.expect_download(timeout=self.config.tv_tester_run_timeout_ms) as info:
            self.click_element_with_fallback("strategy_tester", "export_button")
            try:
                self.click_element_with_fallback("strategy_tester", "export_confirm")
            except DriverError:
                pass
        download = info.value
        target = os.path.join(self.config.tv_export_dir, download.suggested_filename)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        download.save_as(target)
        with open(target, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()

    def list_my_scripts(self) -> List[Dict[str, str]]:
        """§8.5 — Strategien aus dem TV-Konto listen (pine-facade, dann DOM)."""
        self.start()
        out = self._list_via_pine_facade()
        if out:
            return out
        return self._list_via_dom()

    def _list_via_pine_facade(self) -> List[Dict[str, str]]:
        from app.tv.script_catalog import (
            merge_scripts,
            normalize_script_rows,
            pine_facade_list_urls,
        )

        groups: List[List[Dict[str, str]]] = []
        seen_urls = set()
        for url, origin in pine_facade_list_urls():
            if url in seen_urls:
                continue
            seen_urls.add(url)
            try:
                resp = self._context.request.get(url, timeout=15_000)
                if resp.ok:
                    rows = normalize_script_rows(resp.json(), origin=origin)
                    if rows:
                        groups.append(rows)
            except Exception as exc:
                logger.info("pine-facade list via playwright failed (%s): %s", origin, exc)
        return merge_scripts(*groups)

    def _list_via_dom(self) -> List[Dict[str, str]]:
        self.page.goto(f"{self.config.tv_base_url}/u/#published-scripts",
                       wait_until="domcontentloaded")
        out: List[Dict[str, str]] = []
        for selector in self.selectors.get("account", "script_row"):
            try:
                for node in self.page.locator(selector).all():
                    name = (node.inner_text() or "").strip().splitlines()[0]
                    link = node.locator("a").first.get_attribute("href") or ""
                    if name:
                        script_id = link or name
                        out.append({
                            "tv_script_id": script_id,
                            "name": name,
                            "type": "strategy",
                            "url": link,
                            "origin": "published",
                        })
            except Exception:
                continue
            if out:
                break
        return out


def get_driver(config: Optional[SigmaConfig] = None, *, prefer_fake: Optional[bool] = None):
    """FakeDriver ohne TV-Session, echter Driver sobald `tv_storage_state.json` existiert."""
    cfg = config or load_config()
    if prefer_fake is None:
        prefer_fake = not os.path.exists(cfg.tv_storage_state_path)
    if prefer_fake:
        logger.info("TV FakeDriver active (no session at %s)", cfg.tv_storage_state_path)
        return FakeStrategyTesterDriver()
    return PlaywrightStrategyTesterDriver(cfg)


_T = TypeVar("_T")


def run_sync_off_asyncio_loop(fn: Callable[[], _T]) -> _T:
    """Playwright Sync API cannot start on a running asyncio loop (FastAPI/uvicorn).

    Login TV already isolates Playwright on its own thread. Library listing must
    do the same when called from an async route: hop to a worker thread, then
    run the entire driver lifecycle there (start/list/close are thread-affine).
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return fn()
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="tv-pw-sync") as pool:
        return pool.submit(fn).result()


def list_my_scripts_with_playwright(config: Optional[SigmaConfig] = None) -> List[Dict[str, Any]]:
    """Open a Playwright driver, list My Scripts, close — never on the asyncio loop."""

    def _work() -> List[Dict[str, Any]]:
        drv = get_driver(config, prefer_fake=False)
        try:
            return list(drv.list_my_scripts() or [])
        finally:
            try:
                drv.close()
            except Exception:
                pass

    return run_sync_off_asyncio_loop(_work)
