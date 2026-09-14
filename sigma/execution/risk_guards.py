"""
=========================================================
Datei:      sigma/execution/risk_guards.py
Zweck:      Pure Hard-Risk-Guards (KB §8): Hard-Stop im Markt,
            Rastertiefe >= 6 % (Meme-Perp), BTC-Makro-Gate (nur
            closed Bars), Liq-Distanz-HITL, Cooldown, Fee-Covered-BE,
            Wick-/Liquidationsfallen-Guard (β·BTC-Wick).
            KEINE Orderplatzierung, KEINE Strategy-Logik.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Execution-Contract) / Noir (Look-ahead)
=========================================================
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

# ---------------------------------------------------------------------------
# Verträge (Dataclasses mit to_dict, volle Typannotationen)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HardStopResult:
    """Hard-Stop-Orderpreis: Long unter Entry & ueber Liq, Short spiegelbildlich."""

    stop_price: float
    side: str
    buffer_pct: float
    liquidation_price: float
    entry_price: float

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class GridDepthVerdict:
    """Verdict der Rastertiefen-Pruefung (Meme-Perp >= min_meme_depth)."""

    ok: bool
    depth_pct: float
    min_depth_pct: float
    symbol_spec: str
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class MacroBreachVerdict:
    """BTC-Makro-Gate: nur geschlossene 15m/1h-Bars; letzte offene Bar ignoriert."""

    macro_gate_closed: bool
    breached: bool
    closed_bars_used: int
    support_price: float
    side: str
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class LiquidationProximity:
    """Abstand zur Liquidation in %; < 0.05 (5 %) -> needs_hitl=True."""

    distance_pct: float
    needs_hitl: bool
    threshold_pct: float
    mark_price: float
    liquidation_price: float
    side: str

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class WickZoneVerdict:
    """Wick-/Liquidationsfallen-Guard (KB §8 Regel 10)."""

    ok: bool
    reason: str
    liquidation_price: float
    side: str
    wick_boundary_price: float

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class LeverageDepthVerdict:
    """Hebel-Pruefung: Liq-Abstand (1/Hebel bei voller Margin) muss >=
    Rastertiefe + beta*BTC-Wick + Puffer sein, sonst liegt der Liq-Preis
    in der Docht-Zone."""

    ok: bool
    leverage: float
    max_leverage: float
    required_distance_pct: float
    liq_distance_pct: float
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


def hard_stop_distance(
    entry_price: float,
    liquidation_price: float,
    side: str,
    buffer_pct: float = 0.005,
) -> HardStopResult:
    """Hard-Stop im Markt (Order, kein Panic-Close): gepuffert 0,5 % ueber dem
    Liq-Preis (long) bzw. 0,5 % unter ihm (short). Long: Stop < Entry und
    garantiert > Liq. Short spiegelbildlich (Stop > Entry, < Liq).
    """
    side_n = (side or "").lower()
    if side_n not in ("long", "short", "buy", "sell"):
        raise ValueError(f"side muss long/short/buy/sell sein, ist {side!r}")
    if entry_price <= 0 or liquidation_price <= 0:
        raise ValueError("entry_price und liquidation_price muessen > 0 sein")
    is_long = side_n in ("long", "buy")
    if is_long:
        stop = liquidation_price * (1.0 + buffer_pct)
    else:
        stop = liquidation_price * (1.0 - buffer_pct)
    return HardStopResult(
        stop_price=round(stop, 8),
        side="long" if is_long else "short",
        buffer_pct=buffer_pct,
        liquidation_price=liquidation_price,
        entry_price=entry_price,
    )


def _closed_prices(bars: Sequence[Mapping[str, Any]]) -> List[float]:
    """Nur geschlossene Bars. Die letzte Bar gilt als offen und wird ignoriert,
    ausser sie traegt explizit is_closed=True / closed=True."""
    closed: List[float] = []
    for idx, bar in enumerate(bars):
        explicit = bar.get("is_closed", bar.get("closed"))
        if explicit is None:
            if idx == len(bars) - 1:
                continue  # letzte Bar = offene Kerze
        elif not bool(explicit):
            continue
        close = bar.get("c", bar.get("close"))
        if close is not None:
            closed.append(float(close))
    return closed


def btc_macro_breach(
    btc_closed_bars: Sequence[Mapping[str, Any]],
    support_price: float,
    side: str,
) -> MacroBreachVerdict:
    """BTC-Makro-Gate: schliesst BTC (nur closed 15m/1h-Bars) unter den
    Key-Support, ist das Makro-Gate fuer Alt-Kaeufe geschlossen. Long/Buy:
    Close < Support -> breach. Short/Sell: Close > Support -> breach.
    Fail-closed: keine geschlossenen Bars -> macro_gate_closed=True.
    """
    side_n = (side or "").lower()
    is_long = side_n in ("long", "buy")
    if side_n not in ("long", "short", "buy", "sell"):
        raise ValueError(f"side muss long/short/buy/sell sein, ist {side!r}")
    prices = _closed_prices(btc_closed_bars)
    if not prices:
        return MacroBreachVerdict(
            macro_gate_closed=True,
            breached=False,
            closed_bars_used=0,
            support_price=support_price,
            side="long" if is_long else "short",
            reason="no_closed_bars_fail_closed",
        )
    breached = any(p < support_price for p in prices) if is_long else any(
        p > support_price for p in prices
    )
    return MacroBreachVerdict(
        macro_gate_closed=breached,
        breached=breached,
        closed_bars_used=len(prices),
        support_price=support_price,
        side="long" if is_long else "short",
        reason="closed_below_support" if breached and is_long else (
            "closed_above_support" if breached else "no_breach"
        ),
    )


def grid_total_depth_pct(
    ladder_prices: Sequence[float],
    anchor_price: float,
    side: str,
) -> float:
    """Kumulierte Gesamt-Tiefe eines DCA-Rasters in % des Ankerpreises
    (Dezimal, 0.06 = 6 %). Long: (anchor - tiefste Sprosse) / anchor.
    Short: (hoechste Sprosse - anchor) / anchor."""
    if not ladder_prices or anchor_price <= 0:
        return 0.0
    side_n = (side or "").lower()
    if side_n in ("long", "buy"):
        return max(0.0, (anchor_price - min(ladder_prices)) / anchor_price)
    if side_n in ("short", "sell"):
        return max(0.0, (max(ladder_prices) - anchor_price) / anchor_price)
    raise ValueError(f"side muss long/short/buy/sell sein, ist {side!r}")


def _is_meme_perp(symbol_spec: Optional[Any]) -> bool:
    """Meme-Perp-Erkennung aus symbol_spec (str oder Mapping). Fail-closed:
    unbekannte Spec wird als Meme-Perp behandelt (streng)."""
    if symbol_spec is None:
        return True
    if isinstance(symbol_spec, Mapping):
        if "is_meme" in symbol_spec:
            return bool(symbol_spec["is_meme"])
        asset_class = str(symbol_spec.get("asset_class", "")).lower()
        if "meme" in asset_class or "memecoin" in asset_class:
            return True
        if "is_perp" in symbol_spec and not bool(symbol_spec["is_perp"]):
            return False
        text = str(symbol_spec.get("symbol", "")).lower()
    else:
        text = str(symbol_spec).lower()
    if "meme" in text:
        return True
    if text.endswith(("perp", "-perp", "_perp", "perpetual")):
        return True
    # Nicht-perp Kasse-Ticker: kein Meme-Raster-Guard noetig, aber unbekannt
    # bleibt streng.
    return True


def assert_grid_depth(
    depth_pct: float,
    symbol_spec: Optional[Any],
    min_meme_depth: float = 0.06,
) -> GridDepthVerdict:
    """Lehnt Meme-Perp-Raster mit < min_meme_depth (Default 6 %) Gesamt-Tiefe
    ab. Festes 0,15 %-Raster ueber 8 Stufen (~1,1 % Tiefe) ist unzulaessig."""
    if min_meme_depth <= 0:
        raise ValueError("min_meme_depth muss > 0 sein")
    meme = _is_meme_perp(symbol_spec)
    spec_text = (
        str(symbol_spec)
        if not isinstance(symbol_spec, Mapping)
        else str(symbol_spec.get("symbol", symbol_spec))
    )
    if not meme:
        return GridDepthVerdict(
            ok=True,
            depth_pct=float(depth_pct),
            min_depth_pct=0.0,
            symbol_spec=spec_text,
            reason="no_meme_perp",
        )
    ok = float(depth_pct) >= min_meme_depth
    return GridDepthVerdict(
        ok=ok,
        depth_pct=float(depth_pct),
        min_depth_pct=min_meme_depth,
        symbol_spec=spec_text,
        reason="depth_ok" if ok else "grid_too_shallow",
    )


def liquidation_proximity_pct(
    mark_price: float,
    liq_price: float,
    side: str,
    threshold_pct: float = 0.05,
) -> LiquidationProximity:
    """Abstand zur Liquidation in % (Dezimal). < 0.05 (5 %) -> needs_hitl=True
    (HITL-Eskalation; Entscheidung zu Gunsten des Stops, wenn kein Puffer)."""
    if mark_price <= 0 or liq_price <= 0:
        raise ValueError("mark_price und liq_price muessen > 0 sein")
    side_n = (side or "").lower()
    if side_n in ("long", "buy"):
        distance = (mark_price - liq_price) / mark_price
    elif side_n in ("short", "sell"):
        distance = (liq_price - mark_price) / mark_price
    else:
        raise ValueError(f"side muss long/short/buy/sell sein, ist {side!r}")
    distance = max(0.0, distance)
    return LiquidationProximity(
        distance_pct=round(distance, 6),
        needs_hitl=distance < threshold_pct,
        threshold_pct=threshold_pct,
        mark_price=mark_price,
        liquidation_price=liq_price,
        side="long" if side_n in ("long", "buy") else "short",
    )


def cooldown_active(
    last_exit_ts: float,
    now_ts: float,
    min_seconds: float = 1800.0,
) -> bool:
    """True, wenn der Post-Exit-Cooldown (Default 30 min) noch laeuft."""
    return (now_ts - last_exit_ts) < min_seconds


def fee_covered_stop(
    entry_price: float,
    side: str,
    offset_pct: float = 0.0005,
) -> float:
    """Fee-Covered Break-Even (KB §8 Regel 6): SL nach TP1 auf
    entry * 1,0005 (long) bzw. entry * 0,9995 (short) — deckt die
    Roundtrip-Taker-Fees, liegt ueber/unter dem exakten Entry."""
    if entry_price <= 0:
        raise ValueError("entry_price muss > 0 sein")
    side_n = (side or "").lower()
    if side_n in ("long", "buy"):
        return round(entry_price * (1.0 + offset_pct), 8)
    if side_n in ("short", "sell"):
        return round(entry_price * (1.0 - offset_pct), 8)
    raise ValueError(f"side muss long/short/buy/sell sein, ist {side!r}")


def wick_buffer_pct(
    beta: float,
    expected_btc_wick_pct: float,
    extra_pct: float = 0.01,
) -> float:
    """Erwarteter Alt-Wick-Puffer in %: beta * BTC-Wick + extra_pct
    (KB §8 Regel 10: Liq-Abstand >= Rastertiefe + beta*BTC-Wick + Puffer)."""
    if beta < 0 or expected_btc_wick_pct < 0 or extra_pct < 0:
        raise ValueError("beta, expected_btc_wick_pct, extra_pct muessen >= 0 sein")
    return round(beta * expected_btc_wick_pct + extra_pct, 8)


def liq_outside_wick_zone(
    liquidation_price: float,
    wick_low_price: float,
    side: str,
) -> WickZoneVerdict:
    """Prueft, dass der Liquidationspreis AUSSERHALB der erwarteten
    Docht-Zone liegt. long: Liq muss UNTER dem erwarteten Docht-Tief liegen;
    short: Liq muss UEBER dem Docht-Hoch liegen (wick_low_price = Docht-
    Grenze auf der Gefahrenseite). Liq in der Zone -> ok=False (fail-closed)."""
    side_n = (side or "").lower()
    if side_n in ("long", "buy"):
        ok = liquidation_price < wick_low_price
        reason = "liq_below_wick_zone" if ok else "liq_inside_wick_zone"
    elif side_n in ("short", "sell"):
        ok = liquidation_price > wick_low_price
        reason = "liq_above_wick_zone" if ok else "liq_inside_wick_zone"
    else:
        raise ValueError(f"side muss long/short/buy/sell sein, ist {side!r}")
    return WickZoneVerdict(
        ok=ok,
        reason=reason,
        liquidation_price=liquidation_price,
        side="long" if side_n in ("long", "buy") else "short",
        wick_boundary_price=wick_low_price,
    )


def assert_leverage_for_depth(
    beta: float,
    grid_depth_pct: float,
    leverage: float,
    expected_btc_wick_pct: float,
    extra_pct: float = 0.01,
) -> LeverageDepthVerdict:
    """Lehnt Hebel ab, bei denen der Liq-Preis in der Docht-Zone laege.
    Faustregel KB §8 R10: Liq-Abstand (bei voller Margin ~= 1/Hebel)
    >= Rastertiefe + beta*BTC-Wick + Puffer."""
    if leverage <= 0:
        raise ValueError("leverage muss > 0 sein")
    required = float(grid_depth_pct) + wick_buffer_pct(
        beta, expected_btc_wick_pct, extra_pct=extra_pct
    )
    liq_distance = 1.0 / leverage
    max_lev = 1.0 / required if required > 0 else float("inf")
    ok = liq_distance >= required
    return LeverageDepthVerdict(
        ok=ok,
        leverage=float(leverage),
        max_leverage=round(max_lev, 6) if max_lev != float("inf") else 0.0,
        required_distance_pct=round(required, 8),
        liq_distance_pct=round(liq_distance, 8),
        reason="leverage_ok" if ok else "liq_inside_wick_zone",
    )


__all__ = [
    "GridDepthVerdict",
    "HardStopResult",
    "LeverageDepthVerdict",
    "LiquidationProximity",
    "MacroBreachVerdict",
    "WickZoneVerdict",
    "assert_grid_depth",
    "assert_leverage_for_depth",
    "btc_macro_breach",
    "cooldown_active",
    "fee_covered_stop",
    "grid_total_depth_pct",
    "hard_stop_distance",
    "liq_outside_wick_zone",
    "liquidation_proximity_pct",
    "wick_buffer_pct",
]
