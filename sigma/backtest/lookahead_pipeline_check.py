"""
=========================================================
Datei:      sigma/backtest/lookahead_pipeline_check.py
Zweck:      MP-12 Look-ahead-Prüfer: beweist, dass die
            Closed-HTF-Invariante zuschlägt (HTF-Indikatoren
            zur Zeit t sehen nur geschlossene Bars bis t-1).
            assert_no_lookahead für alle Backtest-Tests;
            chronologische Splits + Walk-Forward (2:1), nie
            Random-Splits. Reines Testwerkzeug: keine Plans,
            keine Orders, keine Orchestrator-Gates.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Backtest) / Noir (fail-closed)
=========================================================
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


def _ts(c: Mapping[str, Any]) -> float:
    return float(c.get("ts", c.get("time", 0)) or 0)


def closed_htf_prefix(candles: Sequence[Mapping[str, Any]], t: float) -> List[Mapping[str, Any]]:
    """Alle Bars mit ts <= t, deren Close-Zeitpunkt strikt vor t liegt
    (closed HTF bars up to t-1). Ohne Timestamps -> leer (fail-closed)."""
    out: List[Mapping[str, Any]] = []
    for c in candles or []:
        if _ts(c) <= 0:
            return []
        if _ts(c) < t:
            out.append(c)
    return out


def assert_no_lookahead(tick_ctx_series: Sequence[Mapping[str, Any]]) -> None:
    """Assertion für alle Backtest-Tests: Jeder Tick sieht fuer
    Indikatorberechnung nur geschlossene HTF-Bars bis t-1. Verletzt
    ein Tick die Invariante -> AssertionError mit Tick-Timestamp."""
    for i, tick in enumerate(tick_ctx_series or []):
        t = _ts(tick)
        if t <= 0:
            raise AssertionError(f"tick[{i}]: fehlender Timestamp (fail-closed)")
        for key in ("htf", "htf_candles", "htf_series", "series"):
            htf = tick.get(key)
            if htf is None:
                continue
            leak = [c for c in htf if _ts(c) >= t]
            if leak:
                raise AssertionError(
                    f"tick[{i}] ts={t}: Look-ahead-Leck in '{key}' "
                    f"({len(leak)} Bar(s) mit ts >= t, erste ts={_ts(leak[0])})"
                )
    # Invariante hält über alle Ticks.


def walk_forward_split(
    candles: Sequence[Mapping[str, Any]],
    ratio: float = 2.0,
) -> Tuple[List[Mapping[str, Any]], List[Mapping[str, Any]]]:
    """Chronologischer Split (2:1 train/test Default): erstes Drittel
    train, letztes Drittel test, Mitte wird verworfen (kein Leck durch
    überlappende Indikator-Warmups). Nie Random-Splits."""
    rows = sorted((c for c in candles or [] if _ts(c) > 0), key=_ts)
    if len(rows) < 2:
        return [], []
    cut = len(rows) // int(ratio + 1)
    return rows[:cut], rows[-cut:]


def walk_forward_folds(
    candles: Sequence[Mapping[str, Any]],
    n_folds: int = 3,
    ratio: float = 2.0,
) -> List[Tuple[List[Mapping[str, Any]], List[Mapping[str, Any]]]]:
    """n aufeinanderfolgende 2:1-Scheiben über die Zeitachse; jede
    Scheibe ist strikt nach der vorherigen (chronologisch)."""
    rows = sorted((c for c in candles or [] if _ts(c) > 0), key=_ts)
    if not rows:
        return []
    folds: List[Tuple[List[Mapping[str, Any]], List[Mapping[str, Any]]]] = []
    step = max(1, len(rows) // (n_folds + 1))
    for f in range(n_folds):
        start = f * step
        end = min(len(rows), start + 2 * step)
        if end - start < 2:
            break
        tr, te = walk_forward_split(rows[start:end], ratio=ratio)
        if tr and te:
            folds.append((tr, te))
    return folds


def check_series_closed(candles: Sequence[Mapping[str, Any]]) -> None:
    """Fail-closed: nur geschlossene Bars (offene letzte Bar -> Assert)."""
    rows = list(candles or [])
    if rows and rows[-1].get("is_closed", rows[-1].get("closed")) is False:
        raise AssertionError(
            "offene (unbestätigte) Bar in Backtest-Serie: "
            "nur geschlossene Bars zulässig"
        )
