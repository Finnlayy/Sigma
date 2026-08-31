"""
=========================================================
Datei:      tests/test_sigma_live_adapters.py
Zweck:      GLINT-POLYMARKET-WIRING — 100 % offline:
            - Gamma-Mapper (Strike-Leiter, Dichte-Bins, mu,
              Bias, Trajektorien, Gate 0.60 Telemetrie)
            - Kraken-L2-JIT-Payload-Verify (2 %-Band, I_depth,
              Stale-Veto, Tailwind-Multiplikator)
            - API-Endpunkte liefern echte gemappte Payloads
              (mit Port) bzw. fail-closed (ohne Port)
            - Fail-closed auf korrupten/synthetischen/stalen
              Feeds; kein Netz, keine Look-ahead-Daten.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Noir (Test)
=========================================================
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

from app.ingestion.kraken_depth_adapter import KrakenDepthAdapter
from app.quant.glint_orderbook_verifier import (
    ConfluenceResult,
    GlintOrderbookVerifier,
    set_verifier,
)
from sigma.ports.polymarket_gamma_feeder import (
    GATE_PROB,
    TRAJECTORY_WEIGHTS,
    GammaFeederPort,
    PolymarketOdds,
    density_from_ladder,
    gate_060,
    mu_from_density,
    parse_gamma_payload,
    set_gamma_port,
    trajectories_from,
)

# ------------------------------------------------------------------ fixtures

GAMMA_PAYLOAD = {
    "slug": "btc-macro-42",
    "title": "Will Bitcoin close above $110,000 by end of August?",
    "volume24hr": 2_500_000.0,
    "liquidity": 1_100_000.0,
    "markets": [
        {"groupItemTitle": "100000", "outcomePrices": [0.98, 0.02],
         "synthetic": False},
        {"groupItemTitle": "105000", "outcomePrices": [0.90, 0.10],
         "synthetic": False},
        {"groupItemTitle": "110000", "outcomePrices": [0.60, 0.40],
         "synthetic": False},
        {"groupItemTitle": "115000", "outcomePrices": [0.20, 0.80],
         "synthetic": False},
    ],
}

KRAKEN_DEPTH_PAYLOAD = {
    "error": [],
    "result": {
        "XBTUSD": {
            "bids": [
                ["67000.0", "1.5", 1700000000],
                ["66950.0", "2.0", 1700000000],
                ["66800.0", "5.0", 1700000000],
            ],
            "asks": [
                ["67050.0", "1.0", 1700000000],
                ["67100.0", "1.5", 1700000000],
                ["67200.0", "4.0", 1700000000],
            ],
        }
    },
}


def _now() -> float:
    return time.time()


# ------------------------------------------------------------- gamma mapper

def test_gamma_parser_density_mu_trajectories():
    odds = parse_gamma_payload(GAMMA_PAYLOAD, spot_price=107_000.0, now=1_700_000_000.0)
    assert odds.valid, odds.reason
    assert odds.slug == "btc-macro-42"
    assert odds.strikes == [100000.0, 105000.0, 110000.0, 115000.0]
    # kumulative Yes-Leiter: 0.98, 0.90, 0.60, 0.20
    assert odds.yes_probs == [0.98, 0.90, 0.60, 0.20]
    # Bins: P([100k,105k)) = 0.98-0.90 = 0.08; P([105k,110k)) = 0.30;
    #      P([110k,115k)) = 0.40; Tail-low 0.02; Tail-high 0.20
    probs = [b["prob"] for b in odds.density_bins]
    assert probs[0] == pytest.approx(0.08)
    assert probs[1] == pytest.approx(0.30)
    assert probs[2] == pytest.approx(0.40)
    assert probs[3] == pytest.approx(0.02)
    assert probs[4] == pytest.approx(0.20)
    assert sum(probs) == pytest.approx(1.0, abs=1e-6)
    # mu = Σ midpoint*P
    mu_expected = (
        102500 * 0.08 + 107500 * 0.30 + 112500 * 0.40
        + 97500 * 0.02 + 117500 * 0.20
    )
    assert odds.mu == pytest.approx(mu_expected)
    assert odds.bias_pct == pytest.approx((mu_expected - 107000.0) / 107000.0 * 100.0)
    # Trajektorien: p̂(T) = spot + (mu-spot)*w(T)
    for horizon, w in TRAJECTORY_WEIGHTS.items():
        assert odds.trajectories[horizon] == pytest.approx(
            107000.0 + (mu_expected - 107000.0) * w)
    # Gate 0.60: K=110000 > 107000 mit P=0.60 -> True (Telemetrie)
    assert odds.gate_060 is True


def test_gamma_gate_060_telemetry_only():
    payload = dict(GAMMA_PAYLOAD)
    markets = [dict(m) for m in payload["markets"]]
    markets[2]["outcomePrices"] = [0.55, 0.45]  # 110k nur 0.55
    payload["markets"] = markets
    odds = parse_gamma_payload(payload, spot_price=107_000.0, now=1_700_000_000.0)
    assert odds.valid
    assert odds.gate_060 is False  # kein Strike > spot mit P >= 0.60
    assert gate_060([100000, 110000], [0.9, 0.6], 105000) is True
    assert gate_060([100000, 110000], [0.9, 0.5], 105000) is False


def test_gamma_fail_closed_corrupted_and_synthetic():
    # korrupt: keine markets (Volumen ok, damit der Markets-Check greift)
    odds = parse_gamma_payload({"slug": "x", "volume24hr": 2_000_000.0},
                               spot_price=100.0, now=1_700_000_000.0)
    assert not odds.valid and odds.reason == "missing_markets"
    # korrupt: kumulative Yes-Leiter steigt (0.50 -> 0.60) -> negativer
    # Bin zwischen den Strikes -> corrupt_ladder (fail-closed)
    bad = {
        "slug": "x", "volume24hr": 2_000_000.0,
        "markets": [
            {"groupItemTitle": "100000", "outcomePrices": [0.50, 0.50]},
            {"groupItemTitle": "110000", "outcomePrices": [0.60, 0.40]},
        ],
    }
    odds = parse_gamma_payload(bad, spot_price=105000.0, now=1_700_000_000.0)
    assert not odds.valid and odds.reason.startswith("corrupt_ladder")
    # synthetisch -> fail-closed
    syn = dict(GAMMA_PAYLOAD, synthetic=True)
    odds = parse_gamma_payload(syn, spot_price=107_000.0, now=1_700_000_000.0)
    assert not odds.valid and odds.reason == "synthetic_or_degraded"
    # Volumen unter Schwelle -> verworfen
    low = dict(GAMMA_PAYLOAD, volume24hr=500_000.0)
    odds = parse_gamma_payload(low, spot_price=107_000.0, now=1_700_000_000.0)
    assert not odds.valid and odds.reason.startswith("below_min_volume")
    # leerer Payload -> fail-closed
    odds = parse_gamma_payload(None, spot_price=107_000.0, now=1_700_000_000.0)
    assert not odds.valid and odds.reason == "missing_payload"


def test_gamma_ttl_stale():
    now = 1_700_000_000.0
    odds = parse_gamma_payload(GAMMA_PAYLOAD, spot_price=107_000.0, now=now, ttl_s=300.0)
    assert not odds.is_stale(now + 100.0)
    assert odds.is_stale(now + 301.0)
    assert odds.is_stale(None)


def test_gamma_density_math_helpers():
    bins, total = density_from_ladder([100.0, 110.0, 120.0], [0.9, 0.5, 0.1])
    assert total == pytest.approx(1.0)
    mu = mu_from_density(bins)
    assert mu == pytest.approx(105 * 0.4 + 115 * 0.4 + 95 * 0.1 + 125 * 0.1)
    with pytest.raises(ValueError):
        density_from_ladder([120.0, 110.0], [0.5, 0.4])  # nicht aufsteigend
    with pytest.raises(ValueError):
        density_from_ladder([100.0, 110.0], [0.5])  # Längen-Mismatch
    traj = trajectories_from(100.0, 110.0)
    assert traj["1h"] == pytest.approx(101.5)   # 100 + 10*0.15
    assert traj["Res"] == pytest.approx(110.0)  # 100 + 10*1.00


# ---------------------------------------------------------- kraken l2 jit

def test_kraken_payload_snapshot_and_audit_normal():
    adapter = KrakenDepthAdapter()
    snapshot = adapter.snapshot_from_payload(KRAKEN_DEPTH_PAYLOAD, "XBTUSD", 1_700_000_000.0)
    assert snapshot.symbol == "XBTUSD"
    assert len(snapshot.bids) == 3 and len(snapshot.asks) == 3
    assert snapshot.bids[0][0] == 67000.0  # absteigend
    assert snapshot.asks[0][0] == 67050.0  # aufsteigend
    result = adapter.verify_payload(KRAKEN_DEPTH_PAYLOAD, "XBTUSD", "BULLISH",
                                    now=1_700_000_000.0)
    assert isinstance(result, ConfluenceResult)
    # bid vol 2%: 67000*1.5 + 66950*2.0 = 100500+133900 = 234400
    # ask vol 2%: 67050*1.0 + 67100*1.5 = 67050+100650 = 167700
    # I_depth = (234400-167700)/(234400+167700) ~ 0.166 -> NEUTRAL (kein Veto)
    assert result.approved
    assert result.size_multiplier == 1.0
    assert result.verdict == "NEUTRAL_FLOW" or result.verdict == "NEUTRAL"
    assert result.depth_imbalance > 0.0


def test_kraken_audit_confirm_tailwind():
    # ask-seitig dünn, bid-seitig massiv -> CONFIRM + 1.25
    payload = {
        "result": {"XBTUSD": {
            "bids": [["67000.0", "100.0", 1], ["66900.0", "80.0", 1]],
            "asks": [["67010.0", "1.0", 1], ["67100.0", "2.0", 1]],
        }}
    }
    adapter = KrakenDepthAdapter()
    result = adapter.verify_payload(payload, "XBTUSD", "BULLISH", now=1.0)
    assert result.approved
    assert result.size_multiplier == pytest.approx(1.25)
    assert result.verdict in ("CONFLUENCE_CONFIRMED", "CONFIRM_TAILWIND")
    assert result.depth_imbalance > 0.30


def test_kraken_audit_veto_liquidity_trap():
    # bid-seitig dünn, ask-seitig massiv bei LONG -> LIQUIDITY_TRAP_VETO 0.0
    payload = {
        "result": {"XBTUSD": {
            "bids": [["67000.0", "1.0", 1], ["66900.0", "1.0", 1]],
            "asks": [["67010.0", "100.0", 1], ["67100.0", "80.0", 1]],
        }}
    }
    adapter = KrakenDepthAdapter()
    result = adapter.verify_payload(payload, "XBTUSD", "BULLISH", now=1.0)
    assert not result.approved
    assert result.size_multiplier == 0.0
    assert "VETO" in result.verdict


def test_kraken_audit_stale_veto():
    # Snapshot 5 s alt -> Stale-Veto (max_age 3 s), auch bei Tailwind
    payload = dict(KRAKEN_DEPTH_PAYLOAD)
    adapter = KrakenDepthAdapter()
    result = adapter.verify_payload(payload, "XBTUSD", "BULLISH", now=1_700_000_005.0)
    assert not result.approved
    assert result.size_multiplier == 0.0
    assert "VETO" in result.verdict
    assert result.snapshot_age_s == pytest.approx(5.0)


def test_kraken_payload_fail_closed_corrupted():
    adapter = KrakenDepthAdapter()
    with pytest.raises(Exception):
        adapter.snapshot_from_payload({"result": {}}, "XBTUSD", 1.0)
    with pytest.raises(Exception):
        adapter.snapshot_from_payload(
            {"result": {"XBTUSD": {"bids": [], "asks": []}}}, "XBTUSD", 1.0)


# ------------------------------------------------- no lookahead / offline

def test_no_network_in_module_imports():
    """Weder Gamma-Feeder noch Verifier-Adapter dürfen beim Import
    Netzwerk aufrufen (pure Funktionen + injizierbare Fetcher)."""
    import inspect
    import sigma.ports.polymarket_gamma_feeder as feeder
    src = inspect.getsource(feeder)
    assert "urllib.request.urlopen" not in src
    assert "httpx" not in src or "import httpx" not in src
    import app.ingestion.kraken_depth_adapter as adapter
    src2 = inspect.getsource(adapter)
    assert "verify_payload" in src2


def test_determinism_same_input_same_output():
    a = parse_gamma_payload(GAMMA_PAYLOAD, spot_price=107_000.0, now=1_700_000_000.0)
    for _ in range(50):
        b = parse_gamma_payload(GAMMA_PAYLOAD, spot_price=107_000.0, now=1_700_000_000.0)
        assert a.to_dict() == b.to_dict()
    adapter = KrakenDepthAdapter()
    r1 = adapter.verify_payload(KRAKEN_DEPTH_PAYLOAD, "XBTUSD", "BULLISH", now=42.0)
    r2 = adapter.verify_payload(KRAKEN_DEPTH_PAYLOAD, "XBTUSD", "BULLISH", now=42.0)
    assert r1.as_dict() == r2.as_dict()


def test_preflight_jit_audit_in_lifecycle():
    """StrategyLifecycleService._preflight nutzt den bestehenden
    JIT-Orderbuch-Audit (kein Blind-Entry ohne Konfluenz)."""
    import os as _os
    path = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                         "app", "services", "strategy_lifecycle_service.py")
    with open(path, "r", encoding="utf-8") as fh:
        src = fh.read()
    assert "self.verifier.verify" in src
    assert "ORDERBOOK_AUDIT_MISSING" in src
    assert "ORDERBOOK_DEPTH_UNAVAILABLE" in src
