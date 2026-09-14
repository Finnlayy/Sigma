"""
=========================================================
Datei:      app/execution/kraken_cli_registry.py
Zweck:      Machine-readable Kraken CLI 0.4.1 leaf registry (Single-Book).
            Name → argv → book → dangerous → Sigma adapter.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Carrera-Engine) / Execution
=========================================================

Source of truth for *which* CLI leaves exist. Execution still goes through
``KrakenCliBridge`` (subprocess). This is NOT ``KrakenMCPBridge``'s 149
fake tools.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

REGISTRY_PATH = Path(__file__).with_name("kraken_cli_registry.json")


@lru_cache(maxsize=1)
def load_registry() -> Dict[str, Any]:
    with open(REGISTRY_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def all_commands() -> List[Dict[str, Any]]:
    return list(load_registry().get("commands") or [])


def get_command(name: str) -> Optional[Dict[str, Any]]:
    key = (name or "").strip().lstrip("/")
    for cmd in all_commands():
        if cmd.get("name") == key:
            return cmd
    return None


def argv_for(name: str, *extra: str, binary: str = "kraken") -> List[str]:
    """Build argv for a registered leaf. Raises KeyError if unknown."""
    cmd = get_command(name)
    if cmd is None:
        raise KeyError(f"unknown kraken CLI leaf: {name!r}")
    base = list(cmd.get("argv") or [])
    if base and base[0] == "kraken":
        base[0] = binary
    return base + [str(x) for x in extra]


def commands_by_book(book: str) -> List[Dict[str, Any]]:
    return [c for c in all_commands() if c.get("book") == book]


def dangerous_commands() -> List[Dict[str, Any]]:
    return [c for c in all_commands() if c.get("dangerous")]


def wired_commands() -> List[Dict[str, Any]]:
    """Leaves with a non-stub adapter (specialized or ``run_leaf``)."""
    return [c for c in all_commands() if c.get("adapter") and c.get("adapter") != "stub"]


def stub_commands() -> List[Dict[str, Any]]:
    """Should be empty after Single-Book wiring — kept for regression asserts."""
    return [c for c in all_commands() if (c.get("adapter") or "stub") == "stub"]


def leaf_count() -> int:
    return int(load_registry().get("leaf_count") or len(all_commands()))


def reload_registry() -> Dict[str, Any]:
    """Clear cache and re-read JSON (tests / hot reload)."""
    load_registry.cache_clear()
    return load_registry()
