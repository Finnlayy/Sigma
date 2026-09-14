"""
=========================================================
Datei:      tests/test_polymarket_layer0.py
Zweck:      MP-06 Polymarket Layer 0 — Karte §5: 95k/100k/105k @
            0.85/0.62/0.25 -> Bins + mu; steigend bullish / flach CHOP;
            Platt in [0,1]; T x 0.75; kein Port / missing / synthetic
            -> valid=False. Nur injizierte Payloads, kein Netz.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Blanche (Layer 0)
=========================================================
"""
from __future__ import annotations

import pytest

from sigma.ports.polymarket_port import PolymarketPort, validate_odds_payload
from sigma.signals.polymarket_density import density_from_ladder
from sigma.signals.polymarket_layer0 import (
    POLYMARKET_GATE_THRESHOLD,
    layer0_from_port,
    layer0_pre_regime,
)
from sigma.signals.polymarket_trajectory import (
    BULLISH,
    CHOP,
    STRONG_BULLISH,
    classify_bias,
    optimal_entry_window,
    trajectory_from_quotes,
)


def ladder_payload(**overrides):
    payload = {
        "event_slug": "btc-macro",
        "strikes": [95000.0, 100000.0, 105000.0],
        "yes_prices": [0.85, 0.62, 0.25],
        "volume_usd": 2_500_000.0,
        "ts": 1_704_067_200,
        "quotes": {"1h": 0.40, "2h": 0.46, "4h": 0.55, "EOD": 0.72},
    }
    payload.update(overrides)
    return payload


class FakePolymarketPort:
    """Injizierter Fake — kein Netzwerk."""

    available = True

    def __init__(self, payload):
        self.payload = payload

    def fetch_event_odds(self, event_slug):
        return validate_odds_payload(self.payload)


# ------------------------------------------------------------- density ----

def test_density_bins_and_mu_from_ladder():
    d = density_from_ladder([95000.0, 100000.0, 105000.0], [0.85, 0.62, 0.25])
    assert d.valid is True
    by_low = {b.strike_low: b.prob for b in d.bins}
    # Bin [95k, 100k) = 0.85 - 0.62; Bin [100k, 105k) = 0.62 - 0.25
    assert by_low[95000.0] == pytest.approx(0.23)
    assert by_low[100000.0] == pytest.approx(0.37)
    assert d.mu is not None
    assert 95000.0 < d.mu < 105000.0  # plausibel zwischen den Strikes
    # Korridor um den Peak-Bin (100k-105k mit 0.37): linker Nachbar
    # [95k,100k) und rechter Nachbar [105k,110k) (oberes Rand-Bin)
    assert d.corridor_low == pytest.approx(95000.0)
    assert d.corridor_high == pytest.approx(110000.0)
    # Platt-Default = Identitaet -> mu_calibrated == mu
    assert d.mu_calibrated == pytest.approx(d.mu, abs=1e-6)
    assert 0.0 <= d.mu_calibrated / 200000.0 <= 1.0


def test_density_fail_closed():
    assert density_from_ladder([], []).valid is False
    assert density_from_ladder([100.0], [0.5]).valid is False
    assert density_from_ladder([100.0, 90.0], [0.5, 0.4]).valid is False  # ungeordnet
    assert density_from_ladder([100.0, 110.0], [0.5, 1.2]).valid is False  # out of range
    # nicht-monotone kumulierte Wahrscheinlichkeit
    assert density_from_ladder([100.0, 110.0], [0.4, 0.6]).valid is False


# ---------------------------------------------------------- trajectory ----

def test_trajectory_rising_bullish_flat_chop():
    rising = trajectory_from_quotes({"1h": 0.40, "2h": 0.46, "4h": 0.55, "EOD": 0.72})
    assert rising.valid is True
    assert rising.delta_mu_per_h > 0
    assert rising.bias in (BULLISH, STRONG_BULLISH)
    flat = trajectory_from_quotes({"1h": 0.60, "2h": 0.61, "4h": 0.60, "EOD": 0.61})
    assert flat.bias == CHOP
    assert classify_bias(0.05) == STRONG_BULLISH
    assert classify_bias(0.0) == CHOP
    assert classify_bias(-0.05) == "STRONG_BEARISH"


def test_trajectory_fail_closed_on_missing_horizons():
    r = trajectory_from_quotes({"1h": 0.55, "2h": 0.58})
    assert r.valid is False
    assert "missing_horizons" in r.reason


def test_optimal_window_times_075_and_late_lock():
    expiry = 100.0
    early = optimal_entry_window(expiry, now_ts=25.0)   # 75 % Rest
    assert early.valid is True
    assert early.t_opt_ts == pytest.approx(75.0)         # Expiry x 0.75
    assert early.entry_allowed is True
    late = optimal_entry_window(expiry, now_ts=80.0)     # 20 % Rest < 25 %
    assert late.entry_allowed is False
    assert late.remaining_frac == pytest.approx(0.20)
    assert optimal_entry_window(expiry, now_ts=100.0).valid is False  # abgelaufen


# ------------------------------------------------------------ layer0 -----

def test_layer0_without_port_fails_closed():
    r = layer0_pre_regime(None)
    assert r.valid is False
    assert r.reason == "missing_data"
    r2 = layer0_from_port(None, "btc-macro")
    assert r2.valid is False
    assert r2.reason == "no_feed"


def test_layer0_port_rejects_synthetic_and_missing():
    port = FakePolymarketPort(ladder_payload(synthetic=True))
    r = layer0_from_port(port, "btc-macro")
    assert r.valid is False
    assert r.reason == "synthetic_or_degraded"
    port2 = FakePolymarketPort({"strikes": [95000.0]})
    r2 = layer0_from_port(port2, "btc-macro")
    assert r2.valid is False  # fehlende Felder -> fail-closed
    # unverfuegbarer Port
    port3 = FakePolymarketPort(ladder_payload())
    port3.available = False
    assert layer0_from_port(port3, "btc-macro").valid is False


def test_layer0_port_injected_payload_telemetry():
    port = FakePolymarketPort(ladder_payload())
    r = layer0_from_port(port, "btc-macro")
    assert r.valid is True
    assert r.event_id == "btc-macro"
    assert r.implied_prob is not None and 0.0 <= r.implied_prob <= 1.0
    assert r.regime_hint == "BULLISH"  # steigende mu-Kurve
    d = r.to_dict()
    assert d["details"]["density"]["valid"] is True
    assert d["details"]["trajectory"]["bias"] == "BULLISH"
    # Gate-Schwelle existiert als Konstante, ist aber NICHT aktiv
    assert POLYMARKET_GATE_THRESHOLD == 0.60
    assert d["details"]["gate_active"] is False


def test_orchestrator_without_port_unchanged():
    # Regression: Orchestrator ohne Port verhaelt sich exakt wie heute
    from sigma.orchestration.master_orchestrator import MasterOrchestrator

    orch = MasterOrchestrator()
    snap = type("Snap", (), {"series": {}})()
    ctx = orch.tick(snap, now=1_704_067_200)
    assert ctx["polymarket"]["valid"] is False  # kein Feed -> valid=False
    # mit Port-Injektion bleibt der Tick lauffaehig (Telemetrie-Kontext)
    orch2 = MasterOrchestrator(ports={"polymarket": FakePolymarketPort(ladder_payload())})
    ctx2 = orch2.tick(snap, now=1_704_067_200)
    assert ctx2["polymarket"]["valid"] is True
    assert ctx2["polymarket"]["regime_hint"] == "BULLISH"
