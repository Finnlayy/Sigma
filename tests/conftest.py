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
