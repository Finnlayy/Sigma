"""
TV → Sigma library: discover My Scripts and import as paper/inactive rows.
"""
from __future__ import annotations

from app.core.duckdb_store import DuckDBStore
from app.services.tv_library_service import (
    TvLibraryService,
    library_id_for,
    placeholder_pine,
)
from app.tv.script_catalog import (
    cookies_from_storage_state,
    list_available_scripts,
    list_via_session_http,
    normalize_script_rows,
)
from app.tv.strategy_tester_driver import FakeStrategyTesterDriver


class _FakeProvisioner:
    def webhook_url(self):
        return "http://127.0.0.1:8000/api/v1/signal/webhook"

    def upsert(self, strategy_id, symbol, interval, enable=False):
        assert enable is False
        return {
            "strategy_id": strategy_id,
            "tv_alert_id": "",
            "status": "DISABLED",
            "webhook_url": self.webhook_url(),
            "symbol": symbol,
            "interval": str(interval),
        }


def test_normalize_pine_facade_payload():
    rows = normalize_script_rows([
        {"scriptIdPart": "USER;abc", "scriptName": "My CISD", "extra": {"kind": "strategy"}},
        {"scriptIdPart": "USER;abc", "scriptName": "duplicate"},
        {"name": "skip-me"},
        {"scriptIdPart": "PUB;rsi", "scriptName": "RSI", "type": "strategy", "extra": {"ticker": "ETHUSD"}},
    ], origin="saved")
    assert [r["tv_script_id"] for r in rows] == ["USER;abc", "skip-me", "PUB;rsi"]
    assert rows[0]["name"] == "My CISD" and rows[0]["origin"] == "saved"
    assert rows[2]["symbol"] == "ETHUSD"


def test_session_http_uses_storage_state_cookies(tmp_path):
    state = tmp_path / "tv_storage_state.json"
    state.write_text(
        '{"cookies":[{"name":"sessionid","value":"abc","domain":".tradingview.com"}]}',
        encoding="utf-8",
    )
    assert "sessionid=abc" in cookies_from_storage_state(str(state))
    seen = []

    def http(url, headers):
        seen.append(url)
        assert "sessionid=abc" in headers["Cookie"]
        if "filter=saved" in url:
            return [{"scriptIdPart": "USER;1", "scriptName": "Saved One", "extra": {"kind": "strategy"}}]
        return []

    from app.core.config import load_config

    cfg = load_config()
    cfg.tv_storage_state_path = str(state)
    rows = list_via_session_http(cfg, http=http)
    assert rows[0]["tv_script_id"] == "USER;1"
    assert any("filter=saved" in url for url in seen)


def test_list_available_without_session_is_empty_not_fake(tmp_path):
    from app.core.config import load_config

    cfg = load_config()
    cfg.tv_storage_state_path = str(tmp_path / "missing.json")
    out = list_available_scripts(config=cfg)
    assert out["scripts"] == []
    assert out["session_present"] is False
    assert "sigma-tv-login" in out["reason"]


def test_fake_driver_lists_my_scripts():
    rows = FakeStrategyTesterDriver().list_my_scripts()
    assert {r["tv_script_id"] for r in rows} == {"PUB;fake1", "USER;fake2"}


def test_sync_library_is_idempotent_and_paper_only(tmp_path):
    store = DuckDBStore(str(tmp_path / "lib.duckdb"))
    drv = FakeStrategyTesterDriver()
    svc = TvLibraryService(
        driver_factory=lambda: drv,
        provisioner=_FakeProvisioner(),
    )
    catalog = svc.discover(store)
    assert catalog["count"] == 2
    assert catalog["source"] == "driver"

    first = svc.sync(store, script_ids=["PUB;fake1"], fetch_source=False)
    assert first["imported_count"] == 1 and first["skipped_count"] == 0
    row = first["strategies"][0]
    assert row["executionMode"] == "paper"
    assert row["status"] == "inactive"
    assert row["tv_script_id"] == "PUB;fake1"
    assert row["id"] == library_id_for("PUB;fake1")
    assert row["code"].startswith("//@version=6")
    assert row["parameters"]["source"] == "tradingview"
    assert "live" not in (row["executionMode"] or "").lower()

    listed = store.list_strategies()
    tv_rows = [s for s in listed if s.get("tv_script_id") == "PUB;fake1"]
    assert len(tv_rows) == 1

    second = svc.sync(store, script_ids=["PUB;fake1"], fetch_source=False)
    assert second["imported_count"] == 0
    assert second["skipped_count"] == 1
    assert len([s for s in store.list_strategies() if s.get("tv_script_id") == "PUB;fake1"]) == 1
    assert second["live_trading"] is False
    assert second["execution_mode"] == "paper"


def test_placeholder_pine_is_strategy_v6():
    src = placeholder_pine('Alpha "v2"', "PUB;x", "http://127.0.0.1:8000/api/v1/signal/webhook")
    assert src.startswith("//@version=6")
    assert "strategy(" in src
    assert "PUB;x" in src


def test_run_sync_off_asyncio_loop_passthrough_without_loop():
    import threading

    from app.tv.strategy_tester_driver import run_sync_off_asyncio_loop

    here = threading.get_ident()
    assert run_sync_off_asyncio_loop(lambda: threading.get_ident()) == here


def test_playwright_fallback_is_isolated_from_asyncio_loop(tmp_path, monkeypatch):
    """Regression: Playwright Sync API must not run on FastAPI's event loop."""
    import asyncio
    import threading

    from app.core.config import load_config
    from app.tv import strategy_tester_driver as drvmod

    state = tmp_path / "tv_storage_state.json"
    state.write_text(
        '{"cookies":[{"name":"sessionid","value":"abc","domain":".tradingview.com"}]}',
        encoding="utf-8",
    )
    cfg = load_config()
    cfg.tv_storage_state_path = str(state)

    loop_thread = {"id": None}
    work_meta: dict = {}

    class LoopGuardDriver:
        def list_my_scripts(self):
            work_meta["thread"] = threading.get_ident()
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                work_meta["loop"] = False
            else:
                raise RuntimeError(
                    "It looks like you are using Playwright Sync API inside the asyncio loop. "
                    "Please use the Async API instead."
                )
            return [{
                "tv_script_id": "USER;isolated",
                "name": "Isolated",
                "type": "strategy",
                "origin": "saved",
            }]

        def close(self):
            work_meta["closed"] = True

    monkeypatch.setattr(drvmod, "get_driver", lambda *a, **k: LoopGuardDriver())

    def empty_http(_url, _headers):
        return []

    async def under_loop():
        loop_thread["id"] = threading.get_ident()
        return list_available_scripts(config=cfg, http=empty_http)

    out = asyncio.run(under_loop())
    assert out["source"] == "playwright"
    assert out["session_present"] is True
    assert "asyncio loop" not in (out.get("reason") or "")
    assert [s["tv_script_id"] for s in out["scripts"]] == ["USER;isolated"]
    assert work_meta.get("loop") is False
    assert work_meta.get("closed") is True
    assert work_meta["thread"] != loop_thread["id"]


def test_open_tradingview_login_uses_opener_and_stays_paper():
    from app.core import blueprint as bp
    from app.tv.chrome_login import (
        open_tradingview_login,
        playwright_channel,
        set_tv_chrome_opener,
    )

    assert playwright_channel("/usr/bin/google-chrome-stable") == "chrome"
    assert playwright_channel("/usr/bin/chromium-browser") == "chromium"
    calls = []

    def fake(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "open": True, "url": bp.TV_LOGIN_URL, "reused": bool(calls)}

    set_tv_chrome_opener(fake)
    try:
        first = open_tradingview_login(url=bp.TV_LOGIN_URL)
        second = open_tradingview_login(url=bp.TV_CHART_URL)
        assert first["live_trading"] is False and second["live_trading"] is False
        assert first["url"] == bp.TV_LOGIN_URL
        assert len(calls) == 2
        assert calls[1]["url"] == bp.TV_CHART_URL
    finally:
        set_tv_chrome_opener(None)
