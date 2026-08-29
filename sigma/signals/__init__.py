"""
=========================================================
Datei:      sigma/signals/__init__.py
Zweck:      Phase-1 Signal-Contracts — Session, Throttle, Scout, Ladder, Hurst, Wave.
=========================================================
"""
from __future__ import annotations

from sigma.signals.base_alpha import AlphaSignal, BaseAlphaModel
from sigma.signals.correlation_scout import CorrelationScout, ScoutResult
from sigma.signals.dual_hurst import DualHurst, evaluate_dual_hurst, htf_ready
from sigma.signals.htf_features import extract_htf_flags
from sigma.signals.lead_lag_detector import LeadLagDetector
from sigma.signals.quantum_wave_collider import QuantumWaveCollider, WaveCollapseState
from sigma.signals.scale_features import scale_invariant_features
from sigma.signals.session_clock import SessionClock, SessionState, get_current_market_session
from sigma.signals.timeframe_ladder import (
    BIAS_PAIRS,
    EXEC_PAIRS,
    TimeframePair,
    bias_tf,
    classify_pair,
    exec_tf,
    execution_ladder_tf,
    session_exec_pair,
)
from sigma.signals.volatility_throttle import ThrottleState, VolatilityThrottleGate

__all__ = (
    "AlphaSignal",
    "BIAS_PAIRS",
    "BaseAlphaModel",
    "CorrelationScout",
    "DualHurst",
    "EXEC_PAIRS",
    "LeadLagDetector",
    "QuantumWaveCollider",
    "ScoutResult",
    "WaveCollapseState",
    "SessionClock",
    "SessionState",
    "ThrottleState",
    "TimeframePair",
    "VolatilityThrottleGate",
    "bias_tf",
    "classify_pair",
    "evaluate_dual_hurst",
    "exec_tf",
    "execution_ladder_tf",
    "extract_htf_flags",
    "get_current_market_session",
    "htf_ready",
    "scale_invariant_features",
    "session_exec_pair",
)
