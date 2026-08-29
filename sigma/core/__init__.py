"""
=========================================================
Datei:      sigma/core/__init__.py
Zweck:      Library-Core — shared math + Re-export von app.core
=========================================================
"""
from __future__ import annotations

from sigma.core.math_engine import (NAN_PENALTY, clamp, nan_penalty, sharpe,
                                    sharpe_or_penalty, sortino,
                                    sortino_or_penalty)

__all__ = (
    "NAN_PENALTY",
    "clamp",
    "nan_penalty",
    "sharpe",
    "sharpe_or_penalty",
    "sortino",
    "sortino_or_penalty",
)
