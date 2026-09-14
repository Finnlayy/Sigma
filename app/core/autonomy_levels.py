"""
=========================================================
Datei:      app/core/autonomy_levels.py
Zweck:      Kraken autonomy skill levels (L1–L5) mapped onto
            Sigma paper / supervised / L4 live gates.
            Canonical runtime numbers remain config/autonomy-level-4.yaml
            + app/core/blueprint.py (AUTONOMY_LEVEL=4).
System:     Manas: Ciel Core Matrix — Projekt:Sigma
=========================================================

Alignment with Cursor plugin skill ``kraken-autonomy-levels``:

| Level | Kraken skill        | Sigma default |
|-------|---------------------|---------------|
| 1     | Read-only           | Public/CLI reads; no act |
| 2     | Paper trading       | Default book (``kraken paper``) |
| 3     | Supervised live     | Live env on, human/telemetry confirm pending |
| 4     | Autonomous live     | ``SIGMA_LIVE_TRADING=1`` ∧ ``LIVE_APPROVED`` |
| 5     | Fund management     | **Forbidden** — withdrawals never agent-autonomous |

L4 live act always requires explicit confirm on dangerous CLI leaves
(``confirmed=True``) in addition to the env ∧ telemetry gate.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from app.core import blueprint as bp
from app.core import l4_config

LEVEL_READ_ONLY = 1
LEVEL_PAPER = 2
LEVEL_SUPERVISED = 3
LEVEL_AUTONOMOUS = 4
LEVEL_FUND_MGMT = 5

# Product ceiling: Sigma never enables L5 (withdraw / fund move) for agents.
MAX_ALLOWED_LEVEL = LEVEL_AUTONOMOUS

# Hard-deny in ``KrakenCliBridge.run_leaf`` regardless of ``confirmed=``.
# Read-only deposit/withdrawal *status* queries are allowed; mutative L5 leaves are not.
L5_FORBIDDEN_LEAVES: frozenset[str] = frozenset({
    "withdraw",
    "wallet-transfer",
    "futures/wallet-transfer",
    "futures/transfer",
    "withdrawal/cancel",
    # Mutative deposit address creation is fund-mgmt adjacent — deny.
    "deposit/addresses",
})

LABELS: Mapping[int, str] = {
    LEVEL_READ_ONLY: "Level 1 Read-Only",
    LEVEL_PAPER: "Level 2 Paper Automation",
    LEVEL_SUPERVISED: "Level 3 Supervised Live (awaiting LIVE_APPROVED)",
    LEVEL_AUTONOMOUS: "Level 4 Autonomous Live",
    LEVEL_FUND_MGMT: "Level 5 Fund Management (forbidden)",
}


def live_trading_env_name() -> str:
    """Env var name from L4 YAML ``safety.live_trading_env`` (default SIGMA_LIVE_TRADING)."""
    name = l4_config.get("safety.live_trading_env", "SIGMA_LIVE_TRADING")
    return str(name or "SIGMA_LIVE_TRADING")


def resolve_level(
    *,
    paper_trading: bool = True,
    live_trading: bool = False,
    live_approved: bool = False,
) -> int:
    """Effective Kraken-aligned autonomy level for UI / gates.

    Paper-first: any paper book → L2.
    Live UI without env flag → L3 (supervised / not armed).
    Live env without LIVE_APPROVED → L3.
    Live env + LIVE_APPROVED → L4.
    """
    if paper_trading or not live_trading:
        if paper_trading:
            return LEVEL_PAPER
        # Operator flipped UI to "live" but env gate still off → supervised.
        return LEVEL_SUPERVISED
    if not live_approved:
        return LEVEL_SUPERVISED
    return LEVEL_AUTONOMOUS


def label_for(level: int) -> str:
    return str(LABELS.get(int(level), f"Level {level}"))


def live_act_allowed(
    *,
    paper_trading: bool = True,
    live_trading: bool = False,
    live_approved: bool = False,
) -> bool:
    """True only at L4 — mutative live CLI leaves may run (still need confirmed=)."""
    return resolve_level(
        paper_trading=paper_trading,
        live_trading=live_trading,
        live_approved=live_approved,
    ) == LEVEL_AUTONOMOUS


def is_l5_forbidden_leaf(name: str) -> bool:
    """True for withdraw / wallet-transfer / fund-move leaves — never agent-autonomous."""
    key = (name or "").strip().lstrip("/").lower()
    if key in {k.lower() for k in L5_FORBIDDEN_LEAVES}:
        return True
    # Catch nested mutative withdraw (not read-only withdrawal/* status/info/methods/addresses).
    if key == "withdraw" or key.startswith("withdraw/"):
        return True
    if key.endswith("wallet-transfer") or key in {"futures/transfer", "transfer"}:
        return True
    return False


def level_snapshot(
    *,
    paper_trading: bool = True,
    live_trading: bool = False,
    live_approved: bool = False,
    telemetry_state: Optional[str] = None,
) -> Dict[str, Any]:
    level = resolve_level(
        paper_trading=paper_trading,
        live_trading=live_trading,
        live_approved=live_approved,
    )
    return {
        "automationLevel": level,
        "automationLevelLabel": label_for(level),
        "blueprintAutonomyLevel": bp.AUTONOMY_LEVEL,
        "liveTradingEnv": live_trading_env_name(),
        "liveTrading": bool(live_trading),
        "liveApproved": bool(live_approved),
        "liveActAllowed": live_act_allowed(
            paper_trading=paper_trading,
            live_trading=live_trading,
            live_approved=live_approved,
        ),
        "paperTrading": bool(paper_trading),
        "activeLedgerMode": "paper" if paper_trading else "live",
        "telemetryState": telemetry_state,
        "fundManagementForbidden": True,
        "maxAllowedLevel": MAX_ALLOWED_LEVEL,
        "l4ConfigPath": l4_config.config_path(),
        "krakenSkillAlignment": {
            1: "read_only",
            2: "paper",
            3: "supervised",
            4: "autonomous",
            5: "fund_mgmt_forbidden",
        },
    }
