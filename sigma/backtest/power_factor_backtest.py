"""
=========================================================
Datei:      sigma/backtest/power_factor_backtest.py
Zweck:      MP-16 cos-phi-Pfad-Backtester (Kaufman Efficiency
            Ratio, MP-04): Hysterese-State-Machine
            (+0,40 Long / -0,40 Short / |cos|<=0,15 Exit),
            1-Bar-Lag, Gebühren (0,06 % Roundtrip), Equity,
            Return, Max-DD, annualisierter Sharpe (8.760
            Perioden), Win-Rate, Profit-Faktor, Trades.
            Reine Berechnung auf geschlossenen Bars — keine
            Orders, kein Orchestrator-Kontext, kein Netz.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Backtest)
=========================================================
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

from sigma.signals.power_triangle import cos_phi_path

EPS = 1e-12
ANNUAL_PERIODS_1H = 8760.0
DEFAULT_WINDOW = 20
DEFAULT_LONG_THRESHOLD = 0.40
DEFAULT_SHORT_THRESHOLD = -0.40
DEFAULT_EXIT_THRESHOLD = 0.15
DEFAULT_FEE_ROUNDTRIP = 0.0006  # 0,06 % Roundtrip (je 0,03 % Entry/Exit)


@dataclass(frozen=True)
class PowerFactorParams:
    window: int = DEFAULT_WINDOW
    long_threshold: float = DEFAULT_LONG_THRESHOLD
    short_threshold: float = DEFAULT_SHORT_THRESHOLD
    exit_threshold: float = DEFAULT_EXIT_THRESHOLD
    fee_roundtrip: float = DEFAULT_FEE_ROUNDTRIP
    annual_periods: float = ANNUAL_PERIODS_1H

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class PowerFactorTrade:
    entry_ts: float
    exit_ts: float
    side: str  # "long" | "short"
    pnl_pct: float

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class PowerFactorResult:
    params: PowerFactorParams
    cos_phi: List[float]
    positions: List[int]          # wirksame Position je Bar (1-Bar-Lag)
    labels: List[str]             # entry_long/entry_short/exit/hold_*/flat
    returns: List[float]          # Nettorenditen je Bar (Position*Bewegung - Kosten)
    equity: List[float]
    trades: List[PowerFactorTrade] = field(default_factory=list)
    total_return: float = 0.0
    max_drawdown: float = 0.0
    sharpe: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    trade_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "params": self.params.to_dict(),
            "cos_phi": self.cos_phi,
            "positions": self.positions,
            "labels": self.labels,
            "returns": self.returns,
            "equity": self.equity,
            "trades": [t.to_dict() for t in self.trades],
            "total_return": self.total_return,
            "max_drawdown": self.max_drawdown,
            "sharpe": self.sharpe,
            "win_rate": self.win_rate,
            "profit_factor": self.profit_factor,
            "trade_count": self.trade_count,
        }


def _closed(candles: Sequence[Mapping[str, Any]]) -> list:
    rows = list(candles or [])
    if rows and rows[-1].get("is_closed", rows[-1].get("closed")) is False:
        return rows[:-1]
    return rows


def _closes(candles: Sequence[Mapping[str, Any]]) -> List[float]:
    out: List[float] = []
    for c in candles:
        px = c.get("c", c.get("close"))
        try:
            v = float(px)
        except (TypeError, ValueError):
            v = 0.0
        if v <= 0:
            raise ValueError("Backtest-Serie enthält ungültigen Close-Wert")
        out.append(v)
    return out


def cos_phi_series(closes: Sequence[float], window: int) -> List[float]:
    """cos_phi_path je Bar über das rollierende Fenster; die ersten
    `window` Bars haben keinen vollständigen Pfad -> 0.0 (neutral)."""
    win = max(2, int(window))
    out: List[float] = []
    closes = list(closes)
    for i in range(len(closes)):
        seg = closes[max(0, i - win): i + 1]
        out.append(cos_phi_path(seg, window=win) if len(seg) > win else 0.0)
    return out


def run_power_factor_backtest(
    candles: Sequence[Mapping[str, Any]],
    *,
    window: int = DEFAULT_WINDOW,
    long_threshold: float = DEFAULT_LONG_THRESHOLD,
    short_threshold: float = DEFAULT_SHORT_THRESHOLD,
    exit_threshold: float = DEFAULT_EXIT_THRESHOLD,
    fee_roundtrip: float = DEFAULT_FEE_ROUNDTRIP,
    annual_periods: float = ANNUAL_PERIODS_1H,
) -> PowerFactorResult:
    """Vektorisierter cos-phi-Pfad-Backtest (reine Listen-Arithmetik,
    konsistent zu MP-12; kein VectorBT). Nur geschlossene Bars.
    Position wirkt erst 1 Bar nach dem Signal (kein Look-ahead)."""
    bars = _closed(candles)
    if not bars:
        raise ValueError("keine geschlossenen Bars")
    closes = _closes(bars)
    n = len(closes)
    params = PowerFactorParams(
        window=window,
        long_threshold=float(long_threshold),
        short_threshold=float(short_threshold),
        exit_threshold=float(exit_threshold),
        fee_roundtrip=float(fee_roundtrip),
        annual_periods=float(annual_periods),
    )
    if not (0.0 < params.exit_threshold < params.long_threshold):
        raise ValueError("Hysterese: 0 < exit_threshold < long_threshold")
    if params.short_threshold >= -params.exit_threshold:
        raise ValueError("Hysterese: short_threshold < -exit_threshold")

    cos = cos_phi_series(closes, params.window)
    half_fee = params.fee_roundtrip / 2.0

    # Signal je Bar (aus geschlossener Bar i)
    signals: List[int] = [0] * n
    state = 0
    for i in range(n):
        c = cos[i]
        if state == 0:
            if c >= params.long_threshold:
                signals[i] = 1
                state = 1
            elif c <= params.short_threshold:
                signals[i] = -1
                state = -1
        elif abs(c) <= params.exit_threshold:
            signals[i] = 0
            state = 0
        else:
            signals[i] = state

    # 1-Bar-Lag: wirksame Position ab der Folgebarb
    positions = [0] * n
    for i in range(1, n):
        positions[i] = signals[i - 1]

    # Nettorenditen
    returns: List[float] = [0.0] * n
    prev_pos = 0
    entry_idx: Optional[int] = None
    entry_side = ""
    for i in range(1, n):
        p = positions[i]
        gross = p * (closes[i] / closes[i - 1] - 1.0)
        cost = 0.0
        if p != prev_pos:
            if p != 0:
                cost = half_fee  # Entry
                entry_idx = i
                entry_side = "long" if p > 0 else "short"
            elif entry_idx is not None:
                cost = half_fee  # Exit
                if entry_idx == i:
                    entry_idx = None  # Entry+Exit auf derselben Bar (degeneriert)
        returns[i] = gross - cost
        prev_pos = p

    # Offene Position am Serienende: Zwangs-Close (Exit-Hälfte der Fee)
    if n > 1 and positions[-1] != 0:
        returns[-1] -= half_fee

    # Trades: Entry/Exit-Paare (+ Zwangs-Close am Ende)
    trades: List[PowerFactorTrade] = []
    entry_ts: Optional[float] = None
    entry_px: Optional[float] = None
    side = ""
    for i in range(1, n):
        if positions[i] != 0 and positions[i - 1] == 0:
            entry_ts = float(bars[i]["ts"])
            entry_px = closes[i]
            side = "long" if positions[i] > 0 else "short"
        elif positions[i] == 0 and positions[i - 1] != 0 and entry_ts is not None:
            pnl = (closes[i] - entry_px) / entry_px * (1.0 if side == "long" else -1.0) - params.fee_roundtrip
            trades.append(PowerFactorTrade(entry_ts=entry_ts, exit_ts=float(bars[i]["ts"]),
                                           side=side, pnl_pct=pnl * 100.0))
            entry_ts = None
    if positions[-1] != 0 and entry_ts is not None:
        pnl = (closes[-1] - entry_px) / entry_px * (1.0 if side == "long" else -1.0) - params.fee_roundtrip
        trades.append(PowerFactorTrade(entry_ts=entry_ts, exit_ts=float(bars[-1]["ts"]),
                                       side=side, pnl_pct=pnl * 100.0))

    # Equity
    equity = [1.0]
    for r in returns:
        equity.append(equity[-1] * (1.0 + r))
    equity = equity[1:]

    total_return = equity[-1] - 1.0 if equity else 0.0
    max_dd = _max_drawdown(equity)
    sharpe = _sharpe(returns, params.annual_periods)
    wins = [t.pnl_pct for t in trades if t.pnl_pct > 0]
    losses = [t.pnl_pct for t in trades if t.pnl_pct < 0]
    win_rate = len(wins) / len(trades) if trades else 0.0
    if trades:
        if losses and sum(losses) != 0:
            pf = sum(wins) / abs(sum(losses))
        elif wins:
            pf = 9.99  # kein Verlust -> Deckel (JSON-sicher)
        else:
            pf = 0.0
    else:
        pf = 0.0

    # Labels je Bar
    labels: List[str] = []
    for i in range(n):
        if positions[i] != 0 and (i == 0 or positions[i - 1] == 0):
            labels.append("entry_long" if positions[i] > 0 else "entry_short")
        elif positions[i] == 0 and i > 0 and positions[i - 1] != 0:
            labels.append("exit")
        elif positions[i] > 0:
            labels.append("hold_long")
        elif positions[i] < 0:
            labels.append("hold_short")
        else:
            labels.append("flat")

    return PowerFactorResult(
        params=params,
        cos_phi=cos,
        positions=positions,
        labels=labels,
        returns=[round(x, 10) for x in returns],
        equity=[round(x, 10) for x in equity],
        trades=trades,
        total_return=round(total_return, 10),
        max_drawdown=round(max_dd, 10),
        sharpe=round(sharpe, 6),
        win_rate=round(win_rate, 6),
        profit_factor=round(pf, 6),
        trade_count=len(trades),
    )


def _max_drawdown(equity: Sequence[float]) -> float:
    peak = -1e18
    dd = 0.0
    for e in equity:
        peak = max(peak, e)
        dd = max(dd, peak - e)
    return dd


def _sharpe(returns: Sequence[float], annual_periods: float) -> float:
    vals = [float(r) for r in returns]
    if len(vals) < 2:
        return 0.0
    mean = sum(vals) / len(vals)
    var = sum((r - mean) ** 2 for r in vals) / (len(vals) - 1)
    if var <= EPS:
        return 0.0
    return mean / math.sqrt(var) * math.sqrt(annual_periods)
