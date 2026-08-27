"""
=========================================================
Datei:      app/backtest/BacktestEngine.py
Zweck:      OHLC-Backtester (Replay, Fees, Slippage, Hard-Stop, Equity-Curve,
            Summary-Metriken & regelbasierte AI-Analyse) — vektorisierte
            Indikator-Präkalkulation für tausende GA-WFO-Läufe
Knoten:     Jaune (Carrera-Engine) / Quant
=========================================================
Antwort-Shape = src/types.ts BacktestResult (Frontend-Vertrag).
"""
from __future__ import annotations

import logging
import math
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger("app.backtest.engine")


# ------------------------------------------------------------------ indicators
def _sma_series(closes: List[float], period: int) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(closes)
    if period <= 0:
        return out
    s = 0.0
    for i, v in enumerate(closes):
        s += v
        if i >= period:
            s -= closes[i - period]
        if i >= period - 1:
            out[i] = s / period
    return out


def _ema_series(closes: List[float], period: int) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(closes)
    if period <= 0 or len(closes) < period:
        return out
    k = 2.0 / (period + 1.0)
    seed = sum(closes[:period]) / period
    out[period - 1] = seed
    for i in range(period, len(closes)):
        out[i] = out[i - 1] + k * (closes[i] - out[i - 1])  # type: ignore[operator]
    return out


def _rsi_series(closes: List[float], period: int) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(closes)
    if len(closes) < period + 1:
        return out
    gains = 0.0
    losses = 0.0
    avg_gain = 0.0
    avg_loss = 0.0
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        g, l = max(d, 0.0), max(-d, 0.0)
        if i <= period:
            gains += g
            losses += l
            if i == period:
                avg_gain = gains / period
                avg_loss = losses / period
                out[i] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1 + avg_gain / avg_loss)
        else:
            avg_gain = (avg_gain * (period - 1) + g) / period
            avg_loss = (avg_loss * (period - 1) + l) / period
            out[i] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1 + avg_gain / avg_loss)
    return out


def _precompute(closes: List[float], params: Dict[str, Any]) -> Dict[str, Any]:
    """Einmalige Präkalkulation aller Archetyp-Indikatoren (GA-tauglich)."""
    archetype = str(params.get("archetype") or "sma_cross").lower()
    p: Dict[str, Any] = {"archetype": archetype}
    if archetype == "sma_cross":
        fast_p = int(params.get("smaFast", 12))
        slow_p = int(params.get("smaSlow", 48))
        p["fast"] = _sma_series(closes, fast_p)
        p["slow"] = _sma_series(closes, slow_p)
    elif archetype == "rsi_reversion":
        p["rsi"] = _rsi_series(closes, int(params.get("rsiPeriod", 14)))
        p["rsiLower"] = float(params.get("rsiLower", 32))
        p["rsiUpper"] = float(params.get("rsiUpper", 68))
    else:
        p["fast"] = _ema_series(closes, int(params.get("trendFastEma", 12)))
        p["slow"] = _ema_series(closes, int(params.get("trendSlowEma", 60)))
    return p


def _signal_at_precomputed(pre: Dict[str, Any], i: int) -> Optional[str]:
    """Return 'LONG' | 'SHORT' | None für Bar i (nur auf Bar-Schluss)."""
    a = pre["archetype"]
    if a == "sma_cross":
        f, s = pre["fast"], pre["slow"]
        if i < 2:
            return None
        f1, f0, s1, s0 = f[i], f[i - 1], s[i], s[i - 1]
        if None in (f1, f0, s1, s0):
            return None
        if f0 <= s0 and f1 > s1:
            return "LONG"
        if f0 >= s0 and f1 < s1:
            return "SHORT"
        return None
    if a == "rsi_reversion":
        rsi = pre["rsi"][i]
        if rsi is None:
            return None
        if rsi < pre["rsiLower"]:
            return "LONG"
        if rsi > pre["rsiUpper"]:
            return "SHORT"
        return None
    f, s = pre["fast"], pre["slow"]
    if i < 2:
        return None
    f1, f0, s1, s0 = f[i], f[i - 1], s[i], s[i - 1]
    if None in (f1, f0, s1, s0):
        return None
    if f1 > s1 and f0 <= s0:
        return "LONG"
    if f1 < s1 and f0 >= s0:
        return "SHORT"
    return None


# ------------------------------------------------------------------- backtest
def run_backtest(candles: List[Dict[str, Any]], config: Dict[str, Any]) -> Dict[str, Any]:
    """REMOVED in Sigma — use TvMcpBacktest (TradingView MCP CSV seam)."""
    raise RuntimeError(
        "Local BacktestEngine.run_backtest is disabled in Projekt:Sigma. "
        "Use app.backtest.TvMcpBacktest.run_backtest (TradingView MCP + CSV)."
    )


def _legacy_run_backtest_removed(candles: List[Dict[str, Any]], config: Dict[str, Any]) -> Dict[str, Any]:
    """Legacy body retained only as reference; unreachable."""
    initial_balance = float(config.get("initialBalance") or 10_000.0)
    fee_pct = float(config.get("feePercent") or 0.26) / 100.0
    slippage_pct = float(config.get("slippagePercent") or 0.05) / 100.0
    hard_stop_enabled = bool(config.get("hardStopEnabled", True))
    hard_stop_pct = float(config.get("hardStopPercent") or 5.0) / 100.0
    params = config.get("customParameters") or {}

    closes = [float(c["close"]) for c in candles]
    pre = _precompute(closes, params) if candles else {"archetype": "sma_cross"}

    cash = initial_balance
    position: Optional[Dict[str, Any]] = None
    trades: List[Dict[str, Any]] = []
    equity_curve: List[Dict[str, Any]] = []
    total_fees = 0.0
    peak_equity = initial_balance
    max_dd = 0.0
    max_dd_usd = 0.0
    holds: List[int] = []
    first_close = closes[0] if closes else 0.0
    n = len(candles)

    for i, c in enumerate(candles):
        close = closes[i]
        ts = str(c["ts"])

        if position is not None and i > position["entry_i"]:
            # Trend-Ausstieg: Signal gegen offene Position schließt sofort
            opposite = _signal_at_precomputed(pre, i)
            if opposite and opposite != position["direction"]:
                position["opposite_exit"] = True
        if position is not None and i > position["entry_i"] and position.get("opposite_exit"):
            direction = position["direction"]
            lo = float(c["low"])
            hi = float(c["high"])
            hit_stop = (direction == "LONG" and lo <= position["stop"]) or \
                       (direction == "SHORT" and hi >= position["stop"])
            hit_tp = (direction == "LONG" and hi >= position["tp"]) or \
                     (direction == "SHORT" and lo <= position["tp"])
            exit_price = position["stop"] if hit_stop else (position["tp"] if hit_tp else close)
            exit_price *= (1 - slippage_pct) if direction == "LONG" else (1 + slippage_pct)
            exit_notional = position["qty"] * exit_price
            fee = exit_notional * fee_pct
            total_fees += fee
            if direction == "LONG":
                cash += exit_notional - fee
            else:
                pnl = (position["entry_price"] - exit_price) * position["qty"]
                cash += position["cost"] + pnl - fee
            pnl = (exit_price - position["entry_price"]) * position["qty"] if direction == "LONG" \
                else (position["entry_price"] - exit_price) * position["qty"]
            trades.append({
                "id": f"bt_{uuid.uuid4().hex[:8]}",
                "type": "buy" if direction == "LONG" else "sell",
                "entryTime": position["entry_ts"],
                "exitTime": ts,
                "entryPrice": round(position["entry_price"], 6),
                "exitPrice": round(exit_price, 6),
                "amount": round(position["qty"], 8),
                "totalValue": round(position["cost"], 2),
                "fee": round(fee, 4),
                "pnl": round(pnl, 4),
                "pnlPercent": round(pnl / max(position["cost"], 1e-9) * 100.0, 4),
                "reason": "opposite-signal",
                "status": "closed",
            })
            holds.append(i - position["entry_i"])
            position = None

        if position is None and i >= 60:
            direction = _signal_at_precomputed(pre, i)
            if direction and cash > 10.0:
                slip = close * (1 + slippage_pct) if direction == "LONG" else close * (1 - slippage_pct)
                qty = (cash * 0.95) / slip
                cost = qty * slip
                fee = cost * fee_pct
                cash -= cost + fee
                total_fees += fee
                stop = slip * (1 - hard_stop_pct) if direction == "LONG" else slip * (1 + hard_stop_pct)
                tp = slip * (1 + hard_stop_pct * 2.2) if direction == "LONG" else slip * (1 - hard_stop_pct * 2.2)
                position = {
                    "entry_i": i, "entry_ts": ts, "entry_price": slip,
                    "direction": direction, "qty": qty, "cost": cost,
                    "stop": stop, "tp": tp,
                }
        elif position is not None:
            direction = position["direction"]
            lo = float(c["low"])
            hi = float(c["high"])
            hit_stop = (direction == "LONG" and lo <= position["stop"]) or \
                       (direction == "SHORT" and hi >= position["stop"])
            hit_tp = (direction == "LONG" and hi >= position["tp"]) or \
                     (direction == "SHORT" and lo <= position["tp"])
            if (hard_stop_enabled and hit_stop) or hit_tp or i == n - 1:
                exit_price = position["stop"] if hit_stop else (position["tp"] if hit_tp else close)
                exit_price *= (1 - slippage_pct) if direction == "LONG" else (1 + slippage_pct)
                exit_notional = position["qty"] * exit_price
                fee = exit_notional * fee_pct
                total_fees += fee
                if direction == "LONG":
                    cash += exit_notional - fee
                else:
                    pnl = (position["entry_price"] - exit_price) * position["qty"]
                    cash += position["cost"] + pnl - fee
                pnl = (exit_price - position["entry_price"]) * position["qty"] if direction == "LONG" \
                    else (position["entry_price"] - exit_price) * position["qty"]
                reason = "stop-loss" if (hard_stop_enabled and hit_stop) else \
                         ("take-profit" if hit_tp else "end-of-data")
                trades.append({
                    "id": f"bt_{uuid.uuid4().hex[:8]}",
                    "type": "buy" if direction == "LONG" else "sell",
                    "entryTime": position["entry_ts"],
                    "exitTime": ts,
                    "entryPrice": round(position["entry_price"], 6),
                    "exitPrice": round(exit_price, 6),
                    "amount": round(position["qty"], 8),
                    "totalValue": round(position["cost"], 2),
                    "fee": round(fee, 4),
                    "pnl": round(pnl, 4),
                    "pnlPercent": round(pnl / max(position["cost"], 1e-9) * 100.0, 4),
                    "reason": reason,
                    "status": "closed",
                })
                holds.append(i - position["entry_i"])
                position = None

        if position is not None:
            if position["direction"] == "LONG":
                equity = max(0.0, cash + position["qty"] * close)
            else:
                pos_pnl = (position["entry_price"] - close) * position["qty"]
                equity = max(0.0, cash + position["cost"] + pos_pnl)
        else:
            equity = max(0.0, cash)
        peak_equity = max(peak_equity, equity)
        dd = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0.0
        max_dd = max(max_dd, dd)
        max_dd_usd = max(max_dd_usd, peak_equity - equity)
        benchmark = initial_balance * (close / first_close if first_close else 1.0)
        equity_curve.append({
            "timestamp": ts,
            "time": ts,
            "price": round(close, 6),
            "equity": round(equity, 4),
            "benchmarkEquity": round(benchmark, 4),
            "drawdown": round(dd * 100.0, 4),
            "cash": round(cash, 4),
            "assetHoldings": round(equity - cash, 4),
        })

    summary = _summary(initial_balance, equity_curve, trades, total_fees,
                       max_dd, max_dd_usd, holds)
    return {
        "id": f"bt_{uuid.uuid4().hex[:10]}",
        "strategyId": config.get("strategyId"),
        "strategyName": config.get("strategyName"),
        "assetPair": config.get("assetPair"),
        "interval": config.get("interval"),
        "periodLabel": f"{len(candles)} candles",
        "startTime": candles[0]["ts"] if candles else None,
        "endTime": candles[-1]["ts"] if candles else None,
        "totalCandles": len(candles),
        "summary": summary,
        "equityCurve": equity_curve,
        "trades": trades,
        "aiAnalysis": _ai_analysis(summary, equity_curve, trades),
    }


# -------------------------------------------------------------------- resample
def resample_candles(candles_1m: List[Dict[str, Any]], factor: int) -> List[Dict[str, Any]]:
    """1m-Candles zu N-min-Candles zusammenfassen (DuckDB-Resample-Proxy)."""
    if factor <= 1:
        return candles_1m
    out = []
    for i in range(0, len(candles_1m) - factor + 1, factor):
        chunk = candles_1m[i: i + factor]
        out.append({
            "ts": chunk[-1]["ts"],
            "open": chunk[0]["open"],
            "high": max(c["high"] for c in chunk),
            "low": min(c["low"] for c in chunk),
            "close": chunk[-1]["close"],
            "volume": sum(c["volume"] for c in chunk),
        })
    return out


# -------------------------------------------------------------------- summary
def _summary(initial_balance: float, equity_curve: List[Dict[str, Any]],
             trades: List[Dict[str, Any]], total_fees: float,
             max_dd: float, max_dd_usd: float, holds: List[int]) -> Dict[str, Any]:
    final = equity_curve[-1]["equity"] if equity_curve else initial_balance
    ret_usd = final - initial_balance
    ret_pct = ret_usd / initial_balance * 100.0 if initial_balance else 0.0
    benchmark_pct = 0.0
    if equity_curve:
        last = equity_curve[-1]
        benchmark_pct = (last["benchmarkEquity"] - initial_balance) / initial_balance * 100.0

    returns = []
    prev = initial_balance
    for p in equity_curve:
        if prev > 0:
            returns.append(p["equity"] / prev - 1.0)
        prev = p["equity"]
    sharpe = _sharpe(returns, periods_per_year=365 * 24 * 4)
    sortino = _sortino(returns, periods_per_year=365 * 24 * 4)

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    pf = gross_win / gross_loss if gross_loss > 0 else (9999.0 if gross_win > 0 else 0.0)

    return {
        "initialBalance": initial_balance,
        "finalBalance": round(final, 2),
        "totalReturnUSD": round(ret_usd, 2),
        "totalReturnPercent": round(ret_pct, 4),
        "benchmarkReturnPercent": round(benchmark_pct, 4),
        "alpha": round(ret_pct - benchmark_pct, 4),
        "maxDrawdownPercent": round(max_dd * 100.0, 4),
        "maxDrawdownUSD": round(max_dd_usd, 2),
        "sharpeRatio": round(sharpe, 4),
        "sortinoRatio": round(sortino, 4),
        "profitFactor": round(min(pf, 999.0), 4),
        "winRate": round(len(wins) / len(trades) * 100.0, 2) if trades else 0.0,
        "totalTrades": len(trades),
        "winningTrades": len(wins),
        "losingTrades": len(losses),
        "averageTradeReturn": round(sum(t["pnlPercent"] for t in trades) / len(trades), 4) if trades else 0.0,
        "bestTradeUSD": round(max((t["pnl"] for t in trades), default=0.0), 2),
        "worstTradeUSD": round(min((t["pnl"] for t in trades), default=0.0), 2),
        "avgHoldCandles": round(sum(holds) / len(holds), 2) if holds else 0.0,
        "totalFeesPaid": round(total_fees, 2),
    }


def _sharpe(returns: List[float], periods_per_year: float) -> float:
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    sd = math.sqrt(var)
    if sd == 0:
        return 0.0
    return mean / sd * math.sqrt(periods_per_year)


def _sortino(returns: List[float], periods_per_year: float) -> float:
    neg = [r for r in returns if r < 0]
    if len(returns) < 2 or not neg:
        return _sharpe(returns, periods_per_year)
    mean = sum(returns) / len(returns)
    dvar = sum(r ** 2 for r in neg) / len(returns)
    dsd = math.sqrt(dvar)
    if dsd == 0:
        return 0.0
    return mean / dsd * math.sqrt(periods_per_year)


def _ai_analysis(summary: Dict[str, Any], equity_curve: List[Dict[str, Any]],
                 trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    score = 50.0
    score += min(20, max(-10, summary["totalReturnPercent"] * 2))
    score += min(15, max(-15, summary["sharpeRatio"] * 4))
    score -= min(15, summary["maxDrawdownPercent"] * 0.8)
    score += min(10, (summary["winRate"] - 45) * 0.3)
    score = max(0.0, min(100.0, score))
    if score >= 75:
        verdict = "Exceptional"
    elif score >= 55:
        verdict = "Viable"
    elif score >= 35:
        verdict = "Needs Optimization"
    else:
        verdict = "High Risk"
    stopouts = [t for t in trades if t["reason"] == "stop-loss"]
    tps = [t for t in trades if t["reason"] == "take-profit"]
    tweaks = []
    if summary["maxDrawdownPercent"] > 8:
        tweaks.append("Hard-Stop auf 3.5% verschärfen, um Drawdown-Schwänze zu kürzen.")
    if len(stopouts) > len(tps):
        tweaks.append("ATR-Stop-Multiplikator erhöhen (1.5 → 2.2) gegen Churn-Fehlauslösungen.")
    if summary["totalTrades"] < 10:
        tweaks.append("Filter weiter lockern — Trade-Starvation riskiert (N < 30).")
    if not tweaks:
        tweaks.append("Parameter sind im Zielkorridor — WFO-Gate vor Shadow-Deployment prüfen.")
    return {
        "score": round(score, 1),
        "verdict": verdict,
        "executiveSummary": (
            f"{summary['totalTrades']} Trades, {summary['totalReturnPercent']:+.2f}% "
            f"vs. Benchmark {summary['benchmarkReturnPercent']:+.2f}% (Alpha "
            f"{summary['alpha']:+.2f}%), MaxDD {summary['maxDrawdownPercent']:.2f}%, "
            f"PF {summary['profitFactor']:.2f}, WinRate {summary['winRate']:.0f}%."
        ),
        "regimePerformance": {
            "trendingUp": f"{len(tps)} Take-Profit-Abschlüsse in Trendphasen",
            "trendingDown": f"{len(stopouts)} Stop-outs gegen Trend",
            "choppyRange": f"{summary['totalTrades'] - len(tps) - len(stopouts)} Range/End-of-Data-Ausgänge",
        },
        "drawdownDiagnosis": (
            "Drawdown konzentriert sich auf Stop-out-Ketten nach Regimewechsel."
            if len(stopouts) > 3 else
            "Drawdown im tolerablen Korridor, keine Cluster-Muster erkennbar."
        ),
        "recommendedTweaks": tweaks,
    }
