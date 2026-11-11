"""
=========================================================
Datei:      app/scraper/live_stream.py
Zweck:      Live-OHLC über eine dauerhafte TradingView-WebSocket
            pro (Exchange, Ticker, Timeframe). Der vendored
            Streamer baut die Chart-Session auf (`timescale_update`
            = Historie), danach pusht TradingView `du`-Pakete für
            die laufende und jede neue Kerze. Ein Daemon-Thread pro
            Stream liest sie, beantwortet Heartbeats und führt jedes
            Update in den Ringpuffer. Abbrüche reconnecten mit
            Backoff; solange ein Stream keine Daten hat, bedient
            der Einmal-Abruf in `vendor_bridge` die Anfrage.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Blanche (Datenabfrage)
=========================================================
"""
from __future__ import annotations

import json
import logging
import re
import sys
import threading
import time
from collections import deque
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("app.scraper.stream")

_FRAME = re.compile(r"~m~\d+~m~")
_HEARTBEAT = re.compile(r"^~h~\d+$")
STALE_SECONDS = 120.0       # kein Paket (auch kein Heartbeat) so lange -> Stream gilt als down
RECV_TIMEOUT_S = 45.0
BACKOFF_S = (2.0, 5.0, 15.0, 30.0, 60.0)
MIN_DEPTH = 300             # jeder Stream lädt mindestens so viel Historie
MAX_STREAMS = 24
IDLE_SECONDS = 900.0        # Streams ohne Leser so lange -> gestoppt
INVALID_RETRY_SECONDS = 600.0  # unbekanntes Symbol: so lange kein neuer Stream

NOISY_LOGGERS = ("urllib3", "requests", "websocket", "websockets", "tradingview_scraper")

StreamKey = Tuple[str, str, str]


# =============================================================================
# Log-Hygiene: der Vendor ruft beim Import `logging.basicConfig(level=DEBUG)`
# =============================================================================

def quiet_vendor_logging(level: int = logging.WARNING) -> None:
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(level)


class preserve_root_logging:
    """Kontextmanager für Importe, die das Root-Logging auf DEBUG stellen."""

    def __enter__(self) -> None:
        root = logging.getLogger()
        self._level, self._handlers = root.level, list(root.handlers)

    def __exit__(self, *_exc: object) -> None:
        root = logging.getLogger()
        for handler in list(root.handlers):
            if handler not in self._handlers:
                root.removeHandler(handler)
        root.setLevel(self._level)
        quiet_vendor_logging()


# =============================================================================
# Paket-Parsing
# =============================================================================

def parse_frames(raw: str) -> List[str]:
    return [part for part in _FRAME.split(raw) if part]


def bars_from_packet(pkt: Dict[str, Any]) -> List[Dict[str, Any]]:
    """OHLC aus `timescale_update` oder `du` (beide tragen `p[1].sds_1.s`)."""
    if pkt.get("m") not in {"timescale_update", "du"}:
        return []
    try:
        series = pkt["p"][1]["sds_1"]["s"]
    except (KeyError, IndexError, TypeError):
        return []
    out: List[Dict[str, Any]] = []
    for entry in series:
        values = (entry.get("v") or []) if isinstance(entry, dict) else []
        if len(values) < 5:
            continue
        ts = float(values[0])
        out.append({
            "timestamp": int(ts / 1000 if ts > 1e11 else ts),
            "open": float(values[1]),
            "high": float(values[2]),
            "low": float(values[3]),
            "close": float(values[4]),
            "volume": float(values[5]) if len(values) > 5 and values[5] is not None else 0.0,
        })
    out.sort(key=lambda c: c["timestamp"])
    return out


def merge_bars(ring: deque, bars: List[Dict[str, Any]]) -> None:
    """`du`-Update: laufende Kerze ersetzen, neue anhängen."""
    if not bars:
        return
    first = bars[0]["timestamp"]
    while ring and ring[-1]["timestamp"] >= first:
        ring.pop()
    ring.extend(bars)


# =============================================================================
# Ein Stream
# =============================================================================

class LiveStream:
    def __init__(self, vendor_path: str, exchange: str, ticker: str, timeframe: str,
                 depth: int, maxlen: int,
                 connect: Optional[Callable[["LiveStream"], Any]] = None) -> None:
        self.vendor_path = vendor_path
        self.exchange, self.ticker, self.timeframe = exchange, ticker, timeframe
        self.depth = depth
        self.ring: deque = deque(maxlen=maxlen)
        self.lock = threading.Lock()
        self.last_packet = 0.0
        self.last_read = time.time()
        self.has_history = False
        self.invalid = False
        self.updates = 0
        self.errors = 0
        self.last_error: Optional[str] = None
        self._connect_fn = connect or _vendor_connect
        self._stop = threading.Event()
        self._ws: Any = None
        self._thread = threading.Thread(
            target=self._run, name=f"tv-stream-{exchange}:{ticker}:{timeframe}", daemon=True)

    # ----------------------------------------------------------- lifecycle
    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            if self._ws is not None:
                self._ws.close()
        except Exception:  # noqa: BLE001 - Socket kann schon zu sein
            logger.debug("close on stop failed for %s:%s", self.exchange, self.ticker)

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    @property
    def live(self) -> bool:
        return self.has_history and time.time() - self.last_packet < STALE_SECONDS

    def snapshot(self, count: int) -> List[Dict[str, Any]]:
        self.last_read = time.time()
        with self.lock:
            return [dict(bar) for bar in list(self.ring)[-count:]]

    def status(self) -> Dict[str, Any]:
        return {
            "live": self.live,
            "bars": len(self.ring),
            "depth": self.depth,
            "updates": self.updates,
            "last_packet_age_s": round(time.time() - self.last_packet, 1) if self.last_packet else None,
            "invalid": self.invalid,
            "errors": self.errors,
            "last_error": self.last_error,
        }

    # -------------------------------------------------------------- thread
    def handle(self, raw: str) -> None:
        self.last_packet = time.time()
        for part in parse_frames(raw):
            if _HEARTBEAT.match(part):
                if self._ws is not None:
                    self._ws.send(f"~m~{len(part)}~m~{part}")  # Echo hält die Session offen
                continue
            try:
                pkt = json.loads(part)
            except ValueError:
                continue
            bars = bars_from_packet(pkt)
            if not bars:
                continue
            with self.lock:
                # TradingView schickt `timescale_update` auch für einzelne neue
                # Kerzen; nur das erste ersetzt den Puffer, alle weiteren mergen.
                if pkt.get("m") == "timescale_update" and not self.has_history:
                    self.ring.clear()
                    self.ring.extend(bars)
                    self.has_history = True
                else:
                    merge_bars(self.ring, bars)
                self.updates += 1

    def _run(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            try:
                self._ws = self._connect_fn(self)
                attempt = 0
                while not self._stop.is_set():
                    self.handle(self._ws.recv())
            except ValueError as exc:
                if "Invalid exchange:symbol" in str(exc):
                    self.invalid = True
                    self.last_error = str(exc)[:200]
                    logger.warning("tv stream %s:%s unknown at TradingView, stopped",
                                   self.exchange, self.ticker)
                    self._stop.set()
                    break
                self._record_drop(exc)
            except Exception as exc:  # noqa: BLE001 - jeder Abbruch: Reconnect mit Backoff
                if self._stop.is_set():
                    break
                self._record_drop(exc)
            finally:
                try:
                    if self._ws is not None:
                        self._ws.close()
                except Exception:  # noqa: BLE001
                    logger.debug("close after drop failed for %s:%s", self.exchange, self.ticker)
                self._ws = None
            self._stop.wait(BACKOFF_S[min(attempt, len(BACKOFF_S) - 1)])
            attempt += 1

    def _record_drop(self, exc: Exception) -> None:
        self.errors += 1
        self.last_error = f"{type(exc).__name__}: {exc}"[:200]
        logger.warning("tv stream %s:%s %s dropped: %s",
                       self.exchange, self.ticker, self.timeframe, self.last_error)


def _vendor_connect(stream: LiveStream) -> Any:
    if stream.vendor_path and stream.vendor_path not in sys.path:
        sys.path.insert(0, stream.vendor_path)
    with preserve_root_logging():
        from tradingview_scraper.symbols.stream import Streamer
        from tradingview_scraper.symbols.stream.utils import validate_symbols

    symbol = f"{stream.exchange}:{stream.ticker}"
    validate_symbols(symbol)
    streamer = Streamer(export_result=False)
    handler = streamer.stream_obj
    streamer._add_symbol_to_sessions(handler.quote_session, handler.chart_session, symbol,
                                     stream.timeframe, stream.depth)
    handler.ws.settimeout(RECV_TIMEOUT_S)
    return handler.ws


# =============================================================================
# Registry: ein Stream pro Key, gestartet beim ersten Lesen
# =============================================================================

class StreamRegistry:
    def __init__(self, vendor_path: str, max_depth: int,
                 connect: Optional[Callable[[LiveStream], Any]] = None) -> None:
        self.vendor_path = vendor_path
        self.max_depth = max_depth
        self._connect = connect
        self._streams: Dict[StreamKey, LiveStream] = {}
        self._invalid_until: Dict[StreamKey, float] = {}
        self._lock = threading.Lock()

    def bars(self, exchange: str, ticker: str, timeframe: str, count: int) -> Optional[List[Dict[str, Any]]]:
        """Die letzten `count` Kerzen aus dem Live-Stream, oder None (Aufrufer holt einmalig)."""
        stream = self.ensure(exchange.upper(), ticker.upper(), timeframe, count)
        if stream is None or not stream.live:
            return None
        # Tiefe >= count (ensure); junge Paare liefern ggf. weniger -> alles, was es gibt.
        return stream.snapshot(count) or None

    def is_invalid(self, exchange: str, ticker: str) -> bool:
        """TradingView kennt das Symbol nicht (in irgendeinem Timeframe gemeldet)."""
        now = time.time()
        with self._lock:
            if any(k[:2] == (exchange, ticker) and until > now
                   for k, until in self._invalid_until.items()):
                return True
            return any(k[:2] == (exchange, ticker) and s.invalid for k, s in self._streams.items())

    def ensure(self, exchange: str, ticker: str, timeframe: str, count: int) -> Optional[LiveStream]:
        key = (exchange, ticker, timeframe)
        now = time.time()
        with self._lock:
            self._evict_idle(now)
            if self._invalid_until.get(key, 0.0) > now:
                return None
            stream = self._streams.get(key)
            if stream is not None and stream.invalid:
                self._invalid_until[key] = now + INVALID_RETRY_SECONDS
                del self._streams[key]
                return None
            if stream is not None and (stream.depth < count or stream.stopped):
                stream.stop()
                stream = None
            if stream is None:
                if len(self._streams) >= MAX_STREAMS:
                    return None
                depth = min(max(count, MIN_DEPTH), self.max_depth)
                stream = LiveStream(self.vendor_path, exchange, ticker, timeframe,
                                    depth=depth, maxlen=self.max_depth, connect=self._connect)
                self._streams[key] = stream
                stream.start()
            return stream

    def _evict_idle(self, now: float) -> None:
        for key, stream in list(self._streams.items()):
            if now - stream.last_read > IDLE_SECONDS:
                stream.stop()
                del self._streams[key]

    def stop_all(self) -> None:
        with self._lock:
            for stream in self._streams.values():
                stream.stop()
            self._streams.clear()

    def status(self) -> Dict[str, Any]:
        with self._lock:
            streams = {":".join(k): s.status() for k, s in self._streams.items()}
        return {"count": len(streams), "live": sum(1 for s in streams.values() if s["live"]),
                "max": MAX_STREAMS, "streams": streams}
