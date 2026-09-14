"""
=========================================================
Datei:      sigma/__init__.py
Zweck:      Public facade der Quant-Library. Implementation bleibt unter app/.
            Re-exportiert app.* plus Loop-Ports, Base*-Contracts, Math-Engine.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Library-Fassade)
=========================================================

Kein Rename app/ → sigma/. Live-Orders laufen weiter nur durch Loop A
(Kraken CLI). Fehlende Daten liefern empty/degraded — nie synthetische Coins.
"""
from __future__ import annotations

import app as app
import app.backtest as backtest
import app.core as core
import app.optimizer as optimizer
import app.quant as quant
import app.scout as scout
import app.tv as tv

from sigma.loops import LoopAPort, LoopBPort, LoopCPort, LoopDPort, LoopEPort
from sigma.orchestration import MasterOrchestrator

__all__ = (
    "app",
    "backtest",
    "core",
    "optimizer",
    "quant",
    "scout",
    "tv",
    "LoopAPort",
    "LoopBPort",
    "LoopCPort",
    "LoopDPort",
    "LoopEPort",
    "MasterOrchestrator",
)
