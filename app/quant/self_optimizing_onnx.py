"""
=========================================================
Datei:      app/quant/self_optimizing_onnx.py
Zweck:      §21 / Masterprompt §3.B — Self-Optimizing ONNX Engine.
            Brier-Score-Tracking, adaptive Temperatur, Drift-Erkennung,
            Shadow-Gate + Zero-Downtime Hot-Reload.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune / Noir (Shadow-Gate)
=========================================================
"""
from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Tuple

from app.core import blueprint as bp

logger = logging.getLogger("app.quant.self_opt")


@dataclass
class CalibrationRecord:
    predicted: float
    outcome: float          # 1.0 = Gewinn, 0.0 = Verlust
    ts: float = field(default_factory=time.time)
    strategy_id: str = ""


class SelfOptimizingOnnxEngine:
    """Hält die Konfidenz ehrlich: schlechter Brier → höhere Temperatur → kleinere Kelly."""

    def __init__(self, quant_engine=None, window: int = 100,
                 brier_threshold: float = bp.BRIER_DRIFT_THRESHOLD,
                 min_samples: int = bp.MIN_TRADES_FOR_GATE):
        self.quant = quant_engine
        self.window = window
        self.brier_threshold = brier_threshold
        self.min_samples = min_samples
        self.temperature = bp.ONNX_TEMPERATURE_DEFAULT
        self.bias = 0.0
        self._records: Deque[CalibrationRecord] = deque(maxlen=window)
        self.retrain_requested = False
        self.retrain_count = 0
        self.last_retrain_ts = 0.0
        self.shadow_candidate: Optional[str] = None

    # ---------------------------------------------------------------- ingest
    def record(self, predicted: float, outcome: float, strategy_id: str = "") -> Dict[str, Any]:
        self._records.append(CalibrationRecord(float(predicted), 1.0 if outcome > 0 else 0.0,
                                               strategy_id=strategy_id))
        return self.evaluate()

    @property
    def sample_size(self) -> int:
        return len(self._records)

    @property
    def brier(self) -> float:
        if not self._records:
            return 0.0
        return bp.brier_score([r.predicted for r in self._records],
                              [r.outcome for r in self._records])

    # -------------------------------------------------------------- evaluate
    def evaluate(self) -> Dict[str, Any]:
        """Passt die Temperatur an und markiert Drift (ab min_samples)."""
        bs = self.brier
        drifting = self.sample_size >= self.min_samples and bs > self.brier_threshold
        if self.sample_size >= self.min_samples:
            new_temp = bp.next_temperature(self.temperature, bs)
            if abs(new_temp - self.temperature) > 1e-9:
                logger.info("ONNX temperature %.2f -> %.2f (brier %.4f)", self.temperature, new_temp, bs)
            self.temperature = new_temp
            if self.quant is not None:
                try:
                    self.quant.config.onnx_temperature = self.temperature
                except Exception:  # pragma: no cover
                    pass
        if drifting and self.temperature >= bp.ONNX_TEMPERATURE_MAX:
            # Dämpfung ausgereizt -> autonomes Re-Training anfordern
            self.retrain_requested = True
        return self.snapshot(drifting=drifting)

    def calibrate(self, raw_confidence: float) -> float:
        return bp.calibrate_confidence(raw_confidence, self.temperature, self.bias)

    # ----------------------------------------------------- shadow + reload
    def propose_model(self, candidate_path: str) -> None:
        self.shadow_candidate = candidate_path

    def shadow_gate(self, candidate_predictions: List[float], outcomes: List[float]) -> Dict[str, Any]:
        """Neues Modell darf nur live, wenn es den aktuellen Brier schlägt (§21)."""
        if len(candidate_predictions) < self.min_samples:
            return {"passed": False, "reason": f"need >= {self.min_samples} shadow samples",
                    "samples": len(candidate_predictions)}
        candidate_bs = bp.brier_score(candidate_predictions, outcomes)
        incumbent_bs = self.brier
        passed = candidate_bs < incumbent_bs and candidate_bs <= self.brier_threshold
        return {
            "passed": passed,
            "candidate_brier": round(candidate_bs, 6),
            "incumbent_brier": round(incumbent_bs, 6),
            "threshold": self.brier_threshold,
            "samples": len(candidate_predictions),
            "reason": "candidate better" if passed else "candidate rejected",
        }

    def hot_reload(self, model_path: Optional[str] = None) -> Dict[str, Any]:
        path = model_path or self.shadow_candidate
        if not path:
            return {"reloaded": False, "reason": "no candidate"}
        ok = False
        if self.quant is not None:
            ok = bool(self.quant.reload_model(path))
        self.retrain_requested = False
        self.retrain_count += 1
        self.last_retrain_ts = time.time()
        self.temperature = bp.ONNX_TEMPERATURE_DEFAULT
        self._records.clear()
        self.shadow_candidate = None
        logger.info("ONNX hot-reload %s (ok=%s)", path, ok)
        return {"reloaded": ok, "model_path": path, "temperature": self.temperature}

    # -------------------------------------------------------------- snapshot
    def snapshot(self, drifting: Optional[bool] = None) -> Dict[str, Any]:
        bs = self.brier
        if drifting is None:
            drifting = self.sample_size >= self.min_samples and bs > self.brier_threshold
        return {
            "brier": round(bs, 6),
            "brier_threshold": self.brier_threshold,
            "temperature": round(self.temperature, 3),
            "bias": self.bias,
            "samples": self.sample_size,
            "min_samples": self.min_samples,
            "drift": bool(drifting),
            "retrain_requested": self.retrain_requested,
            "retrain_count": self.retrain_count,
            "shadow_candidate": self.shadow_candidate,
            "model_available": bool(self.quant and self.quant.model_available),
        }


_engine: Optional[SelfOptimizingOnnxEngine] = None


def get_self_optimizing_engine(quant_engine=None) -> SelfOptimizingOnnxEngine:
    global _engine
    if _engine is None:
        _engine = SelfOptimizingOnnxEngine(quant_engine)
    return _engine
