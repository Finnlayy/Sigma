"""
=========================================================
Datei:      sigma/signals/correlation_scout.py
Zweck:      Rolling r / β vs Leader (BTC oder XAU) → Buckets 1–3.
            Fehlende Serie → leere Buckets, keine synthetischen Coins.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Blanche (Scout-Features)
=========================================================
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

BUCKET1_R = 0.80
BUCKET1_BETA = 1.8
BUCKET2_R = 0.20


@dataclass
class BucketRow:
    symbol: str
    correlation: float
    beta: float
    bucket: int  # 1 high-beta, 2 inverse/weak, 3 blocked
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class ScoutResult:
    leader: str
    rows: List[BucketRow] = field(default_factory=list)
    bucket_1: List[str] = field(default_factory=list)
    bucket_2: List[str] = field(default_factory=list)
    bucket_3: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "leader": self.leader,
            "rows": [r.to_dict() for r in self.rows],
            "bucket_1": list(self.bucket_1),
            "bucket_2": list(self.bucket_2),
            "bucket_3": list(self.bucket_3),
        }


class CorrelationScout:
    def find_high_beta_candidates(
        self,
        series: Optional[Mapping[str, Sequence[Mapping[str, Any]]]],
        leader: str = "BTC/USD",
        window: int = 48,
    ) -> ScoutResult:
        if not series:
            return ScoutResult(leader=leader)
        leader_closes = _closes(series.get(leader) or [])
        if len(leader_closes) < window:
            return ScoutResult(leader=leader)
        r_lead = _returns(leader_closes[-window:])
        if len(r_lead) < 8:
            return ScoutResult(leader=leader)
        out = ScoutResult(leader=leader)
        for symbol, candles in series.items():
            if symbol == leader:
                continue
            closes = _closes(candles)
            if len(closes) < window:
                out.rows.append(BucketRow(symbol, 0.0, 0.0, 3, "short_series"))
                out.bucket_3.append(symbol)
                continue
            r_alt = _returns(closes[-window:])
            n = min(len(r_lead), len(r_alt))
            if n < 8:
                out.rows.append(BucketRow(symbol, 0.0, 0.0, 3, "short_returns"))
                out.bucket_3.append(symbol)
                continue
            corr, beta = _corr_beta(r_lead[-n:], r_alt[-n:])
            if corr > BUCKET1_R and beta > BUCKET1_BETA:
                bucket, reason = 1, "high_beta"
            elif corr < BUCKET2_R:
                bucket, reason = 2, "inverse_or_weak"
            else:
                bucket, reason = 3, "decoupled_or_illiquid"
            row = BucketRow(symbol, round(corr, 4), round(beta, 4), bucket, reason)
            out.rows.append(row)
            if bucket == 1:
                out.bucket_1.append(symbol)
            elif bucket == 2:
                out.bucket_2.append(symbol)
            else:
                out.bucket_3.append(symbol)
        return out


def _closes(candles: Sequence[Mapping[str, Any]]) -> List[float]:
    out: List[float] = []
    for c in candles or []:
        px = c.get("c", c.get("close"))
        try:
            val = float(px)
        except (TypeError, ValueError):
            continue
        if val > 0:
            out.append(val)
    return out


def _returns(closes: Sequence[float]) -> List[float]:
    return [
        math.log(closes[i] / closes[i - 1])
        for i in range(1, len(closes))
        if closes[i - 1] > 0 and closes[i] > 0
    ]


def _corr_beta(x: Sequence[float], y: Sequence[float]) -> tuple:
    n = min(len(x), len(y))
    if n < 3:
        return 0.0, 0.0
    xs, ys = list(x[-n:]), list(y[-n:])
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / (n - 1)
    vx = sum((a - mx) ** 2 for a in xs) / (n - 1)
    vy = sum((b - my) ** 2 for b in ys) / (n - 1)
    if vx <= 0.0:
        return 0.0, 0.0
    beta = cov / vx
    denom = math.sqrt(vx * vy) if vx > 0 and vy > 0 else 0.0
    corr = cov / denom if denom > 0 else 0.0
    return corr, beta
