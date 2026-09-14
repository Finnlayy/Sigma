#!/usr/bin/env python3
"""Manual UI == CLI capital proof (Single-Book F).

Usage:
  # Against live CLI (no Sigma server required for SoT path):
  .venv/bin/python scripts/verify_ui_cli_capital.py

  # Optional pytest live smoke:
  SIGMA_CLI_LIVE_SMOKE=1 .venv/bin/python -m pytest tests/test_kraken_single_book.py -k live_smoke -q

What Finn checks manually when Sigma is up:
  1. ``kraken paper status -o json`` → note current_value / starting_balance
  2. ``kraken paper balance -o json`` → note USD/BTC totals
  3. Open Sigma capital / logs API (``GET /api/logs`` or capital panel) —
     balances and portfolio value must match CLI numbers, not 50k seeds.
  4. If CLI offline, UI must show empty/offline — never homemade PAPER-* fills.
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
    status = _cli_json(["kraken", "paper", "status", "-o", "json"])
    clear_paper_capital_cache()
    bridge = KrakenCliBridge(execution_mode=bp.ExecutionMode.KRAKEN_PAPER.value)
    snap = fetch_paper_capital(bridge, force=True)
    if not snap.ok:
        print(f"FAIL: SoT helper offline: {snap.error}")
        return 1
    cv = float(status["current_value"])
    sb = float(status["starting_balance"])
    ok = (
        abs(float(snap.current_value) - cv) < 1e-6
        and abs(float(snap.starting_balance) - sb) < 1e-6
    )
    print(f"CLI  current_value={cv} starting_balance={sb}")
    print(f"SoT  current_value={snap.current_value} starting_balance={snap.starting_balance}")
    print(f"SoT  balances={snap.balances} source={snap.source}")
    if not ok:
        print("FAIL: UI SoT helper != kraken paper status")
        return 1
    print("OK: fetch_paper_capital matches `kraken paper status`")
    print("Manual UI check: capital panel / GET balances must show these same numbers.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
