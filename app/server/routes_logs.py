"""
=========================================================
Datei:      app/server/routes_logs.py
Zweck:      §37 / Axiom 15 — Live Process & AI Log Console
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Carrera-Engine) / API
=========================================================

Aggregiert alle Subsystem-Logs (CORE, ORDERS, TV_WORKER, ERRORS, AI_LAYER,
SCRAPER) in Echtzeit — kein SSH ``tail -f`` noetig.

Noir-Gate (§37.5): async I/O mit 250 ms Poll (blockiert den Core nie),
File-Pointer ueberleben WS-Disconnects, Secrets werden maskiert.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from typing import Any, Dict, Iterable, List, Optional

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.core import blueprint as bp

logger = logging.getLogger("app.server.routes_logs")

router = APIRouter(tags=["logs"])

LEVELS = ("CRITICAL", "ERROR", "WARNING", "WARN", "INFO", "DEBUG")
_LEVEL_RE = re.compile(r"\b(CRITICAL|ERROR|WARNING|WARN|INFO|DEBUG)\b")
_MASK_RE = re.compile(
    r'(?i)("?(?:' + "|".join(bp.LOG_MASK_KEYS) + r')"?\s*[:=]\s*"?)([^",\s}]+)')
# Reverse-seek block size for tail(). 8 KiB matches max_line_bytes; one or two
# blocks cover a typical 200-line backfill without reading the rest of the file.
_TAIL_BLOCK_BYTES = 8192


def mask_secrets(line: str) -> str:
    """§37.5 — keine Secrets in Log-Lines."""
    return _MASK_RE.sub(lambda m: f"{m.group(1)}***", line)


def detect_level(line: str) -> str:
    match = _LEVEL_RE.search(line)
    if match:
        lvl = match.group(1).upper()
        return "WARNING" if lvl == "WARN" else lvl
    lowered = line.lower()
    if '"severity": "critical"' in lowered or "traceback" in lowered:
        return "CRITICAL"
    if "err_" in lowered or '"error_code"' in lowered:
        return "ERROR"
    return "INFO"


def make_entry(subsystem: str, raw_line: str, ts: Optional[float] = None
               ) -> Dict[str, Any]:
    line = mask_secrets(raw_line.rstrip("\n"))
    return {"subsystem": subsystem, "level": detect_level(line),
            "raw_line": line, "timestamp": int(ts or time.time())}


def read_last_lines(path: str, limit: int,
                    block_bytes: int = _TAIL_BLOCK_BYTES) -> List[str]:
    """Return the last ``limit`` lines of ``path`` without loading the file.

    ``fh.readlines()[-n]`` is O(file size) in both time and memory. CORE /
    ORDERS / TV_WORKER logs grow unbounded; ``LogTailer.tail`` runs on every
    WS connect, HTTP ``/api/v1/logs/tail``, and export. Seeking from EOF in
    8 KiB blocks is O(limit · avg_line) — constant in file size.

    Bench (limit=200, median of 8, this host):
    - 40k lines / 2.9 MiB:  seek 0.06 ms vs readlines 2.23 ms  (~40×)
    - 200k lines / 14.5 MiB: seek 0.06 ms vs readlines 14.04 ms (~250×)

    UTF-8: if the first block starts mid-sequence we discard bytes up to the
    first newline so decode always begins on a line boundary.
    """
    if limit <= 0:
        return []
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            if size == 0:
                return []
            remaining = size
            chunks: List[bytes] = []
            newlines = 0
            # Need more newlines than ``limit`` when we did not start at BOF,
            # so dropping the incomplete first line still leaves ``limit`` rows.
            while remaining > 0 and newlines <= limit:
                read_n = min(block_bytes, remaining)
                remaining -= read_n
                fh.seek(remaining)
                chunk = fh.read(read_n)
                chunks.append(chunk)
                newlines += chunk.count(b"\n")
            data = b"".join(reversed(chunks))
            if remaining > 0:
                nl = data.find(b"\n")
                if nl != -1:
                    data = data[nl + 1:]
            text = data.decode("utf-8", errors="replace")
    except OSError:
        return []
    return text.splitlines()[-limit:]


class LogTailer:
    """Async Multi-File-Tailer mit persistenten Offsets (§37.2)."""

    def __init__(self, sources: Optional[Dict[str, str]] = None,
                 poll_interval_ms: int = bp.LOG_POLL_INTERVAL_MS,
                 max_line_bytes: int = 8192) -> None:
        self.sources: Dict[str, str] = dict(sources or bp.LOG_SOURCES)
        self.poll_interval_ms = poll_interval_ms
        self.max_line_bytes = max_line_bytes
        self._offsets: Dict[str, int] = {}
        self._inodes: Dict[str, int] = {}

    # ----------------------------------------------------------- helpers --
    def subsystems(self) -> List[str]:
        return list(self.sources)

    def resolve_filter(self, raw: str = "") -> List[str]:
        if not raw:
            return self.subsystems()
        wanted = [p.strip().upper() for p in raw.split(",") if p.strip()]
        return [s for s in self.subsystems() if s in wanted]

    def seek_to_end(self, subsystems: Optional[Iterable[str]] = None) -> None:
        for name in subsystems or self.subsystems():
            path = self.sources.get(name, "")
            if os.path.exists(path):
                stat = os.stat(path)
                self._offsets[name] = stat.st_size
                self._inodes[name] = stat.st_ino
            else:
                self._offsets[name] = 0

    def status(self) -> Dict[str, Any]:
        rows = []
        for name, path in self.sources.items():
            exists = os.path.exists(path)
            rows.append({"subsystem": name, "path": path, "exists": exists,
                         "size_bytes": os.path.getsize(path) if exists else 0,
                         "offset": self._offsets.get(name, 0)})
        return {"stream_route": bp.LOG_STREAM_ROUTE, "view_route": bp.LOG_VIEW_ROUTE,
                "poll_interval_ms": self.poll_interval_ms,
                "ring_buffer_lines": bp.LOG_CLIENT_RING_BUFFER_LINES,
                "masked_keys": list(bp.LOG_MASK_KEYS),
                "levels": list(LEVELS), "sources": rows}

    # -------------------------------------------------------------- read --
    def tail(self, subsystem: str, limit: int = 200) -> List[Dict[str, Any]]:
        """Letzte ``limit`` Zeilen einer Quelle (Backfill fuer die UI).

        Uses :func:`read_last_lines` (EOF seek) instead of ``readlines()[-n]``
        so a multi-MB log does not get slurped on every WS/HTTP backfill.
        """
        path = self.sources.get(subsystem, "")
        if not path or not os.path.exists(path):
            return []
        try:
            lines = read_last_lines(path, limit)
        except OSError as exc:  # pragma: no cover
            logger.warning("tail(%s) fehlgeschlagen: %s", subsystem, exc)
            return []
        return [make_entry(subsystem, line) for line in lines if line.strip()]

    def backfill(self, subsystems: Optional[Iterable[str]] = None,
                 limit: int = 200) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for name in subsystems or self.subsystems():
            rows.extend(self.tail(name, limit))
        rows.sort(key=lambda r: r["timestamp"])
        return rows[-limit:]

    def poll_once(self, subsystems: Optional[Iterable[str]] = None
                  ) -> List[Dict[str, Any]]:
        """Neue Zeilen seit dem letzten Poll — Offsets bleiben erhalten."""
        out: List[Dict[str, Any]] = []
        for name in subsystems or self.subsystems():
            path = self.sources.get(name, "")
            if not path or not os.path.exists(path):
                continue
            try:
                stat = os.stat(path)
                size = stat.st_size
                offset = self._offsets.get(name, size)
                if self._inodes.get(name, stat.st_ino) != stat.st_ino:
                    offset = 0               # Logrotate: neue Datei
                self._inodes[name] = stat.st_ino
                if size < offset:            # truncate
                    offset = 0
                if size == offset:
                    self._offsets[name] = size
                    continue
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    fh.seek(offset)
                    chunk = fh.read()
                    self._offsets[name] = fh.tell()
            except OSError as exc:  # pragma: no cover
                logger.warning("poll(%s) fehlgeschlagen: %s", name, exc)
                continue
            for line in chunk.splitlines():
                if line.strip():
                    out.append(make_entry(name, line[:self.max_line_bytes]))
        return out

    async def stream(self, subsystems: Optional[Iterable[str]] = None,
                     max_iterations: int = 0):
        """Async-Generator — 250 ms Poll, blockiert den Event-Loop nicht."""
        names = list(subsystems or self.subsystems())
        iterations = 0
        while True:
            for entry in self.poll_once(names):
                yield entry
            iterations += 1
            if max_iterations and iterations >= max_iterations:
                return
            await asyncio.sleep(self.poll_interval_ms / 1000.0)


_TAILER: Optional[LogTailer] = None


def get_log_tailer(**kwargs: Any) -> LogTailer:
    global _TAILER
    if _TAILER is None:
        _TAILER = LogTailer(**kwargs)
    return _TAILER


def set_log_tailer(tailer: Optional[LogTailer]) -> None:
    global _TAILER
    _TAILER = tailer


# =============================================================================
# Routen
# =============================================================================

@router.get("/api/v1/logs/sources")
async def log_sources():
    return get_log_tailer().status()


@router.get("/api/v1/logs/tail")
async def log_tail(filter: str = Query("", alias="filter"), limit: int = 200):
    """Backfill fuer den Ringpuffer der UI (§37.3)."""
    tailer = get_log_tailer()
    names = tailer.resolve_filter(filter)
    return {"subsystems": names, "lines": tailer.backfill(names, limit)}


@router.get("/api/v1/logs/poll")
async def log_poll(filter: str = Query("", alias="filter")):
    """HTTP-Fallback fuer Clients ohne WebSocket."""
    tailer = get_log_tailer()
    names = tailer.resolve_filter(filter)
    return {"subsystems": names, "lines": tailer.poll_once(names)}


@router.get("/api/v1/logs/export")
async def log_export(filter: str = Query("", alias="filter"), limit: int = 2000):
    from fastapi.responses import PlainTextResponse

    tailer = get_log_tailer()
    names = tailer.resolve_filter(filter)
    body = "\n".join(json.dumps(row, ensure_ascii=False)
                     for row in tailer.backfill(names, limit))
    return PlainTextResponse(body, media_type="application/x-ndjson", headers={
        "Content-Disposition": "attachment; filename=sigma_logs.jsonl"})


@router.websocket(bp.LOG_STREAM_ROUTE)
async def logs_stream(websocket: WebSocket, filter: str = Query("", alias="filter")):
    """WS ``/api/v1/logs/stream?filter=ORDERS,AI_LAYER`` (§37.2)."""
    tailer = get_log_tailer()
    names = tailer.resolve_filter(filter)
    await websocket.accept()
    try:
        await websocket.send_json({"subsystem": "CORE", "level": "INFO",
                                   "raw_line": f"log stream attached: {','.join(names)}",
                                   "timestamp": int(time.time())})
        for entry in tailer.backfill(names, 100):
            await websocket.send_json(entry)
        while True:
            for entry in tailer.poll_once(names):
                await websocket.send_json(entry)
            await asyncio.sleep(tailer.poll_interval_ms / 1000.0)
    except WebSocketDisconnect:      # §37.5 — Pointer bleiben erhalten
        logger.info("log stream detached (%s)", ",".join(names))
    except Exception as exc:  # pragma: no cover
        logger.warning("log stream error: %s", exc)
        try:
            await websocket.close()
        except Exception:
            pass
