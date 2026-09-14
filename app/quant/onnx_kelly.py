"""
=========================================================
Datei:      app/quant/onnx_kelly.py
Zweck:      §4.2 Schritt 3+4 — QuantEngine: Confidence-Inferenz (ONNX,
            sonst deterministische Heuristik) + Half-Kelly-Sizing mit Cap.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Carrera-Engine) / Quant
=========================================================

ONNX ist optional: fehlt `onnxruntime` oder das Modell, arbeitet die Engine
mit der Heuristik weiter — ohne stillen Fehlschlag, aber ohne Absturz.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

from app.core import blueprint as bp
from app.core.config import SigmaConfig, load_config

logger = logging.getLogger("app.quant.onnx_kelly")


@dataclass
class SizingDecision:
    quantity: float
    notional: float
    win_prob: float
    kelly_fraction_used: float
    capped: bool
    source: str            # "onnx" | "heuristic"
    stop_loss: float
    take_profit: float

    def to_dict(self) -> Dict[str, Any]:
        return {k: (round(v, 8) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


class QuantEngine:
    """Confidence + Sizing. Ein Ort, eine Wahrheit."""

    def __init__(self, config: Optional[SigmaConfig] = None, model_path: Optional[str] = None):
        self.config = config or load_config()
        self.model_path = model_path or self.config.onnx_model_path
        self._session = None
        self._input_name = ""
        self._load_attempted = False

    # ------------------------------------------------------------ onnx model
    @property
    def model_available(self) -> bool:
        self._ensure_session()
        return self._session is not None

    def _ensure_session(self) -> None:
        if self._load_attempted:
            return
        self._load_attempted = True
        if not self.model_path or not os.path.exists(self.model_path):
            logger.info("ONNX model %s absent — heuristic confidence active", self.model_path)
            return
        try:
            import onnxruntime  # type: ignore

            self._session = onnxruntime.InferenceSession(
                self.model_path, providers=["CPUExecutionProvider"])
            self._input_name = self._session.get_inputs()[0].name
            logger.info("ONNX model loaded: %s", self.model_path)
        except Exception as exc:
            logger.warning("ONNX load failed (%s) — heuristic confidence active", exc)
            self._session = None

    def reload_model(self, path: Optional[str] = None) -> bool:
        """Hot-Reload nach Shadow-Gate (§21)."""
        if path:
            self.model_path = path
        self._session = None
        self._load_attempted = False
        self._ensure_session()
        return self._session is not None

    # ------------------------------------------------------------ confidence
    def predict_confidence(self, rsi: float, atr: float, cisd_score: float = 0.5,
                           *, price: float = 0.0, temperature: Optional[float] = None) -> Dict[str, Any]:
        """§4.2 Schritt 3 — Gewinnwahrscheinlichkeit ŷ ∈ (0,1)."""
        self._ensure_session()
        atr_norm = (atr / price) if price else min(abs(atr) / 100.0, 1.0)
        raw, source = self._infer(rsi, atr_norm, cisd_score)
        temp = temperature if temperature is not None else self.config.onnx_temperature
        calibrated = bp.calibrate_confidence(raw, temperature=temp)
        return {
            "win_prob": round(calibrated, 6),
            "raw": round(raw, 6),
            "temperature": temp,
            "source": source,
            "features": {"rsi": rsi, "atr_norm": round(atr_norm, 6), "cisd": cisd_score},
        }

    def _infer(self, rsi: float, atr_norm: float, cisd: float) -> tuple[float, str]:
        if self._session is not None:
            try:
                import numpy as np  # type: ignore

                vec = np.array([[float(rsi), float(atr_norm), float(cisd)]], dtype=np.float32)
                out = self._session.run(None, {self._input_name: vec})[0]
                value = float(np.ravel(out)[0])
                return min(max(value, 0.0), 1.0), "onnx"
            except Exception as exc:  # pragma: no cover - Laufzeitfehler im Modell
                logger.warning("ONNX inference failed (%s) — falling back to heuristic", exc)
        return self._heuristic(rsi, atr_norm, cisd), "heuristic"

    @staticmethod
    def _heuristic(rsi: float, atr_norm: float, cisd: float) -> float:
        """Deterministisch, monoton, dokumentiert — kein Rauschen."""
        # RSI-Extreme geben Edge (Reversion), Mitte ist neutral
        rsi_edge = abs(50.0 - float(rsi)) / 50.0            # 0..1
        # Zu hohe normierte Vol dämpft die Konfidenz
        vol_penalty = min(max(atr_norm, 0.0), 0.10) / 0.10  # 0..1
        base = 0.42 + 0.22 * rsi_edge + 0.20 * (float(cisd) - 0.5) * 2 * 0.5
        conf = base - 0.12 * vol_penalty
        return min(max(conf, 0.01), 0.95)

    # ---------------------------------------------------------------- sizing
    def size_position(self, *, equity: float, price: float, win_prob: float,
                      atr: float, action: str, rrr: float = bp.KELLY_DEFAULT_RRR) -> SizingDecision:
        """§4.2 Schritt 4+5 — Half-Kelly, Cap 10 %, ATR-Brackets."""
        qty = bp.calculate_kelly(equity, price, win_prob, rrr)
        edge = win_prob - (1.0 - win_prob) / max(rrr, 1e-9)
        raw_fraction = max(0.0, edge) * self.config.kelly_fraction
        capped = raw_fraction > self.config.max_portfolio_risk_per_trade
        sl, tp = bp.bracket_prices(price, atr, action)
        return SizingDecision(
            quantity=qty, notional=qty * price, win_prob=win_prob,
            kelly_fraction_used=min(raw_fraction, self.config.max_portfolio_risk_per_trade),
            capped=capped, source="onnx" if self._session is not None else "heuristic",
            stop_loss=sl, take_profit=tp,
        )

    def snapshot(self) -> Dict[str, Any]:
        return {
            "model_path": self.model_path,
            "model_available": self.model_available,
            "kelly_fraction": self.config.kelly_fraction,
            "max_portfolio_risk_per_trade": self.config.max_portfolio_risk_per_trade,
            "temperature": self.config.onnx_temperature,
            "atr_stop_multiplier": self.config.atr_stop_multiplier,
            "atr_take_profit_multiplier": self.config.atr_take_profit_multiplier,
        }


_engine: Optional[QuantEngine] = None


def get_quant_engine(config: Optional[SigmaConfig] = None) -> QuantEngine:
    global _engine
    if _engine is None:
        _engine = QuantEngine(config)
    return _engine
