"""
=========================================================
Datei:      sigma/ports/polymarket_gamma_feeder.py
Zweck:      MP-06-Production-Wire: echter Polymarket-Gamma-Mapper
            (GET https://gamma-api.polymarket.com/events?slug=...)
            -> kanonische PolymarketOdds: Strike-Leiter aus
            markets[*].groupItemTitle, Yes-Preise aus
            outcomePrices[0], kumulative Dichte -> diskrete
            Bin-Wahrscheinlichkeiten, Erwartungswert mu,
            Directional-Bias und Decay-Trajektorien
            (1h/2h/4h/EOD/Res). Gate 0.60 = reine Telemetrie
            (kein Trade-Blocker). TTL + fail-closed: fehlende/
            korrupte/synthetische Feeds -> valid=False.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Blanche (Ingestion) / Jaune (Valuation)
=========================================================
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

EPS = 1e-12

# Trajektorien-Gewichte (Master-Prompt §2.B.4) — kanonisch, nicht frei.
TRAJECTORY_WEIGHTS: Dict[str, float] = {
    "1h": 0.15,
    "2h": 0.30,
    "4h": 0.55,
    "EOD": 0.85,
    "Res": 1.00,
}

GAMMA_MIN_VOLUME_USD = 1_000_000.0   # Märkte < 1 Mio. USD Volumen verwerfen
GAMMA_DEFAULT_TTL_S = 300.0          # Snapshot-TTL (Settings: POLYMARKET_TTL_S)
GATE_PROB = 0.60                     # Gate 0.60 — nur Telemetrie/Anzeige


@dataclass(frozen=True)
class PolymarketOdds:
    """Kanonische, validierte Gamma-Ableitung (Strike-Leiter)."""

    valid: bool
    reason: str
    slug: str = ""
    title: str = ""
    volume24hr_usd: float = 0.0
    liquidity_usd: float = 0.0
    spot_price: float = 0.0
    strikes: List[float] = field(default_factory=list)      # K_1 < ... < K_n
    yes_probs: List[float] = field(default_factory=list)    # P(K_i) kumulativ
    density_bins: List[Dict[str, float]] = field(default_factory=list)
    mu: Optional[float] = None
    bias_pct: Optional[float] = None
    trajectories: Dict[str, float] = field(default_factory=dict)
    gate_060: bool = False
    source_ts: Optional[float] = None
    ttl_s: float = GAMMA_DEFAULT_TTL_S

    def is_stale(self, now: Optional[float] = None) -> bool:
        if self.source_ts is None or now is None:
            return True
        return (float(now) - self.source_ts) > self.ttl_s

    def to_dict(self) -> Dict[str, Any]:
        return {
            "valid": self.valid,
            "reason": self.reason,
            "slug": self.slug,
            "title": self.title,
            "volume24hr_usd": round(self.volume24hr_usd, 2),
            "liquidity_usd": round(self.liquidity_usd, 2),
            "spot_price": round(self.spot_price, 8),
            "strikes": self.strikes,
            "yes_probs": self.yes_probs,
            "density_bins": self.density_bins,
            "mu": (None if self.mu is None else round(self.mu, 8)),
            "bias_pct": (None if self.bias_pct is None else round(self.bias_pct, 4)),
            "trajectories": {k: round(v, 8) for k, v in self.trajectories.items()},
            "gate_060": self.gate_060,
            "source_ts": self.source_ts,
            "ttl_s": self.ttl_s,
            "stale": self.is_stale(),
        }


# ------------------------------------------------------------------ parsing

def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_strike(raw: Any) -> Optional[float]:
    """groupItemTitle -> Strike-Preis. Titel wie '45,000' oder '>50k' -> 0/None."""
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "").replace(" ", "")
    if not text:
        return None
    cleaned = text.lstrip(">").lstrip("<").lstrip("=")
    try:
        value = float(cleaned)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return value


def _yes_price(market: Mapping[str, Any]) -> Optional[float]:
    """outcomePrices[0] = Yes-Preis der Strike-Leiter."""
    raw = market.get("outcomePrices")
    if not isinstance(raw, (list, tuple)) or not raw:
        return None
    value = _as_float(raw[0], default=float("nan"))
    if value != value:  # NaN
        return None
    return value


# ------------------------------------------------------------ math (Kern)

def density_from_ladder(
    strikes: Sequence[float],
    yes_probs: Sequence[float],
) -> Tuple[List[Dict[str, float]], float]:
    """Diskrete Dichte aus der kumulativen Yes-Leiter (Wiring-Doc: NICHT
    Σ(strike·pYes)/Σ(pYes)):

        P([K_i, K_{i+1})) = P(K_i) - P(K_{i+1})
        P([-inf, K_1))    = 1 - P(K_1)
        P([K_n, +inf))    = P(K_n)

    Negative Bins (nicht-monotone Leiter) -> ValueError (fail-closed).
    Gibt (bins, sum_prob) zurück; sum_prob muss ~1 sein."""
    ks = [float(k) for k in strikes]
    ps = [max(0.0, min(1.0, float(p))) for p in yes_probs]
    if len(ks) != len(ps) or len(ks) < 2:
        raise ValueError("Strike-Leiter braucht >= 2 geordnete Stufen")
    if any(ks[i] >= ks[i + 1] for i in range(len(ks) - 1)):
        raise ValueError("Strikes nicht streng aufsteigend")
    if any(p > 1.0 + EPS or p < -EPS for p in ps):
        raise ValueError("Yes-Wahrscheinlichkeiten ausserhalb [0,1]")

    bins: List[Dict[str, float]] = []
    probs: List[float] = []
    for i in range(len(ks) - 1):
        p = ps[i] - ps[i + 1]
        if p < -EPS:
            raise ValueError("kumulative Yes-Leiter nicht monoton fallend "
                             f"(Bin {ks[i]}->{ks[i + 1]}: {p:.4f})")
        probs.append(max(0.0, p))
        bins.append({
            "low": ks[i],
            "high": ks[i + 1],
            "mid": (ks[i] + ks[i + 1]) / 2.0,
            "prob": round(max(0.0, p), 8),
        })
    # Ränder
    p_tail_low = 1.0 - ps[0]
    p_tail_high = ps[-1]
    if p_tail_low < -EPS or p_tail_high < -EPS:
        raise ValueError("Leiter-Ränder negativ (korrupter Feed)")
    probs.append(p_tail_low)
    bins.append({"low": None, "high": ks[0], "mid": ks[0] - (ks[1] - ks[0]) / 2.0,
                 "prob": round(max(0.0, p_tail_low), 8), "tail": "low"})
    probs.append(p_tail_high)
    bins.append({"low": ks[-1], "high": None, "mid": ks[-1] + (ks[-1] - ks[-2]) / 2.0,
                 "prob": round(max(0.0, p_tail_high), 8), "tail": "high"})
    sum_prob = sum(probs)
    if abs(sum_prob - 1.0) > 0.05:
        raise ValueError(f"Wahrscheinlichkeitssumme {sum_prob:.3f} != ~1 (korrupter Feed)")
    return bins, sum_prob


def mu_from_density(bins: Sequence[Mapping[str, Any]]) -> float:
    """Wahrscheinlichkeitsgewichteter Erwartungswert: mu = Σ midpoint_k · P_k."""
    return sum(float(b["mid"]) * float(b["prob"]) for b in bins)


def bias_from_mu(mu: float, spot_price: float) -> float:
    """Bias_% = (mu - p_spot) / p_spot * 100."""
    if spot_price <= 0:
        raise ValueError("spot_price muss > 0 sein")
    return (mu - spot_price) / spot_price * 100.0


def trajectories_from(spot_price: float, mu: float,
                      weights: Optional[Mapping[str, float]] = None) -> Dict[str, float]:
    """Decay-gewichtete Zielzonen: p̂(T) = p_spot + (mu - p_spot) · w(T)."""
    w = dict(weights) if weights else dict(TRAJECTORY_WEIGHTS)
    return {k: spot_price + (mu - spot_price) * float(v) for k, v in w.items()}


def gate_060(strikes: Sequence[float], yes_probs: Sequence[float],
             spot_price: float) -> bool:
    """Gate 0.60 (Telemetrie): ∃ K_i > p_spot mit P(K_i) >= 0.60.
    KEIN Trade-Blocker — reine Anzeige."""
    return any(k > spot_price and p >= GATE_PROB
               for k, p in zip(strikes, yes_probs))


# ------------------------------------------------------------ main mapper

def parse_gamma_payload(
    payload: Optional[Mapping[str, Any]],
    spot_price: float,
    *,
    now: Optional[float] = None,
    ttl_s: float = GAMMA_DEFAULT_TTL_S,
    min_volume_usd: float = GAMMA_MIN_VOLUME_USD,
) -> PolymarketOdds:
    """Roh-Gamma-JSON -> PolymarketOdds. Fail-closed bei fehlenden,
    synthetischen oder korrupten Feeds. Kein Netz hier (pure Funktion)."""
    now = now if now is not None else datetime.datetime.now(
        datetime.timezone.utc).timestamp()
    if not payload:
        return PolymarketOdds(False, "missing_payload", ttl_s=ttl_s)
    if payload.get("synthetic") is True or str(payload.get("source") or "").lower() in {"synthetic", "seed"}:
        return PolymarketOdds(False, "synthetic_or_degraded", ttl_s=ttl_s)

    slug = str(payload.get("slug") or "")
    title = str(payload.get("title") or "")
    volume = _as_float(payload.get("volume24hr", payload.get("volume")))
    liquidity = _as_float(payload.get("liquidity"))
    if volume < min_volume_usd:
        return PolymarketOdds(False, f"below_min_volume:{volume:.0f}", slug=slug,
                              title=title, volume24hr_usd=volume,
                              liquidity_usd=liquidity, ttl_s=ttl_s)
    if spot_price <= 0:
        return PolymarketOdds(False, "missing_spot", slug=slug, title=title,
                              volume24hr_usd=volume, liquidity_usd=liquidity,
                              ttl_s=ttl_s)

    markets = payload.get("markets")
    if not isinstance(markets, list) or not markets:
        return PolymarketOdds(False, "missing_markets", slug=slug, title=title,
                              volume24hr_usd=volume, liquidity_usd=liquidity,
                              ttl_s=ttl_s)

    ladder: List[Tuple[float, float]] = []  # (strike, yes_prob)
    for market in markets:
        if not isinstance(market, dict):
            continue
        if market.get("synthetic") is True:
            continue
        strike = _parse_strike(market.get("groupItemTitle"))
        yes = _yes_price(market)
        if strike is None or yes is None:
            continue
        ladder.append((strike, yes))
    ladder.sort(key=lambda pair: pair[0])
    if len(ladder) < 2:
        return PolymarketOdds(False, "invalid_ladder", slug=slug, title=title,
                              volume24hr_usd=volume, liquidity_usd=liquidity,
                              ttl_s=ttl_s)

    strikes = [k for k, _ in ladder]
    yes_probs = [p for _, p in ladder]
    try:
        bins, _ = density_from_ladder(strikes, yes_probs)
        mu = mu_from_density(bins)
        bias = bias_from_mu(mu, spot_price)
        traj = trajectories_from(spot_price, mu)
        gate = gate_060(strikes, yes_probs, spot_price)
    except ValueError as exc:
        return PolymarketOdds(False, f"corrupt_ladder:{exc}", slug=slug,
                              title=title, volume24hr_usd=volume,
                              liquidity_usd=liquidity, ttl_s=ttl_s)

    return PolymarketOdds(
        valid=True,
        reason="ok",
        slug=slug,
        title=title,
        volume24hr_usd=volume,
        liquidity_usd=liquidity,
        spot_price=float(spot_price),
        strikes=strikes,
        yes_probs=[round(p, 8) for p in yes_probs],
        density_bins=bins,
        mu=mu,
        bias_pct=bias,
        trajectories=traj,
        gate_060=gate,
        source_ts=now,
        ttl_s=ttl_s,
    )


# ---------------------------------------------------------------- Port

class GammaFeederPort:
    """Dünner Port für die Route: hält den letzten validen Snapshot.
    Ohne Feed -> None (fail-closed, kein Fake)."""

    def __init__(self, odds: Optional[PolymarketOdds] = None) -> None:
        self._odds = odds

    @property
    def odds(self) -> Optional[PolymarketOdds]:
        return self._odds

    def set_odds(self, odds: Optional[PolymarketOdds]) -> None:
        self._odds = odds


_GAMMA_PORT: Optional[GammaFeederPort] = None


def get_gamma_port() -> GammaFeederPort:
    global _GAMMA_PORT
    if _GAMMA_PORT is None:
        _GAMMA_PORT = GammaFeederPort()
    return _GAMMA_PORT


def set_gamma_port(port: Optional[GammaFeederPort]) -> None:
    global _GAMMA_PORT
    _GAMMA_PORT = port
