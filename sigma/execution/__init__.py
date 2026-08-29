"""
=========================================================
Datei:      sigma/execution/__init__.py
Zweck:      Execution-Contracts — Kraken CLI first; CCXT nicht live-registriert
=========================================================
"""
from __future__ import annotations

from sigma.execution.base_bridge import (BaseExecutionBridge, CcxtExecutionBridge,
                                         ExecutionReceipt, KrakenCliExecutionBridge)
from sigma.execution.universe import (
    CcxtExecutionUniverse,
    CompositeExecutionUniverse,
    ExecutionUniverse,
    KrakenExecutionUniverse,
    PionexExecutionUniverse,
    default_execution_universe,
    register_venue,
    reset_venues,
)

__all__ = (
    "BaseExecutionBridge",
    "CcxtExecutionBridge",
    "CcxtExecutionUniverse",
    "CompositeExecutionUniverse",
    "ExecutionReceipt",
    "ExecutionUniverse",
    "KrakenCliExecutionBridge",
    "KrakenExecutionUniverse",
    "PionexExecutionUniverse",
    "default_execution_universe",
    "register_venue",
    "reset_venues",
)
