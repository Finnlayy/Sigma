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
import os
from typing import Any, Dict, List

_HEADER = """# Sigma MP-12 Backtest-Hypothesen (H1-H7)

Deterministisch auf synthetischen, lückenfreien OHLCV-Serien; kein
Netz, keine Orders. Ergebnisse: JSON in `tests/backtest/results/`.
Bewertung: „bestätigt / offen / verworfen“ je Hypothese.

"""


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
        if isinstance(data, dict) and all(isinstance(v, dict) for v in data.values()):
            # gruppierte Kennzahlen (z. B. Leverage-Sweep, Fenster-Sweep)
            keys = list(data.keys())
            metrics = sorted({k for v in data.values() for k in v})
            header = "| " + " | ".join([""] + keys) + " |"
            sep = "| " + " | ".join(["---"] * (len(keys) + 1)) + " |"
            lines.append(header)
            lines.append(sep)
            for m in metrics:
                row = [m]
                for k in keys:
                    v = data[k].get(m)
                    row.append(f"{v:.4f}" if isinstance(v, float) else str(v))
                lines.append("| " + " | ".join(row) + " |")
        else:
            for k, v in sorted(data.items()):
                val = f"{v:.4f}" if isinstance(v, float) else str(v)
                lines.append(f"- {k}: {val}")
        lines.append("")
    return "\n".join(lines)


def write_report(results_dir: str, out_md: str) -> None:
    os.makedirs(os.path.dirname(out_md) or ".", exist_ok=True)
    with open(out_md, "w") as f:
        f.write(render_markdown(results_dir))
