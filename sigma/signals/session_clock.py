"""
=========================================================
Datei:      sigma/signals/session_clock.py
Zweck:      AMD-Sessions + BTC 21:00-UTC-Liquiditätslücke auf Kraken-Zeit.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Blanche (Zeit) / Noir (Fail-Closed)
=========================================================
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from app.core.exchange_clock import get_exchange_clock

# UTC hours on Kraken server time (Axiom 4)
# Asia accumulation 00:00–07:00 UTC
# London Judas-sweep 07:00–13:00 UTC
# New York expansion 13:00–20:00 UTC
# US close / 21:00 UTC crypto liquidity gap 20:00–24:00 UTC
BTC_LIQUIDITY_GAP_UTC_HOUR = 21
GAP_WINDOW_MINUTES = 60


@dataclass(frozen=True)
class SessionState:
    session: str
    volatility_bias: str
    recommended_strategy: str
    max_leverage: int
    liquidity_gap: bool
    weekend_alts_paper_only: bool
    hour_utc: int
    ts: float
    description: str

    def to_dict(self) -> dict:
        return dict(self.__dict__)


class SessionClock:
    """Asia / London / NY / US-close. Crypto stays UTC. Forex EST is optional docs-only."""

    def __init__(self, now_fn: Optional[Callable[[], float]] = None) -> None:
        self._now_fn = now_fn

    def now(self) -> float:
        if self._now_fn is not None:
            return float(self._now_fn())
        try:
            return float(get_exchange_clock().now())
        except Exception:
            return time.time()

    def evaluate(self, now: Optional[float] = None) -> SessionState:
        ts = float(now) if now is not None else self.now()
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        hour = dt.hour
        minute = dt.minute
        time_float = hour + (minute / 60.0)
        weekday = dt.weekday()  # Mon=0
        weekend = weekday >= 5
        gap = self._in_liquidity_gap(hour, minute)

        if 0.0 <= time_float < 7.0:
            return SessionState(
                session="ASIA_ACCUMULATION",
                volatility_bias="LOW",
                recommended_strategy="MICRO_RANGE_GRID",
                max_leverage=5,
                liquidity_gap=False,
                weekend_alts_paper_only=weekend,
                hour_utc=hour,
                ts=ts,
                description="Narrow range. No aggressive breakouts.",
            )
        if 7.0 <= time_float < 13.0:
            return SessionState(
                session="LONDON_MANIPULATION",
                volatility_bias="CHOPPY_SWEEPS",
                recommended_strategy="DUAL_HEDGE_DCA",
                max_leverage=10,
                liquidity_gap=False,
                weekend_alts_paper_only=weekend,
                hour_utc=hour,
                ts=ts,
                description="Liquidity sweeps. Fade fakeouts with dual hedge.",
            )
        if 13.0 <= time_float < 20.0:
            return SessionState(
                session="NEW_YORK_EXPANSION",
                volatility_bias="MAX_TREND",
                recommended_strategy="HIGH_BETA_MOMENTUM",
                max_leverage=25,
                liquidity_gap=False,
                weekend_alts_paper_only=weekend,
                hour_utc=hour,
                ts=ts,
                description="Wall Street volume. HTF-direction momentum.",
            )
        return SessionState(
            session="US_CLOSE_TRANSITION",
            volatility_bias="DECREASING",
            recommended_strategy="AUTO_UNWIND_FLAT",
            max_leverage=5,
            liquidity_gap=gap,
            weekend_alts_paper_only=weekend,
            hour_utc=hour,
            ts=ts,
            description="Volatility fading. Flatten; 21:00 UTC gap is high-NSR.",
        )

    @staticmethod
    def _in_liquidity_gap(hour: int, minute: int) -> bool:
        if hour == BTC_LIQUIDITY_GAP_UTC_HOUR:
            return True
        if hour == BTC_LIQUIDITY_GAP_UTC_HOUR - 1 and minute >= 45:
            return True
        return False


def get_current_market_session(now: Optional[float] = None) -> dict:
    return SessionClock().evaluate(now).to_dict()
