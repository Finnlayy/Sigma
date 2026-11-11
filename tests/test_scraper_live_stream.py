"""Live TradingView OHLC streams in the scraper sidecar (no network: fake socket)."""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque

from app.scraper import live_stream as ls
from app.scraper.live_stream import (LiveStream, StreamRegistry, bars_from_packet, merge_bars,
                                     parse_frames, preserve_root_logging)


def _frame(obj) -> str:
    body = obj if isinstance(obj, str) else json.dumps(obj)
    return f"~m~{len(body)}~m~{body}"


def _series(method: str, bars):
    return {"m": method, "p": ["cs_x", {"sds_1": {"s": [{"i": i, "v": list(b)} for i, b in enumerate(bars)]}}]}


HISTORY = [(1700000000, 10.0, 12.0, 9.0, 11.0, 100.0),
           (1700000060, 11.0, 13.0, 10.0, 12.0, 110.0),
           (1700000120, 12.0, 14.0, 11.0, 13.0, 120.0)]


class FakeWs:
    def __init__(self, packets):
        self.packets = list(packets)
        self.sent = []
        self.closed = False
        self._idle = threading.Event()

    def recv(self):
        if self.packets:
            return self.packets.pop(0)
        self._idle.wait(0.05)
        raise TimeoutError("idle")

    def send(self, data):
        self.sent.append(data)

    def close(self):
        self.closed = True


def _stream(depth=300):
    return LiveStream("", "KRAKEN", "BTCUSD", "1m", depth=depth, maxlen=5000)


def test_parse_frames_splits_multiple_messages():
    raw = _frame({"m": "a"}) + _frame("~h~7") + _frame({"m": "b"})
    assert parse_frames(raw) == ['{"m": "a"}', "~h~7", '{"m": "b"}']


def test_bars_from_packet_reads_ohlcv_and_ignores_other_methods():
    bars = bars_from_packet(_series("timescale_update", HISTORY))
    assert [b["timestamp"] for b in bars] == [1700000000, 1700000060, 1700000120]
    assert bars[1] == {"timestamp": 1700000060, "open": 11.0, "high": 13.0, "low": 10.0,
                       "close": 12.0, "volume": 110.0}
    assert bars_from_packet({"m": "qsd", "p": []}) == []
    assert bars_from_packet({"m": "du", "p": ["cs_x", {}]}) == []


def test_bars_from_packet_converts_millisecond_timestamps():
    bars = bars_from_packet(_series("du", [(1700000000000, 1, 2, 0.5, 1.5, 3)]))
    assert bars[0]["timestamp"] == 1700000000


def test_merge_replaces_forming_bar_and_appends_new_bar():
    ring = deque(bars_from_packet(_series("timescale_update", HISTORY)))
    merge_bars(ring, bars_from_packet(_series("du", [(1700000120, 12.0, 15.0, 11.0, 14.5, 130.0)])))
    assert len(ring) == 3 and ring[-1]["close"] == 14.5 and ring[-1]["high"] == 15.0
    merge_bars(ring, bars_from_packet(_series("du", [(1700000180, 14.5, 14.6, 14.0, 14.2, 5.0)])))
    assert [b["timestamp"] for b in ring][-2:] == [1700000120, 1700000180]


def test_handle_history_then_update_makes_stream_live():
    stream = _stream()
    assert stream.live is False
    stream.handle(_frame(_series("timescale_update", HISTORY)))
    assert stream.live is True and len(stream.ring) == 3
    stream.handle(_frame(_series("du", [(1700000120, 12.0, 16.0, 11.0, 15.0, 140.0)])))
    snap = stream.snapshot(2)
    assert [b["close"] for b in snap] == [12.0, 15.0]
    assert stream.status()["updates"] == 2


def test_heartbeat_is_echoed_back():
    stream = _stream()
    stream._ws = FakeWs([])
    stream.handle(_frame("~h~42"))
    assert stream._ws.sent == ["~m~5~m~~h~42"]


def test_stream_goes_stale_without_packets(monkeypatch):
    stream = _stream()
    stream.handle(_frame(_series("timescale_update", HISTORY)))
    monkeypatch.setattr(ls.time, "time", lambda: stream.last_packet + ls.STALE_SECONDS + 1)
    assert stream.live is False


def _wait(pred, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_registry_serves_bars_from_live_stream():
    sockets = []

    def connect(stream):
        ws = FakeWs([_frame(_series("timescale_update", HISTORY))])
        sockets.append((stream.depth, ws))
        return ws

    reg = StreamRegistry("", max_depth=5000, connect=connect)
    try:
        reg.bars("kraken", "btcusd", "1m", 2)  # erster Aufruf startet den Stream
        assert _wait(lambda: reg.bars("KRAKEN", "BTCUSD", "1m", 2) is not None)
        bars = reg.bars("KRAKEN", "BTCUSD", "1m", 2)
        assert [b["close"] for b in bars] == [12.0, 13.0]
        assert sockets[0][0] == ls.MIN_DEPTH
        status = reg.status()
        assert status["count"] == 1 and status["live"] == 1
        assert status["streams"]["KRAKEN:BTCUSD:1m"]["bars"] == 3
    finally:
        reg.stop_all()


def test_registry_restarts_deeper_when_more_history_is_requested():
    depths = []

    def connect(stream):
        depths.append(stream.depth)
        return FakeWs([_frame(_series("timescale_update", HISTORY))])

    reg = StreamRegistry("", max_depth=5000, connect=connect)
    try:
        first = reg.ensure("KRAKEN", "BTCUSD", "1h", 100)
        second = reg.ensure("KRAKEN", "BTCUSD", "1h", 1200)
        third = reg.ensure("KRAKEN", "BTCUSD", "1h", 9000)
        assert first is not second and first.stopped
        assert second is not third
        assert (first.depth, second.depth, third.depth) == (300, 1200, 5000)
        assert reg.ensure("KRAKEN", "BTCUSD", "1h", 50) is third
    finally:
        reg.stop_all()


def test_unknown_symbol_stops_stream_and_is_not_retried():
    calls = []

    def connect(stream):
        calls.append(stream.ticker)
        raise ValueError("Invalid exchange:symbol 'KRAKEN:XBTUSD'")

    reg = StreamRegistry("", max_depth=5000, connect=connect)
    try:
        stream = reg.ensure("KRAKEN", "XBTUSD", "15m", 500)
        assert _wait(lambda: stream.invalid)
        assert reg.bars("KRAKEN", "XBTUSD", "15m", 500) is None
        assert reg.bars("KRAKEN", "XBTUSD", "15m", 500) is None
        assert calls == ["XBTUSD"]
        assert reg.status()["count"] == 0
    finally:
        reg.stop_all()


def test_registry_caps_stream_count(monkeypatch):
    monkeypatch.setattr(ls, "MAX_STREAMS", 2)
    reg = StreamRegistry("", max_depth=5000, connect=lambda s: FakeWs([]))
    try:
        assert reg.ensure("KRAKEN", "A", "1m", 10) is not None
        assert reg.ensure("KRAKEN", "B", "1m", 10) is not None
        assert reg.ensure("KRAKEN", "C", "1m", 10) is None
    finally:
        reg.stop_all()


def test_idle_streams_are_stopped():
    reg = StreamRegistry("", max_depth=5000, connect=lambda s: FakeWs([]))
    try:
        stream = reg.ensure("KRAKEN", "BTCUSD", "1m", 10)
        stream.last_read = time.time() - ls.IDLE_SECONDS - 1
        reg.ensure("KRAKEN", "ETHUSD", "1m", 10)
        assert stream.stopped
        assert list(reg.status()["streams"]) == ["KRAKEN:ETHUSD:1m"]
    finally:
        reg.stop_all()


def test_preserve_root_logging_undoes_vendor_debug_config():
    root = logging.getLogger()
    level, handlers = root.level, list(root.handlers)
    with preserve_root_logging():
        logging.basicConfig(level=logging.DEBUG, force=False)
        root.setLevel(logging.DEBUG)
        root.addHandler(logging.NullHandler())
    assert root.level == level and root.handlers == handlers
    assert logging.getLogger("tradingview_scraper").level == logging.WARNING
    assert logging.getLogger("urllib3").level == logging.WARNING


def test_later_single_bar_timescale_update_does_not_wipe_history():
    stream = _stream()
    stream.handle(_frame(_series("timescale_update", HISTORY)))
    stream.handle(_frame(_series("timescale_update", [(1700000180, 13.0, 13.5, 12.5, 13.2, 9.0)])))
    assert [b["timestamp"] for b in stream.snapshot(10)] == [1700000000, 1700000060, 1700000120, 1700000180]


def test_known_invalid_symbol_is_reported_for_every_timeframe():
    reg = StreamRegistry("", max_depth=5000,
                         connect=lambda s: (_ for _ in ()).throw(ValueError("Invalid exchange:symbol 'X'")))
    try:
        stream = reg.ensure("KRAKEN", "XBTUSD", "1d", 10)
        assert _wait(lambda: stream.invalid)
        assert reg.is_invalid("KRAKEN", "XBTUSD") is True
        reg.bars("KRAKEN", "XBTUSD", "1d", 10)      # moves it to the retry-blocklist
        assert reg.is_invalid("KRAKEN", "XBTUSD") is True
        assert reg.is_invalid("KRAKEN", "BTCUSD") is False
    finally:
        reg.stop_all()
