"""
=========================================================
Datei:      sigma/strategies/quantum_sniper_dca.py
Zweck:      MP-07 Quantum-Sniper (15m Wave -> 1m/5m Retest,
            Ranker-Freigabe, 05-48-Minuten-TTL, DCA-Ladder
            via MP-02, Hard-SL via MP-01). Paper-only Intent;
            keine Orders, kein Pine. Pfad alpha (Kante mit
            Retest-Konfluenz) und Pfad beta (Breakout-Bestaeti-
            gung Dirigent+Alt auf geschlossenen Bars) als
            details-Kontext.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Rouge (Template) / Blanche (Retest)
=========================================================
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence

from sigma.execution.risk_guards import (
    assert_leverage_for_depth,
    hard_stop_distance,
    liq_outside_wick_zone,
)
from sigma.signals.marubozu_fvg import evaluate as evaluate_fvg
from sigma.signals.quantum_wave_collider import STATUS_COLLAPSED, STATUS_INVALIDATED
from sigma.signals.two_bar_thrust import evaluate as evaluate_thrust
from sigma.strategies.base_strategy import BaseStrategy, StrategyIntent
from sigma.strategies.dca_ladder import (
    LADDER_TTL_SECONDS,
    average_fill_price,
    build_ladder,
    take_profit_price,
)

# --- Sniper-Parameter (KB §5.4 / Prompt) -------------------------------
SNIPER_STRATEGY_ID = "quantum_sniper_dca"
ENTRY_MINUTE_MIN = 5      # frueheste Entry-Minute der bestaetigten 1h-Kerze
ENTRY_MINUTE_MAX = 48     # Minute >= 48 -> Zwangs-FLAT (TTL-Hartregel)
WAVE_INTERVAL_MIN = 15    # 15m-BTC-Wave (KB §4.5 High-Beta-Meme-Sniper)
LTF_INTERVALS = (1, 5)    # Retest nur auf 1m/5m
N_SAFETY = 5              # 4-6 Sprossen
STEP_PCT = 0.002          # 0,2 % Step
STEP_MULT = 1.10
VOLUME_MULT = 1.15
TP_PCT = 0.02             # 2 % auf Avg (KB-Spanne 1,5-3 %)
HARD_SL_BUFFER_PCT = 0.005      # MP-01: 0,5 % ueber Liq
RANGE_LOW_SL_BUFFER_PCT = 0.001  # knapp unter Range-Low
RETEST_TOLERANCE = 0.005  # 0,5 % Toleranz um CE50/Zone
MIN_LTF_BARS = 5          # nur geschlossene Bars


@dataclass(frozen=True)
class RetestVerdict:
    """LTF-Retest-Pruefung: Touch der CE50-Zone + Thrust/FVG-Bestaetigung."""

    confirmed: bool
    reason: str
    touched: bool
    dipped: bool
    thrust: bool
    fvg_in_zone: bool

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def _closed(candles: Sequence[Mapping[str, Any]]) -> list:
    """Nur geschlossene Bars; eine als offen markierte letzte Bar wird
    verworfen (Look-ahead-Verbot)."""
    rows = list(candles or [])
    if rows and rows[-1].get("is_closed", rows[-1].get("closed")) is False:
        return rows[:-1]
    return rows


def _l(b: Mapping[str, Any]) -> float:
    return float(b.get("l", b.get("low", 0.0)) or 0.0)


def _h(b: Mapping[str, Any]) -> float:
    return float(b.get("h", b.get("high", 0.0)) or 0.0)


def _c(b: Mapping[str, Any]) -> float:
    return float(b.get("c", b.get("close", 0.0)) or 0.0)


def utc_minute(now_ts: Optional[float]) -> Optional[int]:
    """UTC-Minute der Stunde (0..59) aus dem Timestamp; None bei fehlendem now."""
    if now_ts is None:
        return None
    try:
        return datetime.fromtimestamp(float(now_ts), tz=timezone.utc).minute
    except (TypeError, ValueError, OSError):
        return None


def minute_phase(minute: Optional[int]) -> str:
    """PRE_EXECUTION (< 5) | ACTIVE_EXECUTION (5..47) | TTL_EXPIRED (>= 48)."""
    if minute is None:
        return "TTL_EXPIRED"  # fail-closed: ohne Zeit keine Sniper-Entries
    if minute < ENTRY_MINUTE_MIN:
        return "PRE_EXECUTION"
    if minute < ENTRY_MINUTE_MAX:
        return "ACTIVE_EXECUTION"
    return "TTL_EXPIRED"


def retest_confirmed(ltf_bars: Sequence[Mapping[str, Any]], ce50: Optional[float]) -> RetestVerdict:
    """Retest = letzte geschlossene 1m/5m-Kerze beruehrt die CE50-Zone,
    eine vorherige Kerze hat die Zone unterschritten (Retest, nicht erster
    Touch) UND Thrust (MP-03) oder bullishes FVG in der Zone bestaetigt.
    Alles auf geschlossenen Bars; fehlende Daten -> not confirmed."""
    empty = RetestVerdict(False, "missing_retest_data", False, False, False, False)
    if ce50 is None or ce50 <= 0:
        return empty
    closed = _closed(ltf_bars)
    if len(closed) < MIN_LTF_BARS:
        return RetestVerdict(False, "insufficient_ltf_bars", False, False, False, False)
    last = closed[-1]
    touched = _l(last) <= ce50 * (1.0 + RETEST_TOLERANCE)
    dipped = any(_l(b) < ce50 * (1.0 - RETEST_TOLERANCE) for b in closed[-4:-1])
    thrust = evaluate_thrust(closed, support_price=ce50)
    fvg = evaluate_fvg(closed)
    fvg_ok = (
        fvg.valid
        and fvg.fvg_bullish
        and fvg.gap_low is not None
        and fvg.gap_high is not None
        and ce50 >= fvg.gap_low * (1.0 - RETEST_TOLERANCE)
        and ce50 <= fvg.gap_high * (1.0 + RETEST_TOLERANCE)
    )
    thrust_ok = bool(thrust.signal and thrust.support_confluence)
    confirmed = touched and dipped and (thrust_ok or fvg_ok)
    reason = "retest_confirmed" if confirmed else "no_retest_confirmation"
    return RetestVerdict(
        confirmed=confirmed,
        reason=reason,
        touched=touched,
        dipped=dipped,
        thrust=thrust_ok,
        fvg_in_zone=fvg_ok,
    )


def beta_retest_confirmed(
    ltf_bars: Sequence[Mapping[str, Any]],
    htf_bars: Sequence[Mapping[str, Any]],
    breakout_level: Optional[float],
    side: str = "buy",
) -> tuple:
    """Pfad beta: Dirigent (HTF) UND Alt (LTF) haben den Breakout auf
    geschlossenen Bars bestaetigt; LTF retestet das Ausbruchslevel.
    side='buy' -> Breakout ueber range_high; side='sell' -> Bruch unter
    range_low. -> (confirmed: bool, reason: str)."""
    if breakout_level is None or breakout_level <= 0:
        return False, "missing_breakout_level"
    htf_closed = _closed(htf_bars)
    ltf_closed = _closed(ltf_bars)
    if len(htf_closed) < 2 or len(ltf_closed) < MIN_LTF_BARS:
        return False, "insufficient_bars"
    htf_last = htf_closed[-1]
    htf_prev = htf_closed[-2]
    ltf_last = ltf_closed[-1]
    is_buy = str(side).lower() in ("buy", "long")
    if is_buy:
        conductor_breakout = _c(htf_last) > max(_h(htf_prev), breakout_level)
        alt_retest = (
            _l(ltf_last) <= breakout_level * (1.0 + RETEST_TOLERANCE)
            and _c(ltf_last) >= breakout_level * (1.0 - RETEST_TOLERANCE)
        )
    else:
        conductor_breakout = _c(htf_last) < min(_l(htf_prev), breakout_level)
        alt_retest = (
            _h(ltf_last) >= breakout_level * (1.0 - RETEST_TOLERANCE)
            and _c(ltf_last) <= breakout_level * (1.0 + RETEST_TOLERANCE)
        )
    if conductor_breakout and alt_retest:
        return True, "confirmed_breakout_retest"
    return False, "breakout_not_confirmed"


class QuantumSniperDCA(BaseStrategy):
    """Sniper-Template: nur planen (Intent), nie Orders platzieren."""

    STRATEGY_ID = SNIPER_STRATEGY_ID

    def plan(self, ctx: Optional[Mapping[str, Any]]) -> StrategyIntent:
        return plan_sniper(ctx)


def _flat(symbol: str, reason: str, details: Optional[dict] = None) -> StrategyIntent:
    det = {"reason": reason}
    if details:
        det.update(details)
    return StrategyIntent(
        strategy_id=SNIPER_STRATEGY_ID,
        symbol=symbol or "",
        action="FLAT",
        execution_mode="kraken_paper",
        details=det,
    )


def _wave_dict(ctx: Mapping[str, Any]) -> Optional[dict]:
    wave = ctx.get("wave")
    if isinstance(wave, dict):
        return wave
    if wave is not None and hasattr(wave, "to_dict"):
        return wave.to_dict()
    return None


def _ranker_row(screening: Optional[Mapping[str, Any]], symbol: str, side: str) -> Optional[dict]:
    """Sucht das Symbol in der Long-/Short-Rangliste des MP-05-Rankers."""
    if not screening:
        return None
    result = screening.get("screening")
    if not isinstance(result, Mapping):
        return None
    rows = result.get("long_rank" if side == "buy" else "short_rank")
    if not isinstance(rows, list):
        return None
    for row in rows:
        if isinstance(row, Mapping) and str(row.get("symbol") or "") == symbol:
            return dict(row)
        if hasattr(row, "symbol") and str(getattr(row, "symbol") or "") == symbol:
            return row
    return None


def plan_sniper(ctx: Optional[Mapping[str, Any]]) -> StrategyIntent:
    """Alle Entry-Bedingungen, sonst FLAT mit reason. Keine Orders."""
    if not ctx:
        return _flat("", "missing_data")
    symbol = str(ctx.get("symbol") or "")
    if not symbol:
        return _flat("", "missing_symbol")

    # --- Zeit: 05-48-Fenster (TTL-Hartregel) -----------------------------
    minute = utc_minute(ctx.get("now"))
    phase = minute_phase(minute)
    if phase == "TTL_EXPIRED":
        return _flat(symbol, "ttl_minute_48", {"minute_utc": minute, "phase": phase})
    if phase == "PRE_EXECUTION":
        return _flat(symbol, "outside_execution_window", {"minute_utc": minute, "phase": phase})

    session = ctx.get("session") or {}
    if isinstance(session, Mapping):
        if session.get("liquidity_gap"):
            return _flat(symbol, "utc_21_gap")
        if session.get("weekend_alts_paper_only") and symbol not in ("BTC/USD", "ETH/USD"):
            return _flat(symbol, "weekend_alt_paper_only")

    # --- Wave: nur COLLAPSED_INTO_ZONE auf 15m ---------------------------
    wave = _wave_dict(ctx)
    if not wave:
        return _flat(symbol, "missing_wave", {"minute_utc": minute})
    status = wave.get("status")
    if status == STATUS_INVALIDATED:
        return _flat(symbol, "wave_invalidated", {"minute_utc": minute})
    if status != STATUS_COLLAPSED:
        return _flat(symbol, f"wave_not_collapsed:{status}", {"minute_utc": minute})
    interval = wave.get("interval_min")
    if interval is not None and int(interval) != WAVE_INTERVAL_MIN:
        return _flat(symbol, f"wave_interval_not_15m:{interval}", {"minute_utc": minute})
    ce50 = wave.get("ce50")
    range_low = wave.get("range_low")
    range_high = wave.get("range_high")
    if ce50 is None or range_low is None or range_high is None:
        return _flat(symbol, "missing_wave_zones", {"minute_utc": minute})

    # --- LTF: 1m/5m + Retest-Bestaetigung --------------------------------
    ltf_interval = int(ctx.get("ltf_interval_min") or 0)
    if ltf_interval not in LTF_INTERVALS:
        return _flat(symbol, "ltf_timeframe_not_1m_5m", {"minute_utc": minute})
    ltf = ctx.get("ltf_candles") or ctx.get("candles") or []
    htf = ctx.get("htf_candles") or []
    retest = retest_confirmed(ltf, ce50)
    entry_path = "alpha"
    beta_reason = ""
    if not retest.confirmed:
        # Pfad beta: Breakout (Dirigent+Alt) auf geschlossenen Bars
        # bestaetigt + Retest am Ausbruchslevel -> beta-Entry.
        beta_buy_ok, beta_buy_reason = beta_retest_confirmed(ltf, htf, range_high, "buy")
        beta_sell_ok, beta_sell_reason = beta_retest_confirmed(ltf, htf, range_low, "sell")
        beta_ok, beta_direction = (beta_buy_ok, "buy") if beta_buy_ok else (beta_sell_ok, "sell")
        beta_reason = beta_buy_reason if beta_buy_ok else beta_sell_reason
        if not beta_ok:
            return _flat(
                symbol,
                "no_retest_confirmation",
                {
                    "minute_utc": minute,
                    "retest": retest.to_dict(),
                    "path_beta_armed": False,
                    "path_beta_reason": beta_reason,
                },
            )
        entry_path = "beta"
        _beta_direction = beta_direction

    # --- Ranker-Freigabe (MP-05): sniper_hedge + entry_ready -------------
    screening = ctx.get("screening")
    side = "buy"
    row = _ranker_row(screening, symbol, side)
    if row is None:
        # Short-Seite pruefen
        side = "sell"
        row = _ranker_row(screening, symbol, side)
    if row is None:
        return _flat(
            symbol,
            "missing_ranker_release",
            {"minute_utc": minute, "retest": retest.to_dict()},
        )
    if entry_path == "beta" and side != _beta_direction:
        return _flat(
            symbol,
            "beta_direction_mismatch",
            {"minute_utc": minute, "beta_direction": _beta_direction, "ranker_side": side},
        )
    rec = str(row.get("recommendation", "")) if isinstance(row, Mapping) else str(getattr(row, "recommendation", ""))
    if rec != "sniper_hedge":
        return _flat(symbol, "ranker_not_sniper", {"recommendation": rec, "minute_utc": minute})
    entry_ready = row.get("entry_ready", True) if isinstance(row, Mapping) else bool(getattr(row, "entry_ready", True))
    if not entry_ready:
        return _flat(symbol, "ranker_chasing_zone", {"minute_utc": minute})

    # --- Ladder (MP-02) + Hard-SL (MP-01) + Guards -----------------------
    entry_price = _c(_closed(ltf)[-1]) if _closed(ltf) else 0.0
    if entry_price <= 0:
        return _flat(symbol, "missing_entry_price", {"minute_utc": minute})
    ladder = build_ladder(
        entry_price,
        side=side,
        n_safety=N_SAFETY,
        step_pct=STEP_PCT,
        step_mult=STEP_MULT,
        base_margin_pct=0.01,
        volume_mult=VOLUME_MULT,
    )
    liq = ctx.get("liquidation_price")
    liq_f = float(liq) if liq is not None else None
    guard_notes: dict = {}
    if liq_f is not None and liq_f > 0:
        # MP-01: Liq nicht in der erwarteten Wick-Zone (Range-Low als Zone)
        wick_verdict = liq_outside_wick_zone(liq_f, float(range_low), side)
        guard_notes["wick_zone"] = wick_verdict.to_dict() if hasattr(wick_verdict, "to_dict") else dict(wick_verdict)
        if not wick_verdict.ok:
            return _flat(
                symbol,
                "liq_inside_wick_zone",
                {"minute_utc": minute, "liq": liq_f, "wick_low": range_low},
            )
        stop_result = hard_stop_distance(entry_price, liq_f, side, buffer_pct=HARD_SL_BUFFER_PCT)
        guard_notes["hard_sl"] = stop_result.to_dict() if hasattr(stop_result, "to_dict") else dict(stop_result)
        guard_notes["hard_sl_basis"] = "liquidation_price"
        stop_loss = float(stop_result.stop_price)
    else:
        # Fallback: knapp unter/ueber Range-Low/-High (Prompt: „bzw. knapp
        # unter Range-Low“). Hard-Stop steht damit immer im Intent.
        stop_loss = float(range_low) * (1.0 - RANGE_LOW_SL_BUFFER_PCT) if side == "buy" else float(range_high) * (1.0 + RANGE_LOW_SL_BUFFER_PCT)
        guard_notes["hard_sl_basis"] = "range_low_high"
    if stop_loss is None or stop_loss <= 0:
        return _flat(symbol, "missing_stop_reference", {"minute_utc": minute})

    # MP-01 Hebel-vs-Tiefe-Guard (Wick-Liq-Zone): nur wenn beta + BTC-Wick
    # im Kontext stehen (Ranker liefert beta).
    beta = row.get("beta", 0.0) if isinstance(row, Mapping) else float(getattr(row, "beta", 0.0))
    btc_wick = ctx.get("expected_btc_wick_pct")
    session_max_lev = session.get("max_leverage") if isinstance(session, Mapping) else 5
    leverage = int(ctx.get("leverage") or session_max_lev or 5)
    if beta and btc_wick is not None:
        verdict = assert_leverage_for_depth(
            float(beta), ladder.total_depth_pct, leverage, float(btc_wick)
        )
        guard_notes["leverage_depth"] = verdict.to_dict() if hasattr(verdict, "to_dict") else dict(verdict)
        if not verdict.ok:
            return _flat(symbol, "leverage_depth_guard", {"minute_utc": minute, **guard_notes})

    avg_price = average_fill_price(ladder.rungs)
    take_profit = take_profit_price(avg_price, side, tp_pct=TP_PCT)
    total_volume = sum(r.volume for r in ladder.rungs)
    intent = StrategyIntent(
        strategy_id=SNIPER_STRATEGY_ID,
        symbol=symbol,
        action="BUY" if side == "buy" else "SELL",
        side=side,
        volume=round(total_volume, 6),
        price=round(entry_price, 10),
        stop_loss=round(float(stop_loss), 10),
        take_profit=round(float(take_profit), 10),
        pair="",
        execution_mode="kraken_paper",
        details={
            "path": entry_path,
            "minute_utc": minute,
            "phase": phase,
            "ttl_seconds": LADDER_TTL_SECONDS,
            "wave_status": status,
            "ce50": ce50,
            "range_low": range_low,
            "range_high": range_high,
            "retest": retest.to_dict(),
            "confirmed_breakout_retest": entry_path == "beta",
            "path_beta_reason": beta_reason,
            "ladder": ladder.to_dict(),
            "tp_pct": TP_PCT,
            "avg_fill_price": avg_price,
            "risk_guards": guard_notes,
            "ranker": {
                "recommendation": rec,
                "entry_ready": entry_ready,
                "beta": beta,
            },
        },
    )
    return intent


__all__ = [
    "ENTRY_MINUTE_MAX",
    "ENTRY_MINUTE_MIN",
    "N_SAFETY",
    "QuantumSniperDCA",
    "RetestVerdict",
    "SNIPER_STRATEGY_ID",
    "STEP_PCT",
    "TP_PCT",
    "VOLUME_MULT",
    "beta_retest_confirmed",
    "minute_phase",
    "plan_sniper",
    "retest_confirmed",
    "utc_minute",
]
