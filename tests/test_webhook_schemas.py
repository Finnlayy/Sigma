"""§33 — Standardisierte Webhook-Alert-Schemata (Pydantic V2, Ingestion-Router)."""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core import blueprint as bp
from app.server.main import app
from app.server.schemas import (ERR_PIONEX_DISABLED, ERR_SCHEMA_INVALID,
                                ERR_SCHEMA_UNKNOWN, MLFeaturePayload,
                                PionexSignalPayload, SchemaDetectionError,
                                SignalExecutionResponse, SigmaL4AlertPayload,
                                detect_schema, normalize_epoch, normalize_symbol,
                                parse_payload)
from app.quant.glint_orderbook_verifier import OrderbookSnapshot
from app.quant.epidemic_contagion_engine import (ContagionInputs,
                                                 EpidemicContagionEngine,
                                                 set_contagion_engine)

SECRET = "sigma_prod_secure_token_8849"


def _alert(**over) -> dict:
    body = {
        "secret": SECRET,
        "idempotency_key": f"sig_cisd_v6_XBTUSD_{int(time.time())}",
        "strategy_id": "cisd_sniper_breakout_v6",
        "bot_id": "bot_xbt_01",
        "symbol": "KRAKEN:XBTUSD.P",
        "action": "BUY",
        "order_type": "MARKET",
        "price": 68_000.0,
        "stop_loss": 67_000.0,
        "take_profit": 70_000.0,
        "fixed_leverage": 5,
        "execution_mode": "kraken_paper",
        "timestamp": int(time.time()),
        "features": {"rsi": 28.4, "atr": 0.0052, "cisd_score": 0.88,
                     "bb_bandwidth": 0.024},
    }
    body.update(over)
    return {k: v for k, v in body.items() if v is not None or k == "take_profit"}


@pytest.fixture(scope="module")
def client() -> TestClient:
    """Eigene Pipeline mit bekanntem Secret — Reihenfolge-unabhaengig (§17.1)."""
    import os

    import app.server.routes_sigma as routes

    previous = os.environ.get("SIGMA_WEBHOOK_SECRET")
    os.environ["SIGMA_WEBHOOK_SECRET"] = SECRET
    import app.execution.SafetyGuard as safety_module

    safety_module._guard = None            # Secret wird beim Bau eingelesen
    contagion = EpidemicContagionEngine()
    contagion.evaluate(ContagionInputs())
    set_contagion_engine(contagion)
    routes.set_pipeline(None)
    routes._ORDER_DISPATCHER = None
    routes.set_depth_adapter(type("_Depth", (), {
        "fetch": staticmethod(lambda symbol: OrderbookSnapshot(
            symbol, [(67_999.0, 80.0)], [(68_001.0, 20.0)], time.time()
        ))
    })())
    yield TestClient(app)
    safety_module._guard = None
    routes.set_pipeline(None)
    routes.set_depth_adapter(None)
    set_contagion_engine(None)
    if previous is None:
        os.environ.pop("SIGMA_WEBHOOK_SECRET", None)
    else:
        os.environ["SIGMA_WEBHOOK_SECRET"] = previous


# ------------------------------------------------------------ Schema A (§33.1)

def test_valid_sigma_l4_alert_parses():
    alert = SigmaL4AlertPayload.model_validate(_alert())
    assert alert.symbol == "XBTUSD"          # KRAKEN:-Prefix und .P entfernt
    assert alert.market_type == "futures"    # Marktidentitaet bleibt separat erhalten
    assert alert.side == "buy"
    assert alert.features.cisd_score == 0.88
    assert alert.feature_dict()["rsi"] == 28.4


def test_required_fields_are_enforced():
    for field in ("secret", "idempotency_key", "strategy_id", "bot_id", "symbol",
                  "action", "price", "stop_loss", "timestamp"):
        body = _alert()
        body.pop(field)
        with pytest.raises(ValidationError):
            SigmaL4AlertPayload.model_validate(body)
    assert set(bp.SIGMA_L4_REQUIRED_FIELDS) <= set(SigmaL4AlertPayload.model_fields)


def test_unknown_fields_are_a_contract_breach():
    with pytest.raises(ValidationError):
        SigmaL4AlertPayload.model_validate(_alert(pyramiding="yes"))


def test_short_secret_and_key_are_rejected():
    with pytest.raises(ValidationError):
        SigmaL4AlertPayload.model_validate(_alert(secret="kurz"))
    with pytest.raises(ValidationError):
        SigmaL4AlertPayload.model_validate(_alert(idempotency_key="abc"))


def test_leverage_is_bounded_1_to_5():
    with pytest.raises(ValidationError):
        SigmaL4AlertPayload.model_validate(_alert(fixed_leverage=10))
    with pytest.raises(ValidationError):
        SigmaL4AlertPayload.model_validate(_alert(fixed_leverage=0))
    assert SigmaL4AlertPayload.model_validate(_alert(fixed_leverage=1)).fixed_leverage == 1


def test_action_and_order_type_are_literals():
    with pytest.raises(ValidationError):
        SigmaL4AlertPayload.model_validate(_alert(action="HODL"))
    with pytest.raises(ValidationError):
        SigmaL4AlertPayload.model_validate(_alert(order_type="ICEBERG"))
    assert SigmaL4AlertPayload.model_validate(_alert(action="sell", price=68_000.0,
                                                     stop_loss=69_000.0,
                                                     take_profit=66_000.0)).action == "SELL"


def test_bracket_side_is_validated():
    with pytest.raises(ValidationError):
        SigmaL4AlertPayload.model_validate(_alert(stop_loss=69_000.0))   # BUY-SL zu hoch
    with pytest.raises(ValidationError):
        SigmaL4AlertPayload.model_validate(_alert(take_profit=60_000.0))  # TP unter Entry


def test_millisecond_timestamps_are_normalised():
    now = int(time.time())
    alert = SigmaL4AlertPayload.model_validate(_alert(timestamp=now * 1000))
    assert alert.timestamp == now
    assert normalize_epoch(now * 1000) == now
    assert normalize_epoch(now) == now


@pytest.mark.parametrize("raw,expected", [
    ("KRAKEN:XRPUSD.P", "XRPUSD"), ("BINANCE:BTCUSDT", "BTCUSDT"),
    ("xbtusd", "XBTUSD"), (" ETHUSD ", "ETHUSD"),
])
def test_symbol_normalisation(raw, expected):
    assert normalize_symbol(raw) == expected


# ------------------------------------------------------------ Schema B (§33.2)

def test_pionex_payload_parses_native_placeholders():
    payload = PionexSignalPayload.model_validate({
        "data": {"action": "buy", "contracts": "1", "position_size": "1"},
        "price": "68000.5", "signal_param": "{}",
        "signal_type": "8a17bcf9-0d9c-4a09-92ae-27adf755d95d",
        "symbol": "BINANCE:BTCUSDT", "time": "2026-08-28T00:00:00Z",
    })
    assert payload.symbol == "BTCUSDT"
    assert payload.numeric_price == pytest.approx(68000.5)


# ------------------------------------------------------------ Schema C (§33.3)

def test_ml_feature_ranges():
    with pytest.raises(ValidationError):
        MLFeaturePayload.model_validate({"rsi": 120.0, "atr": 1.0})
    with pytest.raises(ValidationError):
        MLFeaturePayload.model_validate({"rsi": 50.0, "atr": 0.0})
    features = MLFeaturePayload.model_validate({"rsi": 50.0, "atr": 1.0})
    assert features.cisd_score == 0.5 and features.bb_bandwidth == 0.0
    assert set(bp.ML_FEATURE_FIELDS) <= set(MLFeaturePayload.model_fields)


# --------------------------------------------------------------- Erkennung ---

def test_schema_detection():
    assert detect_schema(_alert()) == "SIGMA_L4_MASTER"
    assert detect_schema({"data": {"action": "buy"}, "signal_type": "uuid-1234"}) \
        == "PIONEX_NATIVE"
    assert detect_schema({"rsi": 30.0, "atr": 1.0}) == "ML_TELEMETRY"
    with pytest.raises(SchemaDetectionError) as exc:
        detect_schema({"foo": "bar"})
    assert exc.value.code == ERR_SCHEMA_UNKNOWN
    with pytest.raises(SchemaDetectionError):
        detect_schema({})


def test_parse_payload_roundtrip():
    family, model = parse_payload(_alert())
    assert family == "SIGMA_L4_MASTER"
    again = SigmaL4AlertPayload.model_validate(model.model_dump())
    assert again == model


def test_response_contract_status_values():
    resp = SignalExecutionResponse(status="EXECUTED", schema_family="SIGMA_L4_MASTER")
    assert resp.accepted is True
    for status in ("REJECTED", "DUPLICATE_IGNORED", "VETO_ORDERBOOK"):
        assert SignalExecutionResponse(status=status,
                                       schema_family="SIGMA_L4_MASTER").accepted is False


# ------------------------------------------------------------- Router (§33.5)

def test_ingest_rejects_unknown_schema(client):
    resp = client.post("/api/v1/signal/ingest", json={"foo": "bar"})
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == ERR_SCHEMA_UNKNOWN


def test_ingest_rejects_invalid_alert(client):
    resp = client.post("/api/v1/signal/ingest", json=_alert(fixed_leverage=99))
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == ERR_SCHEMA_INVALID


def test_ingest_blocks_pionex_when_disabled(client):
    resp = client.post("/api/v1/signal/ingest", json={
        "data": {"action": "buy", "contracts": "1", "position_size": "1"},
        "price": "1.0", "signal_param": "{}", "signal_type": "8a17bcf9-0d9c",
        "symbol": "BTCUSDT", "time": "1",
    })
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == ERR_PIONEX_DISABLED


def test_ingest_rejects_bare_ml_payload(client):
    resp = client.post("/api/v1/signal/ingest", json={"rsi": 30.0, "atr": 1.0})
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == ERR_SCHEMA_INVALID


def test_ingest_executes_and_then_ignores_duplicate(client):
    key = f"sig_test_XBTUSD_{int(time.time())}_dup"
    body = _alert(idempotency_key=key, symbol="KRAKEN:XBTUSD")
    first = client.post("/api/v1/signal/ingest", json=body)
    assert first.status_code == 200, first.json()
    assert first.json()["status"] == "EXECUTED"
    assert first.json()["schema_family"] == "SIGMA_L4_MASTER"
    assert first.json()["fixed_leverage"] == 5

    second = client.post("/api/v1/signal/ingest", json=body)
    assert second.status_code == 200
    assert second.json()["status"] == "DUPLICATE_IGNORED"
    assert second.json()["stage"] == "idempotency"


def test_ingest_records_orderbook_veto_before_execution(client):
    import app.server.routes_sigma as routes

    previous = routes._DEPTH_ADAPTER
    routes.set_depth_adapter(type("_OpposingDepth", (), {
        "fetch": staticmethod(lambda symbol: OrderbookSnapshot(
            symbol, [(67_999.0, 10.0)], [(68_001.0, 90.0)], time.time()
        ))
    })())
    try:
        body = _alert(idempotency_key=f"sig_veto_XBTUSD_{int(time.time())}_book")
        response = client.post("/api/v1/signal/ingest", json=body)
        assert response.status_code == 200
        assert response.json()["status"] == "VETO_ORDERBOOK"
        assert response.json()["code"] == bp.ORDERBOOK_WALL_REJECT
    finally:
        routes.set_depth_adapter(previous)


def test_ingest_fails_closed_for_unsupported_live_futures(client):
    body = _alert(
        execution_mode="live",
        idempotency_key=f"sig_live_future_{int(time.time())}_blocked",
    )
    response = client.post("/api/v1/signal/ingest", json=body)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "FUTURES_LIVE_BRACKET_UNAVAILABLE"


def test_ingest_fails_closed_for_unsupported_live_spot(client):
    body = _alert(
        symbol="KRAKEN:XBTUSD",
        execution_mode="live",
        idempotency_key=f"sig_live_spot_{int(time.time())}_blocked",
    )
    response = client.post("/api/v1/signal/ingest", json=body)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "SPOT_LIVE_PNL_RECONCILIATION_UNAVAILABLE"


def test_ingest_rejects_non_allowlisted_symbol(client):
    resp = client.post("/api/v1/signal/ingest", json=_alert(
        symbol="KRAKEN:DOGEUSD", price=0.5, stop_loss=0.45, take_profit=0.6,
        idempotency_key=f"sig_doge_{int(time.time())}"))
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "SYMBOL_NOT_ALLOWED"


def test_schema_catalog_endpoint(client):
    body = client.get("/api/v1/signal/schemas").json()
    assert body["families"] == list(bp.WEBHOOK_SCHEMAS)
    assert body["ingestion_steps"] == list(bp.INGESTION_PIPELINE_STEPS)
    assert "SIGMA_L4_MASTER" in body["json_schema"]


def test_section_33_is_no_longer_pending():
    assert not any(s.startswith("33 ") for s in bp.DOCS_PENDING_SECTIONS)
    import os
    assert os.path.exists(bp.PINE_EMITTER_TEMPLATE_PATH.lstrip("./"))
