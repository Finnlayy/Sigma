"""Rate-limited Kraken REST depth snapshots for the JIT Glint gate."""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

from app.core import blueprint as bp
from app.core.exchange_clock import ExchangeClock, get_exchange_clock
from app.core.rate_limiter import ProviderRateLimiter, get_rate_limiter
from app.quant.glint_orderbook_verifier import OrderbookSnapshot, depth_imbalance
from app.tv.symbol_map import to_kraken_pair

logger = logging.getLogger("app.ingestion.kraken_depth")

KRAKEN_DEPTH_URL = "https://api.kraken.com/0/public/Depth"


class KrakenDepthError(RuntimeError):
    """Depth could not be obtained or validated; callers must fail closed."""


class KrakenDepthAdapter:
    def __init__(
        self,
        *,
        limiter: Optional[ProviderRateLimiter] = None,
        clock: Optional[ExchangeClock] = None,
        fetcher: Optional[Callable[[str, int], Dict[str, Any]]] = None,
        count: int = 100,
        timeout_s: float = bp.CLOCK_SYNC_TIMEOUT_S,
    ) -> None:
        self.limiter = limiter or get_rate_limiter()
        self.clock = clock or get_exchange_clock()
        self.fetcher = fetcher or self._fetch
        self.count = max(10, min(500, int(count)))
        self.timeout_s = timeout_s
        self.last_symbol = ""
        self.last_error: Optional[str] = None

    def _fetch(self, pair: str, count: int) -> Dict[str, Any]:
        import httpx

        response = httpx.get(
            KRAKEN_DEPTH_URL,
            params={"pair": pair, "count": count},
            timeout=self.timeout_s,
        )
        if response.status_code == 429:
            self.limiter.note_429("kraken_api")
        response.raise_for_status()
        return response.json()

    def fetch(self, symbol: str) -> OrderbookSnapshot:
        pair = to_kraken_pair(symbol)
        self.limiter.acquire("kraken_api")
        try:
            payload = self.fetcher(pair, self.count)
            errors = payload.get("error") or []
            if errors:
                raise KrakenDepthError("; ".join(map(str, errors)))
            result = payload.get("result") or {}
            book = next(iter(result.values()), None)
            if not isinstance(book, dict):
                raise KrakenDepthError(f"Kraken Depth result missing for {pair}")
            bids = self._levels(book.get("bids"))
            asks = self._levels(book.get("asks"))
            if not bids or not asks:
                raise KrakenDepthError(f"Kraken Depth empty for {pair}")
            self.limiter.note_success("kraken_api")
            self.last_symbol = pair
            self.last_error = None
            return OrderbookSnapshot(
                symbol=pair,
                bids=sorted(bids, key=lambda level: level[0], reverse=True),
                asks=sorted(asks, key=lambda level: level[0]),
                timestamp=self.clock.now(),
            )
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            if isinstance(exc, KrakenDepthError):
                raise
            raise KrakenDepthError(self.last_error) from exc

    @staticmethod
    def _levels(raw: Any) -> list[tuple[float, float]]:
        levels: list[tuple[float, float]] = []
        for level in raw or []:
            if not isinstance(level, (list, tuple)) or len(level) < 2:
                continue
            try:
                price, volume = float(level[0]), float(level[1])
            except (TypeError, ValueError):
                continue
            if price > 0 and volume >= 0:
                levels.append((price, volume))
        return levels

    # --------------------------------------------------- payload entry ---
    def snapshot_from_payload(
        self, payload: Dict[str, Any], pair: str, timestamp: float
    ) -> OrderbookSnapshot:
        """Baut aus einem rohen Kraken-Depth-Payload
        (``result.{pair}.bids|asks`` → [price, volume, ts]) ein
        ``OrderbookSnapshot``. Fehlerhafte Payloads → ``KrakenDepthError``
        (fail-closed); kein Netz, keine Ausnahmen schlucken."""
        result = payload.get("result") or {}
        book = result.get(pair)
        if not isinstance(book, dict):
            # Fallback: erster Eintrag (Kraken liefert den Pair-Key variabel)
            book = next((v for v in result.values() if isinstance(v, dict)), None)
        if not isinstance(book, dict):
            raise KrakenDepthError(f"Kraken Depth result missing for {pair}")
        bids = self._levels(book.get("bids"))
        asks = self._levels(book.get("asks"))
        if not bids or not asks:
            raise KrakenDepthError(f"Kraken Depth empty for {pair}")
        return OrderbookSnapshot(
            symbol=pair,
            bids=sorted(bids, key=lambda level: level[0], reverse=True),
            asks=sorted(asks, key=lambda level: level[0]),
            timestamp=timestamp,
        )

    def verify_payload(
        self,
        payload: Dict[str, Any],
        pair: str,
        direction: str,
        now: float,
    ) -> Any:
        """JIT-Entry: roher Kraken-Depth-Payload → bestehender
        ``GlintOrderbookVerifier.verify`` (2 %-Band, I_depth, Spread,
        Stale-Veto < 3 s). Kein Duplikat der Audit-Logik. Der
        Snapshot-Timestamp wird aus den Level-Timestamps (Index 2)
        abgeleitet — sonst wäre der Stale-Check wirkungslos."""
        from app.quant.glint_orderbook_verifier import get_verifier

        result = payload.get("result") or {}
        book = result.get(pair)
        if not isinstance(book, dict):
            book = next((v for v in result.values() if isinstance(v, dict)), None)
        level_ts: Optional[float] = None
        if isinstance(book, dict):
            for side in ("bids", "asks"):
                for level in book.get(side) or []:
                    if isinstance(level, (list, tuple)) and len(level) >= 3:
                        try:
                            ts = float(level[2])
                        except (TypeError, ValueError):
                            continue
                        level_ts = ts if level_ts is None else max(level_ts, ts)
        # Ohne Level-Timestamps: JIT-Fallback auf now (kein Stale-Veto).
        snapshot_ts = float(now) if level_ts is None else level_ts
        snapshot = self.snapshot_from_payload(payload, pair, snapshot_ts)
        return get_verifier().verify(snapshot, direction, now=now)

    @staticmethod
    def absorption(snapshot: OrderbookSnapshot) -> float:
        """Scale-independent 0..1 proxy: balanced depth and a tight spread."""
        bid_volume = sum(volume for _, volume in snapshot.bids)
        ask_volume = sum(volume for _, volume in snapshot.asks)
        balance = 1.0 - abs(depth_imbalance(bid_volume, ask_volume))
        spread_factor = max(
            0.0,
            1.0 - snapshot.spread_bps / max(bp.CONFLUENCE_MAX_SPREAD_BPS, 1.0),
        )
        return max(0.05, min(1.0, balance * spread_factor))


_ADAPTER: Optional[KrakenDepthAdapter] = None


def get_kraken_depth_adapter(**kwargs: Any) -> KrakenDepthAdapter:
    global _ADAPTER
    if _ADAPTER is None:
        _ADAPTER = KrakenDepthAdapter(**kwargs)
    return _ADAPTER


def set_kraken_depth_adapter(adapter: Optional[KrakenDepthAdapter]) -> None:
    global _ADAPTER
    _ADAPTER = adapter
