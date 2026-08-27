"""
=========================================================
Datei:      app/execution/M8StateEngine.py (Blueprint v1.2.0 Final / Skeleton 1.6.4)
Zweck:      Atomare & Idempotente Redis Lua State Machine
            ACTIVE -> THROTTLED -> QUARANTINED -> RETIRED
            + Vault Profit Sweep (v1.2.0: 100% auf jeden Net Win)
            + EOD Profit-Factor-Gate (3 Tage PF<1 -> THROTTLED, 7 -> QUARANTINED)
            + RETIRED-Pfad (4 Wochen Shadow ohne GA-Rekalibrierung)
Knoten:     Jaune (Carrera-Engine)
=========================================================
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional, Set

from app.core.config import AlphaConfig
from app.core.event_bus import get_event_bus
from app.core.telemetry import get_telemetry_center

logger = logging.getLogger("app.execution.m8_state_engine")


def _ga_ts_to_iso(ts: float) -> str:
    import datetime

    return datetime.datetime.fromtimestamp(float(ts), datetime.timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def _hashify(result: Any) -> Dict[str, Any]:
    """Lua HGETALL-Reply (flache List [k1,v1,k2,v2]) → Dict normalisieren."""
    if isinstance(result, dict):
        return result
    if isinstance(result, (list, tuple)):
        out: Dict[str, Any] = {}
        for i in range(0, len(result) - 1, 2):
            out[str(result[i])] = result[i + 1]
        return out
    return {}


KEY_STATE = "m8:state:{}"
KEY_PROCESSED = "m8:processed_trades:{}"
KEY_HALT_SYMBOL = "halt:symbol:{}"
TOPIC_WAKE = "strategies:wake_up"

# ---------------------------------------------------------------------------
# Lua: idempotenter Post-Trade-State-Update (SISMEMBER/SADD Guard, v1.6.4)
# Budget-Mathematik folgt dem frozen v1.2.0 Sweep: bei Gewinn wird das Budget
# auf Base begrenzt, der Überschuss wird VOR dem Call als Vault-Sweep erfasst.
# ---------------------------------------------------------------------------
LUA_IDEMPOTENT_POST_TRADE_SCRIPT = """
local key = KEYS[1]
local processed_set_key = KEYS[2]
local trade_id = ARGV[1]
local pnl = tonumber(ARGV[2])
local instance_id = ARGV[3]

if redis.call('SISMEMBER', processed_set_key, trade_id) == 1 then
    return redis.call('HGETALL', key)
end

redis.call('SADD', processed_set_key, trade_id)

local base_budget = tonumber(redis.call('HGET', key, 'base_budget_usd'))
local current_budget = tonumber(redis.call('HGET', key, 'current_budget_usd'))
local status = redis.call('HGET', key, 'status')

if not current_budget then
    return redis.error_reply("Instance state not found")
end

if status == "QUARANTINED" or status == "RETIRED" then
    redis.call('HINCRBY', key, 'shadow_trades_count', 1)
    if pnl > 0 then
        redis.call('HINCRBY', key, 'shadow_wins', 1)
    end
    return redis.call('HGETALL', key)
end

if pnl < 0 then
    current_budget = current_budget + pnl
    redis.call('HINCRBY', key, 'consecutive_losses', 1)
else
    redis.call('HSET', key, 'consecutive_losses', 0)
    -- v1.2.0 Vault Sweep: Budget wird auf Base zurückgesetzt (Überschuss -> Vault)
    local needed = base_budget - current_budget
    if needed > 0 then
        local retained = math.min(pnl, needed)
        current_budget = current_budget + retained
    end
end

if current_budget <= 0.0 then
    status = "QUARANTINED"
    current_budget = 0.0
    redis.call('HSET', key, 'budget_multiplier', 0.0)
    redis.call('PUBLISH', 'strategies:wake_up', instance_id)
elseif current_budget <= (base_budget * 0.5) then
    status = "THROTTLED"
    redis.call('HSET', key, 'budget_multiplier', 0.5)
elseif status == "THROTTLED" and current_budget > (base_budget * 0.5) then
    -- v1.2.0 frozen: Re-Promotion bei > 50% Base (Delta 1.6.4: >= 80% Lua-Promotion)
    status = "ACTIVE"
    redis.call('HSET', key, 'budget_multiplier', 1.0)
end

redis.call('HSET', key, 'current_budget_usd', current_budget)
redis.call('HSET', key, 'status', status)

return redis.call('HGETALL', key)
"""

# ---------------------------------------------------------------------------
# Lua: EOD Profit-Factor-Gate (atomar). has_trades=0 -> Zähler bleibt stehen.
# ---------------------------------------------------------------------------
LUA_EOD_PF_SCRIPT = """
local key = KEYS[1]
local instance_id = ARGV[1]
local has_trades = ARGV[2]
local pf = tonumber(ARGV[3])

if has_trades ~= "1" then
    redis.call('HSET', key, 'last_eod_date', ARGV[4])
    return redis.call('HGETALL', key)
end

local c = tonumber(redis.call('HGET', key, 'consecutive_low_pf_days') or '0')
local status = redis.call('HGET', key, 'status')

if pf < 1.0 then
    c = c + 1
else
    c = 0
end
redis.call('HSET', key, 'consecutive_low_pf_days', c)
redis.call('HSET', key, 'last_eod_date', ARGV[4])

if c >= 7 then
    status = "QUARANTINED"
    redis.call('HSET', key, 'budget_multiplier', 0.0)
    redis.call('PUBLISH', 'strategies:wake_up', instance_id)
elseif c >= 3 and status == "ACTIVE" then
    status = "THROTTLED"
    redis.call('HSET', key, 'budget_multiplier', 0.5)
end

redis.call('HSET', key, 'status', status)
return redis.call('HGETALL', key)
"""


@dataclass
class StrategyState:
    strategy_id: str
    status: str = "ACTIVE"
    base_budget_usd: float = 50.0
    current_budget_usd: float = 50.0
    consecutive_losses: int = 0
    consecutive_low_pf_days: int = 0
    shadow_trades_count: int = 0
    shadow_wins: int = 0
    budget_multiplier: float = 1.0
    last_ga_recalibration_ts: Optional[float] = None
    last_eod_date: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_hash(cls, data: Dict[str, Any]) -> "StrategyState":
        def _f(k, d=0.0):
            try:
                return float(data.get(k, d))
            except (TypeError, ValueError):
                return d

        def _i(k, d=0):
            try:
                return int(float(data.get(k, d)))
            except (TypeError, ValueError):
                return d

        def _ga(k):
            v = data.get(k)
            if not v:
                return None
            try:
                return float(v)
            except (TypeError, ValueError):
                import datetime

                try:
                    return datetime.datetime.strptime(str(v)[:19],
                                                      "%Y-%m-%d %H:%M:%S").timestamp()
                except Exception:
                    return None

        return cls(
            strategy_id=str(data.get("strategy_id", "")),
            status=str(data.get("status", "ACTIVE")),
            base_budget_usd=_f("base_budget_usd", 50.0),
            current_budget_usd=_f("current_budget_usd", 50.0),
            consecutive_losses=_i("consecutive_losses"),
            consecutive_low_pf_days=_i("consecutive_low_pf_days"),
            shadow_trades_count=_i("shadow_trades_count"),
            shadow_wins=_i("shadow_wins"),
            budget_multiplier=_f("budget_multiplier", 1.0),
            last_ga_recalibration_ts=_ga("last_ga_recalibration_ts"),
            last_eod_date=data.get("last_eod_date"),
        )


class M8StateEngine:
    """Redis-first (Lua atomar), Local-Fallback bei fehlendem Redis."""

    def __init__(self, redis_client=None, config: Optional[AlphaConfig] = None):
        self.redis = redis_client
        self.config = config or AlphaConfig()
        self.lua_trade_sha: Optional[str] = None
        self.lua_eod_sha: Optional[str] = None
        self.states: Dict[str, StrategyState] = {}
        self.local_processed_trades: Set[str] = set()
        self.store = None  # injektierbarer DuckDBStore (Default: get_store())

    def _store(self):
        if self.store is None:
            from app.core.duckdb_store import get_store

            self.store = get_store()
        return self.store

    # ------------------------------------------------------------------ setup
    async def initialize_scripts(self) -> None:
        if self.redis:
            self.lua_trade_sha = await self.redis.script_load(LUA_IDEMPOTENT_POST_TRADE_SCRIPT)
            self.lua_eod_sha = await self.redis.script_load(LUA_EOD_PF_SCRIPT)

    async def register_strategy(self, instance_id: str, base_budget_usd: Optional[float] = None,
                                status: str = "ACTIVE",
                                last_ga_recalibration_ts: Optional[float] = None) -> StrategyState:
        base = float(base_budget_usd or self.config.base_budget_usd)
        state = StrategyState(
            strategy_id=instance_id,
            status=status,
            base_budget_usd=base,
            current_budget_usd=base,
            budget_multiplier=1.0 if status == "ACTIVE" else (0.5 if status == "THROTTLED" else 0.0),
            last_ga_recalibration_ts=last_ga_recalibration_ts or time.time(),
        )
        self.states[instance_id] = state
        if self.redis:
            payload = {
                "strategy_id": instance_id,
                "status": state.status,
                "base_budget_usd": state.base_budget_usd,
                "current_budget_usd": state.current_budget_usd,
                "budget_multiplier": state.budget_multiplier,
                "consecutive_losses": 0,
                "consecutive_low_pf_days": 0,
                "shadow_trades_count": 0,
                "shadow_wins": 0,
            }
            if last_ga_recalibration_ts:
                payload["last_ga_recalibration_ts"] = _ga_ts_to_iso(last_ga_recalibration_ts)
            await self.redis.hset(KEY_STATE.format(instance_id),
                                  mapping={k: str(v) for k, v in payload.items()})
        return state

    def get_strategy_state(self, instance_id: str) -> Optional[StrategyState]:
        return self.states.get(instance_id)

    def _hash_key(self, instance_id: str) -> str:
        return KEY_STATE.format(instance_id)

    async def _redis_state(self, instance_id: str) -> Optional[Dict[str, Any]]:
        raw = await self.redis.hgetall(self._hash_key(instance_id))
        if not raw:
            return None
        state = StrategyState.from_hash(raw)
        self.states[instance_id] = state
        return state.to_dict()

    # ------------------------------------------------------------- post trade
    async def update_post_trade_state(self, instance_id: str, pnl_usd: float,
                                      trade_id: str = "trd_default") -> Dict[str, Any]:
        """Idempotenter Post-Trade-Update + v1.2.0 Vault-Sweep + DuckDB-Sync."""
        if self.redis and self.lua_trade_sha:
            pre = await self._redis_state(instance_id)
            if pre is None:
                raise ValueError(f"Instance state '{instance_id}' not found in Redis.")
            key = self._hash_key(instance_id)
            processed_set_key = KEY_PROCESSED.format(instance_id)
            result = await self.redis.evalsha(
                self.lua_trade_sha, 2, key, processed_set_key,
                trade_id, str(pnl_usd), instance_id,
            )
            state = StrategyState.from_hash(_hashify(result))
            self.states[instance_id] = state
            self._sync_vault_sweep(instance_id, pre, state.to_dict(), pnl_usd)
            self._persist_budget(state.to_dict())
            if state.status == "QUARANTINED" and pre.get("status") != "QUARANTINED":
                await self._publish_wake(instance_id, reason="BUDGET_ZERO_QUARANTINE")
            return state.to_dict()

        # ------------------------------------------------------ local fallback
        if trade_id in self.local_processed_trades:
            state = self.states.get(instance_id)
            return state.to_dict() if state else {}
        self.local_processed_trades.add(trade_id)
        state = self.states.get(instance_id)
        if not state:
            raise ValueError(f"Instanz '{instance_id}' nicht registriert.")

        pre_state = state.to_dict()
        pre_status = state.status
        if state.status in ("QUARANTINED", "RETIRED"):
            state.shadow_trades_count += 1
            if pnl_usd > 0:
                state.shadow_wins += 1
        else:
            if pnl_usd < 0:
                state.current_budget_usd += pnl_usd
                state.consecutive_losses += 1
            else:
                state.consecutive_losses = 0
                needed = state.base_budget_usd - state.current_budget_usd
                if needed > 0:
                    state.current_budget_usd += min(pnl_usd, needed)

            if state.current_budget_usd <= 0.0:
                state.status = "QUARANTINED"
                state.budget_multiplier = 0.0
                state.current_budget_usd = 0.0
            elif state.current_budget_usd <= (state.base_budget_usd * self.config.throttle_budget_pct):
                state.status = "THROTTLED"
                state.budget_multiplier = 0.5
            elif state.status == "THROTTLED":
                prom = (self.config.v164_promotion_pct
                        if self.config.use_v164_promotion
                        else self.config.activate_budget_pct)
                if state.current_budget_usd > (state.base_budget_usd * prom):
                    state.status = "ACTIVE"
                    state.budget_multiplier = 1.0

        self._sync_vault_sweep(instance_id, pre_state, state.to_dict(), pnl_usd)
        self._persist_budget(state.to_dict())
        if state.status == "QUARANTINED" and pre_status != "QUARANTINED":
            await self._publish_wake(instance_id, reason="BUDGET_ZERO_QUARANTINE")
        return state.to_dict()

    # ------------------------------------------------------------------ vault
    def _sync_vault_sweep(self, instance_id: str, pre: Dict[str, Any],
                          post: Dict[str, Any], pnl_usd: float) -> None:
        """v1.2.0: Jeder Net Win -> 100% USD-Vault-Sweep, Budget reset auf Base."""
        if pnl_usd <= 0 or not self.config.vault_sweep_enabled:
            return
        if pre is None:
            return
        pre_budget = float(pre.get("current_budget_usd") or 0.0)
        base = float(post.get("base_budget_usd") or 0.0)
        post_budget = float(post.get("current_budget_usd") or 0.0)
        if pre.get("status") in ("QUARANTINED", "RETIRED"):
            return
        # Überschuss oberhalb Base, der nicht im Budget verblieben ist
        sweep = max(0.0, (pre_budget + pnl_usd) - max(base, post_budget))
        if sweep <= 1e-9:
            return
        try:
            from app.execution.VaultEngine import VaultEngine

            store = self._store()
            engine = VaultEngine(store)
            engine.credit_sweep(strategy_id=instance_id, amount_usd=sweep,
                                trade_id=None, reason="PROFIT_SWEEP_V120")
            get_event_bus().log(
                "info",
                f"💰 Vault Sweep: {sweep:.2f} USD von {instance_id} (v1.2.0 100%-Profit-Sweep)",
                category="VAULT", strategy_id=instance_id,
                payload={"amount_usd": round(sweep, 4)},
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Vault sweep failed for %s: %s", instance_id, exc)

    def _persist_budget(self, state: Dict[str, Any]) -> None:
        """Write-Through: DuckDB strategy_budgets + Redis m8:state-Hash (Konsistenz für SCAN)."""
        try:
            self._store().sync_budget(state)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Budget write-through failed: %s", exc)
        if not self.redis:
            return
        import asyncio

        payload = {
            k: state[k] for k in (
                "strategy_id", "status", "base_budget_usd", "current_budget_usd",
                "budget_multiplier", "consecutive_losses", "consecutive_low_pf_days",
                "shadow_trades_count", "shadow_wins",
            ) if k in state
        }
        if state.get("last_ga_recalibration_ts"):
            payload["last_ga_recalibration_ts"] = _ga_ts_to_iso(state["last_ga_recalibration_ts"])
        if state.get("last_eod_date"):
            payload["last_eod_date"] = state["last_eod_date"]
        iid = state.get("strategy_id") or state.get("instance_id")
        coro = self.redis.hset(KEY_STATE.format(iid), mapping={k: str(v) for k, v in payload.items()})
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            try:
                asyncio.run(coro)
            except Exception as exc:  # pragma: no cover
                logger.warning("redis write-back failed: %s", exc)
            return
        loop.create_task(coro)

    async def _publish_wake(self, instance_id: str, reason: str) -> None:
        bus = get_event_bus()
        bus.publish_sync(TOPIC_WAKE, {
            "instance_id": instance_id,
            "reason": reason,
            "natural_key": f"wake:{instance_id}:{reason}:{int(time.time() // 60)}",
        })
        bus.log("warn", f"🚨 QUARANTINE WAKE: {instance_id} ({reason})",
                category="CIRCUIT_BREAKER", strategy_id=instance_id,
                payload={"reason": reason})

    # -------------------------------------------------------------------- EOD
    async def update_eod_profit_factor(self, instance_id: str,
                                       daily_pf: Optional[float],
                                       daily_trades_count: int,
                                       day_label: Optional[str] = None) -> StrategyState:
        """EOD-Gate: Tage ohne Trades erhöhen den low-PF-Zähler NICHT.
        3x PF<1 -> THROTTLED, 7x PF<1 -> QUARANTINED (frozen v1.2.0)."""
        day_label = day_label or time.strftime("%Y-%m-%d", time.gmtime())

        if self.redis and self.lua_eod_sha:
            pre = await self._redis_state(instance_id)
            if pre is None:
                raise ValueError(f"Instance state '{instance_id}' not found in Redis.")
            result = await self.redis.evalsha(
                self.lua_eod_sha, 1, self._hash_key(instance_id),
                instance_id,
                "1" if daily_trades_count and daily_trades_count > 0 else "0",
                str(daily_pf if daily_pf is not None else -1.0),
                day_label,
            )
            state = StrategyState.from_hash(_hashify(result))
            self.states[instance_id] = state
            self._persist_budget(state.to_dict())
            if state.status == "QUARANTINED" and pre.get("status") != "QUARANTINED":
                await self._publish_wake(instance_id, reason="EOD_7D_PF_QUARANTINE")
            return state

        state = self.states.get(instance_id)
        if not state:
            raise ValueError(f"Instanz '{instance_id}' nicht registriert.")

        pre_status = state.status
        if not daily_trades_count or daily_trades_count <= 0:
            state.last_eod_date = day_label
            return state
        if state.status in ("QUARANTINED", "RETIRED"):
            state.last_eod_date = day_label
            return state
        if daily_pf is not None and daily_pf < 1.0:
            state.consecutive_low_pf_days += 1
        else:
            state.consecutive_low_pf_days = 0
        state.last_eod_date = day_label

        if state.consecutive_low_pf_days >= self.config.low_pf_quarantine_days:
            state.status = "QUARANTINED"
            state.budget_multiplier = 0.0
        elif (state.consecutive_low_pf_days >= self.config.low_pf_throttle_days
              and state.status == "ACTIVE"):
            state.status = "THROTTLED"
            state.budget_multiplier = 0.5
        elif state.status == "THROTTLED" and state.consecutive_low_pf_days == 0:
            state.status = "ACTIVE"
            state.budget_multiplier = 1.0

        self._persist_budget(state.to_dict())
        if state.status == "QUARANTINED" and pre_status != "QUARANTINED":
            await self._publish_wake(instance_id, reason="EOD_7D_PF_QUARANTINE")
        return state

    # ----------------------------------------------------------------- RETIRED
    def check_retirement(self, instance_id: str,
                         now: Optional[float] = None) -> Optional[StrategyState]:
        """RETIRED-Pfad (v1.2.0 'still missing' → umgesetzt):
        4 Wochen Shadow (PAPER) ohne GA-Rekalibrierung -> RETIRED (terminal)."""
        state = self.states.get(instance_id)
        if not state or state.status == "RETIRED":
            return state
        if state.status in ("QUARANTINED",):
            return state
        now = now or time.time()
        last_ga = state.last_ga_recalibration_ts or 0.0
        weeks_shadow = (now - last_ga) / (7 * 24 * 3600)
        if weeks_shadow >= self.config.retired_shadow_weeks:
            state.status = "RETIRED"
            state.budget_multiplier = 0.0
            self._persist_budget(state.to_dict())
            get_event_bus().log(
                "warn",
                f"️ RETIRED: {instance_id} — {weeks_shadow:.1f} Wochen Shadow ohne GA-Rekalibrierung",
                category="CIRCUIT_BREAKER", strategy_id=instance_id,
                payload={"weeks_shadow": round(weeks_shadow, 2)},
            )
        return state

    # ------------------------------------------------------------- admin ops
    async def promote(self, instance_id: str, force: bool = False) -> Dict[str, Any]:
        """Explizite Re-Promotion QUARANTINED/THROTTLED -> ACTIVE (Blueprint: 'Explicit re-promotion only')."""
        state = self.states.get(instance_id)
        if not state:
            raise ValueError(f"Instanz '{instance_id}' nicht registriert.")
        if state.status == "RETIRED":
            raise ValueError("RETIRED ist terminal — nur Neuregistrierung möglich.")
        if state.status == "ACTIVE" and not force:
            return state.to_dict()
        state.status = "ACTIVE"
        state.budget_multiplier = 1.0
        state.consecutive_low_pf_days = 0
        state.consecutive_losses = 0
        if state.current_budget_usd <= 0.0:
            state.current_budget_usd = state.base_budget_usd
        self._persist_budget(state.to_dict())
        get_event_bus().log("info", f"⬆️ PROMOTED -> ACTIVE: {instance_id}",
                            category="SYSTEM", strategy_id=instance_id)
        return state.to_dict()

    async def quarantine(self, instance_id: str, reason: str = "manual") -> Dict[str, Any]:
        state = self.states.get(instance_id)
        if not state:
            raise ValueError(f"Instanz '{instance_id}' nicht registriert.")
        state.status = "QUARANTINED"
        state.budget_multiplier = 0.0
        self._persist_budget(state.to_dict())
        await self._publish_wake(instance_id, reason=reason.upper())
        return state.to_dict()

    async def retire(self, instance_id: str) -> Dict[str, Any]:
        state = self.states.get(instance_id)
        if not state:
            raise ValueError(f"Instanz '{instance_id}' nicht registriert.")
        state.status = "RETIRED"
        state.budget_multiplier = 0.0
        self._persist_budget(state.to_dict())
        get_event_bus().log("warn", f"⚰️ MANUAL RETIRE: {instance_id}",
                            category="CIRCUIT_BREAKER", strategy_id=instance_id)
        return state.to_dict()

    def mark_ga_recalibration(self, instance_id: str, now: Optional[float] = None) -> None:
        state = self.states.get(instance_id)
        if state:
            state.last_ga_recalibration_ts = now or time.time()
            self._persist_budget(state.to_dict())

    # ------------------------------------------------------------------ halts
    async def halt_symbol(self, symbol: str, ttl_seconds: int = 300) -> None:
        """halt:symbol:{symbol} — String, TTL 300s (Blueprint §4)."""
        if self.redis:
            await self.redis.set(KEY_HALT_SYMBOL.format(symbol), "HALTED", ex=ttl_seconds)
        else:
            self.states.setdefault(symbol, StrategyState(strategy_id=symbol, status="QUARANTINED"))
            self._local_halts.setdefault(symbol, time.time() + ttl_seconds)
        get_event_bus().log("warn", f"⛔ SYMHALT {symbol} für {ttl_seconds}s",
                            category="CIRCUIT_BREAKER", payload={"symbol": symbol})

    _local_halts: Dict[str, float] = {}

    async def is_symbol_halted(self, symbol: str) -> bool:
        if self.redis:
            return bool(await self.redis.get(KEY_HALT_SYMBOL.format(symbol)))
        until = self._local_halts.get(symbol)
        if until and until > time.time():
            return True
        return False

    # ------------------------------------------------------------------ listing
    async def scan_states(self) -> Dict[str, Dict[str, Any]]:
        """Redis SCAN über m8:state:* (Phase 2 Acceptance)."""
        out: Dict[str, Dict[str, Any]] = {}
        if self.redis:
            async for key in self.redis.scan_iter(match="m8:state:*", count=100):
                raw = await self.redis.hgetall(key)
                if raw:
                    iid = key.split(":", 2)[2]
                    out[iid] = StrategyState.from_hash(raw).to_dict()
        for iid, state in self.states.items():
            out.setdefault(iid, state.to_dict())
        return out
