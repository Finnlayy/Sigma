"""
=========================================================
Datei:      app/panels/panel_engine.py
Zweck:      MP-17 Live-Panel-Engine — echte Daten statt Mocks.
            Vier Produktionsdatenverträge:
              * market_geometry  (Kraken L2 Depth / Redis Cache)
              * quantum_regime   (onnx_kelly + M8StateEngine)
              * power_physics    (DuckDB Tick-/Bar-Aggregation)
              * glint_polymarket (Polymarket Gamma + Glint-Radar)
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Ciel (Sigma Core) / Blanche (Ingestion)
=========================================================

Designregeln (L4):
  * Keine synthetischen Werte, keine Platzhalter. Wenn eine Quelle nicht
    liefert, wird ``PanelUnavailable`` geworfen -> HTTP 503 in der Route.
  * Redis ist reiner Cache/Bus (``sigma:panel:*``, ``sigma:orderbook:depth``).
    Fehlt der Cache-Eintrag, rechnet die Engine live und schreibt ihn zurueck.
  * Alle blockierenden I/O-Pfade (httpx, DuckDB, Kraken REST) laufen ueber
    ``asyncio.to_thread`` — nie auf dem Event-Loop.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.core import blueprint as bp
from app.core.config import SigmaConfig, load_config

logger = logging.getLogger("app.panels.panel_engine")

# --------------------------------------------------------------- Redis keys
KEY_DEPTH_SNAPSHOT = "sigma:orderbook:depth"
KEY_PANEL_MARKET_GEOMETRY = "sigma:panel:market_geometry"
KEY_PANEL_QUANTUM_REGIME = "sigma:panel:quantum_regime"
KEY_PANEL_POWER_PHYSICS = "sigma:panel:power_physics"
KEY_PANEL_GLINT_POLYMARKET = "sigma:panel:glint_polymarket"

# TTLs (Sekunden) — Panels sind Anzeige, nicht Ausfuehrung.
TTL_MARKET_GEOMETRY = 3
TTL_QUANTUM_REGIME = 10
TTL_POWER_PHYSICS = 5
TTL_GLINT_POLYMARKET = 60

GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"
DEPTH_LEVELS = 20            # Top-20 Bids/Asks (Datenvertrag)
PHYSICS_WINDOW_S = 300       # tick_buffer_5m


class PanelUnavailable(RuntimeError):
    """Datenquelle liefert (noch) nichts — Route antwortet fail-closed 503."""

    def __init__(self, detail: str, source: str = "unknown"):
        super().__init__(detail)
        self.detail = detail
        self.source = source


# ============================================================== helpers ====

def _now() -> float:
    return time.time()


def _round(value: Optional[float], digits: int = 6) -> Optional[float]:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return round(f, digits)


def panel_symbol(config: Optional[SigmaConfig] = None) -> str:
    cfg = config or load_config()
    symbols = tuple(getattr(cfg, "market_symbols", ()) or ())
    return symbols[0] if symbols else "BTC/USD"


async def _cache_get(redis, key: str) -> Optional[Dict[str, Any]]:
    if redis is None:
        return None
    try:
        raw = await redis.get(key)
    except Exception as exc:  # pragma: no cover - Redis-Ausfall
        logger.warning("panel cache read failed (%s): %s", key, exc)
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("panel cache corrupt for %s — recomputing", key)
        return None
    return data if isinstance(data, dict) else None


async def _cache_put(redis, key: str, payload: Dict[str, Any], ttl: int) -> None:
    if redis is None:
        return
    try:
        await redis.set(key, json.dumps(payload), ex=ttl)
    except Exception as exc:  # pragma: no cover
        logger.warning("panel cache write failed (%s): %s", key, exc)


def _fresh(payload: Optional[Dict[str, Any]], max_age_s: float) -> bool:
    if not payload:
        return False
    ts = payload.get("generated_ts")
    try:
        return (_now() - float(ts)) <= max_age_s
    except (TypeError, ValueError):
        return False


# ================================================= MP-17.1 Market Geometry =

def _levels_from(raw: Any) -> List[Tuple[float, float]]:
    out: List[Tuple[float, float]] = []
    for level in raw or []:
        if isinstance(level, dict):
            price, volume = level.get("price"), level.get("volume", level.get("size"))
        elif isinstance(level, (list, tuple)) and len(level) >= 2:
            price, volume = level[0], level[1]
        else:
            continue
        try:
            p, v = float(price), float(volume)
        except (TypeError, ValueError):
            continue
        if p > 0 and v >= 0:
            out.append((p, v))
    return out


def _vpoc(levels: Sequence[Tuple[float, float]], buckets: int = 24
          ) -> Tuple[Optional[float], List[Dict[str, float]]]:
    """Volume Point of Control + Liquiditaetscluster aus Preis-Buckets."""
    if not levels:
        return None, []
    prices = [p for p, _ in levels]
    lo, hi = min(prices), max(prices)
    if hi <= lo:
        return prices[0], [{"price": prices[0],
                            "volume": sum(v for _, v in levels), "share": 1.0}]
    width = (hi - lo) / buckets
    hist: Dict[int, float] = {}
    for price, volume in levels:
        idx = min(buckets - 1, int((price - lo) / width))
        hist[idx] = hist.get(idx, 0.0) + volume
    total = sum(hist.values()) or 1.0
    clusters = [
        {
            "price": round(lo + (idx + 0.5) * width, 8),
            "low": round(lo + idx * width, 8),
            "high": round(lo + (idx + 1) * width, 8),
            "volume": round(vol, 8),
            "share": round(vol / total, 6),
        }
        for idx, vol in sorted(hist.items(), key=lambda kv: kv[1], reverse=True)
    ]
    return clusters[0]["price"], clusters


def compute_market_geometry(snapshot: Any, *, depth_levels: int = DEPTH_LEVELS
                            ) -> Dict[str, Any]:
    """Orderbuch-Imbalance (Top-N), VPOC, Cluster, dynamische S/R."""
    from app.quant.glint_orderbook_verifier import band_volume, depth_imbalance

    bids = list(snapshot.bids)[:depth_levels]
    asks = list(snapshot.asks)[:depth_levels]
    if not bids or not asks:
        raise PanelUnavailable("orderbook snapshot empty", "kraken_live_l2")

    bid_vol = sum(float(v) for _, v in bids)
    ask_vol = sum(float(v) for _, v in asks)
    imbalance = depth_imbalance(bid_vol, ask_vol)
    mid = snapshot.mid
    band_bid = band_volume(snapshot.bids, mid, bp.DEPTH_BAND_PCT, "bid")
    band_ask = band_volume(snapshot.asks, mid, bp.DEPTH_BAND_PCT, "ask")

    vpoc, clusters = _vpoc(list(bids) + list(asks))
    support = [c for c in clusters if c["price"] < mid][:3]
    resistance = [c for c in clusters if c["price"] > mid][:3]

    return {
        "symbol": snapshot.symbol,
        "generated_ts": _now(),
        "snapshot_ts": float(snapshot.timestamp),
        "depth_levels": depth_levels,
        "best_bid": _round(snapshot.best_bid, 8),
        "best_ask": _round(snapshot.best_ask, 8),
        "mid": _round(mid, 8),
        "spread_bps": _round(snapshot.spread_bps, 4),
        "imbalance": {
            "top_n": depth_levels,
            "bid_volume": _round(bid_vol, 8),
            "ask_volume": _round(ask_vol, 8),
            "ratio": _round(imbalance, 6),
            "band_pct": bp.DEPTH_BAND_PCT,
            "band_bid_volume": _round(band_bid, 8),
            "band_ask_volume": _round(band_ask, 8),
            "band_ratio": _round(depth_imbalance(band_bid, band_ask), 6),
            "pressure": ("BID" if imbalance > bp.DEPTH_IMBALANCE_CONFIRM else
                         "ASK" if imbalance < -bp.DEPTH_IMBALANCE_CONFIRM else "BALANCED"),
        },
        "vpoc": _round(vpoc, 8),
        "liquidity_clusters": clusters[:8],
        "support_levels": [c["price"] for c in support],
        "resistance_levels": [c["price"] for c in resistance],
        "bids": [{"price": _round(p, 8), "volume": _round(v, 8)} for p, v in bids],
        "asks": [{"price": _round(p, 8), "volume": _round(v, 8)} for p, v in asks],
    }


def _snapshot_from_cache(raw: Dict[str, Any]):
    from app.quant.glint_orderbook_verifier import OrderbookSnapshot

    bids = _levels_from(raw.get("bids"))
    asks = _levels_from(raw.get("asks"))
    if not bids or not asks:
        raise PanelUnavailable("cached depth snapshot empty", "redis_depth_cache")
    ts = raw.get("timestamp", raw.get("ts"))
    try:
        ts = float(ts)
    except (TypeError, ValueError):
        raise PanelUnavailable("cached depth snapshot has no timestamp",
                               "redis_depth_cache")
    return OrderbookSnapshot(
        symbol=str(raw.get("symbol") or ""),
        bids=sorted(bids, key=lambda level: level[0], reverse=True),
        asks=sorted(asks, key=lambda level: level[0]),
        timestamp=ts,
    )


async def market_geometry(redis=None, *, symbol: Optional[str] = None,
                          config: Optional[SigmaConfig] = None) -> Dict[str, Any]:
    cfg = config or load_config()
    sym = symbol or panel_symbol(cfg)

    cached = await _cache_get(redis, KEY_PANEL_MARKET_GEOMETRY)
    if _fresh(cached, TTL_MARKET_GEOMETRY) and cached.get("symbol"):
        return cached

    snapshot = None
    raw_depth = await _cache_get(redis, KEY_DEPTH_SNAPSHOT)
    if raw_depth is not None:
        try:
            candidate = _snapshot_from_cache(raw_depth)
        except PanelUnavailable:
            candidate = None
        if candidate is not None and (_now() - candidate.timestamp) <= bp.MAX_CACHED_DEPTH_AGE_S:
            snapshot = candidate

    if snapshot is None:
        from app.ingestion.kraken_depth_adapter import (KrakenDepthError,
                                                        get_kraken_depth_adapter)
        adapter = get_kraken_depth_adapter()
        try:
            snapshot = await asyncio.to_thread(adapter.fetch, sym)
        except KrakenDepthError as exc:
            raise PanelUnavailable(f"Kraken depth unavailable: {exc}",
                                   "kraken_live_l2") from exc
        except Exception as exc:  # pragma: no cover - Netzfehler
            raise PanelUnavailable(f"Kraken depth error: {exc}",
                                   "kraken_live_l2") from exc
        await _cache_put(redis, KEY_DEPTH_SNAPSHOT, {
            "symbol": snapshot.symbol,
            "timestamp": snapshot.timestamp,
            "bids": [[p, v] for p, v in list(snapshot.bids)[:100]],
            "asks": [[p, v] for p, v in list(snapshot.asks)[:100]],
        }, bp.MAX_CACHED_DEPTH_AGE_S if bp.MAX_CACHED_DEPTH_AGE_S >= 1 else 1)

    payload = compute_market_geometry(snapshot)
    payload["requested_symbol"] = sym
    await _cache_put(redis, KEY_PANEL_MARKET_GEOMETRY, payload, TTL_MARKET_GEOMETRY)
    return payload


# ================================================== MP-17.2 Quantum Regime =

def _fractal_dimension(hurst: Optional[float]) -> Optional[float]:
    """Higuchi/Hurst-Relation D = 2 - H (1D-Preisreihe)."""
    if hurst is None:
        return None
    return _round(2.0 - float(hurst), 6)


def _candles_for(store, symbol: str, interval_sec: int, limit: int) -> List[Dict[str, Any]]:
    """DuckDB-OHLCV -> kanonische Candle-Dicts (beide Key-Stile o/h/l/c/v)."""
    rows = store.ohlcv(symbol, interval_sec, limit=limit)
    out: List[Dict[str, Any]] = []
    for row in rows:
        try:
            o, h = float(row["open"]), float(row["high"])
            low, close = float(row["low"]), float(row["close"])
            volume = float(row.get("volume") or 0.0)
        except (KeyError, TypeError, ValueError):
            continue
        out.append({
            "ts": row.get("ts"),
            "open": o, "high": h, "low": low, "close": close, "volume": volume,
            "o": o, "h": h, "l": low, "c": close, "v": volume,
        })
    return out


def rsi(closes: Sequence[float], period: int = 14) -> Optional[float]:
    """Wilder-RSI der Schlusskurse; None wenn zu wenige Bars."""
    if len(closes) <= period:
        return None
    gains, losses = 0.0, 0.0
    for i in range(1, period + 1):
        delta = float(closes[i]) - float(closes[i - 1])
        gains += max(delta, 0.0)
        losses += max(-delta, 0.0)
    avg_gain, avg_loss = gains / period, losses / period
    for i in range(period + 1, len(closes)):
        delta = float(closes[i]) - float(closes[i - 1])
        avg_gain = (avg_gain * (period - 1) + max(delta, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-delta, 0.0)) / period
    if avg_loss <= 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _m8_aggregate(m8_states: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    total = len(m8_states)
    counts: Dict[str, int] = {}
    multipliers: List[float] = []
    for state in m8_states.values():
        status = str(state.get("status") or "UNKNOWN").upper()
        counts[status] = counts.get(status, 0) + 1
        try:
            multipliers.append(float(state.get("budget_multiplier", 1.0)))
        except (TypeError, ValueError):
            continue
    avg = sum(multipliers) / len(multipliers) if multipliers else None
    return {
        "instances": total,
        "status_counts": counts,
        "avg_budget_multiplier": _round(avg, 4),
        "active": counts.get("ACTIVE", 0),
        "throttled": counts.get("THROTTLED", 0),
        "quarantined": counts.get("QUARANTINED", 0),
        "retired": counts.get("RETIRED", 0),
    }


async def quantum_regime(redis=None, *, symbol: Optional[str] = None,
                         config: Optional[SigmaConfig] = None,
                         m8=None) -> Dict[str, Any]:
    cfg = config or load_config()
    sym = symbol or panel_symbol(cfg)

    cached = await _cache_get(redis, KEY_PANEL_QUANTUM_REGIME)
    if _fresh(cached, TTL_QUANTUM_REGIME):
        return cached

    from app.core.duckdb_store import get_store
    from app.quant.onnx_kelly import get_quant_engine
    from app.quant.regime_detector import detect_regime
    from app.quant.self_optimizing_onnx import get_self_optimizing_engine

    store = get_store(cfg)
    candles = await asyncio.to_thread(
        _candles_for, store, sym, int(cfg.candle_interval_sec), 720)
    if len(candles) < bp.EMA_SLOW_PERIOD:
        raise PanelUnavailable(
            f"regime needs >= {bp.EMA_SLOW_PERIOD} candles for {sym} "
            f"(have {len(candles)})", "onnx_kelly_m8")

    regime = detect_regime(candles)
    quant = get_quant_engine(cfg)
    self_opt = get_self_optimizing_engine(quant)
    calib = self_opt.snapshot()

    last = candles[-1]
    price = float(last["close"])
    atr_abs = float(regime.get("atr") or 0.0)
    atr_norm = (atr_abs / price) if price else 0.0
    rsi_now = rsi([c["close"] for c in candles])
    prediction = quant.predict_confidence(
        rsi=float(rsi_now if rsi_now is not None else 50.0),
        atr=atr_abs, cisd_score=0.5, price=price)
    win_prob = float(prediction["win_prob"])
    calibrated = float(self_opt.calibrate(prediction["raw"]))
    sizing = quant.size_position(
        equity=float(cfg.base_budget_usd), price=price,
        win_prob=calibrated, atr=atr_abs, action="BUY")

    if m8 is None:
        try:
            from app.server.main import state as _server_state
            m8 = getattr(_server_state, "m8", None)
        except Exception:  # pragma: no cover - Import-Zyklen im Test
            m8 = None
    m8_block: Dict[str, Any] = {"available": False, "instances": 0,
                                "status_counts": {}, "avg_budget_multiplier": None}
    if m8 is not None:
        try:
            states = await m8.scan_states()
            m8_block = {"available": True, **_m8_aggregate(states or {})}
        except Exception as exc:  # pragma: no cover
            logger.warning("M8 scan for quantum-regime failed: %s", exc)

    payload = {
        "symbol": sym,
        "generated_ts": _now(),
        "volatility_regime": {
            "regime": regime.get("regime"),
            "band": regime.get("volatility_band"),
            "atr_percentile": regime.get("atr_percentile"),
            "atr_norm": _round(atr_norm, 6),
            "ema_delta_pct": regime.get("ema_delta_pct"),
            "crisis": regime.get("crisis"),
            "entry_blocked": regime.get("entry_blocked"),
            "sample_size": regime.get("sample_size"),
            "rsi": _round(rsi_now, 4),
        },
        "brier": {
            "score": calib.get("brier"),
            "threshold": calib.get("brier_threshold"),
            "drifting": calib.get("drifting"),
            "samples": calib.get("sample_size"),
            "temperature": calib.get("temperature"),
        },
        "fractal": {
            "hurst": regime.get("hurst"),
            "hurst_class": regime.get("hurst_class"),
            "dimension": _fractal_dimension(regime.get("hurst")),
        },
        "kelly": {
            "fraction_setting": cfg.kelly_fraction,
            "half_kelly_multiplier": _round(
                sizing.kelly_fraction_used / max(cfg.kelly_fraction, 1e-9), 6),
            "fraction_used": _round(sizing.kelly_fraction_used, 6),
            "capped": sizing.capped,
            "cap": cfg.max_portfolio_risk_per_trade,
            "win_prob_raw": _round(win_prob, 6),
            "win_prob_calibrated": _round(calibrated, 6),
            "notional_usd": _round(sizing.notional, 2),
            "source": sizing.source,
            "inference_source": prediction.get("source"),
            "model_available": quant.model_available,
        },
        "m8": m8_block,
    }
    await _cache_put(redis, KEY_PANEL_QUANTUM_REGIME, payload, TTL_QUANTUM_REGIME)
    return payload


# =================================================== MP-17.3 Power Physics =

def compute_power_physics(bars: Sequence[Dict[str, float]], *,
                          window_s: int = PHYSICS_WINDOW_S) -> Dict[str, Any]:
    """Kinetic Momentum, Orderflow-Beschleunigung, Exhaustion, Vol-Energie.

    Physik-Analogie (§MP-04): v = dP/dt (Rendite je Bar), m = Volumen,
    p = m*v (Impuls), E = 0.5*m*v^2 (Energie), a = dv/dt.
    """
    closes = [float(b["close"]) for b in bars]
    volumes = [float(b.get("volume") or 0.0) for b in bars]
    if len(closes) < 3:
        raise PanelUnavailable("tick buffer needs >= 3 bars", "duckdb_orderflow")

    velocities = [(closes[i] - closes[i - 1]) / closes[i - 1]
                  for i in range(1, len(closes)) if closes[i - 1] > 0]
    if len(velocities) < 2:
        raise PanelUnavailable("tick buffer degenerate (no price movement basis)",
                               "duckdb_orderflow")
    accelerations = [velocities[i] - velocities[i - 1] for i in range(1, len(velocities))]

    mass = volumes[-1] if volumes else 0.0
    v_now = velocities[-1]
    momentum = mass * v_now
    energy = 0.5 * mass * v_now * v_now
    avg_vol = sum(volumes) / len(volumes) if volumes else 0.0

    # Exhaustion: hoher Volumen-Anteil bei fallender |Geschwindigkeit|.
    speed_now = abs(v_now)
    speed_avg = sum(abs(v) for v in velocities) / len(velocities)
    vol_ratio = (mass / avg_vol) if avg_vol > 0 else 0.0
    speed_decay = 1.0 - (speed_now / speed_avg) if speed_avg > 0 else 0.0
    exhaustion = max(0.0, min(1.0, 0.5 * max(0.0, speed_decay)
                              + 0.5 * max(0.0, min(1.0, (vol_ratio - 1.0)))))

    variance = sum((v - (sum(velocities) / len(velocities))) ** 2
                   for v in velocities) / len(velocities)
    realized_vol = math.sqrt(variance)

    return {
        "generated_ts": _now(),
        "window_s": window_s,
        "bars": len(bars),
        "kinetic_momentum": _round(momentum, 8),
        "velocity": _round(v_now, 8),
        "avg_velocity": _round(sum(velocities) / len(velocities), 8),
        "orderflow_acceleration": _round(accelerations[-1], 8),
        "acceleration_series": [_round(a, 8) for a in accelerations[-30:]],
        "exhaustion_index": _round(exhaustion, 6),
        "volatility_energy": _round(energy, 8),
        "realized_volatility": _round(realized_vol, 8),
        "volume_last": _round(mass, 8),
        "volume_avg": _round(avg_vol, 8),
        "volume_ratio": _round(vol_ratio, 6),
        "direction": ("UP" if v_now > 0 else "DOWN" if v_now < 0 else "FLAT"),
    }


async def power_physics(redis=None, *, symbol: Optional[str] = None,
                        config: Optional[SigmaConfig] = None) -> Dict[str, Any]:
    cfg = config or load_config()
    sym = symbol or panel_symbol(cfg)

    cached = await _cache_get(redis, KEY_PANEL_POWER_PHYSICS)
    if _fresh(cached, TTL_POWER_PHYSICS):
        return cached

    from app.core.duckdb_store import get_store

    store = get_store(cfg)
    interval = int(cfg.candle_interval_sec) or 60
    limit = max(3, PHYSICS_WINDOW_S // interval)
    bars = await asyncio.to_thread(_candles_for, store, sym, interval, limit)
    if len(bars) < 3:
        raise PanelUnavailable(
            f"tick_buffer_5m empty for {sym} (bars={len(bars)})", "duckdb_orderflow")

    payload = compute_power_physics(bars, window_s=PHYSICS_WINDOW_S)
    payload["symbol"] = sym
    payload["interval_sec"] = interval
    await _cache_put(redis, KEY_PANEL_POWER_PHYSICS, payload, TTL_POWER_PHYSICS)
    return payload


# ================================================ MP-17.4 Glint/Polymarket =

def polymarket_slug(config: Optional[SigmaConfig] = None) -> str:
    import os

    cfg = config or load_config()
    return (os.environ.get("SIGMA_POLYMARKET_SLUG")
            or getattr(cfg, "polymarket_event_slug", "") or "")


def _fetch_gamma_event(slug: str, timeout_s: float = 8.0) -> Optional[Dict[str, Any]]:
    import httpx

    response = httpx.get(GAMMA_EVENTS_URL, params={"slug": slug}, timeout=timeout_s)
    response.raise_for_status()
    data = response.json()
    if isinstance(data, list):
        return data[0] if data else None
    return data if isinstance(data, dict) else None


async def glint_polymarket(redis=None, *, symbol: Optional[str] = None,
                           config: Optional[SigmaConfig] = None) -> Dict[str, Any]:
    cfg = config or load_config()
    sym = symbol or panel_symbol(cfg)

    cached = await _cache_get(redis, KEY_PANEL_GLINT_POLYMARKET)
    if _fresh(cached, TTL_GLINT_POLYMARKET):
        return cached

    from sigma.ports.polymarket_gamma_feeder import (get_gamma_port,
                                                     parse_gamma_payload)

    slug = polymarket_slug(cfg)
    odds = get_gamma_port().odds
    spot: Optional[float] = None
    try:
        from app.core.duckdb_store import get_store
        spot = await asyncio.to_thread(get_store(cfg).latest_close, sym)
    except Exception as exc:  # pragma: no cover
        logger.warning("spot lookup for glint panel failed: %s", exc)

    if (odds is None or not odds.valid or odds.is_stale()) and slug:
        if not spot or spot <= 0:
            raise PanelUnavailable(
                f"no spot price for {sym} — Polymarket mapping impossible",
                "polymarket_gamma_live")
        try:
            raw = await asyncio.to_thread(_fetch_gamma_event, slug)
        except Exception as exc:
            raise PanelUnavailable(f"Polymarket gamma fetch failed: {exc}",
                                   "polymarket_gamma_live") from exc
        odds = parse_gamma_payload(raw, float(spot))
        if odds.valid:
            get_gamma_port().set_odds(odds)

    if odds is None or not odds.valid:
        reason = odds.reason if odds is not None else (
            "no_polymarket_slug_configured" if not slug else "no_snapshot")
        raise PanelUnavailable(f"Polymarket feed invalid: {reason}",
                               "polymarket_gamma_live")
    if odds.is_stale():
        raise PanelUnavailable("Polymarket snapshot stale", "polymarket_gamma_live")

    from app.quant.glint_orderbook_verifier import get_verifier

    verifier = get_verifier()
    audits = verifier.recent(limit=5)
    latest = audits[-1] if audits else None

    spread_bps = latest.get("spread_bps") if latest else None
    spread_efficiency = None
    if spread_bps is not None:
        try:
            spread_efficiency = _round(
                max(0.0, 1.0 - float(spread_bps) / max(bp.CONFLUENCE_MAX_SPREAD_BPS, 1e-9)), 6)
        except (TypeError, ValueError):
            spread_efficiency = None

    bias_pct = odds.bias_pct or 0.0
    payload = {
        "symbol": sym,
        "generated_ts": _now(),
        "polymarket": {
            "slug": odds.slug,
            "title": odds.title,
            "spot_price": _round(odds.spot_price, 8),
            "mu": _round(odds.mu, 8),
            "bias_pct": _round(bias_pct, 6),
            "strikes": odds.strikes,
            "yes_probs": odds.yes_probs,
            "density_bins": odds.density_bins,
            "trajectories": {k: _round(v, 8) for k, v in odds.trajectories.items()},
            "jit_gate_060": odds.gate_060,          # Telemetrie, kein Blocker
            "gate_is_blocker": False,
            "volume24hr_usd": _round(odds.volume24hr_usd, 2),
            "liquidity_usd": _round(odds.liquidity_usd, 2),
            "source_ts": odds.source_ts,
            "ttl_s": odds.ttl_s,
        },
        "glint_radar": {
            "status": (latest.get("verdict") if latest else "NO_AUDIT"),
            "available": bool(latest),
            "depth_imbalance": latest.get("depth_imbalance") if latest else None,
            "size_multiplier": latest.get("size_multiplier") if latest else None,
            "snapshot_age_s": latest.get("snapshot_age_s") if latest else None,
            "recent_audits": audits,
        },
        "spread": {
            "bps": spread_bps,
            "max_bps": bp.CONFLUENCE_MAX_SPREAD_BPS,
            "efficiency": spread_efficiency,
        },
        "directional_bias": {
            "label": ("BULLISH" if bias_pct > 0 else
                      "BEARISH" if bias_pct < 0 else "CHOP"),
            "pct": _round(bias_pct, 6),
            "confirmed_by_orderflow": bool(
                latest and latest.get("depth_imbalance") is not None
                and float(latest["depth_imbalance"]) * bias_pct > 0),
        },
    }
    await _cache_put(redis, KEY_PANEL_GLINT_POLYMARKET, payload, TTL_GLINT_POLYMARKET)
    return payload
