"""
=========================================================
Datei:      sigma/loops/__init__.py
Zweck:      Öffentliche Loop-Ports A–E — dünne Adapter über app.* Engines
=========================================================
"""
from __future__ import annotations

from sigma.loops.loop_a import LoopAPort
from sigma.loops.loop_b import LoopBPort
from sigma.loops.loop_c import LoopCPort
from sigma.loops.loop_d import LoopDPort
from sigma.loops.loop_e import LoopEPort

__all__ = (
    "LoopAPort",
    "LoopBPort",
    "LoopCPort",
    "LoopDPort",
    "LoopEPort",
)
