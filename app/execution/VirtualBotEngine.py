"""
=========================================================
Datei:      app/execution/VirtualBotEngine.py
Zweck:      §20 — Pionex-Prinzip auf Kraken: isoliertes Budget je Strategie,
            Sizing NUR auf bot.current_equity, Max-Loss -> QUARANTINED +
            Alert disable, Profit Sweep in den Vault.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Rouge (Allokation) / Jaune (Implementierung)
=========================================================
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from app.core import blueprint as bp

logger = logging.getLogger("app.execution.virtual_bot")


@dataclass
class VirtualBot:
    """Eine Bot-Karte im VirtualBotDeck (§8 UI)."""

    bot_id: str
    strategy_id: str
    symbol: str
    timeframe: str = "15"
    budget_eur: float = 100.0
    current_equity: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    max_loss_eur: float = 0.0            # default: 50 % des Budgets
    swept_to_vault: float = 0.0
    status: str = "PAUSED"               # RUNNING | PAUSED | QUARANTINED
    m8_state: str = bp.M8State.ACTIVE.value
    style: str = "STYLE_INTRADAY_MOMENT"
    xp: int = 0
    strikes: int = 0
    open_positions: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if self.current_equity <= 0:
            self.current_equity = self.budget_eur
        if self.max_loss_eur <= 0:
            self.max_loss_eur = round(self.budget_eur * 0.5, 2)

    @property
    def drawdown_eur(self) -> float:
        return max(0.0, self.budget_eur - self.current_equity)

    @property
    def pnl_eur(self) -> float:
        return self.current_equity + self.swept_to_vault - self.budget_eur

    def to_card(self) -> Dict[str, Any]:
        """Pflichtfelder der StrategyCard laut Blueprint §0.0."""
        return {
            "bot_id": self.bot_id,
            "strategy_id": self.strategy_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "runner_status": self.status,
            "capital_eur": round(self.budget_eur, 2),
            "equity_eur": round(self.current_equity, 2),
            "bot_pnl": round(self.pnl_eur, 2),
            "max_loss": round(self.max_loss_eur, 2),
            "xp_strikes": {"xp": self.xp, "strikes": self.strikes},
            "m8_state": self.m8_state,
            "style": self.style,
            "budget_multiplier": bp.alert_policy_for_state(self.m8_state).budget_multiplier,
            "swept_to_vault": round(self.swept_to_vault, 2),
        }


class VirtualBotEngine:
    """Budget-Ringfencing. Ein Bot kann nie mehr verlieren als sein Max-Loss."""

    def __init__(self, vault_engine=None, alert_provisioner=None, state_engine=None):
        self.vault = vault_engine
        self.alerts = alert_provisioner
        self.state_engine = state_engine
        self._bots: Dict[str, VirtualBot] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------- lifecycle
    def create_bot(self, strategy_id: str, symbol: str, budget_eur: float,
                   *, timeframe: str = "15", max_loss_eur: float = 0.0,
                   style: str = "STYLE_INTRADAY_MOMENT", bot_id: str = "") -> VirtualBot:
        with self._lock:
            bid = bot_id or f"bot_{strategy_id}_{symbol.replace('/', '')}".lower()
            bot = VirtualBot(bot_id=bid, strategy_id=strategy_id, symbol=symbol,
                             timeframe=timeframe, budget_eur=float(budget_eur),
                             max_loss_eur=float(max_loss_eur), style=style)
            self._bots[bid] = bot
            logger.info("VirtualBot %s created (budget %.2f EUR, max loss %.2f)",
                        bid, bot.budget_eur, bot.max_loss_eur)
            return bot

    def get(self, bot_id: str) -> Optional[VirtualBot]:
        return self._bots.get(bot_id)

    def for_strategy(self, strategy_id: str) -> List[VirtualBot]:
        return [b for b in self._bots.values() if b.strategy_id == strategy_id]

    def list_cards(self) -> List[Dict[str, Any]]:
        return [b.to_card() for b in self._bots.values()]

    def start(self, bot_id: str) -> Dict[str, Any]:
        bot = self._require(bot_id)
        if bot.status == "QUARANTINED":
            return {"ok": False, "reason": "bot quarantined", "bot": bot.to_card()}
        bot.status = "RUNNING"
        bot.updated_at = time.time()
        self._sync_alert(bot, bp.AlertAction.ENABLE)
        return {"ok": True, "bot": bot.to_card()}

    def pause(self, bot_id: str) -> Dict[str, Any]:
        bot = self._require(bot_id)
        bot.status = "PAUSED"
        bot.updated_at = time.time()
        self._sync_alert(bot, bp.AlertAction.DISABLE)   # §4.6 UI-Stop -> Alert aus
        return {"ok": True, "bot": bot.to_card()}

    # ---------------------------------------------------------------- sizing
    def size_order(self, bot_id: str, price: float, win_prob: float,
                   rrr: float = bp.KELLY_DEFAULT_RRR) -> Dict[str, Any]:
        """Half-Kelly auf **Bot-Equity** (niemals Gesamtkonto) × M8-Multiplier."""
        bot = self._require(bot_id)
        if bot.status != "RUNNING":
            return {"quantity": 0.0, "reason": f"bot {bot.status}", "allowed": False}
        policy = bp.alert_policy_for_state(bot.m8_state)
        if not policy.accept_webhook:
            return {"quantity": 0.0, "reason": f"m8 {bot.m8_state}", "allowed": False}

        base_qty = bp.calculate_kelly(bot.current_equity, price, win_prob, rrr)
        qty = base_qty * policy.budget_multiplier
        notional = qty * price
        # Ring-Fence: Notional darf die verbleibende Equity nie übersteigen
        if notional > bot.current_equity:
            qty = bot.current_equity / price
            notional = bot.current_equity
        return {
            "quantity": round(qty, 8),
            "notional_eur": round(notional, 2),
            "equity_basis": round(bot.current_equity, 2),
            "budget_multiplier": policy.budget_multiplier,
            "allowed": qty > 0,
            "reason": "ok" if qty > 0 else "zero size",
        }

    # ------------------------------------------------------------ pnl / loss
    def apply_trade_result(self, bot_id: str, pnl_eur: float,
                           *, fees_eur: float = 0.0) -> Dict[str, Any]:
        """Realisiertes Ergebnis buchen; Max-Loss und Profit-Sweep prüfen."""
        bot = self._require(bot_id)
        net = float(pnl_eur) - abs(float(fees_eur))
        bot.realized_pnl += net
        bot.current_equity += net
        bot.updated_at = time.time()

        events: List[str] = []
        if net > 0:
            bot.xp += 1
        elif net < 0:
            bot.strikes += 1 if net < -0.02 * bot.budget_eur else 0

        # 1) Max-Loss -> Quarantäne + Alert aus (andere Bots unberührt)
        if bot.drawdown_eur >= bot.max_loss_eur or bot.current_equity <= 0:
            events.append(self._quarantine(bot, "max_loss_reached"))
        # 2) Profit Sweep in den Vault
        elif bot.current_equity > bot.budget_eur:
            profit = bot.current_equity - bot.budget_eur
            swept = self._sweep(bot, profit)
            if swept:
                events.append(f"vault_sweep:{swept:.2f}")
        # 3) 3 Strikes -> Quarantäne (Reward-Shaping-Kopplung, §21)
        if bot.strikes >= bp.STRIKES_TO_QUARANTINE and bot.status != "QUARANTINED":
            events.append(self._quarantine(bot, "three_strikes"))

        return {"bot": bot.to_card(), "net_eur": round(net, 2), "events": events}

    def apply_m8_state(self, bot_id: str, state: str) -> Dict[str, Any]:
        """M8 -> Alert-Kopplung nach der normativen Matrix (§4.6)."""
        bot = self._require(bot_id)
        bot.m8_state = bp.M8State(state).value
        policy = bp.alert_policy_for_state(bot.m8_state)
        if policy.alert is bp.AlertAction.DISABLE:
            bot.status = "QUARANTINED" if bot.m8_state == bp.M8State.QUARANTINED.value else "PAUSED"
            self._sync_alert(bot, bp.AlertAction.DISABLE)
        elif policy.alert is bp.AlertAction.ENABLE and bot.status == "RUNNING":
            self._sync_alert(bot, bp.AlertAction.ENABLE)
        # KEEP: THROTTLED lässt den Alert bewusst an
        bot.updated_at = time.time()
        return bot.to_card()

    # -------------------------------------------------------------- internals
    def _quarantine(self, bot: VirtualBot, reason: str) -> str:
        bot.status = "QUARANTINED"
        bot.m8_state = bp.M8State.QUARANTINED.value
        self._sync_alert(bot, bp.AlertAction.DISABLE)
        logger.warning("VirtualBot %s QUARANTINED (%s)", bot.bot_id, reason)
        return f"quarantined:{reason}"

    def _sweep(self, bot: VirtualBot, profit: float) -> float:
        if profit <= 0:
            return 0.0
        bot.current_equity -= profit
        bot.swept_to_vault += profit
        if self.vault is not None:
            try:
                self.vault.credit_sweep(bot.strategy_id, profit, reason="virtual_bot_profit")
            except Exception as exc:  # pragma: no cover
                logger.warning("vault sweep failed for %s: %s", bot.bot_id, exc)
        return profit

    def _sync_alert(self, bot: VirtualBot, action: bp.AlertAction) -> None:
        if self.alerts is None:
            return
        try:
            if action is bp.AlertAction.ENABLE:
                self.alerts.enable(bot.strategy_id)
            elif action is bp.AlertAction.DISABLE:
                self.alerts.disable(bot.strategy_id)
        except Exception as exc:  # pragma: no cover
            logger.warning("alert sync failed for %s: %s", bot.strategy_id, exc)

    def _require(self, bot_id: str) -> VirtualBot:
        bot = self._bots.get(bot_id)
        if bot is None:
            raise KeyError(f"unknown virtual bot {bot_id!r}")
        return bot

    def snapshot(self) -> Dict[str, Any]:
        return {
            "bots": [asdict(b) for b in self._bots.values()],
            "total_budget_eur": round(sum(b.budget_eur for b in self._bots.values()), 2),
            "total_equity_eur": round(sum(b.current_equity for b in self._bots.values()), 2),
            "total_swept_eur": round(sum(b.swept_to_vault for b in self._bots.values()), 2),
            "exchange": bp.VIRTUAL_BOT_EXCHANGE_PRIMARY,
            "regulatory_region": bp.REGULATORY_REGION,
        }


_engine: Optional[VirtualBotEngine] = None


def get_virtual_bot_engine(**kwargs) -> VirtualBotEngine:
    global _engine
    if _engine is None:
        _engine = VirtualBotEngine(**kwargs)
    return _engine
