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
    """§8 Presets + §30 CAPITAL_OPS (Execution-Plane-Panels)."""
    src = _read(TERMINAL_TSX)
    listed = src.split("export const PRESETS = [", 1)[1].split("]", 1)[0]
    names = [p.strip().strip("'\"") for p in listed.split(",") if p.strip()]
    assert names[:len(bp.TERMINAL_PRESETS)] == list(bp.TERMINAL_PRESETS)
    assert "CAPITAL_OPS" in names
    for preset in names:
        assert preset in bp.ALL_TERMINAL_PRESETS
        assert f"  {preset}: {{" in src


@pytest.mark.parametrize("panel", [
    "OrderbookConfluencePanel", "SchedulerTelemetryPanel", "OrderReceiptsPanel",
    "RateLimiterPanel", "ContagionRadarPanel", "FlywheelBudgetPanel",
])
def test_extended_panels_are_registered(panel):
    """§30 — die Execution-Plane-Panels haengen in der FlexLayout-Registry."""
    src = _read(PANELS_TSX)
    assert f"export function {panel}()" in src
    registry = src.split("PANEL_REGISTRY", 1)[1].split("};", 1)[0]
    assert f"  {panel},\n" in registry
    assert panel in bp.ALL_TERMINAL_PANELS


def test_terminal_is_wired_into_app_navigation():
    src = _read(APP_TSX)
    assert "SigmaTerminal" in src
    assert "'terminal'" in src


def test_api_client_targets_blueprint_routes():
    src = _read(API_TS)
    # a representative slice of §7 must be reachable from the UI
    for route in ("/api/v1/health", "/api/v1/bots", "/api/v1/safety",
                  "/api/v1/deadman", "/api/v1/memory", "/api/v1/reward/matrix",
                  "/api/v1/ml/self-optimizing", "/api/v1/academy/badges",
                  "/api/v1/telegram", "/api/tv/jobs"):
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


def test_package_json_pins_terminal_dependencies():
    import json

    with open(os.path.join(ROOT, "package.json"), "r", encoding="utf-8") as fh:
        pkg = json.load(fh)
    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
    assert "flexlayout-react" in deps
    assert "lightweight-charts" in deps
