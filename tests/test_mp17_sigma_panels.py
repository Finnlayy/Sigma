"""
=========================================================
Datei:      tests/test_mp17_sigma_panels.py
Zweck:      MP-17 — Sigma-Frontend-Panels (Frontend-Vertrag):
            Panel-Registry enthält zwölf IDs + drei Presets;
            Leerzustände statt Fake-Daten (fail-closed, FeedBadge
            trennt LIVE/STALE/SYNTHETIC); Schreibaktionen nur
            Operator-Token + Modal (disabled ohne Backend);
            Blinded-Modus ASSET_###; Lint-Script = tsc --noEmit.
            Backend-Routen-Tests liegen in test_api_contract.py
            (ein App-Start pro Prozess, Muster des Repos).
            Kein Netz.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Test)
=========================================================
"""
from __future__ import annotations

import os

from app.core import blueprint as bp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MP17_PANEL_IDS = [
    "QuantumRegimePanel", "MarketGeometryPanel", "PowerPhysicsPanel",
    "SymbolScoutPanel", "PolymarketPanel", "LadderArchitectPanel",
    "FractalTradePanel", "ProvisionerPanel", "OnnxBrainPanel",
    "RiskGuardPanel", "UnwindPanel", "ResearchLabPanel",
]

MP17_PRESETS = ["QUANTUM_OPS", "POSITION_DESK", "RESEARCH_LAB"]


def _read(path: str) -> str:
    with open(os.path.join(ROOT, path), "r", encoding="utf-8") as fh:
        return fh.read()


# ------------------------------------------------------------- Registry

def test_panel_registry_contains_twelve_mp17_ids():
    panels = _read("src/components/sigma/panels.tsx")
    registry = panels.split("PANEL_REGISTRY", 1)[1].split("};", 1)[0]
    titles = panels.split("PANEL_TITLES", 1)[1].split("};", 1)[0]
    for panel in MP17_PANEL_IDS:
        assert f"  {panel}_,\n" in registry, f"{panel} fehlt in Registry"
        assert f"  {panel}_: " in titles, f"{panel} fehlt in Titeln"
    assert "from './mp17Panels'" in panels


def test_mp17_panels_render_fail_closed_empty_states():
    src = _read("src/components/sigma/mp17Panels.tsx")
    # Leerzustände statt Fake-Daten; keine synthetischen Live-Werte
    assert "fail-closed" in src
    assert "keine geschlossenen Bars" in src or "kein Feed" in src
    assert "Polymarket feed unavailable — gate inaktiv" in src
    assert "kein Coin erfüllt die Hard-Filter" in src
    assert "FeedBadge" in src
    # FeedBadge zeigt Herkunft — SYNTHETIC darf nie als LIVE getarnt werden
    assert "SYNTHETIC" in _read("src/components/sigma/panels.tsx")
    assert "LIVE :8001" in _read("src/components/sigma/panels.tsx")


def test_write_buttons_require_operator_and_modal():
    src = _read("src/components/sigma/mp17Panels.tsx")
    assert "Operator-Token + Bestätigungs-Modal" in src
    assert "disabled" in src  # Buttons ohne Backend deaktiviert
    api = _read("src/lib/sigmaApi.ts")
    assert "operatorPost" in api
    for route in ("/api/v1/sigma/scan", "/api/v1/sigma/provisions",
                  "/api/v1/sigma/provisions/harden", "/api/v1/research/run"):
        assert route in api


def test_blinded_mode_asset_alias():
    api = _read("src/lib/sigmaApi.ts")
    assert "ASSET_" in api
    assert "blindedSymbol" in api
    panels = _read("src/components/sigma/mp17Panels.tsx")
    assert "blindedSymbol" in panels
    assert "Blinded-Modus" in panels


def test_three_mp17_presets_registered():
    term = _read("src/components/SigmaTerminal.tsx")
    listed = term.split("export const PRESETS = [", 1)[1].split("]", 1)[0]
    names = [p.strip().strip("'\"") for p in listed.split(",") if p.strip()]
    for preset in MP17_PRESETS:
        assert preset in names
        assert f"  {preset}: {{" in term
    # Presets nutzen die neuen Panels
    assert "QuantumRegimePanel_" in term
    assert "ResearchLabPanel_" in term
    # Blueprint-Vertrag kennt die drei Presets ebenfalls
    for preset in MP17_PRESETS:
        assert preset in bp.ALL_TERMINAL_PRESETS


def test_blueprint_core_panels_untouched():
    """Bestandsvertrag unverändert: 11 Blueprint-Panels."""
    assert len(bp.TERMINAL_PANELS) == 11


def test_mp17_panels_single_feed_source_per_panel():
    """Jedes Panel hat genau einen Polling-Feed (dumm, dünn)."""
    src = _read("src/components/sigma/mp17Panels.tsx")
    for panel in MP17_PANEL_IDS:
        block = src.split(f"export function {panel}(", 1)
        assert len(block) == 2, f"{panel} fehlt"
        body = block[1].split("\n}", 1)[0]
        assert "usePoll(" in body, f"{panel} ohne Feed-Polling"


def test_lint_script_is_strict_tsc():
    import json
    with open(os.path.join(ROOT, "package.json"), "r", encoding="utf-8") as fh:
        pkg = json.load(fh)
    assert pkg["scripts"]["lint"] == "tsc --noEmit"
    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
    for lib in ("lightweight-charts", "recharts", "lucide-react"):
        assert lib in deps
