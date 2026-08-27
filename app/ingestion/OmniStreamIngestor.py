"""
=========================================================
Datei:      app/ingestion/OmniStreamIngestor.py
Zweck:      Tick-/Candle-Ingestion (Blueprint: OmniStream / Glint / CCXT WS)
Knoten:     Jaune (Carrera-Engine)
=========================================================
Quellen:
  synthetic — deterministischer Regime-GBM (Sandbox/Paper-Default)
  ccxt_ws   — [MOCK-SEAM] echte CCXT-WebSocket-Verbindung. Im Sandbox-Run
              ohne Exchange-Zugriff wird der Feed automatisch auf synthetic
              zurückgefallen. Ersetze `_ccxt_feed` durch echten ccxt pro-Client.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import random
import time
from typing import Any, Dict, List, Optional

from app.core.config import AlphaConfig
from app.core.event_bus import get_event_bus
from app.core.telemetry import get_telemetry_center

logger = logging.getLogger("app.ingestion.omnistream")

BASE_PRICES = {
    "BTC/USD": 97_000.0,
    "ETH/USD": 3_400.0,
    "SOL/USD": 180.0,
    "XRP/USD": 2.20,
}
BASE_24H = {
    "BTC/USD": 1_400_000_000.0,
    "ETH/USD": 620_000_000.0,
    "SOL/USD": 18_000_000_000.0,
    "XRP/USD": 5_200_000_000.0,
}


def symbol_seed(symbol: str) -> int:
    return int(hashlib.sha256(symbol.encode()).hexdigest()[:8], 16)


class OmniStreamIngestor:
    """Hält Live-Preise, aggregiert 1m-Candles, persistiert in DuckDB+Redis."""

    def __init__(self, config: AlphaConfig, redis=None, store=None):
        self.config = config
        self.redis = redis
        self.store = store
        self.prices: Dict[str, float] = {}
        self.change24h: Dict[str, float] = {}
        self.high24h: Dict[str, float] = {}
        self.low24h: Dict[str, float] = {}
        self.volume24h: Dict[str, float] = {}
        self._candles: Dict[str, Dict[str, Any]] = {}
        self._rngs: Dict[str, random.Random] = {}
        self._regime_phase: Dict[str, float] = {}
        self._running = False
        self._bursts: Dict[str, Dict[str, float]] = {}
        self.last_candle_close_ts: Dict[str, float] = {}
        self.candle_close_subscribers: List = []
        self._tick_count = 0

    # ------------------------------------------------------------------- setup
    def attach(self, redis=None, store=None) -> None:
        if redis is not None:
            self.redis = redis
        if store is not None:
            self.store = store

    def _rng(self, symbol: str) -> random.Random:
        if symbol not in self._rngs:
            base = BASE_PRICES.get(symbol, 100.0)
            self.prices[symbol] = base
            self.change24h[symbol] = (symbol_seed(symbol) % 900 - 400) / 100.0  # ±4%
            self.high24h[symbol] = base * 1.02
            self.low24h[symbol] = base * 0.97
            self.volume24h[symbol] = BASE_24H.get(symbol, 1e8)
            self._rngs[symbol] = random.Random(symbol_seed(symbol))
            self._regime_phase[symbol] = symbol_seed(symbol) % 7
        return self._rngs[symbol]

    # -------------------------------------------------------------- seed lake
    def seed_history(self, candles_per_symbol: Optional[int] = None) -> int:
        """Generiert die 1m-Historie in die Vergangenheit (Deterministisch)."""
        candles_per_symbol = candles_per_symbol or self.config.seed_candle_count
        total = 0
        now = time.time()
        for symbol in self.config.market_symbols:
            # Idempotenz: nur Seed, wenn noch keine Historie existiert
            if self.store is not None and self.store._one(
                    "SELECT 1 AS x FROM ohlcv WHERE symbol = ? LIMIT 1", [symbol]):
                self.prices[symbol] = self.store.latest_close(symbol) or \
                    BASE_PRICES.get(symbol, 100.0)
                continue
            rng = self._rng(symbol)
            price = BASE_PRICES.get(symbol, 100.0) * 0.995
            vol = price * 0.0006
            candles: List[Dict[str, Any]] = []
            t0 = now - candles_per_symbol * 60
            for i in range(candles_per_symbol):
                ts = t0 + i * 60
                phase = (i + self._regime_phase[symbol] * 37) / 220.0
                drift = 0.00006 * math.sin(phase) * price
                o = price
                c = max(price * 0.5, o + drift + rng.gauss(0, vol))
                h = max(o, c) * (1 + abs(rng.gauss(0, vol * 0.6)) / max(o, 1e-9))
                l = min(o, c) * (1 - abs(rng.gauss(0, vol * 0.6)) / max(o, 1e-9))
                v = rng.uniform(0.5, 3.0) * (BASE_24H.get(symbol, 1e8) / 60 / 24 / 1000.0)
                candles.append({"ts": _ts_us(ts), "open": o, "high": h,
                                "low": l, "close": c, "volume": v})
                price = c
            if self.store is not None:
                total += self.store.seed_ohlcv(symbol, 60, candles)
            # Letzten Preis übernehmen
            self.prices[symbol] = price
        # Parquet-Lake initial partitionieren (L2)
        if self.store is not None:
            from app.execution.StorageUtils import flush_candles_to_parquet

            day = time.strftime("%Y%m%d", time.gmtime(now - candles_per_symbol * 60))
            for symbol in self.config.market_symbols:
                batch = self.store.ohlcv(symbol, 60, limit=candles_per_symbol)
                if batch:
                    flush_candles_to_parquet(self.config.resolved_parquet_dir,
                                             symbol, 60, batch, day)
        logger.info("Seeded %d historical 1m candles for %d symbols",
                    total, len(self.config.market_symbols))
        return total

    # ------------------------------------------------------------- live loop
    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        source = self.config.market_source
        if source == "ccxt_ws":
            # [MOCK-SEAM] Echter CCXT WS-Feed — im Sandbox ohne Exchange-Zugriff
            # automatisch Synthetic-Fallback (siehe _ccxt_feed).
            if not await _ccxt_feed_available():
                logger.warning("[MOCK] CCXT WS nicht erreichbar → Synthetic-Fallback aktiv.")
        self.seed_history()
        self._loop_task = asyncio.create_task(self._tick_loop())
        logger.info("OmniStream ingestor started (source=%s, tick=%.1fs)",
                    source, self.config.tick_interval_seconds)

    async def stop(self) -> None:
        self._running = False
        task = getattr(self, "_loop_task", None)
        if task:
            task.cancel()

    async def _tick_loop(self) -> None:
        tele = get_telemetry_center()
        bus = get_event_bus()
        while self._running:
            t_start = time.time()
            self._tick_count += 1
            for symbol in self.config.market_symbols:
                tick = self._synthetic_tick(symbol)
                self._apply_tick(symbol, tick)
                if self.redis:
                    try:
                        await self.redis.hset(f"mkt:price:{symbol}", mapping={
                            "price": str(tick["price"]),
                            "ts": str(tick["ts"]),
                        })
                    except Exception:
                        pass
            tele.ingestion_rate_events_per_sec = (
                len(self.config.market_symbols) / max(self.config.tick_interval_seconds, 1e-6)
            )
            tele.beat()
            tele.avg_latency_microseconds = (time.time() - t_start) * 1e6
            tele.l1_ringbuffer_bytes = min(
                tele.l1_capacity_bytes, int(tele.l1_ringbuffer_bytes * 0.9 + 4096)
            )
            await asyncio.sleep(self.config.tick_interval_seconds)

    def _synthetic_tick(self, symbol: str) -> Dict[str, Any]:
        rng = self._rng(symbol)
        price = self.prices.get(symbol, BASE_PRICES.get(symbol, 100.0))
        t = time.time()
        phase = (t + self._regime_phase[symbol] * 1000) / 900.0
        drift = 0.00012 * math.sin(phase) * price
        vol = price * 0.00045
        # Regime-Bursts: kurze Trend-/Chop-Schübe alle ~3–6 min (krypto-typisch)
        burst = self._bursts.setdefault(symbol, {"until": 0.0, "dir": 1})
        if t >= burst["until"]:
            if rng.random() < 0.02:  # ~1 Burst alle 75 Ticks
                burst["until"] = t + rng.uniform(90, 240)
                burst["dir"] = 1 if rng.random() < 0.5 else -1
                burst["kick"] = rng.uniform(0.0006, 0.0016)
            else:
                burst["kick"] = 0.0
        if t < burst["until"]:
            drift += burst["dir"] * burst["kick"] * price
            vol *= 1.8
        new_price = max(price * 0.5, price + drift + rng.gauss(0, vol))
        return {"price": new_price, "ts": t,
                "volume": rng.uniform(0.2, 2.5) * (1.6 if t < burst["until"] else 1.0)}

    def _apply_tick(self, symbol: str, tick: Dict[str, Any]) -> None:
        price = float(tick["price"])
        ts = float(tick["ts"])
        prev = self.prices.get(symbol, price)
        self.prices[symbol] = price
        self.high24h[symbol] = max(self.high24h.get(symbol, price), price)
        self.low24h[symbol] = min(self.low24h.get(symbol, price), price)
        self.volume24h[symbol] = self.volume24h.get(symbol, 0.0) + float(tick.get("volume", 0.0))

        bucket = int(ts // 60) * 60
        cur = self._candles.get(symbol)
        if cur is None or int(cur["ts_bucket"]) != bucket:
            if cur is not None:
                self._close_candle(symbol, cur)
            self._candles[symbol] = {
                "symbol": symbol, "ts_bucket": bucket,
                "open": price, "high": price, "low": price, "close": price,
                "volume": float(tick.get("volume", 0.0)),
            }
        else:
            cur["high"] = max(cur["high"], price)
            cur["low"] = min(cur["low"], price)
            cur["close"] = price
            cur["volume"] += float(tick.get("volume", 0.0))

    def _close_candle(self, symbol: str, candle: Dict[str, Any]) -> None:
        import datetime

        row = {
            "symbol": symbol,
            "ts": _ts_us(candle["ts_bucket"]),
            "open": candle["open"], "high": candle["high"],
            "low": candle["low"], "close": candle["close"],
            "volume": candle["volume"],
        }
        self.last_candle_close_ts[symbol] = candle["ts_bucket"]
        if self.store is not None:
            try:
                self.store.seed_ohlcv(symbol, 60, [row])
            except Exception as exc:
                logger.warning("candle persist failed: %s", exc)
        for cb in list(self.candle_close_subscribers):
            try:
                cb(symbol, row)
            except Exception as exc:
                logger.warning("candle subscriber failed: %s", exc)

    # ----------------------------------------------------------------- queries
    def ticker_rows(self) -> List[Dict[str, Any]]:
        import datetime

        rows = []
        for symbol in self.config.market_symbols:
            self._rng(symbol)
            price = self.prices[symbol]
            rows.append({
                "pair": symbol,
                "price": round(price, 4),
                "change24h": round(self.change24h[symbol], 2),
                "high": round(self.high24h[symbol], 4),
                "low": round(self.low24h[symbol], 4),
                "volume": round(self.volume24h[symbol], 0),
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            })
        return rows

    def last_price(self, symbol: str) -> float:
        return self.prices.get(symbol, BASE_PRICES.get(symbol, 100.0))


def _ts_us(ts: float):
    import datetime

    return datetime.datetime.fromtimestamp(int(ts), datetime.timezone.utc).replace(microsecond=0)


async def _ccxt_feed_available() -> bool:
    """[MOCK-SEAM] Prüft Erreichbarkeit eines echten CCXT-Feeds.
    Im Sandbox-Run: False → Synthetic. Für den LAN-Produktionsbetrieb
    (Ubuntu-Core 192.168.178.50) hier echten ccxt.pro-Test einbauen, z.B.:
        import ccxt.pro as ccxtpro
        ex = ccxtpro.kraken()
        ok = await ex.load_markets()
        return True
    """
    try:
        import ccxt  # noqa: F401
    except ImportError:
        return False
    # [MOCK] Keine Exchange-Verbindung im Sandbox — deterministischer Fallback.
    return False
