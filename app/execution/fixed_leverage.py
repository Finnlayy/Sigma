"""
=========================================================
Datei:      app/execution/fixed_leverage.py
Zweck:      §29 — Strategy-Bound Fixed Leverage (kein dynamischer Hebel)
Knoten:     Jaune (Carrera-Engine) / Execution
=========================================================

Der Hebel wird **einmal pro Strategie/Bot** in ``profile.json`` festgelegt
und danach unveraendert an ``kraken trade add-order --leverage=N`` gereicht.
Eine per-Trade-Neuberechnung (``dynamic_leverage_engine``) ist im Blueprint
ausdruecklich verworfen — Latenz, TV-Divergenz, Race Conditions.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

from app.core import blueprint as bp

logger = logging.getLogger("app.execution.fixed_leverage")

PROFILE_FILENAME = "profile.json"


class DynamicLeverageRejected(RuntimeError):
    """§29 — Laufzeit-Hebelumschaltung ist nicht Teil der Spezifikation."""


@dataclass
class LeverageProfile:
    strategy_id: str
    fixed_leverage: int
    style: str = "STYLE_INTRADAY_MOMENT"
    source: str = "default"

    @property
    def badge(self) -> str:
        """Strategy-Card Badge, z. B. ``[ 5x HEBEL ]``."""
        return f"[ {self.fixed_leverage}x HEBEL ]"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "fixed_leverage": self.fixed_leverage,
            "style": self.style,
            "source": self.source,
            "badge": self.badge,
            "cli_flag": f"--leverage={self.fixed_leverage}",
        }


def clamp_leverage(value: Any) -> int:
    try:
        leverage = int(round(float(value)))
    except (TypeError, ValueError):
        leverage = bp.FIXED_LEVERAGE_DEFAULT
    return max(bp.FIXED_LEVERAGE_MIN, min(bp.FIXED_LEVERAGE_MAX, leverage))


def default_leverage_for_style(style: str) -> int:
    return bp.STYLE_DEFAULT_LEVERAGE.get(style, bp.FIXED_LEVERAGE_DEFAULT)


def profile_path(strategy_id: str, strategies_root: str = "./data/strategies") -> str:
    return os.path.join(strategies_root, strategy_id, PROFILE_FILENAME)


def load_profile(strategy_id: str, *, strategies_root: str = "./data/strategies",
                 style: Optional[str] = None) -> LeverageProfile:
    """Liest ``profile.json``; faellt sonst auf den Style-Default zurueck."""
    path = profile_path(strategy_id, strategies_root)
    data: Dict[str, Any] = {}
    source = "style_default"
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh) or {}
            source = "profile.json"
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("profile.json unlesbar (%s): %s", path, exc)

    resolved_style = str(data.get("style") or style or "STYLE_INTRADAY_MOMENT")
    if "fixed_leverage" in data:
        leverage = clamp_leverage(data["fixed_leverage"])
    else:
        leverage = default_leverage_for_style(resolved_style)
        source = "style_default"
    if "dynamic_leverage" in data or "dynamic_leverage_engine" in data:
        raise DynamicLeverageRejected(
            f"{path}: dynamischer Hebel ist laut Blueprint §29 verworfen"
        )
    return LeverageProfile(strategy_id=strategy_id, fixed_leverage=leverage,
                           style=resolved_style, source=source)


def save_profile(profile: LeverageProfile, *,
                 strategies_root: str = "./data/strategies") -> str:
    path = profile_path(profile.strategy_id, strategies_root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {
        "strategy_id": profile.strategy_id,
        "fixed_leverage": clamp_leverage(profile.fixed_leverage),
        "style": profile.style,
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    return path


def leverage_flag(profile: LeverageProfile) -> Optional[str]:
    """CLI-Flag; bei 1x kein Margin-Flag senden (Spot-kompatibel)."""
    if profile.fixed_leverage <= 1:
        return None
    return f"--leverage={profile.fixed_leverage}"
