"""
=========================================================
Datei:      sigma/strategies/fractal_directional.py
Zweck:      MP-15 fraktaler High-Leverage-Einzeltrade (KB §5.5):
            TP-Staffel 40/30/20/10, harter Initial-SL (0,6 % oder
            naeherer MP-01-Liq-Puffer), Pflicht-Update auf Fee-
            Covered-BE nach TP1 (fee_covered_stop aus MP-01, kein
            Duplikat), Kill-Switch (Exhaustion / Ziel-Sweep /
            Minute >= 55). Paper-only Intent; keine Orders im
            Orchestrator, keine freie Hebelwahl.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Rouge (Template) / Noir (Kill-Switch)
=========================================================
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional, Sequence

from sigma.execution.risk_guards import fee_covered_stop
from sigma.signals.quantum_wave_collider import STATUS_COLLAPSED
from sigma.strategies.base_strategy import BaseStrategy, StrategyIntent

FRACTAL_STRATEGY_ID = "fractal_directional"

# --- TP-Staffel (KB §5.5) ----------------------------------------------
TP1_QTY_PCT = 40
TP2_QTY_PCT = 30
TP3_QTY_PCT = 20
RUNNER_QTY_PCT = 10
TP1_TARGET_PCT = 0.01    # +1,0 %
TP2_TARGET_PCT = 0.02    # +2,0 %
TP3_TARGET_PCT = 0.035   # +3,5 %
RUNNER_TRAIL_ATR_MULT = 3.0   # ATR-Trailing fuer den Runner

INITIAL_SL_PCT = 0.006   # 0,6 % gegen Entry (Default)
ENTRY_MINUTE_MIN = 5
ENTRY_MINUTE_MAX = 48
TTL_MINUTE = 55          # Minute >= 55 -> Runner-FLAT (Kill-Switch)
FRACTAL_MAX_LEVERAGE = 50

RANKER_RECS = ("fractal_directional", "sniper_hedge")


@dataclass(frozen=True)
class FractalTranche:
    """Eine Teilposition der Staffel."""

    index: int
    label: str       # TP1 | TP2 | TP3 | RUNNER
    qty_pct: float
    target_pct: float  # 0.01 = +1 %
    price: float     # Zielpreis (long aufsteigend, short absteigend)
    kind: str        # "tp" | "runner"

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class FractalPlan:
    """Berechneter Staffel-Plan (SL-Zustand initial / fee_be, TTL)."""

    valid: bool
    reason: str
    side: str = ""
    entry_price: float = 0.0
    tranches: list = field(default_factory=list)
    initial_sl: float = 0.0
    sl_basis: str = ""       # "default_0.6pct" | "liq_puffer"
    fee_be_sl: float = 0.0   # zwingend nach TP1 (fee_covered_stop)
    leverage: int = 0
    ttl_minute: int = 0
    kill_switch: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "valid": self.valid,
            "reason": self.reason,
            "side": self.side,
            "entry_price": self.entry_price,
            "tranches": [t.to_dict() for t in self.tranches],
            "initial_sl": self.initial_sl,
            "sl_basis": self.sl_basis,
            "fee_be_sl": self.fee_be_sl,
            "leverage": self.leverage,
            "ttl_minute": self.ttl_minute,
            "kill_switch": self.kill_switch,
        }


def _closed(candles: Sequence) -> list:
    rows = list(candles or [])
    if rows and rows[-1].get("is_closed", rows[-1].get("closed")) is False:
        return rows[:-1]
    return rows


def _close(c: Mapping[str, Any]) -> float:
    return float(c.get("c", c.get("close", 0.0)) or 0.0)


def utc_minute(now_ts: Optional[float]) -> Optional[int]:
    if now_ts is None:
        return None
    try:
        return datetime.fromtimestamp(float(now_ts), tz=timezone.utc).minute
    except (TypeError, ValueError, OSError):
        return None


def build_tranches(entry_price: float, side: str) -> list:
    """TP-Staffel 40/30/20/10; long aufsteigend, short spiegelbildlich."""
    sign = 1.0 if str(side).lower() in ("buy", "long") else -1.0
    specs = [
        (1, "TP1", TP1_QTY_PCT, TP1_TARGET_PCT),
        (2, "TP2", TP2_QTY_PCT, TP2_TARGET_PCT),
        (3, "TP3", TP3_QTY_PCT, TP3_TARGET_PCT),
        (4, "RUNNER", RUNNER_QTY_PCT, 0.0),
    ]
    out = []
    for index, label, qty, target in specs:
        price = entry_price * (1.0 + sign * target)
        out.append(FractalTranche(
            index=index, label=label, qty_pct=qty, target_pct=target,
            price=round(price, 10), kind="runner" if label == "RUNNER" else "tp",
        ))
    return out


def initial_sl_distance(liq_puffer_pct: Optional[float]) -> float:
    """Strengerer der beiden: 0,6 %-Default ODER naeherer Liq-Puffer."""
    if liq_puffer_pct is None or liq_puffer_pct <= 0:
        return INITIAL_SL_PCT
    return min(INITIAL_SL_PCT, float(liq_puffer_pct))


class FractalDirectional(BaseStrategy):
    STRATEGY_ID = FRACTAL_STRATEGY_ID

    def plan(self, ctx: Optional[Mapping[str, Any]]) -> StrategyIntent:
        return plan_fractal(ctx)


def _flat(symbol: str, reason: str, extra: Optional[dict] = None) -> StrategyIntent:
    det = {"reason": reason}
    if extra:
        det.update(extra)
    return StrategyIntent(
        strategy_id=FRACTAL_STRATEGY_ID, symbol=symbol or "", action="FLAT",
        execution_mode="kraken_paper", details=det)


def plan_fractal(ctx: Optional[Mapping[str, Any]]) -> StrategyIntent:
    """Alle Entry-Bedingungen, sonst FLAT (fail-closed). Keine Orders."""
    if not ctx:
        return _flat("", "missing_data")
    symbol = str(ctx.get("symbol") or "")
    if not symbol:
        return _flat("", "missing_symbol")

    minute = utc_minute(ctx.get("now"))
    if minute is None:
        return _flat(symbol, "missing_time")
    if minute >= TTL_MINUTE:
        return _flat(symbol, "kill_switch_ttl_minute_55",
                     {"minute_utc": minute, "kill_switch": "ttl"})
    if minute < ENTRY_MINUTE_MIN or minute >= ENTRY_MINUTE_MAX:
        return _flat(symbol, "outside_execution_window", {"minute_utc": minute})

    # --- Kill-Switch: Exhaustion / Ziel-Sweep ---------------------------
    exhaustion = ctx.get("exhaustion")
    if isinstance(exhaustion, Mapping) and exhaustion.get("exhausted"):
        return _flat(symbol, "kill_switch_exhaustion", {"minute_utc": minute})
    sweep_zone = ctx.get("sweep_zone")
    candles = ctx.get("ltf_candles") or ctx.get("candles") or []
    closed = _closed(candles)
    last_close = _close(closed[-1]) if closed else 0.0
    if sweep_zone is not None and last_close > 0:
        zone = float(sweep_zone)
        if (str(ctx.get("side") or "buy").lower() in ("buy", "long")
                and last_close >= zone) or (
                str(ctx.get("side") or "buy").lower() in ("sell", "short")
                and last_close <= zone):
            return _flat(symbol, "kill_switch_sweep_zone", {"minute_utc": minute})

    # --- Ranker-Freigabe (MP-05) ----------------------------------------
    screening = ctx.get("screening")
    side = "buy"
    row = _ranker_row(screening, symbol, side)
    if row is None:
        side = "sell"
        row = _ranker_row(screening, symbol, side)
    if row is None:
        return _flat(symbol, "missing_ranker_release", {"minute_utc": minute})
    rec = row.get("recommendation") if isinstance(row, Mapping) else getattr(row, "recommendation", "")
    if rec not in RANKER_RECS:
        return _flat(symbol, "ranker_not_fractal",
                     {"recommendation": rec, "minute_utc": minute})
    entry_ready = row.get("entry_ready", True) if isinstance(row, Mapping) else bool(getattr(row, "entry_ready", True))
    if not entry_ready:
        return _flat(symbol, "ranker_chasing_zone", {"minute_utc": minute})

    # --- BTC-Lead-Signal (geschlossene Bar) -----------------------------
    lead = ctx.get("lead")
    lead_ok = False
    if isinstance(lead, Mapping):
        lead_ok = bool(lead.get("confirmed"))
    if not lead_ok:
        wave = ctx.get("wave")
        wave_status = ""
        if isinstance(wave, Mapping):
            wave_status = str(wave.get("status") or "")
        elif wave is not None and hasattr(wave, "to_dict"):
            wave_status = str(wave.to_dict().get("status") or "")
        lead_ok = wave_status == STATUS_COLLAPSED and bool(ctx.get("lead_thrust"))
    if not lead_ok:
        return _flat(symbol, "missing_lead_signal", {"minute_utc": minute})

    # --- Entry-Preis + Hebel (keine freie Wahl) -------------------------
    if not closed:
        return _flat(symbol, "missing_bars", {"minute_utc": minute})
    entry_price = _close(closed[-1])
    if entry_price <= 0:
        return _flat(symbol, "missing_entry_price", {"minute_utc": minute})
    leverage = int(ctx.get("leverage") or 0)
    ranker_max = ctx.get("ranker_max_leverage")
    if leverage <= 0 or leverage > FRACTAL_MAX_LEVERAGE:
        return _flat(symbol, "leverage_out_of_bounds", {"minute_utc": minute})
    if ranker_max is not None and leverage > int(ranker_max):
        return _flat(symbol, "leverage_above_ranker_cap", {"minute_utc": minute})

    # --- SL: naeherer Liq-Puffer schlaegt 0,6 %-Default -----------------
    liq_puffer = ctx.get("liq_puffer_pct")
    liq_f = float(liq_puffer) if liq_puffer is not None else None
    dist = initial_sl_distance(liq_f)
    sl_basis = "liq_puffer" if (liq_f is not None and liq_f < INITIAL_SL_PCT) else "default_0.6pct"
    is_long = side == "buy"
    initial_sl = entry_price * (1.0 - dist) if is_long else entry_price * (1.0 + dist)

    # --- Staffel + zwingender Fee-BE nach TP1 (MP-01, kein Duplikat) -----
    tranches = build_tranches(entry_price, side)
    fee_be = fee_covered_stop(entry_price, side)  # entry x 1,0005 / 0,9995
    total_qty = sum(t.qty_pct for t in tranches)
    tp3_price = tranches[2].price
    return StrategyIntent(
        strategy_id=FRACTAL_STRATEGY_ID,
        symbol=symbol,
        action="BUY" if is_long else "SELL",
        side=side,
        volume=round(total_qty, 6),
        price=round(entry_price, 10),
        stop_loss=round(initial_sl, 10),
        take_profit=round(tp3_price, 10),
        pair="",
        execution_mode="kraken_paper",
        details={
            "minute_utc": minute,
            "tranches": [t.to_dict() for t in tranches],
            "initial_sl": round(initial_sl, 10),
            "sl_basis": sl_basis,
            "liq_puffer_pct": liq_f,
            # Pflicht nach TP1-Fill: SL-Nachzug auf Fee-Covered-BE —
            # nicht abschaltbar (KB §8 Regel 6).
            "update_sl": round(fee_be, 10),
            "update_sl_reason": "TP1_HIT_FEE_COVERED_BREAKEVEN",
            "kill_switch": {
                "exhaustion_armed": True,
                "sweep_zone": sweep_zone,
                "ttl_minute": TTL_MINUTE,
            },
            "leverage": leverage,
            "ttl_seconds": 7200,
            "runner_trail_atr_mult": RUNNER_TRAIL_ATR_MULT,
        },
    )


def _ranker_row(screening: Optional[Mapping[str, Any]], symbol: str, side: str) -> Optional[dict]:
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


__all__ = [
    "ENTRY_MINUTE_MAX",
    "ENTRY_MINUTE_MIN",
    "FRACTAL_MAX_LEVERAGE",
    "FRACTAL_STRATEGY_ID",
    "FractalDirectional",
    "FractalPlan",
    "FractalTranche",
    "INITIAL_SL_PCT",
    "RUNNER_QTY_PCT",
    "RUNNER_TRAIL_ATR_MULT",
    "TP1_QTY_PCT",
    "TP1_TARGET_PCT",
    "TP2_QTY_PCT",
    "TP2_TARGET_PCT",
    "TP3_QTY_PCT",
    "TP3_TARGET_PCT",
    "TTL_MINUTE",
    "build_tranches",
    "initial_sl_distance",
    "plan_fractal",
    "utc_minute",
]
