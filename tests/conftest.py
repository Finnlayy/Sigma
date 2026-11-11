"""Isolate pytest from the production DuckDB file and live Kraken account."""
from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def _isolate_sigma_data_dir(tmp_path_factory):
    if not os.environ.get("SIGMA_DATA_DIR"):
        os.environ["SIGMA_DATA_DIR"] = str(tmp_path_factory.mktemp("sigma-data"))
    os.environ.setdefault("SIGMA_LIVE_TRADING", "0")
    yield


@pytest.fixture(scope="session", autouse=True)
def _hide_host_kraken_cli():
    """No exchange access in tests: a host-installed `kraken` would run real orders.

    Same view as CI (no CLI). Opt-in comparison stays: SIGMA_CLI_LIVE_SMOKE=1.
    """
    import shutil

    if os.environ.get("SIGMA_CLI_LIVE_SMOKE", "").strip():
        yield
        return
    original = os.environ.get("PATH", "")
    kept = [d for d in original.split(os.pathsep)
            if not (d and shutil.which("kraken", path=d))]
    os.environ["PATH"] = os.pathsep.join(kept)
    yield
    os.environ["PATH"] = original
