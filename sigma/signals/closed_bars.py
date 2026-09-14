"""
=========================================================
Datei:      sigma/signals/closed_bars.py
Zweck:      Shared closed-bar helper for MP-03 candle/regime signals.
            Drops a trailing incomplete bar; never invents OHLC.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Blanche (Feature) / Noir (Look-ahead)
=========================================================
"""
from __future__ import annotations

from typing import Any, List, Mapping, Sequence


def closed_only(bars: Sequence[Mapping[str, Any]]) -> List[Mapping[str, Any]]:
    """Return only closed bars. A trailing open bar (``is_closed``/``closed``
    explicitly False) is dropped. Bars without an explicit open flag are kept
    (synthetic fixtures treat unmarked bars as closed).
    """
    rows = list(bars)
    if not rows:
        return []
    last = rows[-1]
    explicit = last.get("is_closed", last.get("closed"))
    if explicit is False:
        return rows[:-1]
    return rows


__all__ = ["closed_only"]
