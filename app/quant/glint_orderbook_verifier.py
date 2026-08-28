"""
=========================================================
Datei:      app/quant/glint_orderbook_verifier.py
Zweck:      §24 — Glint x Orderbook Confluence (Just-in-Time)
Knoten:     Ciel (Sigma Core)
=========================================================

    I_depth = (bid_vol_2pct - ask_vol_2pct) / (bid_vol_2pct + ask_vol_2pct)

Der Verifier wird **nur** event-driven aufgerufen (Webhook-Entry, Bot-Start,
gezielter Spot-Kauf) — nie als globaler Poll-Loop (§23.2, Tier 0).
Ein Depth-Snapshot aelter als ``max_cached_depth_age_seconds`` (3 s) gilt als
unbrauchbar und fuehrt zu einem konservativen Veto.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from app.core import blueprint as bp

logger = logging.getLogger("app.quant.glint_orderbook_verifier")

Level = Tuple[float, float]  # (price, volume)


@dataclass
class OrderbookSnapshot:
    symbol: str
    bids: Sequence[Level]          # absteigend sortiert
    asks: Sequence[Level]          # aufsteigend sortiert
    timestamp: float

    @property
    def best_bid(self) -> float:
        return float(self.bids[0][0]) if self.bids else 0.0

    @property
    def best_ask(self) -> float:
        return float(self.asks[0][0]) if self.asks else 0.0

    @property
    def mid(self) -> float:
        if not self.bids or not self.asks:
            return 0.0
        return (self.best_bid + self.best_ask) / 2.0

    @property
    def spread_bps(self) -> float:
        mid = self.mid
        if mid <= 0:
            return float("inf")
        return (self.best_ask - self.best_bid) / mid * 10_000.0


@dataclass
class ConfluenceResult:
    symbol: str
    direction: str
    verdict: str
    depth_imbalance: float
    spread_bps: float
    bid_volume: float
    ask_volume: float
    size_multiplier: float
    snapshot_age_s: float
    reason: str
    reject_code: Optional[str] = None

    @property
    def approved(self) -> bool:
        return self.verdict != bp.ConfluenceVerdict.LIQUIDITY_TRAP_VETO.value

    def as_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "verdict": self.verdict,
            "approved": self.approved,
            "depth_imbalance": round(self.depth_imbalance, 4),
            "spread_bps": (None if not math.isfinite(self.spread_bps)
                           else round(self.spread_bps, 2)),
            "bid_volume_2pct": round(self.bid_volume, 6),
            "ask_volume_2pct": round(self.ask_volume, 6),
            "size_multiplier": self.size_multiplier,
            "snapshot_age_s": round(self.snapshot_age_s, 3),
            "reason": self.reason,
            "reject_code": self.reject_code,
        }


def band_volume(levels: Iterable[Level], mid: float, band_pct: float,
                side: str) -> float:
    """Summiert Volumen innerhalb ``band_pct`` um den Mid-Preis."""
    if mid <= 0:
        return 0.0
    lower = mid * (1.0 - band_pct)
    upper = mid * (1.0 + band_pct)
    total = 0.0
    for price, volume in levels:
        price = float(price)
        if side == "bid" and price < lower:
            break
        if side == "ask" and price > upper:
            break
        if lower <= price <= upper:
            total += float(volume)
    return total


def depth_imbalance(bid_volume: float, ask_volume: float) -> float:
    denom = bid_volume + ask_volume
    if denom <= 0:
        return 0.0
    return (bid_volume - ask_volume) / denom


class GlintOrderbookVerifier:
    """JIT-Audit: passt die Orderbuchtiefe zur Glint-Richtung? (§24)"""

    def __init__(
        self,
        *,
        band_pct: float = bp.DEPTH_BAND_PCT,
        confirm_threshold: float = bp.DEPTH_IMBALANCE_CONFIRM,
        veto_threshold: float = bp.DEPTH_IMBALANCE_VETO,
        max_spread_bps: float = bp.CONFLUENCE_MAX_SPREAD_BPS,
        max_age_s: float = bp.MAX_CACHED_DEPTH_AGE_S,
        size_multiplier: float = bp.CONFLUENCE_SIZE_MULTIPLIER,
    ) -> None:
        self.band_pct = band_pct
        self.confirm_threshold = confirm_threshold
        self.veto_threshold = veto_threshold
        self.max_spread_bps = max_spread_bps
        self.max_age_s = max_age_s
        self.size_multiplier = size_multiplier
        self._history: List[Dict[str, Any]] = []

    # ------------------------------------------------------------ core ---
    def verify(
        self,
        snapshot: OrderbookSnapshot,
        direction: str,
        *,
        now: Optional[float] = None,
    ) -> ConfluenceResult:
        side = direction.upper()
        if side in ("BUY", "LONG"):
            side = "BULLISH"
        elif side in ("SELL", "SHORT"):
            side = "BEARISH"
        if side not in ("BULLISH", "BEARISH"):
            raise ValueError(f"unsupported direction: {direction}")

        ts = snapshot.timestamp if now is None else now
        age = max(0.0, ts - snapshot.timestamp)
        mid = snapshot.mid
        bid_vol = band_volume(snapshot.bids, mid, self.band_pct, "bid")
        ask_vol = band_volume(snapshot.asks, mid, self.band_pct, "ask")
        imbalance = depth_imbalance(bid_vol, ask_vol)
        spread = snapshot.spread_bps
        # Fuer BEARISH spiegeln: positives Signed-Imbalance = Rueckenwind.
        signed = imbalance if side == "BULLISH" else -imbalance

        result = self._decide(snapshot.symbol, side, signed, imbalance, spread,
                              bid_vol, ask_vol, age, mid)
        self._history.append(result.as_dict())
        del self._history[:-50]
        return result

    def _decide(self, symbol: str, side: str, signed: float, raw: float,
                spread: float, bid_vol: float, ask_vol: float, age: float,
                mid: float) -> ConfluenceResult:
        def build(verdict: str, mult: float, reason: str,
                  code: Optional[str] = None) -> ConfluenceResult:
            return ConfluenceResult(
                symbol=symbol, direction=side, verdict=verdict,
                depth_imbalance=raw, spread_bps=spread, bid_volume=bid_vol,
                ask_volume=ask_vol, size_multiplier=mult, snapshot_age_s=age,
                reason=reason, reject_code=code,
            )

        if mid <= 0 or (bid_vol <= 0 and ask_vol <= 0):
            return build(bp.ConfluenceVerdict.LIQUIDITY_TRAP_VETO.value, 0.0,
                         "leeres Orderbuch", bp.ORDERBOOK_WALL_REJECT)
        if age > self.max_age_s:
            return build(bp.ConfluenceVerdict.LIQUIDITY_TRAP_VETO.value, 0.0,
                         f"Depth-Snapshot {age:.1f}s alt (>{self.max_age_s}s)",
                         bp.ORDERBOOK_WALL_REJECT)
        if signed <= self.veto_threshold:
            return build(bp.ConfluenceVerdict.LIQUIDITY_TRAP_VETO.value, 0.0,
                         f"Wand gegen die Richtung (I_depth={raw:+.2f})",
                         bp.ORDERBOOK_WALL_REJECT)
        if signed >= self.confirm_threshold and spread <= self.max_spread_bps:
            return build(bp.ConfluenceVerdict.CONFLUENCE_CONFIRMED.value,
                         self.size_multiplier,
                         f"Tiefe stuetzt {side} (I_depth={raw:+.2f}, "
                         f"spread={spread:.1f}bps)")
        if signed >= self.confirm_threshold:
            return build(bp.ConfluenceVerdict.NEUTRAL.value, 1.0,
                         f"Tiefe ok, Spread {spread:.1f}bps > "
                         f"{self.max_spread_bps:.0f}bps — kein Bonus")
        return build(bp.ConfluenceVerdict.NEUTRAL.value, 1.0,
                     f"keine Konfluenz (I_depth={raw:+.2f})")

    # ------------------------------------------------------ telemetrie ---
    def recent(self, limit: int = 20) -> List[Dict[str, Any]]:
        return self._history[-limit:]

    def panel_state(self) -> Dict[str, Any]:
        return {
            "band_pct": self.band_pct,
            "confirm_threshold": self.confirm_threshold,
            "veto_threshold": self.veto_threshold,
            "max_spread_bps": self.max_spread_bps,
            "max_cached_depth_age_seconds": self.max_age_s,
            "size_multiplier": self.size_multiplier,
            "recent_audits": self.recent(),
        }


_VERIFIER: Optional[GlintOrderbookVerifier] = None


def get_verifier() -> GlintOrderbookVerifier:
    global _VERIFIER
    if _VERIFIER is None:
        _VERIFIER = GlintOrderbookVerifier()
    return _VERIFIER


def set_verifier(verifier: Optional[GlintOrderbookVerifier]) -> None:
    global _VERIFIER
    _VERIFIER = verifier
