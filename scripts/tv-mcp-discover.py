#!/usr/bin/env python3
"""Discover the official TradingView MCP tool surface.

Handshake: initialize → notifications/initialized → tools/list over
streamable HTTP. Prints one JSON object. Calls no other tool.

Auth comes from SIGMA_TV_MCP_TOKEN (Bearer). The token is never printed.
If the endpoint answers 401, the probe stops and reports OAuth.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

ENDPOINT = "https://mcp.tradingview.com/mcp"
PROTOCOL = "2025-03-26"
ACCEPT = "application/json, text/event-stream"


def _post(url: str, payload: dict[str, Any] | None, headers: dict[str, str]) -> tuple[int, dict[str, str], bytes]:
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, {k.lower(): v for k, v in exc.headers.items()}, exc.read()


def _decode_body(content_type: str, raw: bytes) -> Any:
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return None
    if "text/event-stream" in content_type:
        messages: list[Any] = []
        for block in text.split("\n\n"):
            data_lines = [
                line[5:].lstrip()
                for line in block.splitlines()
                if line.startswith("data:")
            ]
            if not data_lines:
                continue
            chunk = "\n".join(data_lines).strip()
            if chunk == "[DONE]":
                continue
            try:
                messages.append(json.loads(chunk))
            except json.JSONDecodeError:
                messages.append({"unparsed": chunk})
        if len(messages) == 1:
            return messages[0]
        return messages
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"unparsed": text[:2000]}


def _verdict(tools: list[dict[str, Any]]) -> dict[str, str]:
    names = [str(tool.get("name") or "") for tool in tools if isinstance(tool, dict)]

    def pick(needles: tuple[str, ...]) -> str:
        for name in names:
            lowered = name.lower()
            if any(needle in lowered for needle in needles):
                return name
        return "none"

    return {
        "BACKTEST_TOOL": pick(("backtest", "strategy_tester")),
        "OHLC_TOOL": pick(("ohlc", "candle", "ohlcv")),
    }


def _tool_summaries(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        schema = tool.get("inputSchema") or tool.get("input_schema") or {}
        props = schema.get("properties") if isinstance(schema, dict) else None
        rows.append({
            "name": tool.get("name"),
            "input_schema_keys": sorted(props.keys()) if isinstance(props, dict) else [],
        })
    return rows


def main() -> int:
    token = os.environ.get("SIGMA_TV_MCP_TOKEN", "").strip()
    headers = {
        "Content-Type": "application/json",
        "Accept": ACCEPT,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    init_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": PROTOCOL,
            "capabilities": {},
            "clientInfo": {"name": "sigma-tv-mcp-discover", "version": "1"},
        },
    }
    status, response_headers, raw = _post(ENDPOINT, init_payload, headers)
    content_type = response_headers.get("content-type", "")
    body = _decode_body(content_type, raw)
    report: dict[str, Any] = {
        "endpoint": ENDPOINT,
        "auth": "bearer" if token else "none",
        "initialize": {
            "http_status": status,
            "www_authenticate": response_headers.get("www-authenticate"),
            "body": body,
        },
        "serverInfo": None,
        "tools": [],
        "BACKTEST_TOOL": "none",
        "OHLC_TOOL": "none",
    }

    if status == 401:
        report["auth"] = "oauth"
        report["stopped"] = "oauth_only"
        json.dump(report, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    if status >= 400:
        report["stopped"] = f"http_{status}"
        json.dump(report, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 1

    result = body.get("result") if isinstance(body, dict) else None
    if isinstance(result, dict):
        report["serverInfo"] = result.get("serverInfo")
    session = response_headers.get("mcp-session-id")
    if session:
        headers["Mcp-Session-Id"] = session
        report["mcp_session_id_present"] = True

    note_status, _, _ = _post(
        ENDPOINT,
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers,
    )
    report["initialized_notification_status"] = note_status

    list_status, list_headers, list_raw = _post(
        ENDPOINT,
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        headers,
    )
    listed = _decode_body(list_headers.get("content-type", ""), list_raw)
    tools: list[dict[str, Any]] = []
    payload = listed.get("result") if isinstance(listed, dict) else None
    if isinstance(payload, dict) and isinstance(payload.get("tools"), list):
        tools = [item for item in payload["tools"] if isinstance(item, dict)]
    report["tools_list_status"] = list_status
    report["tools"] = _tool_summaries(tools)
    report.update(_verdict(tools))
    json.dump(report, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0 if list_status < 400 else 1


if __name__ == "__main__":
    raise SystemExit(main())
