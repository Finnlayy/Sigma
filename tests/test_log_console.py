"""§37 — Live Process & AI Log Console."""
from __future__ import annotations

import asyncio
import json
import os

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
