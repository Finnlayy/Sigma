"""§38 — Netron ONNX Visualization & Inspection Stack."""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.core import blueprint as bp
from app.server.main import app
from app.services.netron_server import (NetronUnavailable, NetronVisualizerService,
                                        get_netron_service, set_netron_service)


class FakeNetron:
    def __init__(self):
        self.calls = []
        self.stopped = 0

    def start(self, file=None, address=None, browse=None):
        self.calls.append({"file": file, "address": address, "browse": browse})

    def stop(self):
        self.stopped += 1


@pytest.fixture()
def models_dir(tmp_path):
    d = tmp_path / "models"
    d.mkdir()
    for name in ("regime_classifier.onnx", "glint_scorer.onnx"):
        (d / name).write_bytes(b"onnx-bytes")
    (d / "notes.txt").write_text("nicht laden")
    return d


@pytest.fixture()
def service(models_dir):
    fake = FakeNetron()
    svc = NetronVisualizerService(models_dir=str(models_dir), netron_module=fake)
    set_netron_service(svc)
    yield svc
    set_netron_service(None)


# --------------------------------------------------------------- blueprint --
def test_blueprint_netron_constants():
    assert bp.PORT_NETRON == 8082
    assert bp.NETRON_DEFAULT_MODEL == "./models/regime_classifier.onnx"
    assert bp.NETRON_BROWSE is False
    assert bp.NETRON_BIND_PROD == "127.0.0.1" and bp.NETRON_BIND_DEV == "0.0.0.0"
    assert bp.NETRON_ALLOWED_SUFFIX == ".onnx"


def test_section_38_not_pending():
    assert not any(s.startswith("38 ") for s in bp.DOCS_PENDING_SECTIONS)
    assert "NetronVisualizerPanel" in bp.ALL_TERMINAL_PANELS
    assert "ML_INSPECTOR" in bp.ALL_TERMINAL_PRESETS


def test_docs_fully_implemented():
    assert bp.DOCS_PENDING_SECTIONS == ()
    assert bp.DOCS_SECTION_RANGE == (1, 38)


def test_systemd_unit_shipped():
    path = "deploy/systemd/sigma-netron.service"
    assert os.path.exists(path)
    unit = open(path, encoding="utf-8").read()
    assert "app/services/netron_server.py" in unit
    assert "User=sigma" in unit and "Restart=always" in unit


# ----------------------------------------------------------------- service --
def test_bind_prod_and_dev(models_dir):
    prod = NetronVisualizerService(models_dir=str(models_dir), netron_module=FakeNetron())
    dev = NetronVisualizerService(models_dir=str(models_dir), dev=True,
                                  netron_module=FakeNetron())
    assert prod.host == "127.0.0.1" and dev.host == "0.0.0.0"
    assert prod.port == 8082


def test_start_server_uses_browse_false(service, models_dir):
    status = service.start_server(str(models_dir / "regime_classifier.onnx"))
    call = service._netron.calls[0]
    assert call["browse"] is False
    assert call["address"] == ("127.0.0.1", 8082)
    assert status["running"] is True
    assert status["version_tag"] == "regime_classifier"


def test_resolve_model_accepts_tag(service, models_dir):
    resolved = service.resolve_model("glint_scorer")
    assert resolved == str((models_dir / "glint_scorer.onnx").resolve())


def test_resolve_model_rejects_non_onnx(service, models_dir):
    with pytest.raises(ValueError):
        service.resolve_model(str(models_dir / "notes.txt"))


def test_resolve_model_rejects_path_traversal(service):
    with pytest.raises(ValueError):
        service.resolve_model("../../etc/passwd.onnx")


def test_resolve_model_missing_file(service):
    with pytest.raises(FileNotFoundError):
        service.resolve_model("does_not_exist")


def test_load_model_switch(service):
    service.start_server("regime_classifier")
    assert service.load_model("glint_scorer") is True
    assert service.version_tag == "glint_scorer"
    assert len(service._netron.calls) == 2


def test_load_model_invalid_returns_false(service):
    assert service.load_model("nope") is False
    assert "nope" in service.last_error


def test_models_listing_marks_active(service):
    service.start_server("glint_scorer")
    rows = service.models()
    assert [r["version_tag"] for r in rows] == ["glint_scorer", "regime_classifier"]
    assert next(r for r in rows if r["version_tag"] == "glint_scorer")["active"]
    assert all(r["path"].endswith(".onnx") for r in rows)


def test_status_shape(service):
    status = service.status()
    assert status["port"] == 8082 and status["running"] is False
    assert status["url"].endswith(":8082")
    assert status["default_model"] == bp.NETRON_DEFAULT_MODEL
    assert len(status["models"]) == 2


def test_missing_netron_module_is_reported(models_dir):
    svc = NetronVisualizerService(models_dir=str(models_dir))

    class Boom:
        def __getattr__(self, item):
            raise NetronUnavailable("netron nicht installiert")

    svc._netron = Boom()
    with pytest.raises(NetronUnavailable):
        svc.netron.start()


def test_stop(service):
    service.start_server("regime_classifier")
    service.stop()
    assert service.running is False and service._netron.stopped == 1


def test_singleton():
    set_netron_service(None)
    assert get_netron_service() is get_netron_service()
    set_netron_service(None)


# -------------------------------------------------------------------- API --
@pytest.fixture()
def client(service):
    return TestClient(app), service


def test_api_status(client):
    c, _ = client
    body = c.get("/api/v1/models/netron/status").json()
    assert body["port"] == bp.PORT_NETRON and body["browse"] is False


def test_api_start(client):
    c, svc = client
    body = c.post("/api/v1/models/netron/start?model=regime_classifier").json()
    assert body["running"] is True and svc.version_tag == "regime_classifier"


def test_api_inspect_version_tag(client):
    c, svc = client
    body = c.post("/api/v1/models/inspect/glint_scorer").json()
    assert body["loaded"] is True and body["version_tag"] == "glint_scorer"
    assert svc.model_path.endswith("glint_scorer.onnx")


def test_api_inspect_unknown_returns_404(client):
    c, _ = client
    r = c.post("/api/v1/models/inspect/ghost")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "NETRON_MODEL_NOT_LOADED"


def test_frontend_panel_present():
    src = open("src/components/sigma/panels.tsx", encoding="utf-8").read()
    assert "NetronVisualizerPanel," in src
    assert "In Netron betrachten" in src
