#!/usr/bin/env python3
"""Manual UI == CLI futures paper capital proof (L4 Futures SoT).

Usage:
  .venv/bin/python scripts/verify_futures_ui_cli_capital.py

What Finn checks when Sigma is up:
  1. ``kraken futures paper status -o json`` → equity / starting_collateral
  2. ``kraken futures paper balance -o json`` → collateral / available_margin
  3. Capital / Pro panel must match these numbers (not spot paper, not 50k seeds).
  4. ``GET /api/kraken/positions/pro`` source is CLI (paper or live), never
     ``live_futures_not_wired`` with invented positions.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core import blueprint as bp  # noqa: E402
from app.execution.KrakenCliBridge import KrakenCliBridge  # noqa: E402
from app.execution.kraken_paper_sot import (  # noqa: E402
    clear_paper_capital_cache,
    fetch_paper_capital,
)


def _cli_json(argv: list[str]) -> dict:
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        raise SystemExit(f"CLI failed ({proc.returncode}): {proc.stderr or proc.stdout}")
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    raise SystemExit(f"no JSON in CLI stdout: {proc.stdout!r}")


def main() -> int:
    if shutil.which("kraken") is None:
        print("FAIL: kraken not on PATH (expect ~/.cargo/bin via sigma-up)")
        return 2
    ver = subprocess.check_output(["kraken", "--version"], text=True).strip()
    print(f"CLI: {ver}")
    status = _cli_json(["kraken", "futures", "paper", "status", "-o", "json"])
    clear_paper_capital_cache()
    bridge = KrakenCliBridge(
        execution_mode=bp.ExecutionMode.KRAKEN_PAPER.value, futures=True,
    )
    snap = fetch_paper_capital(bridge, force=True, futures=True)
    if not snap.ok:
        print(f"FAIL: futures SoT helper offline: {snap.error}")
        return 1
    equity = float(status.get("equity") if status.get("equity") is not None
                   else status["current_value"])
    starting = float(
        status.get("starting_collateral")
        if status.get("starting_collateral") is not None
        else status.get("starting_balance")
    )
    ok = (
        abs(float(snap.current_value) - equity) < 1e-6
        and abs(float(snap.starting_balance) - starting) < 1e-6
    )
    print(f"CLI  equity={equity} starting_collateral={starting}")
    print(f"SoT  current_value={snap.current_value} starting_balance={snap.starting_balance}")
    print(f"SoT  balances={snap.balances} source={snap.source} book={snap.book}")
    if not ok:
        print("FAIL: UI SoT helper != kraken futures paper status")
        return 1
    print("OK: fetch_paper_capital(futures) matches `kraken futures paper status`")
    print("Manual UI check: capital / Pro panel must show these same numbers.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
