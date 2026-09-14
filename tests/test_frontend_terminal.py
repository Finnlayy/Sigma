"""
=========================================================
Datei:      tests/test_frontend_terminal.py
Zweck:      Blueprint §3.2 / §8 — der Sigma-Terminal-Frontend-
            Layer muss exakt die hart kodierte Panel-Registry
            und die 4 Layout-Presets implementieren.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
=========================================================
"""
from __future__ import annotations

import os

import pytest

from app.core import blueprint as bp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PANELS_TSX = os.path.join(ROOT, "src", "components", "sigma", "panels.tsx")
TERMINAL_TSX = os.path.join(ROOT, "src", "components", "SigmaTerminal.tsx")
API_TS = os.path.join(ROOT, "src", "lib", "sigmaApi.ts")
APP_TSX = os.path.join(ROOT, "src", "App.tsx")
TV_LOGIN = os.path.join(ROOT, "bin", "sigma-tv-login")
SIGMA_UP = os.path.join(ROOT, "bin", "sigma-up")
SIGMA_DOWN = os.path.join(ROOT, "bin", "sigma-down")


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def test_frontend_files_exist():
    for path in (PANELS_TSX, TERMINAL_TSX, API_TS, APP_TSX):
        assert os.path.exists(path), f"missing frontend file: {path}"


@pytest.mark.parametrize("panel", bp.TERMINAL_PANELS)
def test_every_blueprint_panel_is_implemented(panel: str):
    src = _read(PANELS_TSX)
    assert f"export function {panel}(" in src, f"{panel} is not implemented"
    assert f"  {panel},\n" in src, f"{panel} is not in PANEL_REGISTRY"


def test_panel_registry_covers_all_eleven():
    src = _read(PANELS_TSX)
    registry = src.split("PANEL_REGISTRY", 1)[1].split("};", 1)[0]
    for panel in bp.TERMINAL_PANELS:
        assert panel in registry
    assert len(bp.TERMINAL_PANELS) == 11


@pytest.mark.parametrize("preset", bp.TERMINAL_PRESETS)
def test_every_preset_has_a_layout(preset: str):
    src = _read(TERMINAL_TSX)
    assert f"'{preset}'" in src or f"{preset}:" in src, f"preset {preset} missing"


def test_presets_cover_blueprint_core_plus_capital_ops():
    """§8 Presets + §30 CAPITAL_OPS / OVERVIEW / LIBRARY / QUANT / CONFIG."""
    src = _read(TERMINAL_TSX)
    listed = src.split("export const PRESETS = [", 1)[1].split("]", 1)[0]
    names = [p.strip().strip("'\"") for p in listed.split(",") if p.strip()]
    assert names[:len(bp.TERMINAL_PRESETS)] == list(bp.TERMINAL_PRESETS)
    assert "CAPITAL_OPS" in names
    for extra in bp.TERMINAL_PRESETS_EXTENDED:
        assert extra in names
        assert extra in bp.ALL_TERMINAL_PRESETS
    for preset in names:
        assert preset in bp.ALL_TERMINAL_PRESETS
        assert f"  {preset}: {{" in src


@pytest.mark.parametrize("panel", [
    "OrderbookConfluencePanel", "SchedulerTelemetryPanel", "OrderReceiptsPanel",
    "RateLimiterPanel", "ContagionRadarPanel", "FlywheelBudgetPanel",
])
def test_extended_panels_are_registered(panel):
    """§30 — die Execution-Plane-Panels haengen in der Dock-Registry."""
    src = _read(PANELS_TSX)
    assert f"export function {panel}()" in src
    registry = src.split("PANEL_REGISTRY", 1)[1].split("};", 1)[0]
    assert f"  {panel},\n" in registry
    assert panel in bp.ALL_TERMINAL_PANELS


def test_terminal_is_wired_into_app_navigation():
    src = _read(APP_TSX)
    assert "SigmaTerminal" in src
    assert "import SigmaTerminal" in src
    assert "activePage" not in src
    assert "nav-tab-" not in src


def test_api_client_targets_blueprint_routes():
    src = _read(API_TS)
    # a representative slice of §7 must be reachable from the UI
    for route in ("/api/v1/health", "/api/v1/bots", "/api/v1/safety",
                  "/api/v1/deadman", "/api/v1/memory", "/api/v1/reward/matrix",
                  "/api/v1/ml/self-optimizing", "/api/v1/academy/badges",
                  "/api/v1/telegram", "/api/tv/jobs",
                  "/api/v1/strategies/library-snapshot"):
        assert route in src, f"UI client does not call {route}"


def test_charts_use_lightweight_charts_not_a_local_engine():
    src = _read(os.path.join(ROOT, "src", "components", "TvLightweightChart.tsx"))
    assert "lightweight-charts" in src


def test_tv_login_bootstrap_exists_and_is_executable():
    assert os.path.exists(TV_LOGIN)
    assert os.access(TV_LOGIN, os.X_OK), "bin/sigma-tv-login must be executable"
    src = _read(TV_LOGIN)
    assert "storage_state" in src
    assert bp.PATH_TV_STORAGE_STATE in src or "PATH_TV_STORAGE_STATE" in src


def test_sigma_up_requires_tv_session_before_stack():
    assert os.path.exists(SIGMA_UP)
    assert os.access(SIGMA_UP, os.X_OK), "bin/sigma-up must be executable"
    assert os.path.exists(SIGMA_DOWN)
    assert os.access(SIGMA_DOWN, os.X_OK), "bin/sigma-down must be executable"
    src = _read(SIGMA_UP)
    assert "ensure_tv_session" in src
    assert src.find("ensure_tv_session") < src.find("start_bg scraper")
    assert src.find("ensure_tv_session") < src.find("start_bg core")
    assert "refusing to start the stack" in src
    assert "sigma-tv-login" in src


def test_unified_shell_panels_and_llm_wiring():
    panels = _read(PANELS_TSX)
    library = _read(os.path.join(ROOT, "src", "components", "sigma", "StrategyLibraryPanel.tsx"))
    dock = _read(os.path.join(ROOT, "src", "components", "sigma", "dock.tsx"))
    api = _read(API_TS)
    registry = panels.split("PANEL_REGISTRY", 1)[1].split("};", 1)[0]
    for panel in bp.TERMINAL_PANELS_EXTENDED:
        assert f"export function {panel}(" in panels, f"{panel} missing export"
        assert f"  {panel},\n" in registry, f"{panel} not in PANEL_REGISTRY"
    for tab in bp.STRATEGY_DETAIL_TABS:
        assert tab in library
    assert "STRATEGY_DETAIL_TABS" in library
    assert "Initialisieren" in library
    assert "Validieren" in library
    assert "animate-pulse" not in library
    assert "new WebSocket" in panels
    assert "llmStreamUrl" in panels and "llmToolCall" in panels
    assert "fromTemplate" in api
    assert "/api/strategies/from-template" in api
    assert "syncTvLibrary" in api
    assert "/api/strategies/tv/sync-library" in api
    assert "/api/strategies/tv/scripts" in api
    assert "Load from TV" in library
    assert "Login TV" in library
    assert library.find("Push to TV") < library.find("Login TV")
    assert "tvLogin" in api
    assert "/api/tv/session/login" in api
    assert "ResizablePanelGroup" in dock
    assert "flexlayout-react" not in dock


def test_package_json_pins_terminal_dependencies():
    import json

    with open(os.path.join(ROOT, "package.json"), "r", encoding="utf-8") as fh:
        pkg = json.load(fh)
    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
    assert "react-resizable-panels" in deps
    assert "lightweight-charts" in deps
    assert "flexlayout-react" not in deps
    assert os.path.exists(os.path.join(ROOT, "components.json"))
    assert os.path.exists(os.path.join(ROOT, "src", "components", "ui", "resizable.tsx"))


def test_use_poll_keeps_fetcher_in_ref():
    """SigmaTerminal health ticks must not restart panel poll timers.

    usePoll used to put `fn` in the interval effect deps. Inline
    `() => api.x()` identities then tore down setInterval and fired a
    duplicate GET on every parent render (header poll = 5s).
    """
    src = _read(PANELS_TSX)
    assert "fnRef.current = fn" in src
    assert "useRef(fn)" in src
    assert "refetchKey" in src
    interval_effect = src.split("const id = setInterval(refresh, ms);", 1)[1][:180]
    assert "[refresh, ms]" in interval_effect
    assert "fn]" not in interval_effect
    assert "memo(function SigmaDock" in _read(
        os.path.join(ROOT, "src", "components", "sigma", "dock.tsx"))


def test_settings_save_feedback_tones():
    src = _read(os.path.join(ROOT, "src", "components", "SettingsPage.tsx"))
    assert 'tone: "ok"' in src
    assert 'tone: "err"' in src
    assert 'tone: "bad"' in src
    assert "FLASH_MS" in src
    assert "Erlaubt:" in src
    assert "Format:" in src
