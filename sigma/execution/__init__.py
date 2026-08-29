"""
=========================================================
Datei:      sigma/execution/__init__.py
Zweck:      Execution-Contracts — Kraken CLI first; CCXT nicht live-registriert
=========================================================
"""
from __future__ import annotations

from sigma.execution.base_bridge import (BaseExecutionBridge, CcxtExecutionBridge,
                                         ExecutionReceipt, KrakenCliExecutionBridge)

__all__ = (
    "BaseExecutionBridge",
    "CcxtExecutionBridge",
    "ExecutionReceipt",
    "KrakenCliExecutionBridge",
)
