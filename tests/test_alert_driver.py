"""Playwright alert driver + provisioner driver wiring + URL-change re-upsert."""
from __future__ import annotations

from app.core import blueprint as bp
from app.tv.alert_driver import FakeAlertDriver, PlaywrightAlertDriver, alert_driver_from_tester
from app.tv.alert_provisioner import AlertProvisioner
from app.tv.strategy_tester_driver import FakeStrategyTesterDriver
from app.tv.worker import JOB_KIND_ALERT_SYNC, TvJobQueue


class FakeLocator:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector

    @property
    def first(self):
        return self

    def wait_for(self, **kwargs):
        return None

    def fill(self, value):
        self.page.fills.append((self.selector, value))

    def click(self):
        self.page.clicks.append(self.selector)


class FakePage:
    def __init__(self):
        self.clicks = []
        self.fills = []

    def locator(self, selector):
        return FakeLocator(self, selector)


class FakeTester:
    def __init__(self):
        self.page = FakePage()
        self.calls = []

    def open_chart(self, symbol, interval):
        self.calls.append(f"open_chart:{symbol}:{interval}")

    def click_element_with_fallback(self, category, name):
        self.calls.append(f"click:{category}.{name}")
        self.page.clicks.append(f"{category}.{name}")


def test_playwright_driver_fills_webhook_against_selectors():
    tester = FakeTester()
    drv = PlaywrightAlertDriver(tester=tester)
    out = drv.upsert_alert(
        "sigma:s1", "BTC/USD", 15,
        "https://abc.ngrok-free.app/api/v1/signal/ingest",
        '{"strategy_id":"s1"}',
    )
    assert out["tv_alert_id"] == "sigma:s1"
    assert "open_chart:BTC/USD:15" in tester.calls
    assert "click:alerts.create_button" in tester.calls
    assert "click:alerts.submit_button" in tester.calls
    filled = [v for _, v in tester.page.fills]
    assert "https://abc.ngrok-free.app/api/v1/signal/ingest" in filled
    assert '{"strategy_id":"s1"}' in filled


def test_provisioner_calls_driver_on_upsert_enable_disable(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGMA_WEBHOOK_SECRET", "tok")
    driver = FakeAlertDriver()
    prov = AlertProvisioner(
        store_path=str(tmp_path / "alerts.json"),
        driver=driver,
        public_webhook_base="https://abc.ngrok-free.app",
    )
    prov.upsert("s1", "BTC/USD", 15, enable=True)
    assert any(c.startswith("upsert_alert:") for c in driver.calls)
    assert any(c.startswith("enable_alert:") for c in driver.calls)
    rec = driver.alerts["sigma:s1"]
    assert rec["webhook_url"].endswith(bp.WEBHOOK_INGEST_ROUTE)
    assert rec["message"]
    prov.disable("s1", reason="rotate")
    assert any(c.startswith("disable_alert:") for c in driver.calls)
    assert rec["enabled"] is False


def test_url_change_triggers_re_upsert(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGMA_WEBHOOK_SECRET", "tok")
    driver = FakeAlertDriver()
    prov = AlertProvisioner(
        store_path=str(tmp_path / "alerts.json"),
        driver=driver,
        public_webhook_base="https://one.ngrok-free.app",
    )
    prov.upsert("s1", "ETH/USD", 15, enable=True)
    driver.calls.clear()
    prov.public_webhook_base = "https://two.ngrok-free.app"
    prov.refresh_webhooks(["s1"])
    assert any(c.startswith("upsert_alert:") for c in driver.calls)
    rec = driver.alerts["sigma:s1"]
    assert rec["webhook_url"].startswith("https://two.ngrok-free.app")


def test_worker_alert_sync_runs_driver(tmp_path):
    from app.core.config import load_config

    fake = FakeStrategyTesterDriver()
    cfg = load_config()
    cfg.tv_jobs_dir = str(tmp_path / "jobs")
    cfg.tv_export_dir = str(tmp_path / "exp")
    queue = TvJobQueue(cfg, driver_factory=lambda: fake)
    job = queue.submit(
        JOB_KIND_ALERT_SYNC,
        strategy_id="s1",
        symbol="BTC/USD",
        interval=15,
        params={
            "action": "upsert",
            "name": "sigma:s1",
            "webhook_url": "https://abc.ngrok-free.app/api/v1/signal/ingest",
            "message": "{}",
        },
    )
    done = queue.run_job(job, driver=fake)
    assert done.status == "done"
    assert done.result.get("synced") is True
    assert "upsert_alert:sigma:s1" in fake.calls


def test_alert_driver_from_tester_prefers_existing_upsert():
    fake = FakeStrategyTesterDriver()
    assert alert_driver_from_tester(fake) is fake
