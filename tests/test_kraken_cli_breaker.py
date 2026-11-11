"""Kraken CLI circuit breaker: an unreachable venue must fail fast, not hang every call."""
from __future__ import annotations

import subprocess

import pytest

from app.execution import KrakenCliBridge as kcb
from app.execution.KrakenCliBridge import CLI_BREAKER, _subprocess_runner


@pytest.fixture(autouse=True)
def _clean_breaker():
    CLI_BREAKER.reset()
    yield
    CLI_BREAKER.reset()


class _Proc:
    def __init__(self, code: int):
        self.stdout, self.stderr, self.returncode = "{}", "", code


def _fake_run(outcomes, calls):
    def run(argv, **_kw):
        calls.append(list(argv))
        result = outcomes.pop(0)
        if result == "timeout":
            raise subprocess.TimeoutExpired(argv, 10)
        return _Proc(result)
    return run


FUT = ["kraken", "futures", "paper", "status", "-o", "json"]
SPOT = ["kraken", "paper", "status", "-o", "json"]


def test_two_timeouts_open_the_breaker_and_next_call_fails_fast(monkeypatch):
    calls = []
    monkeypatch.setattr(kcb.subprocess, "run", _fake_run(["timeout", "timeout"], calls))
    assert _subprocess_runner(FUT, 1.0)[2] == 124
    assert _subprocess_runner(FUT, 1.0)[2] == 124
    out, err, code = _subprocess_runner(FUT, 1.0)
    assert code == 124 and "unreachable" in err
    assert len(calls) == 2                     # third call never spawned the CLI


def test_open_futures_breaker_does_not_block_spot(monkeypatch):
    calls = []
    monkeypatch.setattr(kcb.subprocess, "run", _fake_run(["timeout", "timeout", 0], calls))
    _subprocess_runner(FUT, 1.0)
    _subprocess_runner(FUT, 1.0)
    assert _subprocess_runner(SPOT, 1.0)[2] == 0
    assert len(calls) == 3


def test_fast_errors_never_trip_the_breaker(monkeypatch):
    calls = []
    monkeypatch.setattr(kcb.subprocess, "run", _fake_run([1, 1, 1, 0], calls))
    for _ in range(3):
        assert _subprocess_runner(FUT, 1.0)[2] == 1
    assert _subprocess_runner(FUT, 1.0)[2] == 0
    assert len(calls) == 4


def test_success_resets_the_slow_failure_count(monkeypatch):
    calls = []
    monkeypatch.setattr(kcb.subprocess, "run", _fake_run(["timeout", 0, "timeout", 0], calls))
    for _ in range(4):
        _subprocess_runner(FUT, 1.0)
    assert len(calls) == 4                     # never two slow failures in a row


def test_probe_after_window_reopens_on_failure_and_closes_on_success(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(kcb.time, "monotonic", lambda: clock[0])
    calls = []
    monkeypatch.setattr(kcb.subprocess, "run", _fake_run(["timeout", "timeout", "timeout", 0, 0], calls))
    _subprocess_runner(FUT, 1.0)
    _subprocess_runner(FUT, 1.0)
    assert CLI_BREAKER.remaining("futures") == kcb.CLI_BREAKER_OPEN_S

    clock[0] += kcb.CLI_BREAKER_OPEN_S + 1
    assert _subprocess_runner(FUT, 1.0)[2] == 124        # probe fails -> open again at once
    assert CLI_BREAKER.remaining("futures") == kcb.CLI_BREAKER_OPEN_S

    clock[0] += kcb.CLI_BREAKER_OPEN_S + 1
    assert _subprocess_runner(FUT, 1.0)[2] == 0          # probe succeeds -> closed
    assert _subprocess_runner(FUT, 1.0)[2] == 0
    assert CLI_BREAKER.remaining("futures") == 0.0
    assert len(calls) == 5


def test_slow_nonzero_exit_counts_like_a_timeout():
    CLI_BREAKER.record("futures", 1, kcb.CLI_SLOW_FAILURE_S + 0.1)
    assert CLI_BREAKER.remaining("futures") == 0.0
    CLI_BREAKER.record("futures", 1, kcb.CLI_SLOW_FAILURE_S + 0.1)
    assert CLI_BREAKER.remaining("futures") > 0.0
