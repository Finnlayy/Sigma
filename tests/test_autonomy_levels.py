"""Autonomy level mapping — Kraken skill L1–L5 ↔ Sigma paper/live gates."""
from __future__ import annotations

import os

from app.core import autonomy_levels as al
from app.core import blueprint as bp
from app.core import l4_config


def test_paper_is_level_2():
    assert al.resolve_level(paper_trading=True, live_trading=False, live_approved=False) == 2
    assert al.resolve_level(paper_trading=True, live_trading=True, live_approved=True) == 2


def test_supervised_without_approval():
    assert al.resolve_level(paper_trading=False, live_trading=True, live_approved=False) == 3
    assert al.resolve_level(paper_trading=False, live_trading=False, live_approved=False) == 3


def test_l4_requires_env_and_live_approved():
    assert al.resolve_level(paper_trading=False, live_trading=True, live_approved=True) == 4
    assert al.live_act_allowed(
        paper_trading=False, live_trading=True, live_approved=True) is True
    assert al.live_act_allowed(
        paper_trading=False, live_trading=True, live_approved=False) is False
    assert al.live_act_allowed(
        paper_trading=True, live_trading=True, live_approved=True) is False


def test_fund_management_forbidden_ceiling():
    assert al.MAX_ALLOWED_LEVEL == 4
    assert al.LEVEL_FUND_MGMT == 5
    snap = al.level_snapshot(paper_trading=True)
    assert snap["fundManagementForbidden"] is True
    assert snap["blueprintAutonomyLevel"] == bp.AUTONOMY_LEVEL == 4


def test_live_trading_env_from_l4_yaml():
    assert al.live_trading_env_name() == "SIGMA_LIVE_TRADING"
    assert l4_config.get("safety.live_trading_env") == "SIGMA_LIVE_TRADING"
    assert os.path.exists(l4_config.config_path()) or l4_config.config_path().endswith(
        "autonomy-level-4.yaml")
