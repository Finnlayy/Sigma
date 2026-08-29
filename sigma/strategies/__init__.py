"""
=========================================================
Datei:      sigma/strategies/__init__.py
Zweck:      Strategy-Contracts (Templates folgen in Phase 2)
=========================================================
"""
from __future__ import annotations

from sigma.strategies.base_strategy import BaseStrategy, StrategyIntent
from sigma.strategies.dual_hedge_grid import DualHedgeGrid
from sigma.strategies.dynamic_channel_dca import DynamicChannelDCA
from sigma.strategies.htf_trend_ltf_reversion import HtfTrendLtfReversion
from sigma.strategies.pine_v6_generator import generate_htf_ltf_pine

__all__ = (
    "BaseStrategy",
    "DualHedgeGrid",
    "DynamicChannelDCA",
    "HtfTrendLtfReversion",
    "StrategyIntent",
    "generate_htf_ltf_pine",
)
