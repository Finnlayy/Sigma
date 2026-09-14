"""
=========================================================
Datei:      app/tv/chrome_login.py
Zweck:      Headful Chrome auf TradingView oeffnen — manueller Login.
            Reused Sigma-Profil + storage_state.json (Worker/Scraper).
            Kein Live-Trading, kein Credential-Store.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Carrera-Engine) / TV-Automation
=========================================================
"""
from __future__ import annotations

import logging
import os
import queue
import shutil
import subprocess
import threading
import time
from typing import Any, Callable, Dict, Optional

from app.core import blueprint as bp
from app.core.config import SigmaConfig, load_config

logger = logging.getLogger("app.tv.chrome_login")

SESSION_COOKIES = ("sessionid", "sessionid_sign")
_CHROME_BINS = (
    "google-chrome-stable",
    "google-chrome",
    "google-chrome-beta",
    "chromium-browser",
    "chromium",
    "chrome",
)

_opener: Optional[Callable[..., Dict[str, Any]]] = None
_launcher: Optional["TvChromeLauncher"] = None


def set_tv_chrome_opener(opener: Optional[Callable[..., Dict[str, Any]]]) -> None:
    """Test-Seam — verhindert echten Browser-Start in pytest."""
    global _opener
    _opener = opener


def chrome_binary() -> str:
    for name in _CHROME_BINS:
        path = shutil.which(name)
        if path:
            return path
    return ""


def playwright_channel(binary: str = "") -> str:
    name = os.path.basename(binary or chrome_binary()).lower()
    if "chrome" in name and "chromium" not in name:
        return "chrome"
    if "chromium" in name:
        return "chromium"
    return ""


class TvChromeLauncher:
    """Ein sichtbares Chrome-Fenster, Profil = Sigma TV session."""

    def __init__(self, config: Optional[SigmaConfig] = None):
        self.config = config or load_config()
        self._lock = threading.Lock()
        self._q: "queue.Queue[tuple[str, str]]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._pw = None
        self._context = None
        self._page = None
        self._open = False
        self._reused = False
        self._error = ""
        self._url = bp.TV_LOGIN_URL
        self._mode = ""
        self._pid = 0

    def snapshot(self) -> Dict[str, Any]:
        binary = chrome_binary()
        return {
            "ok": not self._error,
            "open": self._open,
            "reused": self._reused,
            "url": self._url,
            "error": self._error,
            "mode": self._mode,
            "chrome_binary": binary,
            "storage_state_path": self.config.tv_storage_state_path,
            "session_present": os.path.exists(self.config.tv_storage_state_path),
            "profile_dir": self._profile_dir(),
            "live_trading": False,
            "pid": self._pid,
        }

    def _profile_dir(self) -> str:
        override = os.environ.get("SIGMA_TV_CHROME_PROFILE", "")
        if override:
            return os.path.abspath(override)
        state = os.path.abspath(self.config.tv_storage_state_path)
        return os.path.join(os.path.dirname(state), "tv_chrome_profile")

    def open(self, url: str = "") -> Dict[str, Any]:
        target = url or (
            bp.TV_CHART_URL if os.path.exists(self.config.tv_storage_state_path)
            else bp.TV_LOGIN_URL
        )
        with self._lock:
            self._error = ""
            self._url = target
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._loop, name="tv-chrome-login", daemon=True)
                self._thread.start()
            self._q.put(("open", target))
        deadline = time.time() + 12.0
        while time.time() < deadline:
            if self._open or self._error:
                break
            time.sleep(0.15)
        snap = self.snapshot()
        snap["launched"] = bool(self._open) and not snap["reused"]
        return snap

    def _loop(self) -> None:
        while True:
            try:
                cmd, url = self._q.get(timeout=2.0)
            except queue.Empty:
                self._persist_state()
                continue
            if cmd == "open":
                try:
                    self._ensure_open(url)
                except Exception as exc:
                    logger.exception("TV Chrome open failed")
                    self._error = str(exc)
                    self._open = False

    def _page_alive(self) -> bool:
        page = self._page
        if page is None:
            return False
        try:
            return not page.is_closed()
        except Exception:
            return False

    def _ensure_open(self, url: str) -> None:
        self._url = url
        if self._page_alive():
            self._reused = True
            self._page.goto(url, wait_until="domcontentloaded")
            self._open = True
            self._error = ""
            self._persist_state()
            return
        self._close_playwright()
        if self._launch_playwright(url):
            return
        self._launch_subprocess(url)

    def _launch_playwright(self, url: str) -> bool:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.info("playwright missing — falling back to system Chrome")
            return False
        binary = chrome_binary()
        channel = playwright_channel(binary)
        profile = self._profile_dir()
        os.makedirs(profile, exist_ok=True)
        state_path = self.config.tv_storage_state_path
        self._pw = sync_playwright().start()
        launch_args = ["--disable-blink-features=AutomationControlled"]
        try:
            kwargs: Dict[str, Any] = {
                "user_data_dir": profile,
                "headless": False,
                "args": launch_args,
                "viewport": {"width": 1600, "height": 950},
                "accept_downloads": True,
            }
            if channel:
                kwargs["channel"] = channel
            self._context = self._pw.chromium.launch_persistent_context(**kwargs)
            self._mode = f"playwright-persistent:{channel or 'chromium'}"
        except Exception as exc:
            logger.info("persistent Chrome failed (%s) — trying storage_state launch", exc)
            try:
                launch_kw: Dict[str, Any] = {
                    "headless": False,
                    "args": launch_args,
                }
                if channel:
                    launch_kw["channel"] = channel
                browser = self._pw.chromium.launch(**launch_kw)
                ctx_kw: Dict[str, Any] = {
                    "viewport": {"width": 1600, "height": 950},
                    "accept_downloads": True,
                }
                if os.path.exists(state_path):
                    ctx_kw["storage_state"] = state_path
                self._context = browser.new_context(**ctx_kw)
                self._mode = f"playwright-storage:{channel or 'chromium'}"
            except Exception:
                try:
                    self._pw.stop()
                except Exception:
                    pass
                self._pw = None
                return False
        page = self._context.pages[0] if self._context.pages else self._context.new_page()
        page.goto(url, wait_until="domcontentloaded")
        self._page = page
        self._open = True
        self._reused = False
        self._error = ""
        self._persist_state()
        return True

    def _launch_subprocess(self, url: str) -> None:
        binary = chrome_binary()
        if not binary:
            raise RuntimeError(
                "No Chrome/Chromium found. Install google-chrome or run: "
                ".venv/bin/playwright install chromium"
            )
        profile = self._profile_dir()
        os.makedirs(profile, exist_ok=True)
        proc = subprocess.Popen(
            [binary, f"--user-data-dir={profile}", "--new-window", url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self._pid = int(proc.pid or 0)
        self._mode = f"subprocess:{os.path.basename(binary)}"
        self._open = True
        self._reused = False
        self._error = ""
        logger.info("launched %s (pid %s) -> %s", binary, self._pid, url)

    def _persist_state(self) -> None:
        ctx = self._context
        if ctx is None:
            return
        try:
            names = {c.get("name") for c in ctx.cookies()}
            if not all(name in names for name in SESSION_COOKIES):
                return
            path = self.config.tv_storage_state_path
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            tmp = f"{path}.tmp"
            ctx.storage_state(path=tmp)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except Exception as exc:
            logger.info("storage_state persist skipped: %s", exc)

    def _close_playwright(self) -> None:
        self._page = None
        try:
            if self._context is not None:
                self._context.close()
        except Exception:
            pass
        self._context = None
        try:
            if self._pw is not None:
                self._pw.stop()
        except Exception:
            pass
        self._pw = None
        self._open = False


def get_tv_chrome_launcher(config: Optional[SigmaConfig] = None) -> TvChromeLauncher:
    global _launcher
    if _launcher is None:
        _launcher = TvChromeLauncher(config)
    return _launcher


def open_tradingview_login(
    *,
    config: Optional[SigmaConfig] = None,
    url: str = "",
) -> Dict[str, Any]:
    """Oeffnet TradingView in Chrome. Startet kein Live-Trading."""
    if _opener is not None:
        out = dict(_opener(config=config, url=url) or {})
        out.setdefault("ok", True)
        out["live_trading"] = False
        return out
    return get_tv_chrome_launcher(config).open(url)
