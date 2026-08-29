"""
=========================================================
Datei:      sigma/loops/loop_c.py
Zweck:      LoopCPort.poll() -> MarketSnapshot
            Scraper :8001 → DuckDB-Lake → regime_detector.
            Sidecar down / synthetic → degraded=True, leere Serie, keine Coins.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Blanche (Feed) / Jaune (Loop C Port)
=========================================================
"""
from __future__ import annotations

import datetime as _dt
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from app.core import blueprint as bp

logger = logging.getLogger("sigma.loops.loop_c")

_DEFAULT_SYMBOLS = ("BTC/USD", "ETH/USD", "SOL/USD", "XRP/USD")
_SYNTHETIC_SOURCES = frozenset({"synthetic", "synth", "seed"})


@dataclass
class MarketSnapshot:
    series: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    htf_series: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    regime: Optional[Dict[str, Any]] = None
    polymarket: Optional[Dict[str, Any]] = None
    degraded: bool = True
    source: str = "empty"
    symbols: List[str] = field(default_factory=list)
    interval_min: int = 15
    htf_interval_min: int = 60
    ts: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


def _empty_snapshot(reason: str = "sidecar_unavailable") -> MarketSnapshot:
    import time

    return MarketSnapshot(
        series={},
        regime=None,
        degraded=True,
        source=reason,
        symbols=[],
        ts=time.time(),
    )


class LoopCPort:
    """Fail-closed Feed-Port. Kein stiller Synthetic-Fallback im Prod-Pfad."""

    def __init__(
        self,
        scraper: Any = None,
        store: Any = None,
        detector: Any = None,
        symbols: Optional[Sequence[str]] = None,
        interval_min: int = 15,
        count: int = 500,
        polymarket: Any = None,
    ) -> None:
        self.scraper = scraper
        self.store = store
        self.detector = detector
        self.symbols = list(symbols) if symbols is not None else list(_DEFAULT_SYMBOLS)
        self.interval_min = int(interval_min)
        self.htf_interval_min = 60
        self.count = int(count)
        self.polymarket = polymarket

    def poll_pair(
        self,
        exec_interval_min: Optional[int] = None,
        bias_interval_min: Optional[int] = None,
    ) -> MarketSnapshot:
        """Poll both legs of a ladder pair. Closed HTF only is enforced later."""
        exec_tf = int(exec_interval_min or self.interval_min)
        bias_tf = int(bias_interval_min or self.htf_interval_min)
        ltf = self.poll(interval_min=exec_tf)
        if ltf.degraded:
            return ltf
        saved = self.interval_min
        self.interval_min = bias_tf
        try:
            htf = self.poll(interval_min=bias_tf)
        finally:
            self.interval_min = saved
        ltf.htf_series = htf.series if not htf.degraded else {}
        ltf.htf_interval_min = bias_tf
        ltf.interval_min = exec_tf
        if htf.degraded:
            ltf.source = f"{ltf.source}+htf_empty"
        return ltf

    def poll(self, interval_min: Optional[int] = None) -> MarketSnapshot:
        interval = int(interval_min or self.interval_min)
        client = self._scraper()
        if client is None:
            return _empty_snapshot("sidecar_unavailable")
        try:
            health = client.health() if hasattr(client, "health") else {"ok": True}
        except Exception as exc:
            logger.info("loop C sidecar health failed: %s", exc)
            return _empty_snapshot("sidecar_unavailable")
        if not health or not health.get("ok") or health.get("degraded"):
            return _empty_snapshot("sidecar_unavailable")

        series: Dict[str, List[Dict[str, Any]]] = {}
        source = "tv_scraper"
        for symbol in self.symbols:
            try:
                candles, meta = self._fetch(client, symbol, interval)
            except Exception as exc:
                logger.info("loop C fetch failed for %s: %s", symbol, exc)
                continue
            origin = str((meta or {}).get("source") or "").lower()
            if (meta or {}).get("degraded") or origin in _SYNTHETIC_SOURCES:
                logger.info("loop C skip %s source=%s degraded=%s",
                            symbol, origin, (meta or {}).get("degraded"))
                continue
            if not candles:
                continue
            series[symbol] = candles
            if origin:
                source = origin
            self._write_lake(symbol, candles, interval)

        if not series:
            return _empty_snapshot("empty_or_synthetic")

        import time

        regime = self._detect(next(iter(series.values())))
        from sigma.signals.polymarket_layer0 import layer0_pre_regime

        return MarketSnapshot(
            series=series,
            htf_series={},
            regime=regime,
            polymarket=layer0_pre_regime(self.polymarket).to_dict(),
            degraded=False,
            source=source,
            symbols=list(series.keys()),
            interval_min=interval,
            htf_interval_min=self.htf_interval_min,
            ts=time.time(),
        )

    def _scraper(self) -> Any:
        if self.scraper is not None:
            return self.scraper
        try:
            from app.tv.scraper_client import get_scraper_client

            return get_scraper_client()
        except Exception as exc:
            logger.info("loop C scraper client unavailable: %s", exc)
            return None

    def _fetch(self, client: Any, symbol: str, interval_min: Optional[int] = None):
        minutes = int(interval_min or self.interval_min)
        if hasattr(client, "fetch_ohlc_with_meta"):
            return client.fetch_ohlc_with_meta(
                symbol, minutes, self.count,
            )
        candles = client.fetch_ohlc(symbol, minutes, self.count)
        meta = dict(getattr(client, "last_meta", {}) or {})
        return candles, meta

    def _write_lake(self, symbol: str, candles: List[Dict[str, Any]], interval_min: Optional[int] = None) -> None:
        store = self.store
        if store is None:
            return
        minutes = int(interval_min or self.interval_min)
        try:
            store.seed_ohlcv(symbol, minutes * 60, _to_lake_candles(candles))
        except Exception as exc:
            logger.warning("loop C lake write failed for %s: %s", symbol, exc)

    def _detect(self, candles: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        detector = self.detector
        if detector is None:
            try:
                from app.quant.regime_detector import get_regime_detector

                detector = get_regime_detector()
            except Exception:
                return None
        mapped = [_alpha_candle(c) for c in candles]
        try:
            vec = detector.detect(mapped)
            return vec.to_dict() if hasattr(vec, "to_dict") else dict(vec)
        except Exception as exc:
            logger.info("loop C regime detect failed: %s", exc)
            return {
                "regime": bp.Regime.RANGING_CHOP.value,
                "entry_blocked": True,
                "reason": "detect_failed",
            }


def _alpha_candle(row: Dict[str, Any]) -> Dict[str, float]:
    return {
        "ts": float(row.get("ts") or 0.0),
        "o": float(row.get("o", row.get("open", 0.0)) or 0.0),
        "h": float(row.get("h", row.get("high", 0.0)) or 0.0),
        "l": float(row.get("l", row.get("low", 0.0)) or 0.0),
        "c": float(row.get("c", row.get("close", 0.0)) or 0.0),
        "v": float(row.get("v", row.get("volume", 0.0)) or 0.0),
    }


def _to_lake_candles(candles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for row in candles:
        ts = row.get("ts", 0)
        if isinstance(ts, (int, float)):
            unix = float(ts)
            if unix > bp.TIMESTAMP_MS_THRESHOLD:
                unix /= 1000.0
            ts_dt = _dt.datetime.fromtimestamp(int(unix), _dt.timezone.utc).replace(microsecond=0)
        else:
            ts_dt = ts
        out.append({
            "ts": ts_dt,
            "open": float(row.get("open", row.get("o", 0.0)) or 0.0),
            "high": float(row.get("high", row.get("h", 0.0)) or 0.0),
            "low": float(row.get("low", row.get("l", 0.0)) or 0.0),
            "close": float(row.get("close", row.get("c", 0.0)) or 0.0),
            "volume": float(row.get("volume", row.get("v", 0.0)) or 0.0),
        })
    return out
