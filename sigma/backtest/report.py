"""
=========================================================
Datei:      sigma/backtest/report.py
Zweck:      MP-12 Report-Export: Ergebnisse der Hypothesen-
            Harness (tests/backtest/results/*.json) als
            Markdown-/JSON-Übersicht. Klein, deterministisch,
            keine Artefakte in Git (results/ ist gitignored).
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Backtest)
=========================================================
"""
from __future__ import annotations

import json
import math
import os
from typing import Any, Dict, List, Sequence

_HEADER = """# Sigma MP-12 Backtest-Hypothesen (H1-H7)

Deterministisch auf synthetischen, lückenfreien OHLCV-Serien; kein
Netz, keine Orders. Ergebnisse: JSON in `tests/backtest/results/`.
Bewertung: „bestätigt / offen / verworfen“ je Hypothese
(`confirmed|open|rejected`).

"""

VERDICT_CONFIRMED = "confirmed"
VERDICT_OPEN = "open"
VERDICT_REJECTED = "rejected"


def significance_stats(returns: Sequence[float]) -> Dict[str, float]:
    """Mean + simple t-stat significance (no single-number proof)."""
    n = len(returns)
    if n == 0:
        return {"mean": 0.0, "t_stat": 0.0, "n": 0.0}
    mean = sum(returns) / n
    if n < 2:
        return {"mean": float(mean), "t_stat": 0.0, "n": float(n)}
    var = sum((r - mean) ** 2 for r in returns) / (n - 1)
    se = math.sqrt(var / n) if var > 0 else 0.0
    t_stat = (mean / se) if se > 0 else 0.0
    return {"mean": float(mean), "t_stat": float(t_stat), "n": float(n)}


def label_verdict(
    mean: float,
    t_stat: float,
    *,
    expect_positive: bool = True,
) -> str:
    """confirmed | open | rejected from mean direction + |t|≥1.64."""
    if abs(t_stat) < 1.0:
        return VERDICT_OPEN
    positive = mean > 0.0
    if abs(t_stat) >= 1.64:
        if positive == expect_positive:
            return VERDICT_CONFIRMED
        return VERDICT_REJECTED
    return VERDICT_OPEN


def annotate_result(
    payload: Dict[str, Any],
    returns: Sequence[float],
    *,
    expect_positive: bool = True,
) -> Dict[str, Any]:
    """Attach mean, significance, and confirmed|open|rejected label."""
    stats = significance_stats(returns)
    out = dict(payload)
    out["mean"] = stats["mean"]
    out["significance"] = {"t_stat": stats["t_stat"], "n": stats["n"]}
    out["verdict"] = label_verdict(
        stats["mean"], stats["t_stat"], expect_positive=expect_positive
    )
    return out


def load_results(results_dir: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not os.path.isdir(results_dir):
        return out
    for name in sorted(os.listdir(results_dir)):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(results_dir, name), "r") as f:
                out.append({"hypothesis": name[:-5], "data": json.load(f)})
        except (OSError, ValueError):
            continue  # kaputte Artefakte überspringen, nie crashen
    return out


def render_markdown(results_dir: str) -> str:
    lines: List[str] = [_HEADER]
    for entry in load_results(results_dir):
        lines.append(f"## {entry['hypothesis']}")
        data: Dict[str, Any] = entry["data"]
        if not data:
            lines.append("- (leer)\n")
            continue
        verdict = data.get("verdict")
        if verdict in (VERDICT_CONFIRMED, VERDICT_OPEN, VERDICT_REJECTED):
            mean = data.get("mean")
            sig = data.get("significance") or {}
            t_stat = sig.get("t_stat", data.get("t_stat"))
            mean_s = f"{mean:.6f}" if isinstance(mean, float) else str(mean)
            t_s = f"{t_stat:.4f}" if isinstance(t_stat, (int, float)) else str(t_stat)
            lines.append(f"- verdict: **{verdict}** (mean={mean_s}, t={t_s})")
        metric_groups = {
            k: v for k, v in data.items()
            if isinstance(v, dict)
            and k != "significance"
            and v
            and all(isinstance(x, (int, float, str, bool)) or x is None for x in v.values())
        }
        if metric_groups and len(metric_groups) >= 2:
            keys = list(metric_groups.keys())
            metrics = sorted({k for v in metric_groups.values() for k in v})
            header = "| " + " | ".join([""] + keys) + " |"
            sep = "| " + " | ".join(["---"] * (len(keys) + 1)) + " |"
            lines.append(header)
            lines.append(sep)
            for m in metrics:
                row = [m]
                for k in keys:
                    v = metric_groups[k].get(m)
                    row.append(f"{v:.4f}" if isinstance(v, float) else str(v))
                lines.append("| " + " | ".join(row) + " |")
            for k, v in sorted(data.items()):
                if k in metric_groups or k in ("significance", "verdict"):
                    continue
                if isinstance(v, dict):
                    continue
                val = f"{v:.4f}" if isinstance(v, float) else str(v)
                lines.append(f"- {k}: {val}")
        else:
            for k, v in sorted(data.items()):
                if k == "verdict":
                    continue
                if isinstance(v, dict):
                    val = json.dumps(v, sort_keys=True)
                else:
                    val = f"{v:.4f}" if isinstance(v, float) else str(v)
                lines.append(f"- {k}: {val}")
        lines.append("")
    return "\n".join(lines)


def write_report(results_dir: str, out_md: str) -> None:
    os.makedirs(os.path.dirname(out_md) or ".", exist_ok=True)
    with open(out_md, "w") as f:
        f.write(render_markdown(results_dir))
