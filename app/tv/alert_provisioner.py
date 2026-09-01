"""
=========================================================
Datei:      app/tv/alert_provisioner.py
Zweck:      §4.6 / §17.5 — TradingView-Alerts idempotent verwalten.
            upsert / enable / disable, M8-gekoppelt, Secret im Template,
            Orphan-Reconcile.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Rouge (Allokation) / Jaune
=========================================================

Alert-Name ist der Idempotenz-Schlüssel: `sigma:{strategy_id}`.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from app.core import blueprint as bp
from app.core.config import SigmaConfig, load_config

logger = logging.getLogger("app.tv.alert_provisioner")


@dataclass
class AlertRecord:
    strategy_id: str
    name: str
    symbol: str
    interval: str
    tv_alert_id: str = ""
    enabled: bool = False
    webhook_url: str = ""
    message_template: str = ""
    updated_at: float = field(default_factory=time.time)
    last_reason: str = ""
    remote_synced: bool = False
    last_error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = "ENABLED" if self.enabled else "DISABLED"
        return d


def build_alert_payload(
    strategy_id: str,
    secret: str = "",
    *,
    execution_mode: Optional[str] = None,
    fixed_leverage: Optional[int] = None,
    bot_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Schema-A JSON (``SigmaL4AlertPayload``) mit TV-Platzhaltern.

    TradingView substituiert ``{{…}}`` beim Feuern. SL/TP kommen aus den
    Pine-Plots 3/4; fehlen die Plots, füllt der Ingest-Router ATR-Brackets.
    """
    if not secret:
        logger.warning(
            "SIGMA_WEBHOOK_SECRET not set — alert %s will contain placeholder; "
            "webhook auth disabled (dev only)", strategy_id)
    mode = execution_mode or bp.ExecutionMode.KRAKEN_PAPER.value
    leverage = (bp.FIXED_LEVERAGE_DEFAULT if fixed_leverage is None
                else int(fixed_leverage))
    return {
        "secret": secret or "<SIGMA_WEBHOOK_SECRET>",
        "idempotency_key": "{{strategy.order.id}}",
        "strategy_id": strategy_id,
        "bot_id": bot_id or strategy_id,
        "symbol": "{{ticker}}",
        "action": "{{strategy.order.action}}",
        "order_type": "MARKET",
        "price": "{{close}}",
        "stop_loss": "{{plot_3}}",
        "take_profit": "{{plot_4}}",
        "fixed_leverage": leverage,
        "timestamp": "{{timenow}}",
        "interval": "{{interval}}",
        "execution_mode": mode,
        "features": {
            "rsi": "{{plot_0}}",
            "atr": "{{plot_1}}",
            "cisd_score": "{{plot_2}}",
        },
    }


def build_alert_message(
    strategy_id: str,
    secret: str = "",
    *,
    execution_mode: Optional[str] = None,
    fixed_leverage: Optional[int] = None,
    bot_id: Optional[str] = None,
) -> str:
    """Pine ``alert_message`` — Schema A, kein Legacy-Top-Level mehr."""
    return json.dumps(build_alert_payload(
        strategy_id, secret, execution_mode=execution_mode,
        fixed_leverage=fixed_leverage, bot_id=bot_id))


class AlertProvisioner:
    """Persistiert Alert-Zustände lokal und spiegelt sie via Driver nach TV."""

    def __init__(self, config: Optional[SigmaConfig] = None, driver=None,
                 store_path: Optional[str] = None, public_webhook_base: str = "",
                 *, auto_driver: bool = True):
        self.config = config or load_config()
        self.auto_driver = auto_driver and driver is None
        self.driver = driver
        self.driver_error: str = ""
        self.store_path = store_path or os.path.join(
            os.path.dirname(self.config.tv_jobs_dir), "tv_alerts.json")
        self.public_webhook_base = (public_webhook_base
                                    or os.environ.get("SIGMA_PUBLIC_URL", "http://127.0.0.1:8000"))
        self._alerts: Dict[str, AlertRecord] = {}
        self._load()
        if self.auto_driver:
            self._attach_driver()

    # -------------------------------------------------------- driver wiring
    def _attach_driver(self) -> None:
        """Bindet den echten Playwright-Treiber, sobald eine TV-Session
        existiert. Ohne Session bleibt ``driver=None`` — kein Fake-Transport,
        der Provisioner arbeitet dann rein lokal und fail-closed."""
        try:
            from app.tv.tv_driver import get_tv_alert_driver

            self.driver = get_tv_alert_driver(self.config, required=False)
            self.driver_error = "" if self.driver is not None else "no_tv_session"
        except Exception as exc:  # pragma: no cover - defensiv
            self.driver = None
            self.driver_error = f"{type(exc).__name__}: {exc}"
            logger.warning("TV alert driver not attached: %s", self.driver_error)

    def ensure_driver(self):
        """Lazy Re-Bind: nach `bin/sigma-tv-login` ohne Neustart scharf."""
        if self.driver is None and self.auto_driver:
            self._attach_driver()
        return self.driver

    def driver_status(self) -> Dict[str, Any]:
        try:
            from app.tv.tv_driver import driver_snapshot

            status = driver_snapshot(self.config)
        except Exception as exc:  # pragma: no cover
            status = {"driver": "unavailable", "error": f"{type(exc).__name__}: {exc}"}
        status["attached"] = self.driver is not None
        status["attach_error"] = self.driver_error or None
        return status

    # ------------------------------------------------------------ persistence
    def _load(self) -> None:
        try:
            with open(self.store_path, "r", encoding="utf-8") as fh:
                for row in json.load(fh):
                    rec = AlertRecord(**{k: v for k, v in row.items() if k in AlertRecord.__annotations__})
                    self._alerts[rec.strategy_id] = rec
        except (FileNotFoundError, json.JSONDecodeError, TypeError):
            self._alerts = {}

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.store_path) or ".", exist_ok=True)
            tmp = f"{self.store_path}.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump([asdict(a) for a in self._alerts.values()], fh, indent=2)
            os.replace(tmp, self.store_path)
        except OSError as exc:  # pragma: no cover
            logger.warning("alert store persist failed: %s", exc)

    # ------------------------------------------------------------------ api
    def alert_name(self, strategy_id: str) -> str:
        return bp.ALERT_NAME_TEMPLATE.format(strategy_id=strategy_id)

    def webhook_url(self) -> str:
        return f"{self.public_webhook_base.rstrip('/')}{bp.WEBHOOK_INGEST_ROUTE}"

    def get(self, strategy_id: str) -> Optional[AlertRecord]:
        return self._alerts.get(strategy_id)

    def list(self) -> List[Dict[str, Any]]:
        return [a.to_dict() for a in self._alerts.values()]

    def upsert(self, strategy_id: str, symbol: str, interval: Any = 15,
               *, enable: bool = False) -> Dict[str, Any]:
        """Idempotent nach Alert-Name (§17.5)."""
        name = self.alert_name(strategy_id)
        rec = self._alerts.get(strategy_id) or AlertRecord(
            strategy_id=strategy_id, name=name, symbol=symbol, interval=str(interval))
        rec.symbol, rec.interval, rec.name = symbol, str(interval), name
        rec.webhook_url = self.webhook_url()
        rec.message_template = build_alert_message(
            strategy_id, self.config.webhook_secret,
            execution_mode=(bp.ExecutionMode.LIVE.value if self.config.live_trading
                            else bp.ExecutionMode.KRAKEN_PAPER.value),
        )
        rec.updated_at = time.time()
        self._alerts[strategy_id] = rec

        driver = self.ensure_driver()
        if driver is not None and hasattr(driver, "upsert_alert"):
            try:
                remote = driver.upsert_alert(
                    name=name, symbol=symbol, interval=interval,
                    webhook_url=rec.webhook_url, message=rec.message_template)
                rec.tv_alert_id = str(remote.get("tv_alert_id", rec.tv_alert_id))
            except Exception as exc:
                logger.error("TV alert upsert failed for %s: %s", strategy_id, exc)
                rec.last_reason = f"upsert_failed: {exc}"
        if enable:
            return self.enable(strategy_id, reason="upsert")
        self._save()
        return rec.to_dict()

    def enable(self, strategy_id: str, reason: str = "ui_start") -> Dict[str, Any]:
        rec = self._require(strategy_id)
        rec.enabled = True
        rec.last_reason = reason
        rec.updated_at = time.time()
        self._drive("enable_alert", rec)
        self._save()
        logger.info("alert %s ENABLED (%s)", rec.name, reason)
        return rec.to_dict()

    def disable(self, strategy_id: str, reason: str = "ui_stop") -> Dict[str, Any]:
        rec = self._require(strategy_id)
        rec.enabled = False
        rec.last_reason = reason
        rec.updated_at = time.time()
        self._drive("disable_alert", rec)
        self._save()
        logger.info("alert %s DISABLED (%s)", rec.name, reason)
        return rec.to_dict()

    # --------------------------------------------------------- M8 coupling
    def sync_with_m8(self, strategy_id: str, m8_state: str,
                     *, runner_running: bool = True) -> Dict[str, Any]:
        """Normative Matrix §4.6 — THROTTLED lässt den Alert bewusst an."""
        policy = bp.alert_policy_for_state(m8_state)
        rec = self._alerts.get(strategy_id)
        if rec is None:
            return {"strategy_id": strategy_id, "action": "none", "reason": "no alert record"}
        if policy.alert is bp.AlertAction.DISABLE:
            out = self.disable(strategy_id, reason=f"m8_{m8_state.lower()}")
            action = "disable"
        elif policy.alert is bp.AlertAction.ENABLE and runner_running:
            out = self.enable(strategy_id, reason=f"m8_{m8_state.lower()}")
            action = "enable"
        else:
            out = rec.to_dict()
            action = "keep"
        return {**out, "action": action, "budget_multiplier": policy.budget_multiplier,
                "accept_webhook": policy.accept_webhook}

    async def sync_all_with_m8(self, m8_engine, *,
                               runner_running: bool = True) -> Dict[str, Any]:
        """Nativer M8-Lifecycle-Rückkanal (§4.6).

        Liest die kanonischen Zustände direkt aus der ``M8StateEngine``
        (Redis SCAN ``m8:state:*`` mit Local-Fallback) und spiegelt die
        Alert-Matrix nach TradingView. Strategien ohne Alert-Record werden
        übersprungen; Waisen (Record ohne M8-State) werden abgeschaltet.
        """
        try:
            states = await m8_engine.scan_states()
        except Exception as exc:
            logger.error("M8 scan for alert sync failed: %s", exc)
            return {"ok": False, "reason": f"m8_scan_failed: {exc}", "results": []}

        results: List[Dict[str, Any]] = []
        seen: List[str] = []
        for instance_id, state in (states or {}).items():
            if instance_id not in self._alerts:
                continue
            seen.append(instance_id)
            status = str(state.get("status") or "ACTIVE").upper()
            try:
                results.append(self.sync_with_m8(instance_id, status,
                                                 runner_running=runner_running))
            except Exception as exc:  # pragma: no cover - defensiv
                logger.error("alert sync failed for %s: %s", instance_id, exc)
                results.append({"strategy_id": instance_id, "action": "error",
                                "reason": str(exc)})

        orphans: List[str] = []
        for sid, rec in self._alerts.items():
            if sid in seen or not rec.enabled:
                continue
            orphans.append(sid)
            self.disable(sid, reason="m8_state_missing")

        return {"ok": True, "synced": len(results), "orphans_disabled": orphans,
                "results": results, "driver": self.driver_status()}

    def disable_all(self, reason: str = "kill_switch") -> List[Dict[str, Any]]:
        return [self.disable(sid, reason) for sid in list(self._alerts)]

    def reconcile_alerts(self, known_strategy_ids: List[str]) -> Dict[str, Any]:
        """§17.5 — Waisen-Alerts (Strategie gelöscht) abschalten und entfernen."""
        orphans = [sid for sid in self._alerts if sid not in known_strategy_ids]
        for sid in orphans:
            try:
                self.disable(sid, reason="orphan_reconcile")
            finally:
                self._alerts.pop(sid, None)
        self._save()
        return {"orphans_removed": orphans, "active": len(self._alerts)}

    # ------------------------------------------------------------ internals
    def _require(self, strategy_id: str) -> AlertRecord:
        rec = self._alerts.get(strategy_id)
        if rec is None:
            raise KeyError(f"no alert provisioned for {strategy_id!r}")
        return rec

    def _drive(self, method: str, rec: AlertRecord) -> None:
        driver = self.ensure_driver()
        if driver is None:
            rec.remote_synced = False
            rec.last_error = self.driver_error or "no_tv_driver"
            return
        if not hasattr(driver, method):
            rec.remote_synced = False
            rec.last_error = f"driver_missing_{method}"
            return
        try:
            getattr(driver, method)(rec.tv_alert_id or rec.name)
            rec.remote_synced = True
            rec.last_error = ""
        except Exception as exc:
            logger.error("TV %s failed for %s: %s", method, rec.name, exc)
            rec.last_reason = f"{method}_failed: {exc}"
            rec.remote_synced = False
            rec.last_error = f"{method}_failed: {exc}"

    def snapshot(self) -> Dict[str, Any]:
        return {
            "webhook_url": self.webhook_url(),
            "secret_configured": bool(self.config.webhook_secret),
            "alerts": self.list(),
            "enabled_count": sum(1 for a in self._alerts.values() if a.enabled),
            "remote_synced_count": sum(1 for a in self._alerts.values()
                                       if a.remote_synced),
            "driver": self.driver_status(),
        }


_provisioner: Optional[AlertProvisioner] = None


def get_alert_provisioner(**kwargs) -> AlertProvisioner:
    global _provisioner
    if _provisioner is None:
        _provisioner = AlertProvisioner(**kwargs)
    return _provisioner
