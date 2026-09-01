"""
TradingView CSV interchange — canonical seam for Sigma backtesting.

Parameter CSVs map to Pine strategy inputs / GA gene space.
Result CSVs (list-of-trades ± performance summary) map to BacktestResult.
"""
from __future__ import annotations

import csv
import hashlib
import io
import math
import re
import uuid
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

PathOrText = Union[str, bytes]


# --------------------------------------------------------------------------- aliases
_PARAM_NAME_ALIASES = {
    "parameter": "name",
    "param": "name",
    "name": "name",
    "input": "name",
    "key": "name",
    "value": "value",
    "val": "value",
}

_TRADE_COL_ALIASES = {
    "trade #": "trade_num",
    "trade#": "trade_num",
    "trade": "trade_num",
    "#": "trade_num",
    "type": "type",
    "signal": "signal",
    "date/time": "datetime",
    "date time": "datetime",
    "datetime": "datetime",
    "time": "datetime",
    "date": "datetime",
    "price usd": "price",
    "price usdt": "price",
    "price": "price",
    "position size (qty)": "qty",
    "qty": "qty",
    "quantity": "qty",
    "contracts": "qty",
    "position size (value)": "value",
    "value": "value",
    "net p&l usd": "pnl",
    "net p&l usdt": "pnl",
    "net pnl": "pnl",
    "profit usd": "pnl",
    "pnl": "pnl",
    "net p&l %": "pnl_pct",
    "net pnl %": "pnl_pct",
    "pnl %": "pnl_pct",
    "profit %": "pnl_pct",
    "cumulative p&l usd": "cum_pnl",
    "cumulative p&l usdt": "cum_pnl",
    "cum pnl": "cum_pnl",
    "cumulative p&l %": "cum_pnl_pct",
    "fee": "fee",
    "commission": "fee",
    "run-up usd": "runup",
    "drawdown usd": "drawdown",
}

_PERF_ALIASES = {
    "net profit": "totalReturnUSD",
    "net profit %": "totalReturnPercent",
    "total net profit": "totalReturnUSD",
    "gross profit": "grossProfit",
    "gross loss": "grossLoss",
    "max drawdown": "maxDrawdownPercent",
    "max drawdown %": "maxDrawdownPercent",
    "max equity drawdown %": "maxDrawdownPercent",
    "sharpe ratio": "sharpeRatio",
    "sortino ratio": "sortinoRatio",
    "profit factor": "profitFactor",
    "percent profitable": "winRate",
    "win rate": "winRate",
    "total closed trades": "totalTrades",
    "total trades": "totalTrades",
    "# of winning trades": "winningTrades",
    "winning trades": "winningTrades",
    "# of losing trades": "losingTrades",
    "losing trades": "losingTrades",
    "avg trade": "averageTradeReturn",
    "average trade": "averageTradeReturn",
    "largest winning trade": "bestTradeUSD",
    "largest losing trade": "worstTradeUSD",
    "commission paid": "totalFeesPaid",
    "initial capital": "initialBalance",
    "buy & hold return %": "benchmarkReturnPercent",
}


def _norm_header(h: str) -> str:
    return re.sub(r"\s+", " ", (h or "").strip().lower())


def _read_text(src: PathOrText) -> str:
    if isinstance(src, bytes):
        return src.decode("utf-8-sig")
    text = str(src)
    # Heuristic: path vs inline CSV
    if "\n" not in text and len(text) < 512 and not text.strip().lower().startswith("parameter"):
        try:
            with open(text, "r", encoding="utf-8-sig") as f:
                return f.read()
        except OSError:
            pass
    return text


def _parse_number(raw: Any, default: float = 0.0) -> float:
    if raw is None:
        return default
    s = str(raw).strip().replace(",", "").replace("%", "").replace("$", "")
    if not s or s in ("—", "-", "N/A", "n/a"):
        return default
    try:
        return float(s)
    except ValueError:
        return default


def _coerce_value(raw: str) -> Any:
    s = (raw or "").strip()
    if s.lower() in ("true", "yes", "on"):
        return 1
    if s.lower() in ("false", "no", "off"):
        return 0
    try:
        if "." in s or "e" in s.lower():
            return float(s)
        return int(s)
    except ValueError:
        return s


# --------------------------------------------------------------------------- parameter CSV
def parse_parameter_csv(src: PathOrText) -> Dict[str, Any]:
    """Parse TV-style parameter CSV → dict of Pine inputs / genes."""
    text = _read_text(src)
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("Parameter CSV has no header row")
    fields = {_norm_header(f): f for f in reader.fieldnames}
    name_key = value_key = None
    for alias, role in _PARAM_NAME_ALIASES.items():
        if alias in fields:
            if role == "name":
                name_key = fields[alias]
            elif role == "value":
                value_key = fields[alias]
    if not name_key or not value_key:
        # Fallback: first two columns
        cols = list(reader.fieldnames)
        if len(cols) < 2:
            raise ValueError("Parameter CSV needs name + value columns")
        name_key, value_key = cols[0], cols[1]
        # reset reader after consuming fieldnames only
        reader = csv.DictReader(io.StringIO(text))
    out: Dict[str, Any] = {}
    for row in reader:
        name = str(row.get(name_key) or "").strip()
        if not name:
            continue
        out[name] = _coerce_value(str(row.get(value_key) or ""))
    return out


def params_to_csv(params: Mapping[str, Any]) -> str:
    """Serialize params/genes to TradingView-compatible parameter CSV."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Parameter", "Value"])
    for k in sorted(params.keys()):
        v = params[k]
        if isinstance(v, bool):
            v = int(v)
        w.writerow([k, v])
    return buf.getvalue()


def genes_from_parameter_csv(src: PathOrText, gene_ranges: Optional[Mapping[str, tuple]] = None) -> Dict[str, Any]:
    """Map parameter CSV into GA gene dict (intersect known gene names when provided)."""
    raw = parse_parameter_csv(src)
    if not gene_ranges:
        return raw
    out: Dict[str, Any] = {}
    for k, v in raw.items():
        if k in gene_ranges:
            out[k] = v
    return out


# --------------------------------------------------------------------------- result CSV
def _map_trade_row(row: Mapping[str, Any], fieldmap: Dict[str, str]) -> Dict[str, Any]:
    mapped = {}
    for canon, col in fieldmap.items():
        mapped[canon] = row.get(col)
    return mapped


def parse_trades_csv(src: PathOrText) -> List[Dict[str, Any]]:
    """Parse TV List-of-Trades CSV into normalized trade event rows."""
    text = _read_text(src)
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("Trades CSV has no header row")
    fieldmap: Dict[str, str] = {}
    for f in reader.fieldnames:
        alias = _TRADE_COL_ALIASES.get(_norm_header(f))
        if alias and alias not in fieldmap:
            fieldmap[alias] = f
    events: List[Dict[str, Any]] = []
    for row in reader:
        m = _map_trade_row(row, fieldmap)
        typ = str(m.get("type") or m.get("signal") or "").strip().lower()
        if not typ and not m.get("price"):
            continue
        events.append({
            "trade_num": str(m.get("trade_num") or "").strip(),
            "type": typ,
            "signal": str(m.get("signal") or "").strip().lower(),
            "datetime": str(m.get("datetime") or "").strip(),
            "price": _parse_number(m.get("price")),
            "qty": _parse_number(m.get("qty"), 0.0),
            "value": _parse_number(m.get("value"), 0.0),
            "pnl": _parse_number(m.get("pnl"), 0.0),
            "pnl_pct": _parse_number(m.get("pnl_pct"), 0.0),
            "cum_pnl": _parse_number(m.get("cum_pnl"), 0.0),
            "fee": _parse_number(m.get("fee"), 0.0),
        })
    return events


def parse_performance_csv(src: PathOrText) -> Dict[str, float]:
    """Parse optional TV performance-summary CSV → partial BacktestSummary fields."""
    text = _read_text(src)
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return {}
    out: Dict[str, float] = {}
    # Detect header
    start = 0
    if len(rows[0]) >= 2 and _norm_header(rows[0][0]) in ("metric", "name", "performance", "parameter"):
        start = 1
    for row in rows[start:]:
        if len(row) < 2:
            continue
        key = _PERF_ALIASES.get(_norm_header(row[0]))
        if not key:
            continue
        out[key] = _parse_number(row[1])
    return out


def _pair_trades(events: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Pair entry/exit events into closed BacktestTrade objects."""
    trades: List[Dict[str, Any]] = []
    open_pos: Optional[Dict[str, Any]] = None
    for ev in events:
        typ = ev["type"]
        is_entry = typ.startswith("entry") or typ in ("buy", "sell", "long", "short") and open_pos is None
        is_exit = typ.startswith("exit") or "close" in typ or typ in ("stop", "take profit", "tp", "sl")
        # TV often uses "Entry long" / "Exit long"
        if "entry" in typ:
            direction = "buy" if "long" in typ or "buy" in typ else "sell"
            open_pos = {
                "id": f"bt_{uuid.uuid4().hex[:8]}",
                "type": direction,
                "entryTime": ev["datetime"],
                "entryPrice": ev["price"],
                "amount": ev["qty"] or (ev["value"] / ev["price"] if ev["price"] else 0.0),
                "totalValue": ev["value"] or (ev["qty"] * ev["price"]),
                "fee": ev["fee"],
            }
            continue
        if open_pos and ("exit" in typ or is_exit or ev["pnl"] != 0.0):
            open_pos.update({
                "exitTime": ev["datetime"],
                "exitPrice": ev["price"],
                "fee": round(float(open_pos.get("fee") or 0.0) + ev["fee"], 4),
                "pnl": round(ev["pnl"], 4),
                "pnlPercent": round(ev["pnl_pct"], 4),
                "reason": "exit",
                "status": "closed",
            })
            if not open_pos["amount"] and open_pos["entryPrice"]:
                open_pos["amount"] = ev["qty"] or (ev["value"] / open_pos["entryPrice"])
            trades.append(open_pos)
            open_pos = None
            continue
        # Alternate: single closed-trade row with pnl already filled
        if ev["pnl"] != 0.0 and ev["datetime"] and not open_pos:
            direction = "buy" if ("long" in typ or "buy" in typ) else "sell"
            trades.append({
                "id": f"bt_{uuid.uuid4().hex[:8]}",
                "type": direction,
                "entryTime": ev["datetime"],
                "exitTime": ev["datetime"],
                "entryPrice": ev["price"],
                "exitPrice": ev["price"],
                "amount": ev["qty"],
                "totalValue": ev["value"] or (ev["qty"] * ev["price"]),
                "fee": ev["fee"],
                "pnl": round(ev["pnl"], 4),
                "pnlPercent": round(ev["pnl_pct"], 4),
                "reason": "exit",
                "status": "closed",
            })
    return trades


def _summary_from_trades(
    trades: List[Dict[str, Any]],
    initial_balance: float,
    perf: Optional[Mapping[str, float]] = None,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    perf = dict(perf or {})
    equity = initial_balance
    peak = initial_balance
    max_dd = 0.0
    max_dd_usd = 0.0
    curve: List[Dict[str, Any]] = []
    total_fees = 0.0
    for t in trades:
        equity += float(t.get("pnl") or 0.0)
        total_fees += float(t.get("fee") or 0.0)
        peak = max(peak, equity)
        dd_usd = peak - equity
        dd = dd_usd / peak if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
        max_dd_usd = max(max_dd_usd, dd_usd)
        ts = str(t.get("exitTime") or t.get("entryTime") or "")
        curve.append({
            "timestamp": ts,
            "time": ts,
            "price": float(t.get("exitPrice") or t.get("entryPrice") or 0.0),
            "equity": round(equity, 4),
            "benchmarkEquity": round(initial_balance, 4),
            "drawdown": round(dd * 100.0, 4),
            "cash": round(equity, 4),
            "assetHoldings": 0.0,
        })

    wins_count = 0
    losses_count = 0
    gross_win = 0.0
    gross_loss = 0.0
    sum_pnl_percent = 0.0
    best_trade = float('-inf')
    worst_trade = float('inf')

    for t in trades:
        pnl = float(t.get("pnl") or 0)
        pnl_pct = float(t.get("pnlPercent") or 0)
        sum_pnl_percent += pnl_pct

        if pnl > best_trade:
            best_trade = pnl
        if pnl < worst_trade:
            worst_trade = pnl

        if pnl > 0:
            wins_count += 1
            gross_win += pnl
        else:
            losses_count += 1
            gross_loss += abs(pnl)

    if best_trade == float('-inf'):
        best_trade = 0.0
    if worst_trade == float('inf'):
        worst_trade = 0.0

    pf = (gross_win / gross_loss) if gross_loss > 0 else (999.0 if gross_win > 0 else 0.0)

    returns = []
    prev = initial_balance
    for p in curve:
        if prev > 0:
            returns.append(p["equity"] / prev - 1.0)
        prev = p["equity"]
    sharpe = _sharpe(returns)
    sortino = _sortino(returns)

    final = equity
    ret_usd = final - initial_balance
    ret_pct = ret_usd / initial_balance * 100.0 if initial_balance else 0.0

    summary = {
        "initialBalance": round(float(perf.get("initialBalance", initial_balance)), 2),
        "finalBalance": round(float(perf.get("totalReturnUSD", ret_usd) + initial_balance)
                              if "totalReturnUSD" in perf and "finalBalance" not in perf
                              else float(perf.get("finalBalance", final)), 2),
        "totalReturnUSD": round(float(perf.get("totalReturnUSD", ret_usd)), 2),
        "totalReturnPercent": round(float(perf.get("totalReturnPercent", ret_pct)), 4),
        "benchmarkReturnPercent": round(float(perf.get("benchmarkReturnPercent", 0.0)), 4),
        "alpha": 0.0,
        "maxDrawdownPercent": round(float(perf.get("maxDrawdownPercent", max_dd * 100.0)), 4),
        "maxDrawdownUSD": round(float(perf.get("maxDrawdownUSD", max_dd_usd)), 2),
        "sharpeRatio": round(float(perf.get("sharpeRatio", sharpe)), 4),
        "sortinoRatio": round(float(perf.get("sortinoRatio", sortino)), 4),
        "profitFactor": round(min(float(perf.get("profitFactor", pf)), 999.0), 4),
        "winRate": round(float(perf.get("winRate", (wins_count / len(trades) * 100.0) if trades else 0.0)), 2),
        "totalTrades": int(perf.get("totalTrades", len(trades))),
        "winningTrades": int(perf.get("winningTrades", wins_count)),
        "losingTrades": int(perf.get("losingTrades", losses_count)),
        "averageTradeReturn": round(
            float(perf.get("averageTradeReturn",
                           (sum_pnl_percent / len(trades)) if trades else 0.0)),
            4),
        "bestTradeUSD": round(float(perf.get("bestTradeUSD", best_trade)), 2),
        "worstTradeUSD": round(float(perf.get("worstTradeUSD", worst_trade)), 2),
        "avgHoldCandles": round(float(perf.get("avgHoldCandles", 0.0)), 2),
        "totalFeesPaid": round(float(perf.get("totalFeesPaid", total_fees)), 2),
    }
    summary["alpha"] = round(summary["totalReturnPercent"] - summary["benchmarkReturnPercent"], 4)
    if "totalReturnUSD" in perf and "finalBalance" not in perf:
        summary["finalBalance"] = round(initial_balance + summary["totalReturnUSD"], 2)
    return summary, curve


def _sharpe(returns: List[float]) -> float:
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    sd = math.sqrt(var)
    return 0.0 if sd == 0 else mean / sd * math.sqrt(252)


def _sortino(returns: List[float]) -> float:
    neg = [r for r in returns if r < 0]
    if len(returns) < 2 or not neg:
        return _sharpe(returns)
    mean = sum(returns) / len(returns)
    dsd = math.sqrt(sum(r ** 2 for r in neg) / len(returns))
    return 0.0 if dsd == 0 else mean / dsd * math.sqrt(252)


def result_csv_to_backtest_result(
    trades_csv: PathOrText,
    *,
    config: Optional[Mapping[str, Any]] = None,
    performance_csv: Optional[PathOrText] = None,
) -> Dict[str, Any]:
    """Map TV result CSV(s) → BacktestResult dict (frontend contract)."""
    cfg = dict(config or {})
    initial = float(cfg.get("initialBalance") or 10_000.0)
    events = parse_trades_csv(trades_csv)
    trades = _pair_trades(events)
    perf = parse_performance_csv(performance_csv) if performance_csv else {}
    summary, curve = _summary_from_trades(trades, initial, perf)
    start = trades[0]["entryTime"] if trades else ""
    end = trades[-1].get("exitTime") or trades[-1].get("entryTime") if trades else ""
    return {
        "id": f"tv_{uuid.uuid4().hex[:10]}",
        "strategyId": cfg.get("strategyId") or "",
        "strategyName": cfg.get("strategyName") or "TradingView Strategy",
        "assetPair": cfg.get("assetPair") or "BTC/USD",
        "interval": int(cfg.get("interval") or 15),
        "periodLabel": f"TV-MCP CSV ({len(trades)} trades)",
        "startTime": start,
        "endTime": end,
        "totalCandles": int(cfg.get("candleCount") or cfg.get("totalCandles") or 0),
        "summary": summary,
        "equityCurve": curve,
        "trades": trades,
        "source": "tradingview-csv",
    }


def cache_key(strategy_ref: str, params: Mapping[str, Any], symbol: str,
              interval: Any, window_from: Any, window_to: Any) -> str:
    blob = params_to_csv(params) + f"|{strategy_ref}|{symbol}|{interval}|{window_from}|{window_to}"
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def synthesize_result_csv(
    params: Mapping[str, Any],
    *,
    initial_balance: float = 10_000.0,
    seed: str = "",
) -> str:
    """Deterministic fake TV trades CSV for FakeTransport / unit tests (not BacktestEngine)."""
    h = int(hashlib.md5(f"{seed}:{params_to_csv(params)}".encode()).hexdigest()[:8], 16)
    n = 8 + (h % 12)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "Trade #", "Type", "Date/Time", "Signal", "Price USD",
        "Position size (qty)", "Position size (value)",
        "Net P&L USD", "Net P&L %", "Cumulative P&L USD", "Cumulative P&L %", "Fee",
    ])
    cum = 0.0
    price = 100.0 + (h % 50)
    for i in range(1, n + 1):
        direction = "long" if (h + i) % 2 == 0 else "short"
        entry = price
        pnl = ((h % 17) - 8) * (1.0 + 0.01 * float(params.get("atrStopMultiplier") or 2.0))
        if (h + i) % 5 == 0:
            pnl = -abs(pnl)
        exit_p = entry + (pnl / 10.0 if direction == "long" else -pnl / 10.0)
        qty = 1.0
        value = entry * qty
        cum += pnl
        ts_e = f"2024-06-{(i % 28) + 1:02d} 10:00"
        ts_x = f"2024-06-{(i % 28) + 1:02d} 14:00"
        w.writerow([i, f"Entry {direction}", ts_e, direction, f"{entry:.2f}", qty, f"{value:.2f}", 0, 0, f"{cum-pnl:.2f}", 0, 0.1])
        w.writerow([i, f"Exit {direction}", ts_x, direction, f"{exit_p:.2f}", qty, f"{exit_p*qty:.2f}",
                    f"{pnl:.2f}", f"{pnl/value*100:.4f}", f"{cum:.2f}", f"{cum/initial_balance*100:.4f}", 0.1])
        price = exit_p
    return buf.getvalue()
