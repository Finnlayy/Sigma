"""Settings validation: invalid → hint, accepted keys persist."""
from __future__ import annotations

import pytest

from app.core.config import SigmaConfig
from app.security.SettingsEnvManager import (
    SettingValidationError,
    SettingsEnvManager,
    validate_setting,
)


def test_validate_tv_mcp_accepts_fake_and_https():
    validate_setting("SIGMA_TV_MCP_URL", "fake")
    validate_setting("SIGMA_TV_MCP_URL", "https://tv-mcp.example/rpc")


def test_validate_tv_mcp_rejects_garbage():
    with pytest.raises(SettingValidationError) as exc:
        validate_setting("SIGMA_TV_MCP_URL", "not-a-url")
    assert "https" in exc.value.hint or "fake" in exc.value.hint
    assert "fake" in exc.value.allowed


def test_validate_leverage_range():
    validate_setting("ALPHA_MAX_LEVERAGE", "5")
    with pytest.raises(SettingValidationError):
        validate_setting("ALPHA_MAX_LEVERAGE", "12")
    with pytest.raises(SettingValidationError):
        validate_setting("ALPHA_MAX_LEVERAGE", "abc")


def test_validate_live_flag_only_zero_one():
    validate_setting("SIGMA_LIVE_TRADING", "0")
    with pytest.raises(SettingValidationError):
        validate_setting("SIGMA_LIVE_TRADING", "true")


def test_update_rejects_unknown_key(tmp_path):
    mgr = SettingsEnvManager(SigmaConfig(), env_file=str(tmp_path / ".env"))
    with pytest.raises(ValueError, match="nicht für die UI"):
        mgr.update("NOT_A_KEY", "1")


def test_update_accepts_canonical_enum(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    mgr = SettingsEnvManager(SigmaConfig(), env_file=str(env))
    monkeypatch.setenv("SIGMA_MARKET_SOURCE", "synthetic")
    out = mgr.update("SIGMA_MARKET_SOURCE", "CCXT_WS")
    assert out["applied"] is True
    assert out["value"] == "ccxt_ws"
