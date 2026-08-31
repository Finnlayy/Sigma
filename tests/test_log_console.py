"""§37 — Live Process & AI Log Console."""
from __future__ import annotations

import asyncio
import json
import os
import time

import pytest
from fastapi.testclient import TestClient

from app.core import blueprint as bp
from app.server import routes_logs as rl
from app.server.main import app


@pytest.fixture()
def tailer(tmp_path):
    sources = {name: str(tmp_path / f"{name.lower()}.log") for name in bp.LOG_SOURCES}
    t = rl.LogTailer(sources=sources)
    rl.set_log_tailer(t)
    yield t
    rl.set_log_tailer(None)


def append(tailer: rl.LogTailer, subsystem: str, *lines: str) -> None:
    with open(tailer.sources[subsystem], "a", encoding="utf-8") as fh:
        for line in lines:
            fh.write(line + "\n")


# --------------------------------------------------------------- blueprint --
def test_blueprint_log_constants():
    assert bp.LOG_STREAM_ROUTE == "/api/v1/logs/stream"
    assert bp.LOG_VIEW_ROUTE == "/logs"
    assert bp.LOG_POLL_INTERVAL_MS == 250
    assert bp.LOG_CLIENT_RING_BUFFER_LINES == 2000
    assert set(bp.LOG_SOURCES) == {"CORE", "ORDERS", "TV_WORKER", "ERRORS",
                                   "AI_LAYER", "SCRAPER"}


def test_section_37_not_pending():
    assert not any(s.startswith("37 ") for s in bp.DOCS_PENDING_SECTIONS)
    assert "ProcessLogView" in bp.ALL_TERMINAL_PANELS
    assert "OBSERVABILITY" in bp.ALL_TERMINAL_PRESETS


# ---------------------------------------------------------------- masking --
@pytest.mark.parametrize("raw,needle", [
    ('{"secret": "abc123"}', '"secret": "***"'),
    ("token=deadbeef", "token=***"),
    ('api_key: "sk-live-1"', 'api_key: "***"'),
    ("password=hunter2", "password=***"),
])
def test_mask_secrets(raw, needle):
    assert needle in rl.mask_secrets(raw)
    assert "hunter2" not in rl.mask_secrets(raw)


def test_mask_keeps_normal_text():
    assert rl.mask_secrets("order filled 0.01 XBTUSD") == "order filled 0.01 XBTUSD"


def test_detect_level():
    assert rl.detect_level("2026-08-28 ERROR boom") == "ERROR"
    assert rl.detect_level("WARN spread high") == "WARNING"
    assert rl.detect_level('{"error_code": "ERR_X"}') == "ERROR"
    assert rl.detect_level("Traceback (most recent call last)") == "CRITICAL"
    assert rl.detect_level("plain line") == "INFO"


def test_make_entry_shape():
    entry = rl.make_entry("AI_LAYER", "ONNX Brier Score: 0.142\n", ts=1787786800)
    assert entry == {"subsystem": "AI_LAYER", "level": "INFO",
                     "raw_line": "ONNX Brier Score: 0.142",
                     "timestamp": 1787786800}


# ----------------------------------------------------------------- tailer --
def test_tail_backfill(tailer):
    append(tailer, "ORDERS", *[f"fill {i}" for i in range(10)])
    rows = tailer.tail("ORDERS", limit=3)
    assert [r["raw_line"] for r in rows] == ["fill 7", "fill 8", "fill 9"]
    assert all(r["subsystem"] == "ORDERS" for r in rows)


def test_tail_missing_file_is_empty(tailer):
    assert tailer.tail("SCRAPER") == []


def test_read_last_lines_matches_readlines_on_small_file(tmp_path):
    path = tmp_path / "small.log"
    path.write_text("a\nb\nc\nd\n", encoding="utf-8")
    assert rl.read_last_lines(str(path), 2) == ["c", "d"]
    assert rl.read_last_lines(str(path), 10) == ["a", "b", "c", "d"]
    assert rl.read_last_lines(str(path), 0) == []


def test_read_last_lines_no_trailing_newline(tmp_path):
    path = tmp_path / "partial.log"
    path.write_text("one\ntwo\nthree", encoding="utf-8")
    assert rl.read_last_lines(str(path), 2) == ["two", "three"]


def test_read_last_lines_utf8_and_blank_skipped_by_tail(tailer, tmp_path):
    path = tailer.sources["CORE"]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("alpha\n\nbeta ß\ngamma\n")
    rows = tailer.tail("CORE", limit=10)
    assert [r["raw_line"] for r in rows] == ["alpha", "beta ß", "gamma"]


def test_tail_large_file_only_returns_last_n(tailer):
    """EOF-seek must equal readlines()[-n] and stay cheap on a multi-MB log.

    40k lines × ~40 bytes ≈ 1.6 MiB. Seeking 8 KiB blocks for limit=5
    should finish well under a full-file scan (bench in this test).
    """
    path = tailer.sources["CORE"]
    n_lines = 40_000
    with open(path, "w", encoding="utf-8") as fh:
        for i in range(n_lines):
            fh.write(f"fill {i:06d} extra padding for realistic line width\n")

    t0 = time.perf_counter()
    rows = tailer.tail("CORE", limit=5)
    seek_ms = (time.perf_counter() - t0) * 1000.0

    t1 = time.perf_counter()
    with open(path, "r", encoding="utf-8") as fh:
        expected = [ln.rstrip("\n") for ln in fh.readlines()[-5:]]
    scan_ms = (time.perf_counter() - t1) * 1000.0

    assert [r["raw_line"] for r in rows] == expected
    assert expected == [f"fill {i:06d} extra padding for realistic line width"
                        for i in range(n_lines - 5, n_lines)]
    # Seek path is the contract; timing is noisy on CI so we only log it.
    logger = __import__("logging").getLogger("test_log_console")
    logger.info("tail seek=%.2f ms vs readlines=%.2f ms (n=%d)",
                seek_ms, scan_ms, n_lines)


def test_read_last_lines_tiny_blocks_span_multiple_reads(tmp_path):
    path = tmp_path / "blocks.log"
    path.write_text("\n".join(f"L{i}" for i in range(50)) + "\n", encoding="utf-8")
    assert rl.read_last_lines(str(path), 3, block_bytes=8) == ["L47", "L48", "L49"]


def test_poll_once_only_returns_new_lines(tailer):
    append(tailer, "CORE", "boot")
    tailer.seek_to_end()
    assert tailer.poll_once() == []
    append(tailer, "CORE", "webhook received")
    rows = tailer.poll_once()
    assert [r["raw_line"] for r in rows] == ["webhook received"]
    assert tailer.poll_once() == []


def test_poll_handles_truncation(tailer):
    append(tailer, "CORE", "line one", "line two", "line three")
    tailer.seek_to_end()
    with open(tailer.sources["CORE"], "w", encoding="utf-8") as fh:
        fh.write("cut\n")          # kleinere Datei -> Offset-Reset
    assert [r["raw_line"] for r in tailer.poll_once()] == ["cut"]


def test_poll_handles_logrotate(tailer):
    append(tailer, "CORE", "before rotate")
    tailer.seek_to_end()
    path = tailer.sources["CORE"]
    os.replace(path, path + ".1")          # neue Inode nach Rotation
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("after rotate padded to be longer than before\n")
    assert [r["raw_line"] for r in tailer.poll_once()] == [
        "after rotate padded to be longer than before"]


def test_poll_masks_secrets(tailer):
    tailer.seek_to_end()
    append(tailer, "CORE", 'auth {"secret": "topsecret"}')
    row = tailer.poll_once()[0]
    assert "topsecret" not in row["raw_line"] and "***" in row["raw_line"]


def test_resolve_filter(tailer):
    assert tailer.resolve_filter("ORDERS,AI_LAYER") == ["ORDERS", "AI_LAYER"]
    assert tailer.resolve_filter("nonsense") == []
    assert tailer.resolve_filter() == tailer.subsystems()


def test_offsets_survive_between_polls(tailer):
    append(tailer, "TV_WORKER", "start")
    tailer.poll_once(["TV_WORKER"])
    offset = tailer._offsets["TV_WORKER"]
    assert offset > 0
    append(tailer, "TV_WORKER", "pine compiled")
    assert len(tailer.poll_once(["TV_WORKER"])) == 1
    assert tailer._offsets["TV_WORKER"] > offset


def test_status_reports_sources(tailer):
    append(tailer, "ERRORS", '{"error_code": "ERR_X"}')
    status = tailer.status()
    assert status["poll_interval_ms"] == 250
    assert status["ring_buffer_lines"] == 2000
    errors = next(s for s in status["sources"] if s["subsystem"] == "ERRORS")
    assert errors["exists"] is True and errors["size_bytes"] > 0


def test_async_stream_yields(tailer):
    tailer.poll_interval_ms = 1
    tailer.seek_to_end()
    append(tailer, "AI_LAYER", "regime=RISK_ON")

    async def collect():
        out = []
        async for entry in tailer.stream(["AI_LAYER"], max_iterations=1):
            out.append(entry)
        return out

    rows = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(collect())
    assert [r["raw_line"] for r in rows] == ["regime=RISK_ON"]


# -------------------------------------------------------------------- API --
@pytest.fixture()
def client(tailer):
    return TestClient(app), tailer


def test_api_sources(client):
    c, _ = client
    body = c.get("/api/v1/logs/sources").json()
    assert body["stream_route"] == bp.LOG_STREAM_ROUTE
    assert len(body["sources"]) == 6
    assert "secret" in body["masked_keys"]


def test_api_tail_and_filter(client):
    c, t = client
    append(t, "ORDERS", "fill A")
    append(t, "CORE", "boot")
    body = c.get("/api/v1/logs/tail?filter=ORDERS").json()
    assert body["subsystems"] == ["ORDERS"]
    assert [l["raw_line"] for l in body["lines"]] == ["fill A"]


def test_api_poll_incremental(client):
    c, t = client
    append(t, "CORE", "one")
    t.seek_to_end()
    assert c.get("/api/v1/logs/poll").json()["lines"] == []
    append(t, "CORE", "two")
    assert [l["raw_line"] for l in c.get("/api/v1/logs/poll").json()["lines"]] == ["two"]


def test_api_export(client):
    c, t = client
    append(t, "ERRORS", '{"error_code":"ERR_SYS_DUCKDB_LOCK"}')
    r = c.get("/api/v1/logs/export?filter=ERRORS")
    assert r.status_code == 200
    assert json.loads(r.text.splitlines()[0])["subsystem"] == "ERRORS"


def test_websocket_stream_backfill(client):
    c, t = client
    append(t, "AI_LAYER", "onnx warm")
    with c.websocket_connect("/api/v1/logs/stream?filter=AI_LAYER") as ws:
        hello = ws.receive_json()
        assert "attached" in hello["raw_line"]
        entry = ws.receive_json()
        assert entry["subsystem"] == "AI_LAYER" and entry["raw_line"] == "onnx warm"


def test_websocket_masks_secrets(client):
    c, t = client
    append(t, "CORE", 'alert secret="leak-me"')
    with c.websocket_connect("/api/v1/logs/stream?filter=CORE") as ws:
        ws.receive_json()
        entry = ws.receive_json()
        assert "leak-me" not in entry["raw_line"]


def test_singleton_tailer():
    rl.set_log_tailer(None)
    assert rl.get_log_tailer() is rl.get_log_tailer()
    rl.set_log_tailer(None)


def test_frontend_files_exist():
    assert os.path.exists("src/pages/ProcessLogView.tsx")
    src = open("src/pages/ProcessLogView.tsx", encoding="utf-8").read()
    assert "RING_BUFFER_LINES = 2000" in src
    assert "logStreamUrl" in src
    registry = open("src/components/sigma/panels.tsx", encoding="utf-8").read()
    assert "ProcessLogView," in registry
