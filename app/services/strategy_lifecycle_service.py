"""
=========================================================
Datei:      app/services/strategy_lifecycle_service.py
Zweck:      §31 / Axiom 9 — Die 3 Trigger-Pfade zur Strategie-Platzierung
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Carrera-Engine) / Services
=========================================================

Drei kanonische Ausloeser muenden in **dieselbe** Dispatcher-Pipeline:

    PFAD 1 MANUAL            (UI / LLM-Chat / Telegram)   -> live | kraken_paper
    PFAD 2 AUTONOMOUS_REGIME (RegimeStrategyDispatcher)   -> live (nach OB-Audit)
    PFAD 3 SCOUT_INCUBATOR   (Loop D, alle 30 min)        -> immer kraken_paper

Fuenf technische Schritte (§31.4), in genau dieser Reihenfolge:

    1. budget_reservation        (Core   — CapitalFlywheelEngine, isolierter Topf)
    2. chart_navigation          (Worker — TV-Chart mit tv_storage_state.json)
    3. pine_injection_compile    (Worker — code.pine -> Save -> Add to Chart)
    4. webhook_alert_provisioning(Worker — AlertProvisioner, /api/v1/signal/ingest)
    5. arming_m8_active          (Core   — M8 ACTIVE, fixed_leverage, Bot RUNNING)

Noir-Gates: Kill-Switch/Pause blocken jeden Pfad; Pfad 2 verlangt Glint >= 8/10
**und** ein positives Orderbuch-Audit (§24); Pfad 3 darf niemals live werden.
Bei Fehlschlag eines Schrittes wird die Budget-Reservierung zurueckgerollt.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from app.core import blueprint as bp

logger = logging.getLogger("app.services.strategy_lifecycle")

RUNNING = "RUNNING"
PAUSED = "PAUSED"
QUARANTINED = "QUARANTINED"
LIFECYCLE_STATES = (RUNNING, PAUSED, QUARANTINED)


class LifecycleError(RuntimeError):
    """Fachlicher Abbruch eines Trigger-Pfades (mit Code fuer die API)."""

    def __init__(self, code: str, reason: str, *, status_code: int = 409) -> None:
        super().__init__(f"{code}: {reason}")
        self.code = code
        self.reason = reason
        self.status_code = status_code


@dataclass
class StepResult:
    step: str
    ok: bool
    detail: str = ""
    data: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class LifecycleRecord:
    """Ein Platzierungsvorgang — auditierbar von Trigger bis Scharfschaltung."""

    run_id: str
    strategy_id: str
    symbol: str
    trigger_path: str
    execution_mode: str
    budget_eur: float
    fixed_leverage: int
    timeframe: str = "15"
    bot_id: str = ""
    state: str = PAUSED
    steps: List[StepResult] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    ok: bool = False
    code: str = ""
    reason: str = ""

    def as_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["steps"] = [s.as_dict() for s in self.steps]
        data["badge"] = f"[ {self.fixed_leverage}x HEBEL ]"
        return data


class StrategyLifecycleService:
    """Zentrale Dispatcher-Pipeline fuer alle drei Trigger-Pfade (§31)."""

    def __init__(
        self,
        *,
        virtual_bots=None,
        alert_provisioner=None,
        tv_queue=None,
        flywheel=None,
        safety=None,
        verifier=None,
        depth_adapter=None,
        allocator=None,
        config=None,
    ) -> None:
        self.config = config
        self._virtual_bots = virtual_bots
        self._alerts = alert_provisioner
        self._tv_queue = tv_queue
        self._flywheel = flywheel
        self._safety = safety
        self._verifier = verifier
        self._depth_adapter = depth_adapter
        self._allocator = allocator
        self._runs: List[LifecycleRecord] = []
        self._by_strategy: Dict[str, LifecycleRecord] = {}

    # ------------------------------------------------------------ lazy DI ---
    @property
    def virtual_bots(self):
        if self._virtual_bots is None:
            from app.execution.VirtualBotEngine import get_virtual_bot_engine
            self._virtual_bots = get_virtual_bot_engine()
        return self._virtual_bots

    @property
    def alerts(self):
        if self._alerts is None:
            from app.tv.alert_provisioner import get_alert_provisioner
            self._alerts = get_alert_provisioner()
        return self._alerts

    @property
    def tv_queue(self):
        if self._tv_queue is None:
            from app.tv.worker import get_tv_queue
            self._tv_queue = get_tv_queue()
        return self._tv_queue

    @property
    def flywheel(self):
        if self._flywheel is None:
            from app.execution.capital_flywheel_engine import CapitalFlywheelEngine
            self._flywheel = CapitalFlywheelEngine()
        return self._flywheel

    @property
    def safety(self):
        if self._safety is None:
            from app.execution.SafetyGuard import get_safety_guard
            self._safety = get_safety_guard()
        return self._safety

    @property
    def verifier(self):
        if self._verifier is None:
            from app.quant.glint_orderbook_verifier import get_verifier
            self._verifier = get_verifier()
        return self._verifier

    # --------------------------------------------------------- validation ---
    @staticmethod
    def _resolve_mode(trigger_path: str, requested: Optional[str]) -> str:
        allowed = bp.TRIGGER_PATH_MODES.get(trigger_path)
        if allowed is None:
            raise LifecycleError("UNKNOWN_TRIGGER_PATH",
                                 f"{trigger_path} ist kein kanonischer Pfad",
                                 status_code=400)
        mode = requested or allowed[0]
        if mode not in allowed:
            raise LifecycleError(
                "EXECUTION_MODE_NOT_ALLOWED",
                f"Pfad {trigger_path} erlaubt nur {list(allowed)} (angefragt: {mode})",
                status_code=400)
        return mode

    def _guard(self) -> None:
        snapshot = getattr(self.safety, "snapshot", lambda: {})() or {}
        if snapshot.get("kill_switch"):
            raise LifecycleError("KILL_SWITCH_ACTIVE",
                                 "Kill-Switch aktiv — keine Platzierung", status_code=403)
        if snapshot.get("paused"):
            raise LifecycleError("SYSTEM_PAUSED",
                                 "System pausiert — keine Platzierung", status_code=403)

    def _leverage_for(self, strategy_id: str, requested: Optional[int],
                      style: Optional[str]) -> int:
        from app.execution.fixed_leverage import clamp_leverage, load_profile

        if requested is not None:
            return clamp_leverage(requested)
        root = getattr(self.config, "strategies_dir", None) or bp.PATH_STRATEGIES
        return load_profile(strategy_id, strategies_root=root, style=style).fixed_leverage

    # ------------------------------------------------------------- start  ---
    def start(
        self,
        strategy_id: str,
        symbol: str,
        *,
        trigger_path: str = bp.TriggerPath.MANUAL.value,
        budget_eur: float = 250.0,
        execution_mode: Optional[str] = None,
        fixed_leverage: Optional[int] = None,
        timeframe: str = "15",
        style: Optional[str] = None,
        glint_score: Optional[float] = None,
        orderbook=None,
        initiator: str = "operator",
    ) -> LifecycleRecord:
        """Fuehrt die kanonische Pipeline aus und liefert das Audit-Protokoll."""
        mode = self._resolve_mode(trigger_path, execution_mode)
        leverage = self._leverage_for(strategy_id, fixed_leverage, style)
        record = LifecycleRecord(
            run_id=f"lc_{uuid.uuid4().hex[:10]}", strategy_id=strategy_id,
            symbol=symbol, trigger_path=trigger_path, execution_mode=mode,
            budget_eur=float(budget_eur), fixed_leverage=leverage,
            timeframe=str(timeframe),
        )
        self._runs.append(record)
        del self._runs[:-200]

        try:
            self._guard()
            self._preflight(record, glint_score=glint_score, orderbook=orderbook,
                            initiator=initiator)
            self._step_budget(record)
            self._step_chart(record)
            self._step_pine(record)
            self._step_alert(record)
            self._step_arm(record)
            record.ok = True
            record.state = RUNNING
            record.code = "LIFECYCLE_ARMED"
            record.reason = f"{trigger_path} -> {mode} @ {leverage}x"
        except LifecycleError as exc:
            record.ok = False
            record.code = exc.code
            record.reason = exc.reason
            record.state = PAUSED
            self._rollback(record)
            logger.warning("lifecycle %s abgebrochen: %s", record.run_id, exc)
        finally:
            record.finished_at = time.time()
            self._by_strategy[strategy_id] = record
        return record

    # -------------------------------------------------------- pfad-gates  ---
    def _preflight(self, record: LifecycleRecord, *, glint_score: Optional[float],
                   orderbook, initiator: str) -> None:
        path = record.trigger_path
        if path == bp.TriggerPath.SCOUT_INCUBATOR.value:
            if record.execution_mode != bp.ExecutionMode.KRAKEN_PAPER.value:
                raise LifecycleError("SCOUT_MUST_BE_PAPER",
                                     "Loop D ist paper-only (§31.3)", status_code=400)
            record.steps.append(StepResult("preflight", True,
                                           "Scout-Incubation, kein Live-Budget"))
            return

        if path == bp.TriggerPath.AUTONOMOUS_REGIME.value:
            score = float(glint_score if glint_score is not None else 0.0)
            if score < bp.GLINT_SCORE_AUTONOMOUS_ENTRY:
                raise LifecycleError(
                    "GLINT_SCORE_TOO_LOW",
                    f"Glint {score:.1f} < {bp.GLINT_SCORE_AUTONOMOUS_ENTRY} — "
                    "kein autonomer Live-Start")
            if orderbook is None and self._depth_adapter is not None:
                try:
                    orderbook = self._depth_adapter.fetch(record.symbol)
                except Exception as exc:
                    raise LifecycleError(
                        "ORDERBOOK_DEPTH_UNAVAILABLE",
                        f"Kraken JIT depth unavailable: {exc}",
                        status_code=503,
                    ) from exc
            if orderbook is None:
                raise LifecycleError("ORDERBOOK_AUDIT_MISSING",
                                     "Pfad 2 verlangt ein JIT-Orderbuch-Audit (§24)")
            direction = "BULLISH"
            from app.core.exchange_clock import get_exchange_clock

            verdict = self.verifier.verify(
                orderbook, direction, now=get_exchange_clock().now()
            )
            if not verdict.approved:
                raise LifecycleError(verdict.reject_code or bp.ORDERBOOK_WALL_REJECT,
                                     verdict.reason)
            record.steps.append(StepResult(
                "preflight", True,
                f"Glint {score:.1f} + {verdict.verdict}",
                {"glint_score": score, "confluence": verdict.as_dict()}))
            return

        record.steps.append(StepResult("preflight", True, f"manuell durch {initiator}"))

    # ------------------------------------------------------------ schritte --
    def _step_budget(self, record: LifecycleRecord) -> None:
        step = bp.LIFECYCLE_STEPS[0]
        if record.execution_mode == bp.ExecutionMode.KRAKEN_PAPER.value:
            record.steps.append(StepResult(step, True, "Paper-Modus — kein Live-Budget",
                                           {"budget_eur": 0.0}))
            return
        outcome = self.flywheel.allocate_bot_budget(record.strategy_id, record.budget_eur)
        if not outcome.get("reserved"):
            raise LifecycleError(outcome.get("reason", "BUDGET_RESERVATION_FAILED"),
                                 f"Budget {record.budget_eur:.2f} EUR nicht reservierbar "
                                 f"(frei: {outcome.get('free_eur', 0.0)} EUR)")
        record.steps.append(StepResult(step, True,
                                       f"{record.budget_eur:.2f} EUR isoliert", outcome))

    def _step_chart(self, record: LifecycleRecord) -> None:
        step = bp.LIFECYCLE_STEPS[1]
        url = f"https://www.tradingview.com/chart/?symbol=KRAKEN:{record.symbol}"
        record.steps.append(StepResult(step, True, url,
                                       {"session_file": bp.PATH_TV_STORAGE_STATE}))

    def _step_pine(self, record: LifecycleRecord) -> None:
        from app.tv.worker import JOB_KIND_PUSH_CODE

        step = bp.LIFECYCLE_STEPS[2]
        try:
            job = self.tv_queue.submit(JOB_KIND_PUSH_CODE, strategy_id=record.strategy_id,
                                       symbol=record.symbol, interval=record.timeframe)
        except Exception as exc:
            raise LifecycleError("ERR_TV_PINE_COMPILE_ERROR",
                                 f"Pine-Job nicht einreihbar: {exc}") from exc
        record.steps.append(StepResult(step, True, f"job {job.job_id} queued",
                                       {"job_id": job.job_id, "kind": job.kind}))

    def _step_alert(self, record: LifecycleRecord) -> None:
        step = bp.LIFECYCLE_STEPS[3]
        try:
            alert = self.alerts.upsert(record.strategy_id, record.symbol,
                                       record.timeframe, enable=True)
        except Exception as exc:
            raise LifecycleError("ERR_TV_ALERT_PROVISION_FAILED",
                                 f"Alert-Provisionierung fehlgeschlagen: {exc}") from exc
        record.steps.append(StepResult(step, True, alert.get("webhook_url", ""),
                                       {"alert": alert}))

    def _step_arm(self, record: LifecycleRecord) -> None:
        step = bp.LIFECYCLE_STEPS[4]
        bots = self.virtual_bots
        existing = [b for b in bots.for_strategy(record.strategy_id)
                    if b.symbol == record.symbol]
        bot = existing[0] if existing else bots.create_bot(
            record.strategy_id, record.symbol, record.budget_eur,
            timeframe=record.timeframe)
        setattr(bot, "fixed_leverage", record.fixed_leverage)
        setattr(bot, "execution_mode", record.execution_mode)
        setattr(bot, "trigger_path", record.trigger_path)
        if bot.status == QUARANTINED:
            raise LifecycleError("BOT_QUARANTINED",
                                 f"{bot.bot_id} ist in Quarantaene — kein Start")
        result = bots.start(bot.bot_id)
        if not result.get("ok", True):
            raise LifecycleError("BOT_START_REJECTED", str(result.get("reason", "")))
        bots.apply_m8_state(bot.bot_id, bp.M8State.ACTIVE.value)
        record.bot_id = bot.bot_id
        record.steps.append(StepResult(step, True,
                                       f"{bot.bot_id} ACTIVE @ {record.fixed_leverage}x",
                                       {"bot": bot.to_card()}))

    def _rollback(self, record: LifecycleRecord) -> None:
        """Reservierte Mittel freigeben, wenn ein spaeterer Schritt scheitert."""
        reserved = any(s.step == bp.LIFECYCLE_STEPS[0] and s.ok and
                       s.data.get("reserved") for s in record.steps)
        if reserved:
            self.flywheel.release_bot_budget(record.strategy_id, record.budget_eur)
            record.steps.append(StepResult("rollback", True,
                                           "Budget-Reservierung zurueckgerollt"))

    # ------------------------------------------------------ state machine ---
    def pause(self, strategy_id: str, reason: str = "operator") -> Dict[str, Any]:
        return self._transition(strategy_id, PAUSED, reason)

    def resume(self, strategy_id: str, reason: str = "operator") -> Dict[str, Any]:
        self._guard()
        return self._transition(strategy_id, RUNNING, reason)

    def quarantine(self, strategy_id: str, reason: str = "risk") -> Dict[str, Any]:
        return self._transition(strategy_id, QUARANTINED, reason)

    def _transition(self, strategy_id: str, target: str, reason: str) -> Dict[str, Any]:
        if target not in LIFECYCLE_STATES:
            raise LifecycleError("UNKNOWN_STATE", target, status_code=400)
        record = self._by_strategy.get(strategy_id)
        if record is None:
            raise LifecycleError("UNKNOWN_STRATEGY",
                                 f"{strategy_id} wurde nie platziert", status_code=404)
        if record.state == QUARANTINED and target == RUNNING:
            raise LifecycleError("QUARANTINE_LOCKED",
                                 "Quarantaene wird nicht per Resume aufgehoben")
        bots = self.virtual_bots
        cards = []
        for bot in bots.for_strategy(strategy_id):
            if target == RUNNING:
                bots.start(bot.bot_id)
            elif target == PAUSED:
                bots.pause(bot.bot_id)
            else:
                bots.apply_m8_state(bot.bot_id, bp.M8State.QUARANTINED.value)
            cards.append(bot.to_card())
        if target == RUNNING:
            self.alerts.enable(strategy_id, reason=reason)
        else:
            self.alerts.disable(strategy_id, reason=reason)
        record.state = target
        record.reason = reason
        return {"strategy_id": strategy_id, "state": target, "reason": reason,
                "bots": cards, "run": record.as_dict()}

    # --------------------------------------------------------- telemetrie ---
    def status(self, strategy_id: str) -> Optional[Dict[str, Any]]:
        record = self._by_strategy.get(strategy_id)
        return record.as_dict() if record else None

    def snapshot(self, limit: int = 25) -> Dict[str, Any]:
        return {
            "trigger_paths": {p.value: list(bp.TRIGGER_PATH_MODES[p.value])
                              for p in bp.TriggerPath},
            "steps": list(bp.LIFECYCLE_STEPS),
            "states": list(LIFECYCLE_STATES),
            "glint_entry_threshold": bp.GLINT_SCORE_AUTONOMOUS_ENTRY,
            "scout_cycle_minutes": bp.SCOUT_INCUBATOR_CYCLE_MINUTES,
            "active": {sid: rec.state for sid, rec in self._by_strategy.items()},
            "runs": [r.as_dict() for r in self._runs[-limit:]],
        }


_SERVICE: Optional[StrategyLifecycleService] = None


def get_lifecycle_service(**kwargs: Any) -> StrategyLifecycleService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = StrategyLifecycleService(**kwargs)
    return _SERVICE


def set_lifecycle_service(service: Optional[StrategyLifecycleService]) -> None:
    global _SERVICE
    _SERVICE = service
