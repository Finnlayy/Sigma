"""
=========================================================
Datei:      app/quant/epidemic_contagion_engine.py
Zweck:      §27 — Epidemic SIR Contagion (Makro-Fruehwarnung)
Knoten:     Ciel (Sigma Core)
=========================================================

Krisen breiten sich wie eine Infektion aus: ein Schock (Oel-Vol, Gold/DXY)
springt ueber steigende Cross-Asset-Korrelation auf Krypto ueber, waehrend
die Orderbuch-Absorption (Heilungsrate) die Ansteckung daempft.

    beta  = Infektionsrate  (Schock x Korrelation)
    gamma = Heilungsrate    (Absorptionsfaehigkeit des Orderbuchs)
    R0    = beta / gamma

* ``R0 >= 1.5`` -> ``FLIGHT_TO_CASH_AND_HEDGE``
* ``R0 >= 1.0`` -> Futures-Sizing -50 %
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.core import blueprint as bp

logger = logging.getLogger("app.quant.epidemic_contagion_engine")

_GAMMA_FLOOR = 0.05


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass
class ContagionInputs:
    """Normalisierte Makro-Inputs (§27)."""

    oil_vol_zscore: float = 0.0          # Z-Score der Oel-Volatilitaet
    gold_dxy_ratio_change: float = 0.0   # relative Aenderung Gold/DXY
    cross_asset_correlation: float = 0.0 # 0..1 (Korrelationsclustering)
    orderbook_absorption: float = 1.0    # 0..1 (1 = tiefes, robustes Buch)

    def as_dict(self) -> Dict[str, float]:
        return {
            "oil_vol_zscore": self.oil_vol_zscore,
            "gold_dxy_ratio_change": self.gold_dxy_ratio_change,
            "cross_asset_correlation": self.cross_asset_correlation,
            "orderbook_absorption": self.orderbook_absorption,
        }


@dataclass
class ContagionState:
    r0: float
    beta: float
    gamma: float
    mode: str
    size_multiplier: float
    allow_altcoin_treasury: bool
    reason: str
    inputs: Dict[str, float]
    veto_code: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "r0": round(self.r0, 4),
            "beta": round(self.beta, 4),
            "gamma": round(self.gamma, 4),
            "mode": self.mode,
            "size_multiplier": self.size_multiplier,
            "allow_altcoin_treasury": self.allow_altcoin_treasury,
            "reason": self.reason,
            "inputs": self.inputs,
            "veto_code": self.veto_code,
            "thresholds": {
                "hedge": bp.SIR_R0_HEDGE_THRESHOLD,
                "derisk": bp.SIR_R0_DERISK_THRESHOLD,
            },
        }


class EpidemicContagionEngine:
    """SIR-Fruehwarnsystem fuer systemische Marktansteckung (§27)."""

    def __init__(
        self,
        *,
        hedge_threshold: float = bp.SIR_R0_HEDGE_THRESHOLD,
        derisk_threshold: float = bp.SIR_R0_DERISK_THRESHOLD,
        derisk_multiplier: float = bp.SIR_DERISK_SIZE_MULTIPLIER,
    ) -> None:
        self.hedge_threshold = hedge_threshold
        self.derisk_threshold = derisk_threshold
        self.derisk_multiplier = derisk_multiplier
        self._state: Optional[ContagionState] = None
        self._history: List[Dict[str, Any]] = []

    # ------------------------------------------------------------ math ---
    @staticmethod
    def beta(inputs: ContagionInputs) -> float:
        shock = abs(inputs.oil_vol_zscore) * 0.30 + abs(inputs.gold_dxy_ratio_change) * 2.0
        correlation = _clamp(inputs.cross_asset_correlation, 0.0, 1.0)
        return max(0.0, shock * (0.5 + correlation))

    @staticmethod
    def gamma(inputs: ContagionInputs) -> float:
        return max(_GAMMA_FLOOR, _clamp(inputs.orderbook_absorption, 0.0, 1.0))

    def r0(self, inputs: ContagionInputs) -> float:
        return self.beta(inputs) / self.gamma(inputs)

    # ---------------------------------------------------------- update ---
    def evaluate(self, inputs: ContagionInputs) -> ContagionState:
        beta = self.beta(inputs)
        gamma = self.gamma(inputs)
        r0 = beta / gamma

        if r0 >= self.hedge_threshold:
            state = ContagionState(
                r0=r0, beta=beta, gamma=gamma,
                mode=bp.ContagionMode.FLIGHT_TO_CASH_AND_HEDGE.value,
                size_multiplier=0.0, allow_altcoin_treasury=False,
                reason=f"R0={r0:.2f} >= {self.hedge_threshold} — Flucht in Cash + Hedge",
                inputs=inputs.as_dict(), veto_code=bp.CONTAGION_VETO_CODE,
            )
        elif r0 >= self.derisk_threshold:
            state = ContagionState(
                r0=r0, beta=beta, gamma=gamma,
                mode=bp.ContagionMode.DERISK.value,
                size_multiplier=self.derisk_multiplier, allow_altcoin_treasury=False,
                reason=f"R0={r0:.2f} >= {self.derisk_threshold} — Futures-Sizing halbiert",
                inputs=inputs.as_dict(), veto_code=None,
            )
        else:
            state = ContagionState(
                r0=r0, beta=beta, gamma=gamma,
                mode=bp.ContagionMode.NORMAL.value,
                size_multiplier=1.0, allow_altcoin_treasury=True,
                reason=f"R0={r0:.2f} — keine systemische Ansteckung",
                inputs=inputs.as_dict(), veto_code=None,
            )

        self._state = state
        self._history.append(state.as_dict())
        del self._history[:-50]
        if state.mode != bp.ContagionMode.NORMAL.value:
            logger.warning("contagion mode %s (R0=%.2f)", state.mode, r0)
        return state

    # ------------------------------------------------------ allocation ---
    @property
    def state(self) -> ContagionState:
        if self._state is None:
            return self.evaluate(ContagionInputs())
        return self._state

    def apply_sizing(self, notional: float) -> float:
        """Allocator-Hook: skaliert ein Futures-Notional nach Kontagionslage."""
        return notional * self.state.size_multiplier

    def treasury_allowed(self, asset: str) -> bool:
        """Spot-Treasury: bei Kontagion keine Altcoins (§27)."""
        if self.state.allow_altcoin_treasury:
            return True
        return asset.upper() in ("XBT", "BTC", "EUR", "USD", "USDT", "USDC")

    def panel_state(self) -> Dict[str, Any]:
        return {"current": self.state.as_dict(), "history": self._history[-20:],
                "inputs_spec": list(bp.SIR_INPUTS)}


_ENGINE: Optional[EpidemicContagionEngine] = None


def get_contagion_engine() -> EpidemicContagionEngine:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = EpidemicContagionEngine()
    return _ENGINE


def set_contagion_engine(engine: Optional[EpidemicContagionEngine]) -> None:
    global _ENGINE
    _ENGINE = engine
