"""Desktop safety/hosting contracts. Network access is never needed here."""
import os
from pathlib import Path

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient

from app.core.config import SigmaConfig, load_config
from app.ingestion.OmniStreamIngestor import OmniStreamIngestor
from app.mcp.TradingViewMCPClient import TradingViewMCPClient, TvMcpError
from app.security.SettingsEnvManager import SettingsEnvManager
from app.tv.strategy_tester_driver import get_driver, PlaywrightStrategyTesterDriver, DriverError
from app.tv.worker import TvJobQueue, JOB_KIND_PULL_PARAMS
from desktop.web import configure_desktop

TOKEN = 'a' * 64


def test_static_hosting_auth_api_404_and_spa(tmp_path, monkeypatch):
    monkeypatch.setenv('SIGMA_DESKTOP_TOKEN', TOKEN)
    (tmp_path / 'index.html').write_text('<h1>SIGMA</h1>')
    app = FastAPI()

    @app.get('/api/real')
    async def real():
        return {'ok': True}

    @app.websocket('/ws/test')
    async def echo(ws: WebSocket):
        await ws.accept()
        await ws.send_json({'connected': True})
        await ws.close()

    configure_desktop(app, tmp_path)
    with TestClient(app) as client:
        assert client.get('/').status_code == 403
        headers = {'X-Sigma-Desktop': TOKEN}
        assert client.get('/', headers=headers).text == '<h1>SIGMA</h1>'
        assert "script-src 'self'" in client.get('/', headers=headers).headers['content-security-policy']
        assert client.get('/', headers={**headers, 'Origin': 'https://untrusted.example'}).status_code == 403
        assert client.get('/logs', headers=headers).status_code == 200
        assert client.get('/api/real', headers=headers).json() == {'ok': True}
        assert client.get('/api/missing', headers=headers).status_code == 404
        assert client.get('/assets/missing.js', headers=headers).status_code == 404
        with client.websocket_connect('/ws/test', headers=headers) as ws:
            assert ws.receive_json() == {'connected': True}
        from starlette.websockets import WebSocketDisconnect
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect('/ws/test'):
                pytest.fail('unauthorized socket accepted')


def test_desktop_cannot_enable_simulations_or_live(monkeypatch, tmp_path):
    monkeypatch.setenv('SIGMA_DESKTOP', '1')
    monkeypatch.setenv('SIGMA_LIVE_TRADING', '1')
    cfg = load_config()
    assert cfg.live_trading is False
    assert cfg.allow_synthetic is False
    assert cfg.allow_fakeredis is False
    with pytest.raises(TvMcpError):
        TradingViewMCPClient.from_env('fake')
    assert isinstance(get_driver(cfg), PlaywrightStrategyTesterDriver)
    manager = SettingsEnvManager(cfg, str(tmp_path / '.env'))
    for key, value in [('SIGMA_LIVE_TRADING', '1'), ('SIGMA_TV_MCP_URL', 'fake'),
                       ('SIGMA_MARKET_SOURCE', 'ccxt_ws'), ('ALPHA_MARKET_SOURCE', 'synthetic')]:
        with pytest.raises(ValueError):
            manager.update(key, value)


def test_no_synthetic_seed_tick_or_quote_on_missing_feed(monkeypatch):
    cfg = SigmaConfig(allow_synthetic=False, market_source='tv_scraper')
    ingestor = OmniStreamIngestor(cfg)
    monkeypatch.setattr(ingestor, '_seed_history_from_scraper', lambda _: 0)
    assert ingestor.seed_history(10) == 0
    from app.tv.scraper_client import get_scraper_client
    def offline(*_args, **_kwargs):
        raise RuntimeError('offline')
    monkeypatch.setattr(get_scraper_client(), 'fetch_ohlc_with_meta', offline)
    assert ingestor._next_tick('BTC/USD') is None
    assert ingestor._next_tick('BTC/USD') is None  # polling gap is not a synthetic tick
    assert ingestor.ticker_rows() == []
    assert ingestor.last_price('BTC/USD') == 0
    assert ingestor.feed_source_effective == 'unavailable'


def test_driver_construction_failure_is_persisted_not_worker_death(tmp_path):
    cfg = SigmaConfig(tv_jobs_dir=str(tmp_path / 'jobs'), tv_export_dir=str(tmp_path / 'exports'))
    def unavailable():
        raise DriverError('No TradingView session', 'TV_SESSION_MISSING')
    queue = TvJobQueue(cfg, driver_factory=unavailable)
    job = queue.submit(JOB_KIND_PULL_PARAMS)
    # submit returns serialized job; the same persisted record is consumed by the worker.
    job_id = job['job_id'] if isinstance(job, dict) else job.job_id
    queue.run_job(queue._jobs[job_id])
    assert queue._jobs[job_id].status == 'failed'
    assert queue._jobs[job_id].error_code == 'TV_SESSION_MISSING'
    assert Path(cfg.tv_jobs_dir, job_id + '.json').exists()


def test_workspace_is_private_persistent_and_does_not_copy_user_data(tmp_path, monkeypatch):
    from desktop.backend import prepare_workspace
    old_cwd = Path.cwd()
    keys = ["SIGMA_DESKTOP", "SIGMA_LIVE_TRADING", "SIGMA_ALLOW_FAKE_REDIS",
            "SIGMA_ALLOW_SYNTHETIC", "SIGMA_SCRAPER_SYNTHETIC_FALLBACK",
            "SIGMA_SCRAPER_VENDOR_PATH", "SIGMA_DATA_DIR", "SIGMA_MARKET_SOURCE", "SIGMA_TV_MCP_URL", "XDG_CONFIG_HOME"]
    before = {key: os.environ.get(key) for key in keys}
    monkeypatch.delenv("SIGMA_TV_MCP_URL", raising=False)
    try:
        prepare_workspace(tmp_path)
        assert Path.cwd() == tmp_path
        assert tmp_path.stat().st_mode & 0o777 == 0o700
        assert (tmp_path / 'data/secrets').is_dir()
        assert not (tmp_path / 'data/sigma.duckdb').exists()
        assert os.environ['SIGMA_LIVE_TRADING'] == '0'
        assert os.environ['SIGMA_ALLOW_FAKE_REDIS'] == '0'
        assert os.environ['SIGMA_TV_MCP_URL'] == ''
    finally:
        os.chdir(old_cwd)
        for key, value in before.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_desktop_does_not_claim_remote_alert_enable_without_driver(monkeypatch, tmp_path):
    from app.tv.alert_provisioner import AlertProvisioner
    monkeypatch.setenv('SIGMA_DESKTOP', '1')
    provisioner = AlertProvisioner(SigmaConfig(), store_path=str(tmp_path / 'alerts.json'))
    provisioner.upsert('no-session', 'BTC/USD')
    with pytest.raises(DriverError, match='unavailable'):
        provisioner.enable('no-session')
    assert provisioner.get('no-session').enabled is False


def test_strict_feed_rejects_stale_cache_instead_of_relabeling_it_live(monkeypatch):
    from app.tv.scraper_client import get_scraper_client
    cfg = SigmaConfig(allow_synthetic=False, market_source='tv_scraper')
    ingestor = OmniStreamIngestor(cfg)
    ingestor.prices['BTC/USD'] = 100
    candle = {'ts': 1700000000, 'o': 100, 'h': 101, 'l': 99, 'c': 100, 'v': 10}
    monkeypatch.setattr(get_scraper_client(), 'fetch_ohlc_with_meta',
                        lambda *_: ([candle], {'source': 'cache_stale', 'degraded': True}))
    assert ingestor._next_tick('BTC/USD') is None
    assert ingestor.last_price('BTC/USD') == 0
    assert ingestor.feed_source_effective == 'unavailable'


def test_all_literal_frontend_api_references_have_real_routes():
    import re
    from app.server.main import app
    root = Path(__file__).resolve().parents[1]
    paths = set(app.openapi()['paths']) | {'/api/desktop/status', '/api/desktop/paper/initialize'}
    references = set()
    for file in (root / 'src').rglob('*.ts*'):
        for match in re.finditer(r'["\x27`](/api/[^"\x27`\s]*)', file.read_text()):
            path = match.group(1).split('?')[0]
            if '${' not in path and not path.endswith('/'):
                references.add(path)
    missing = [path for path in references if not any(
        re.fullmatch(re.sub(r'\{[^}]+\}', r'[^/]+', route), path) for route in paths)]
    assert missing == []
