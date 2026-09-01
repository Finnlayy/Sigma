"""
Wave-Screen an den Execution-Layer gekoppelt.

Schnitt: Venue = Source of Truth (Universe), Scraper = parallele OHLC,
Academy nur tradable Kollapse. Kein Fake aus Synthetic, kein
Untradable nach Scout/Academy, keine rohen Venue-Ticker.

Test-Contract (Review 2026-08-30):
  1. Kollabiertes SOL/USD bei heutiger Allowlist -> nicht im Screen.
  2. Dieselbe Serie mit Fake-Universe, das SOL erlaubt -> im Screen.
  3. Paralleler Hydrate: cached + via Scraper; synthetic wird verworfen.
  4. Scout plant keine Tasks fuer untradable Symbole.
  5. Academy-Watchlist leer bei leerem Screen; Defaults = Universe.
"""
from __future__ import annotations

import threading
from types import SimpleNamespace

from app.optimizer.AcademyRegistry import AcademyRegistry
from app.optimizer.StrategyAllocator import StrategyAllocator
from app.scout.ScoutDaemon import ScoutDaemon
from app.tv.symbol_map import to_kraken_pair, to_sigma_symbol
from sigma.execution.universe import (
    CompositeExecutionUniverse,
    KrakenExecutionUniverse,
    PionexExecutionUniverse,
    default_execution_universe,
    rank_watchlist,
    register_venue,
    reset_venues,
)
from sigma.loops.loop_c import LoopCPort
from sigma.loops.loop_d import LoopDPort
from sigma.orchestration import MasterOrchestrator
from sigma.signals.quantum_wave_collider import STATUS_COLLAPSED, QuantumWaveCollider

M15 = 900
H1 = 3600
NY_FRIDAY = 1_787_929_200.0  # 2026-08-28 15:00 UTC


def _bar(ts: float, o: float, h: float, l: float, c: float, v: float = 100.0) -> dict:
    return {"ts": ts, "o": o, "h": h, "l": l, "c": c, "v": v}


def _bars(n: int = 20, start: float = 1_700_000_000.0, step: int = M15,
          price: float = 100.0) -> list:
    out = []
    for i in range(n):
        p = price * (1.0 + 0.001 * i)
        out.append(_bar(start + i * step, p, p * 1.01, p * 0.99, p, 100.0 + i))
    return out


def _closed_now(bars: list, interval_sec: int) -> float:
    return float(bars[-1]["ts"]) + float(interval_sec)


def _expansion_then_fvg(start: float = 1_700_000_000.0, step: int = M15) -> list:
    """Uptrend structure + 3-bar bullish FVG; letztes Struktur-High 120."""
    rows = []
    prices = [100.0, 102.0, 104.0, 106.0, 108.0, 110.0, 112.0]
    for i, p in enumerate(prices):
        rows.append(_bar(start + i * step, p, p + 1.0, p - 1.0, p))
    i0 = len(rows)
    rows.append(_bar(start + i0 * step, 112.0, 113.0, 111.0, 112.5))
    rows.append(_bar(start + (i0 + 1) * step, 113.0, 118.0, 113.0, 117.0))
    rows.append(_bar(start + (i0 + 2) * step, 117.0, 120.0, 116.0, 119.0))
    return rows


def _collapsed_series(step: int = M15) -> list:
    """COLLAPSED_INTO_ZONE: Close unter CE50, Wick in den FVG."""
    rows = _expansion_then_fvg(step=step)
    ts = rows[-1]["ts"] + step
    rows.append(_bar(ts, 115.0, 115.0, 108.0, 109.0))
    return rows


class FakePionexUniverse:
    """Simuliert „Pionex haengt dran" — live Adapter, der SOL nimmt."""

    live_registered = True

    def list_symbols(self):
        return ["SOL/USD"]

    def is_tradable(self, symbol: str) -> bool:
        return symbol == "SOL/USD"


# ---------------------------------------------------------------------------
# 1 + 2 — Universe-Gate im Screen
# ---------------------------------------------------------------------------

def test_collapsed_sol_not_in_screen_with_today_kraken_allowlist():
    series = {"SOL/USD": _collapsed_series()}
    now = _closed_now(_collapsed_series(), M15)
    screen = QuantumWaveCollider().screen(
        series, universe=KrakenExecutionUniverse(), interval_min=15, now=now,
    )
    # Kollabiert, aber nicht tradable (SOL nicht in EXCHANGE_SPOT):
    assert screen.states["SOL/USD"].status == STATUS_COLLAPSED
    assert screen.candidates == ()
    # Fallback ist das Universe — nie market_symbols (kein SOL/XRP):
    assert screen.defaults == ("BTC/USD", "ETH/USD")
    assert "SOL/USD" not in screen.defaults


def test_collapsed_sol_in_screen_with_fake_universe_allowing_sol():
    series = {"SOL/USD": _collapsed_series()}
    now = _closed_now(_collapsed_series(), M15)
    screen = QuantumWaveCollider().screen(
        series, universe=FakePionexUniverse(), interval_min=15, now=now,
    )
    cands = [c.symbol for c in screen.candidates]
    assert cands == ["SOL/USD"]
    assert screen.candidates[0].tradable is True


def test_composite_universe_union_and_live_gating():
    comp = CompositeExecutionUniverse([
        KrakenExecutionUniverse(),
        FakePionexUniverse(),
        PionexExecutionUniverse(),   # Stub: live_registered=False -> fliegt raus
    ])
    assert comp.list_symbols() == ["BTC/USD", "ETH/USD", "SOL/USD"]
    assert comp.is_tradable("SOL/USD") is True
    assert comp.is_tradable("XRP/USD") is False


def test_default_universe_is_kraken_until_live_venue_registered():
    reset_venues()
    try:
        uni = default_execution_universe()
        assert uni.list_symbols() == ["BTC/USD", "ETH/USD"]
        assert uni.is_tradable("SOL/USD") is False
        # Nicht-live Stub registrieren -> bleibt Kraken.
        register_venue(PionexExecutionUniverse())
        assert default_execution_universe().list_symbols() == ["BTC/USD", "ETH/USD"]
        # Live Fake registrieren -> Composite mit Union.
        register_venue(FakePionexUniverse())
        comp = default_execution_universe()
        assert comp.is_tradable("SOL/USD") is True
        assert comp.list_symbols() == ["BTC/USD", "ETH/USD", "SOL/USD"]
    finally:
        reset_venues()


def test_to_sigma_symbol_never_leaks_raw_venue_tickers():
    assert to_sigma_symbol("XBTUSD") == "BTC/USD"
    assert to_sigma_symbol("PI_XBTUSD") == "PI_BTC/USD"
    assert to_sigma_symbol("PI_ETHUSD") == "PI_ETH/USD"
    # Round-Trip: kanonische Form -> Kraken-Pair bleibt Loop-A-kompatibel.
    assert to_kraken_pair(to_sigma_symbol("XBTUSD")) == "XBTUSD"
    assert to_kraken_pair(to_sigma_symbol("PI_XBTUSD")) == "XBTUSD"


# ---------------------------------------------------------------------------
# 3 — Parallel-Hydrate (Fail-Closed wie Loop C)
# ---------------------------------------------------------------------------

class _TrackingScraper:
    def __init__(self, payloads):
        self.payloads = dict(payloads)
        self.calls: list = []
        self.max_active = 0
        self._active = 0
        self._lock = threading.Lock()
        self._barrier = threading.Barrier(len(payloads))

    def health(self):
        return {"ok": True, "degraded": False}

    def fetch_ohlc_with_meta(self, symbol, interval, count):
        with self._lock:
            self._active += 1
            self.max_active = max(self.max_active, self._active)
            self.calls.append((symbol, interval))
        try:
            self._barrier.wait(timeout=5)   # erzwingt echte Parallelitaet
            candles, meta = self.payloads[symbol]
            return list(candles), dict(meta)
        finally:
            with self._lock:
                self._active -= 1


def _candles(n: int = 6, start: float = 1_700_000_000.0, step: int = H1) -> list:
    return _bars(n, start=start, step=step)


def test_hydrate_htf_parallel_fills_gaps_and_drops_synthetic():
    scraper = _TrackingScraper({
        "ETH/USD": (_candles(6), {"source": "tv_scraper", "degraded": False}),
        "XRP/USD": (_candles(6), {"source": "synthetic", "degraded": False}),
    })
    port = LoopCPort(scraper=scraper, store=None, symbols=["BTC/USD"])
    out = port.hydrate_htf(["ETH/USD", "XRP/USD"], interval_min=60, workers=2)
    assert "ETH/USD" in out          # echter Scraper-Pfad
    assert "XRP/USD" not in out      # synthetic -> kein Fake im Screen
    assert sorted(s for s, _ in scraper.calls) == ["ETH/USD", "XRP/USD"]
    assert scraper.max_active == 2   # parallel, nicht seriell
    assert len(out["ETH/USD"]) == 6


def test_hydrate_htf_fail_closed_when_sidecar_down():
    class _Down:
        def health(self):
            return {"ok": False, "degraded": True}

        def fetch_ohlc_with_meta(self, *args, **kwargs):
            raise AssertionError("hydrate must not fetch when sidecar is down")

    port = LoopCPort(scraper=_Down(), store=None)
    assert port.hydrate_htf(["BTC/USD", "ETH/USD"]) == {}


# ---------------------------------------------------------------------------
# 4 — Scout plant keine Tasks fuer untradable Symbole
# ---------------------------------------------------------------------------

def test_scout_defaults_are_universe_not_feed_list():
    reset_venues()
    try:
        scout = ScoutDaemon(timeframes=[15])
        assert scout.symbols == ["BTC/USD", "ETH/USD"]
        assert "XRP/USD" not in scout.symbols and "SOL/USD" not in scout.symbols
    finally:
        reset_venues()


def test_scout_plan_symbols_per_tick_without_mutating_singleton():
    scout = ScoutDaemon(symbols=["BTC/USD"], timeframes=[15])
    tasks = scout.plan(["s1"], symbols=["ETH/USD"])
    keys = [t.key for t in tasks]
    assert ("s1", "ETH/USD", "15") in keys
    assert ("s1", "BTC/USD", "15") not in keys
    assert scout.symbols == ["BTC/USD"]     # Singleton nicht mutiert


def test_loop_d_never_plans_untradable_symbols():
    alloc = StrategyAllocator()
    scout = ScoutDaemon(
        allocator=alloc, backtest_runner=lambda *a: {"trades": []},
        symbols=["BTC/USD", "SOL/USD"], timeframes=[15],
    )
    port = LoopDPort(daemon=scout)
    port.tick(
        regime="RANGING_CHOP",
        strategy_ids=["s1"],
        symbols=["BTC/USD", "SOL/USD"],
        universe=KrakenExecutionUniverse(),   # SOL nicht tradable
    )
    keys = set(scout.tasks.keys())
    assert ("s1", "BTC/USD", "15") in keys
    assert ("s1", "SOL/USD", "15") not in keys


# ---------------------------------------------------------------------------
# 5 — Academy-Watchlist: nur tradable Kandidaten, sonst Universe-Defaults
# ---------------------------------------------------------------------------

class _StubStore:
    def __init__(self):
        self.upserts = []

    def get_strategy(self, sid):
        return {"id": sid, "name": sid, "parameters": {"hardStopPercent": 1.0}}

    def upsert_academy_entry(self, entry):
        self.upserts.append(entry)

    def academy_entries(self):
        return []

    def genomes(self, limit=500):
        return []

    def trades(self, **kwargs):
        return []

    def _one(self, *args):
        return None


def test_academy_watchlist_empty_screen_falls_back_to_universe_defaults():
    acad = AcademyRegistry(_StubStore())
    # Leerer Screen -> Universe-Defaults (BTC/ETH), nie market_symbols.
    watch = acad.ingest_wave_screen([], defaults=["BTC/USD", "ETH/USD"])
    assert watch == ["BTC/USD", "ETH/USD"]
    assert "SOL/USD" not in watch and "XRP/USD" not in watch
    # Danach mit Kandidaten -> Watchlist nur tradable Kollapse.
    screen = QuantumWaveCollider().screen(
        {"SOL/USD": _collapsed_series()},
        universe=FakePionexUniverse(),
        interval_min=15,
        now=_closed_now(_collapsed_series(), M15),
    )
    watch2 = acad.ingest_wave_screen(screen.candidates, defaults=list(screen.defaults))
    assert watch2 == ["SOL/USD"]
    assert acad.watchlist() == ["SOL/USD"]


def test_academy_drills_run_on_exactly_the_watchlist():
    acad = AcademyRegistry(_StubStore())
    acad.ingest_wave_screen([], defaults=["BTC/USD"])
    results = acad.drill_watchlist(["htf_trend_ltf_reversion"])
    assert len(results) == 1
    assert results[0]["symbol"] == "BTC/USD"
    assert results[0]["strategyId"] == "htf_trend_ltf_reversion"
    assert acad.store.upserts  # run_drills hat die Registry aktualisiert


# ---------------------------------------------------------------------------
# Orchestrator: Screen -> Loop D + Academy (tradable Kollapse)
# ---------------------------------------------------------------------------

class _CaptureLoopD:
    def __init__(self):
        self.calls: list = []

    def tick(self, **kwargs):
        self.calls.append(kwargs)


class _CaptureAcademy:
    def __init__(self):
        self.watch = None
        self.drilled: list = []

    def ingest_wave_screen(self, candidates, defaults=None):
        self.watch = [c.symbol for c in candidates] or list(defaults or [])
        return list(self.watch)

    def drill_watchlist(self, strategy_ids):
        self.drilled.append(strategy_ids)
        return []


def _snapshot(btc_htf, sol_htf=None):
    ltf = _bars(20, start=btc_htf[0]["ts"], step=M15, price=110.0)
    htf = {"BTC/USD": btc_htf}
    if sol_htf is not None:
        htf["SOL/USD"] = sol_htf
    return SimpleNamespace(series={"BTC/USD": ltf}, htf_series=htf, degraded=False)


def test_orchestrator_feeds_tradable_collapse_to_loop_d_and_academy():
    btc_htf = _bars(20, start=1_700_000_000.0, step=H1)          # IDLE
    sol_htf = _collapsed_series(step=H1)                          # COLLAPSED
    now = max(NY_FRIDAY, _closed_now(btc_htf, H1), _closed_now(sol_htf, H1))
    loop_d, acad = _CaptureLoopD(), _CaptureAcademy()
    universe = CompositeExecutionUniverse([KrakenExecutionUniverse(), FakePionexUniverse()])
    orch = MasterOrchestrator(
        ports={"polymarket": None, "loop_d": loop_d, "academy": acad},
        universe=universe, hydrate_cooldown_s=0,
    )
    out = orch.tick(_snapshot(btc_htf, sol_htf), now=now)
    assert out["status"] == "tick"
    cands = [c["symbol"] for c in out["screen"]["candidates"]]
    assert cands == ["SOL/USD"]                     # BTC idle, SOL tradable Kollaps
    assert loop_d.calls and loop_d.calls[-1]["symbols"] == ["SOL/USD"]
    assert loop_d.calls[-1]["universe"] is universe
    assert acad.watch == ["SOL/USD"]


def test_orchestrator_empty_screen_falls_back_to_universe_defaults():
    btc_htf = _bars(20, start=1_700_000_000.0, step=H1)          # IDLE
    now = max(NY_FRIDAY, _closed_now(btc_htf, H1))
    loop_d, acad = _CaptureLoopD(), _CaptureAcademy()
    orch = MasterOrchestrator(
        ports={"polymarket": None, "loop_d": loop_d, "academy": acad},
        universe=KrakenExecutionUniverse(), hydrate_cooldown_s=0,
    )
    out = orch.tick(_snapshot(btc_htf), now=now)
    assert out["screen"]["candidates"] == []
    assert loop_d.calls and loop_d.calls[-1]["symbols"] == ["BTC/USD", "ETH/USD"]
    assert acad.watch == ["BTC/USD", "ETH/USD"]     # Universe, nicht Feed-Liste
    assert "SOL/USD" not in acad.watch


def test_orchestrator_screen_empty_when_series_degraded():
    snap = SimpleNamespace(series={}, htf_series={}, degraded=True)
    loop_d, acad = _CaptureLoopD(), _CaptureAcademy()
    orch = MasterOrchestrator(
        ports={"polymarket": None, "loop_d": loop_d, "academy": acad},
        universe=KrakenExecutionUniverse(), hydrate_cooldown_s=0,
    )
    out = orch.tick(snap, now=NY_FRIDAY)
    assert out["screen"]["candidates"] == []
    # Sidecar down -> leerer Screen, Scout faellt auf Universe-Defaults.
    assert loop_d.calls and loop_d.calls[-1]["symbols"] == ["BTC/USD", "ETH/USD"]
    assert out["screen"]["states"] == {}


# ---------------------------------------------------------------------------
# TV-movers: nur Sortierung, Universe bleibt zu
# ---------------------------------------------------------------------------

def test_rank_watchlist_promotes_tradable_movers_and_drops_sol():
    wanted = ["BTC/USD", "ETH/USD"]
    rows = [
        {"name": "SOLUSD"},          # Gainer, aber nicht tradable
        {"ticker": "ETHUSD"},
        {"symbol": "XBTUSD"},
    ]
    ranked = rank_watchlist(wanted, rows)
    assert ranked == ["ETH/USD", "BTC/USD"]
    assert "SOL/USD" not in ranked
    assert rank_watchlist(wanted, []) == wanted
    assert rank_watchlist(wanted, None) == wanted


def test_academy_list_exposes_wave_watch():
    acad = AcademyRegistry(_StubStore())
    acad.ingest_wave_screen([], defaults=["ETH/USD", "BTC/USD"])
    acad.store.upsert_academy_entry({
        "id": "s1", "name": "s1", "symbol": "BTC/USD", "interval_min": 15,
        "archetype": "sma_cross", "graduation_level": "CADET",
        "wfo_return": 0.0, "wfo_sharpe": 0.0, "dsr": 0.0,
        "drills_passed": 0, "drills_total": 5,
    })

    class _Store(_StubStore):
        def academy_entries(self):
            return [{"id": "s1", "name": "s1"}]

    acad.store = _Store()
    rows = acad.list()
    assert rows[0]["waveWatch"] == ["ETH/USD", "BTC/USD"]


class _MoversScraper:
    def __init__(self, rows, meta=None):
        self._rows = rows
        self.last_meta = meta or {"source": "tv_scraper", "degraded": False}

    def health(self):
        return {"ok": True, "degraded": False}

    def movers(self, market="crypto", category="gainers", limit=25):
        return list(self._rows)

    def fetch_ohlc_with_meta(self, *args, **kwargs):
        return [], {"source": "tv_scraper", "degraded": False}


def test_orchestrator_movers_reorder_defaults_without_adding_sol():
    btc_htf = _bars(20, start=1_700_000_000.0, step=H1)
    now = max(NY_FRIDAY, _closed_now(btc_htf, H1))
    loop_d, acad = _CaptureLoopD(), _CaptureAcademy()
    scraper = _MoversScraper([
        {"name": "SOLUSD"},
        {"ticker": "ETHUSD"},
        {"symbol": "XBTUSD"},
    ])
    orch = MasterOrchestrator(
        ports={
            "polymarket": None,
            "loop_c": LoopCPort(scraper=scraper, store=None),
            "loop_d": loop_d,
            "academy": acad,
        },
        universe=KrakenExecutionUniverse(),
        hydrate_cooldown_s=0,
    )
    out = orch.tick(_snapshot(btc_htf), now=now)
    assert out["screen"]["defaults"] == ["ETH/USD", "BTC/USD"]
    assert "SOL/USD" not in out["screen"]["defaults"]
    assert loop_d.calls[-1]["symbols"] == ["ETH/USD", "BTC/USD"]
    assert acad.watch == ["ETH/USD", "BTC/USD"]


def test_orchestrator_ignores_synthetic_movers():
    btc_htf = _bars(20, start=1_700_000_000.0, step=H1)
    now = max(NY_FRIDAY, _closed_now(btc_htf, H1))
    loop_d, acad = _CaptureLoopD(), _CaptureAcademy()
    scraper = _MoversScraper(
        [{"ticker": "ETHUSD"}],
        meta={"source": "synthetic", "degraded": False},
    )
    orch = MasterOrchestrator(
        ports={
            "polymarket": None,
            "loop_c": LoopCPort(scraper=scraper, store=None),
            "loop_d": loop_d,
            "academy": acad,
        },
        universe=KrakenExecutionUniverse(),
        hydrate_cooldown_s=0,
    )
    out = orch.tick(_snapshot(btc_htf), now=now)
    assert out["screen"]["defaults"] == ["BTC/USD", "ETH/USD"]


class _CountingMoversScraper(_MoversScraper):
    """Movers-Abruf-Zähler: verifiziert den 300s-TTL-Cache im Orchestrator."""

    def __init__(self, rows, meta=None):
        super().__init__(rows, meta)
        self.calls = 0

    def movers(self, market="crypto", category="gainers", limit=25):
        self.calls += 1
        return super().movers(market, category, limit)


def test_orchestrator_caches_movers_across_ticks():
    """Zwei Ticks innerhalb des TTL-Fensters → nur EIN Sidecar-Abruf."""
    btc_htf = _bars(20, start=1_700_000_000.0, step=H1)
    now = max(NY_FRIDAY, _closed_now(btc_htf, H1))
    loop_d, acad = _CaptureLoopD(), _CaptureAcademy()
    scraper = _CountingMoversScraper([{"ticker": "ETHUSD"}])
    orch = MasterOrchestrator(
        ports={
            "polymarket": None,
            "loop_c": LoopCPort(scraper=scraper, store=None),
            "loop_d": loop_d,
            "academy": acad,
        },
        universe=KrakenExecutionUniverse(),
        hydrate_cooldown_s=0,
    )
    orch.tick(_snapshot(btc_htf), now=now)
    orch.tick(_snapshot(btc_htf), now=now)
    assert scraper.calls == 1  # Cache-Hit im zweiten Tick
    assert orch._movers_cache is not None
    assert orch._movers_cache[1] == [{"ticker": "ETHUSD"}]
