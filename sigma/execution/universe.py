"""
=========================================================
Datei:      sigma/execution/universe.py
Zweck:      ExecutionUniverse — Port über dem Execution-Layer.
            Die angeschlossene Venue ist Source of Truth fürs
            tradable Universe — nicht der Scraper, nicht
            config.market_symbols (das ist Feed, nicht Execution).
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Execution-Contract)
=========================================================

Invarianten:
  * list_symbols() liefert kanonische Sigma-Symbole (BTC/USD),
    nie rohe Venue-Ticker (XBTUSD) — sonst divergieren Loop C
    (Sigma-Form) und Loop A (to_kraken_pair).
  * is_tradable() spiegelt Loop A: ein Symbol ist tradable, wenn
    mindestens eine live-registrierte Venue es annimmt.
  * Nicht-live Adapter (Pionex-Stub, CCXT ohne live Bridge) liefern
    ein leeres Universe — keine toten Symbole, kein Fake.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Iterable, List, Mapping, Optional, Sequence

from app.core import blueprint as bp
from app.tv.symbol_map import is_allowed, market_type, to_sigma_symbol


class ExecutionUniverse(ABC):
    """Port: tradable Watchlist über dem Execution-Layer."""

    #: True, wenn die Venue live registriert ist. Nicht-live Adapter
    #: (Pionex-Stub, CCXT ohne live Bridge) fliegen aus dem Composite.
    live_registered: bool = True

    @abstractmethod
    def list_symbols(self) -> List[str]:
        """Kanonische Sigma-Symbole (BTC/USD). Nie rohe Venue-Ticker."""

    @abstractmethod
    def is_tradable(self, symbol: str) -> bool:
        """True iff mindestens eine aktive Venue das Symbol nimmt."""


class KrakenExecutionUniverse(ExecutionUniverse):
    """Kraken Spot + Kraken Pro (Futures PI_*) über ``is_allowed()``.

    Ein Adapter, beide Märkte — EXCHANGE_SPOT (XBTUSD/ETHUSD) und
    EXCHANGE_FUTURES (PI_*). ``include_futures`` steuert nur die
    Default-Watchlist (``list_symbols``); ``is_tradable`` folgt dem
    Loop-A-Gate unabhängig davon.
    """

    live_registered = True

    def __init__(self, include_futures: bool = False) -> None:
        self.include_futures = bool(include_futures)

    def list_symbols(self) -> List[str]:
        spot = [to_sigma_symbol(p) for p in bp.EXCHANGE_SPOT["allowed_symbols"]]
        futures = (
            [to_sigma_symbol(p) for p in bp.EXCHANGE_FUTURES["allowed_symbols"]]
            if self.include_futures
            else []
        )
        return spot + futures

    def is_tradable(self, symbol: str) -> bool:
        futures = market_type(symbol) == "FUTURES"
        return is_allowed(symbol, futures=futures)


class PionexExecutionUniverse(ExecutionUniverse):
    """Port-Stub — gleiche API, noch keine live Pionex-Bridge.

    Sobald eine Pionex-Bridge live_registered=True ist, wird der Adapter
    ins Composite gehängt; Screen/Academy ändern sich nicht.
    """

    live_registered = False

    def list_symbols(self) -> List[str]:
        return []

    def is_tradable(self, symbol: str) -> bool:
        return False


class CcxtExecutionUniverse(ExecutionUniverse):
    """Liest das Universe erst von einer live-registrierten CCXT-Bridge.

    ``CcxtExecutionBridge`` ist heute NotImplemented / nicht live — daraus
    wird bewusst kein Universe gezogen (sonst wieder tote Symbole).
    Fail-Closed: ohne live Bridge → leeres Universe.
    """

    live_registered = False

    def __init__(self, bridge: Optional[Any] = None) -> None:
        self.bridge = bridge

    def _markets(self) -> List[str]:
        if self.bridge is None or not getattr(self.bridge, "live_registered", False):
            return []
        load_markets = getattr(self.bridge, "load_markets", None)
        if not callable(load_markets):
            return []
        # Später: markets = load_markets() -> kanonische Sigma-Symbole.
        return []

    def list_symbols(self) -> List[str]:
        return self._markets()

    def is_tradable(self, symbol: str) -> bool:
        return symbol in self._markets()


class CompositeExecutionUniverse(ExecutionUniverse):
    """Union der live-registrierten Adapter.

    Ein Symbol ist tradable, wenn mindestens ein aktiver Adapter es
    nimmt. Default bleibt Kraken, bis eine andere Bridge
    live_registered=True ist.
    """

    live_registered = True

    def __init__(self, adapters: Optional[List[ExecutionUniverse]] = None) -> None:
        self.adapters = [
            a for a in (adapters or []) if getattr(a, "live_registered", True)
        ]

    def list_symbols(self) -> List[str]:
        out: List[str] = []
        seen = set()
        for adapter in self.adapters:
            for symbol in adapter.list_symbols():
                if symbol not in seen:
                    seen.add(symbol)
                    out.append(symbol)
        return out

    def is_tradable(self, symbol: str) -> bool:
        return any(adapter.is_tradable(symbol) for adapter in self.adapters)


# ----------------------------------------------------------------------
# Default-Verdrahtung: Kraken bleibt Source of Truth, bis eine andere
# Bridge live_registered=True registriert wird.
# ----------------------------------------------------------------------

_DEFAULT_KRAKEN = KrakenExecutionUniverse()
_REGISTERED_VENUES: List[ExecutionUniverse] = []


def register_venue(adapter: ExecutionUniverse) -> None:
    """Hängt einen Venue-Adapter an das Default-Composite an."""
    if adapter not in _REGISTERED_VENUES:
        _REGISTERED_VENUES.append(adapter)


def reset_venues() -> None:
    """Test-Seam: Registry leeren (nur live Adapter zählen)."""
    _REGISTERED_VENUES.clear()


def default_execution_universe() -> ExecutionUniverse:
    """Kraken (Default) + alle live-registrierten Venues als Composite."""
    live = [a for a in _REGISTERED_VENUES if getattr(a, "live_registered", False)]
    if not live:
        return _DEFAULT_KRAKEN
    return CompositeExecutionUniverse([_DEFAULT_KRAKEN] + live)


def mover_symbol(row: Mapping[str, Any]) -> str:
    """Roh-Ticker aus einer TV-Movers-Zeile → kanonische Sigma-Form oder ''."""
    raw = row.get("symbol") or row.get("ticker") or row.get("name") or row.get("pair") or ""
    if not raw:
        return ""
    try:
        return to_sigma_symbol(str(raw))
    except Exception:
        return ""


def rank_watchlist(
    wanted: Sequence[str],
    mover_rows: Optional[Iterable[Mapping[str, Any]]] = None,
) -> List[str]:
    """Sortiert die Watchlist nach TV-Movers. Erweitert das Universe nicht.

    Symbole, die in ``wanted`` fehlen (z. B. ein SOL-Gainer), werden
    verworfen. Fehlende / leere Movers → ursprüngliche Reihenfolge.
    """
    wanted_list = [s for s in wanted if s]
    if not wanted_list or not mover_rows:
        return list(wanted_list)
    wanted_set = set(wanted_list)
    ranked: List[str] = []
    seen = set()
    for row in mover_rows:
        if not isinstance(row, Mapping):
            continue
        symbol = mover_symbol(row)
        if symbol and symbol in wanted_set and symbol not in seen:
            ranked.append(symbol)
            seen.add(symbol)
    for symbol in wanted_list:
        if symbol not in seen:
            ranked.append(symbol)
    return ranked
