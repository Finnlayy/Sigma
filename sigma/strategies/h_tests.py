"""
=========================================================
Datei:      sigma/strategies/h_tests.py
Zweck:      Loop-B Paper-Hypothesen H1–H5. H3/H4 default aus.
            Kein Live bis E graduert.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Loop B) / Blanche (Hypothesen)
=========================================================
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

from sigma.signals.dual_hurst import evaluate_dual_hurst
from sigma.signals.htf_features import extract_htf_flags
from sigma.signals.session_clock import SessionClock
from sigma.signals.timeframe_ladder import classify_pair, reject_unpaired
from sigma.strategies.htf_trend_ltf_reversion import HtfTrendLtfReversion

# H3 (ICT 12–16x) and H4 (FVG/EQ locators) stay off until they replicate in paper.
H_DEFAULTS: Dict[str, bool] = {
    "H1": True,
    "H2": True,
    "H3": False,
    "H4": False,
    "H5": True,
}


@dataclass
class HypothesisResult:
    hypothesis: str
    enabled: bool
    passed: bool
    reason: str
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


def run_paper_hypotheses(
    htf_candles: Optional[Sequence[Mapping[str, Any]]],
    ltf_candles: Optional[Sequence[Mapping[str, Any]]],
    *,
    htf_interval_min: int = 60,
    ltf_interval_min: int = 15,
    now: Optional[float] = None,
    enabled: Optional[Mapping[str, bool]] = None,
    same_tf_candles: Optional[Sequence[Mapping[str, Any]]] = None,
) -> List[HypothesisResult]:
    flags = dict(H_DEFAULTS)
    if enabled:
        flags.update({k: bool(v) for k, v in enabled.items() if k in flags})
    dual = evaluate_dual_hurst(
        htf_candles, ltf_candles, htf_interval_min=htf_interval_min, now=now,
    )
    out: List[HypothesisResult] = []
    out.append(_h1(flags["H1"], dual, htf_candles, ltf_candles, same_tf_candles, now, htf_interval_min))
    out.append(_h2(flags["H2"], htf_interval_min, ltf_interval_min))
    out.append(_h3(flags["H3"], htf_interval_min, ltf_interval_min))
    out.append(_h4(flags["H4"], htf_candles, htf_interval_min, now))
    out.append(_h5(flags["H5"], now, ltf_candles))
    return out


def _h1(enabled, dual, htf, ltf, same_tf, now, htf_interval_min) -> HypothesisResult:
    if not enabled:
        return HypothesisResult("H1", False, False, "disabled")
    if not dual.htf_ready:
        return HypothesisResult("H1", True, False, dual.reason or "htf_open", dual.to_dict())
    baseline = evaluate_dual_hurst(
        same_tf or ltf, same_tf or ltf, htf_interval_min=htf_interval_min, now=now,
    )
    # Complementary HTF-trend + LTF-reversion is the prior vs same-TF RW/self pair
    beats = dual.complementary and not baseline.complementary
    return HypothesisResult(
        "H1", True, beats,
        "complementary_vs_same_tf" if beats else "no_edge_vs_same_tf",
        {"dual": dual.to_dict(), "baseline": baseline.to_dict()},
    )


def _h2(enabled, htf_min, ltf_min) -> HypothesisResult:
    if not enabled:
        return HypothesisResult("H2", False, False, "disabled")
    pair = classify_pair(htf_min, ltf_min)
    ok = pair is not None and pair.kind == "bias"
    unmatched = reject_unpaired(15, 5)  # M15/M5 = 3x must stay rejected
    return HypothesisResult(
        "H2", True, bool(ok and unmatched),
        "bias_4_to_6x" if ok else "unmatched_pair",
        {"pair": pair.to_dict() if pair else None, "m15_m5_rejected": unmatched},
    )


def _h3(enabled, htf_min, ltf_min) -> HypothesisResult:
    if not enabled:
        return HypothesisResult("H3", False, False, "default_off")
    pair = classify_pair(htf_min, ltf_min)
    ok = pair is not None and pair.kind == "execution"
    return HypothesisResult(
        "H3", True, ok,
        "ict_12_to_16x" if ok else "not_execution_pair",
        {"pair": pair.to_dict() if pair else None},
    )


def _h4(enabled, htf, htf_min, now) -> HypothesisResult:
    if not enabled:
        return HypothesisResult("H4", False, False, "default_off")
    flags = extract_htf_flags(htf, interval_min=htf_min, now=now)
    if not flags.get("valid"):
        return HypothesisResult("H4", True, False, str(flags.get("reason") or "invalid"), flags)
    # Even when enabled, FVG is never a live gate
    live_gate = bool(flags.get("live_gate"))
    has_locator = bool(flags.get("bullish_fvg") or flags.get("bearish_fvg") or flags.get("eq_pos") is not None)
    return HypothesisResult(
        "H4", True, has_locator and not live_gate,
        "locator_only" if has_locator else "no_locator",
        flags,
    )


def _h5(enabled, now, ltf) -> HypothesisResult:
    if not enabled:
        return HypothesisResult("H5", False, False, "disabled")
    clock = SessionClock()
    gap = clock.evaluate(_utc(now, 21, 5))
    weekend = clock.evaluate(_utc(now, 12, 0, weekday=5))
    ny = clock.evaluate(_utc(now, 15, 0))
    intent_gap = HtfTrendLtfReversion().plan({
        "symbol": "ETH/USD", "htf_candles": ltf, "ltf_candles": ltf,
        "session": gap.to_dict(), "now": gap.ts,
    })
    intent_we = HtfTrendLtfReversion().plan({
        "symbol": "ETH/USD", "htf_candles": ltf, "ltf_candles": ltf,
        "session": weekend.to_dict(), "now": weekend.ts,
    })
    reduced = intent_gap.action == "FLAT" and intent_we.action == "FLAT"
    return HypothesisResult(
        "H5", True, reduced and gap.liquidity_gap and weekend.weekend_alts_paper_only,
        "session_gap_weekend_filter" if reduced else "filter_leaked",
        {
            "gap": gap.to_dict(),
            "weekend": weekend.to_dict(),
            "ny": ny.to_dict(),
            "gap_action": intent_gap.action,
            "weekend_action": intent_we.action,
        },
    )


def _utc(now: Optional[float], hour: int, minute: int, weekday: Optional[int] = None) -> float:
    from datetime import datetime, timezone
    if now is not None and weekday is None:
        dt = datetime.fromtimestamp(float(now), tz=timezone.utc)
        return datetime(dt.year, dt.month, dt.day, hour, minute, tzinfo=timezone.utc).timestamp()
    dt = datetime(2026, 8, 28, hour, minute, tzinfo=timezone.utc)  # Friday
    if weekday is not None:
        dt = datetime(2026, 8, 29, hour, minute, tzinfo=timezone.utc)  # Saturday
        if weekday == 5:
            dt = datetime(2026, 8, 29, hour, minute, tzinfo=timezone.utc)
    return dt.timestamp()
