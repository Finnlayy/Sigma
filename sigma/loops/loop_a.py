"""
=========================================================
Datei:      sigma/loops/loop_a.py
Zweck:      LoopAPort.ingest(SchemaA) -> ExecutionReceipt
            Adapter über app.execution.LoopAPipeline — einziger Live/Paper-Pfad.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Loop A Port)
=========================================================
"""
from __future__ import annotations

from typing import Any, Dict, Tuple

from sigma.execution.base_bridge import ExecutionReceipt


class LoopAPort:
    """Dünner Adapter. Keine zweite Pipeline — delegiert an LoopAPipeline."""

    def __init__(self, pipeline: Any = None) -> None:
        self.pipeline = pipeline

    def _pipeline(self) -> Any:
        if self.pipeline is not None:
            return self.pipeline
        try:
            import app.server.routes_sigma as routes

            return getattr(routes, "_pipeline", None)
        except Exception:
            return None

    def ingest(self, schema_a: Any, **kwargs: Any) -> ExecutionReceipt:
        pipeline = self._pipeline()
        if pipeline is None:
            return ExecutionReceipt(ok=False, accepted=False, reason="pipeline_unavailable")
        sig, extra = _to_signal(schema_a)
        extra.update(kwargs)
        response = pipeline.handle_signal(sig, **extra)
        return ExecutionReceipt.from_pipeline(response)


def _to_signal(schema_a: Any) -> Tuple[Any, Dict[str, Any]]:
    from app.execution.LoopAPipeline import SignalRequest

    if isinstance(schema_a, SignalRequest):
        return schema_a, {}
    if hasattr(schema_a, "symbol") and hasattr(schema_a, "action"):
        features = getattr(schema_a, "features", None)
        sig = SignalRequest(
            symbol=str(schema_a.symbol),
            action=str(schema_a.action),
            price=float(getattr(schema_a, "price", 0.0) or 0.0),
            rsi=float(getattr(features, "rsi", 50.0) or 50.0) if features else 50.0,
            atr=float(getattr(features, "atr", 0.0) or 0.0) if features else 0.0,
            cisd_score=(
                float(getattr(features, "cisd_score", 0.5) or 0.5) if features else 0.5
            ),
            timestamp=int(getattr(schema_a, "timestamp", 0) or 0),
            strategy_id=str(getattr(schema_a, "strategy_id", "") or ""),
            interval=getattr(schema_a, "interval", 15) or 15,
            secret=str(getattr(schema_a, "secret", "") or ""),
        )
        extra: Dict[str, Any] = {}
        if getattr(schema_a, "secret", None):
            extra["provided_secret"] = schema_a.secret
        if getattr(schema_a, "idempotency_key", None):
            extra["idempotency_key"] = schema_a.idempotency_key
        if getattr(schema_a, "bot_id", None):
            extra["bot_id"] = schema_a.bot_id
        if getattr(schema_a, "execution_mode", None):
            extra["execution_mode"] = schema_a.execution_mode
        if getattr(schema_a, "fixed_leverage", None) is not None:
            extra["fixed_leverage"] = schema_a.fixed_leverage
        if getattr(schema_a, "market_type", None):
            extra["execution_market"] = schema_a.market_type
        return sig, extra
    if isinstance(schema_a, dict):
        feats = schema_a.get("features") or {}
        sig = SignalRequest(
            symbol=str(schema_a.get("symbol") or ""),
            action=str(schema_a.get("action") or ""),
            price=float(schema_a.get("price") or 0.0),
            rsi=float(feats.get("rsi", 50.0) or 50.0),
            atr=float(feats.get("atr", 0.0) or 0.0),
            cisd_score=float(feats.get("cisd_score", 0.5) or 0.5),
            timestamp=int(schema_a.get("timestamp") or 0),
            strategy_id=str(schema_a.get("strategy_id") or ""),
            interval=schema_a.get("interval") or 15,
            secret=str(schema_a.get("secret") or ""),
        )
        extra = {}
        for key, dest in (
            ("secret", "provided_secret"),
            ("idempotency_key", "idempotency_key"),
            ("bot_id", "bot_id"),
            ("execution_mode", "execution_mode"),
            ("fixed_leverage", "fixed_leverage"),
            ("market_type", "execution_market"),
        ):
            if schema_a.get(key) is not None:
                extra[dest] = schema_a[key]
        return sig, extra
    raise TypeError(f"unsupported Schema A payload: {type(schema_a)!r}")
