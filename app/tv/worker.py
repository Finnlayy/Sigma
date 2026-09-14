"""
=========================================================
Datei:      app/tv/worker.py
Zweck:      §1 / §5 — TV-Job-Queue (Concurrency 1) + Consumer.
            Datei-persistierte Jobs unter ./data/tv_jobs/{job_id}.json,
            CSV-Artefakte unter ./data/tv_exports/{job_id}/.
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Carrera-Engine) / TV-Automation
=========================================================

Start als Dienst:  `python -m app.tv.worker`
"""
from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional

from app.backtest.tv_csv import cache_key, result_csv_to_backtest_result
from app.core import blueprint as bp
from app.core.config import SigmaConfig, load_config
from app.tv.strategy_tester_driver import DriverError, get_driver

logger = logging.getLogger("app.tv.worker")

JOB_QUEUED = "queued"
JOB_RUNNING = "running"
JOB_DONE = "done"
JOB_FAILED = "failed"
JOB_CANCELLED = "cancelled"

JOB_KIND_BACKTEST = "backtest"
JOB_KIND_PULL_PARAMS = "pull_parameters"
JOB_KIND_PUSH_CODE = "push_code"
JOB_KIND_ALERT_SYNC = "alert_sync"


@dataclass
class TvJob:
    job_id: str
    kind: str
    strategy_id: str = ""
    symbol: str = "BTC/USD"
    interval: Any = 15
    params: Dict[str, Any] = field(default_factory=dict)
    window: Dict[str, Any] = field(default_factory=dict)
    code: str = ""
    status: str = JOB_QUEUED
    progress: float = 0.0
    error: str = ""
    error_code: str = ""
    result: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    started_at: float = 0.0
    finished_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["eta_s"] = self.eta_seconds()
        return d

    def eta_seconds(self) -> float:
        if self.status != JOB_RUNNING or self.progress <= 0:
            return 0.0
        elapsed = time.time() - (self.started_at or time.time())
        return round(max(0.0, elapsed / max(self.progress, 0.01) - elapsed), 1)


class TvJobQueue:
    """Serialisierte Job-Verarbeitung — §17.4: Concurrency bleibt 1."""

    def __init__(self, config: Optional[SigmaConfig] = None, driver_factory: Optional[Callable] = None):
        self.config = config or load_config()
        self.jobs_dir = self.config.tv_jobs_dir
        self.export_dir = self.config.tv_export_dir
        self._queue: "queue.Queue[str]" = queue.Queue()
        self._jobs: Dict[str, TvJob] = {}
        self._cache: Dict[str, Dict[str, Any]] = {}     # §17.4 Param-Cache (Pflicht)
        self._driver_factory = driver_factory or (lambda: get_driver(self.config))
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.RLock()
        os.makedirs(self.jobs_dir, exist_ok=True)
        self._restore()

    # ------------------------------------------------------------ persistence
    def _job_path(self, job_id: str) -> str:
        return os.path.join(self.jobs_dir, f"{job_id}.json")

    def _persist(self, job: TvJob) -> None:
        try:
            os.makedirs(self.jobs_dir, exist_ok=True)
            with open(self._job_path(job.job_id), "w", encoding="utf-8") as fh:
                json.dump(job.to_dict(), fh, indent=2)
        except OSError as exc:  # pragma: no cover
            logger.warning("job persist failed: %s", exc)

    def _restore(self) -> None:
        try:
            for name in os.listdir(self.jobs_dir):
                if not name.endswith(".json"):
                    continue
                with open(os.path.join(self.jobs_dir, name), encoding="utf-8") as fh:
                    raw = json.load(fh)
                job = TvJob(**{k: v for k, v in raw.items() if k in TvJob.__annotations__})
                if job.status in (JOB_QUEUED, JOB_RUNNING):
                    job.status = JOB_QUEUED          # nach Neustart erneut einreihen
                    self._queue.put(job.job_id)
                self._jobs[job.job_id] = job
        except FileNotFoundError:
            pass
        except Exception as exc:  # pragma: no cover
            logger.warning("job restore degraded: %s", exc)

    # -------------------------------------------------------------- submit
    def submit(self, kind: str, **payload: Any) -> TvJob:
        job = TvJob(job_id=f"tvj_{uuid.uuid4().hex[:12]}", kind=kind,
                    **{k: v for k, v in payload.items() if k in TvJob.__annotations__})
        with self._lock:
            self._jobs[job.job_id] = job
        self._persist(job)
        self._queue.put(job.job_id)
        logger.info("TV job %s queued (%s, %s)", job.job_id, kind, job.strategy_id or job.symbol)
        return job

    def get(self, job_id: str) -> Optional[TvJob]:
        return self._jobs.get(job_id)

    def list(self, strategy_id: str = "", limit: int = 50) -> List[Dict[str, Any]]:
        jobs = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
        if strategy_id:
            jobs = [j for j in jobs if j.strategy_id == strategy_id]
        return [j.to_dict() for j in jobs[:limit]]

    def cancel(self, job_id: str) -> Dict[str, Any]:
        job = self._jobs.get(job_id)
        if job is None:
            return {"ok": False, "reason": "unknown job"}
        if job.status != JOB_QUEUED:
            return {"ok": False, "reason": f"job is {job.status}", "job": job.to_dict()}
        job.status = JOB_CANCELLED
        job.finished_at = time.time()
        self._persist(job)
        return {"ok": True, "job": job.to_dict()}

    # ------------------------------------------------------------ execution
    def run_job(self, job: TvJob, driver=None) -> TvJob:
        """Synchron ausführbar — genau so nutzt es auch der Worker-Thread."""
        job.status = JOB_RUNNING
        job.started_at = time.time()
        job.progress = 0.05
        self._persist(job)
        own_driver = driver is None
        drv = driver or self._driver_factory()
        try:
            if job.kind == JOB_KIND_PULL_PARAMS:
                job.result = self._do_pull_params(drv, job)
            elif job.kind == JOB_KIND_BACKTEST:
                job.result = self._do_backtest(drv, job)
            elif job.kind == JOB_KIND_PUSH_CODE:
                job.result = self._do_push_code(drv, job)
            elif job.kind == JOB_KIND_ALERT_SYNC:
                job.result = {"synced": True, "strategy_id": job.strategy_id}
            else:
                raise DriverError(f"unknown job kind {job.kind!r}", "UNKNOWN_JOB_KIND")
            job.status = JOB_DONE
            job.progress = 1.0
        except DriverError as exc:
            job.status, job.error, job.error_code = JOB_FAILED, str(exc), exc.code
            logger.error("TV job %s failed: %s (%s)", job.job_id, exc, exc.code)
        except Exception as exc:
            job.status, job.error, job.error_code = JOB_FAILED, str(exc), "TV_JOB_ERROR"
            logger.exception("TV job %s crashed", job.job_id)
        finally:
            job.finished_at = time.time()
            self._persist(job)
            if own_driver:
                try:
                    drv.close()
                except Exception:  # pragma: no cover
                    pass
        return job

    # --------------------------------------------------------------- kinds
    def _do_pull_params(self, drv, job: TvJob) -> Dict[str, Any]:
        drv.open_chart(job.symbol, job.interval)
        job.progress = 0.5
        csv_text = drv.export_parameters()
        # §35: Original-Dateiname des TV-Exports beibehalten, nie umbenennen.
        original_name = getattr(drv, "last_export_filename", "") or \
            f"{job.strategy_id or job.symbol}_properties.csv"
        path = self._write_artifact(job, original_name, csv_text)
        from app.backtest.tv_csv import parse_parameter_csv

        payload: Dict[str, Any] = {
            "parameters_csv": path,
            "original_csv_filename": original_name,
            "parameters": parse_parameter_csv(csv_text),
        }
        if job.strategy_id:
            from app.optimizer.exact_csv_serializer import (CsvHeaderMismatch,
                                                            ingest_tv_export)
            strategy_dir = os.path.join(self.config.strategies_dir, job.strategy_id)
            try:
                handler, baseline = ingest_tv_export(strategy_dir, csv_text,
                                                     original_name)
                payload["baseline_csv"] = baseline
                payload["exact_csv_header"] = handler.exact_header_row
                payload["delimiter"] = handler.delimiter
            except (CsvHeaderMismatch, OSError) as exc:
                logger.warning("baseline freeze failed for %s: %s",
                               job.strategy_id, exc)
                payload["baseline_error"] = str(exc)
        return payload

    def _do_backtest(self, drv, job: TvJob) -> Dict[str, Any]:
        key = cache_key(job.strategy_id or job.symbol, job.params, job.symbol,
                        job.interval, job.window.get("from", ""), job.window.get("to", ""))
        if key in self._cache:
            logger.info("TV job %s served from param cache", job.job_id)
            return {**self._cache[key], "cached": True}
        drv.open_chart(job.symbol, job.interval)
        job.progress = 0.25
        if job.params:
            drv.apply_parameters(job.params)
        job.progress = 0.5
        csvs = drv.run_backtest(job.window)
        job.progress = 0.85
        trades_path = self._write_artifact(job, "trades.csv", csvs["trades_csv"])
        perf_path = self._write_artifact(job, "performance.csv", csvs.get("performance_csv", ""))
        result = result_csv_to_backtest_result(
            csvs["trades_csv"],
            performance_csv=csvs.get("performance_csv") or None,
            config={"strategyId": job.strategy_id, "assetPair": job.symbol,
                    "interval": job.interval},
        )
        payload = {"backtest": result, "trades_csv": trades_path,
                   "performance_csv": perf_path, "cache_key": key,
                   "source": csvs.get("source", "tradingview"), "cached": False}
        self._cache[key] = {k: v for k, v in payload.items() if k != "cached"}
        return payload

    def _do_push_code(self, drv, job: TvJob) -> Dict[str, Any]:
        drv.open_chart(job.symbol, job.interval)
        job.progress = 0.4
        out = drv.push_pine_code(job.code)
        if not out.get("compiled", False):
            raise DriverError(f"pine compile failed: {out.get('errors')}", "PINE_COMPILE_ERROR")
        job.progress = 0.9
        return out

    def _write_artifact(self, job: TvJob, filename: str, content: str) -> str:
        folder = os.path.join(self.export_dir, job.job_id)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, filename)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content or "")
        return path

    # ------------------------------------------------------------ lifecycle
    def _loop(self) -> None:
        logger.info("TV worker online (concurrency %d)", bp.TV_MAX_CONCURRENCY)
        while not self._stop.is_set():
            try:
                job_id = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            job = self._jobs.get(job_id)
            if job is None or job.status != JOB_QUEUED:
                continue
            self.run_job(job)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="tv-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> bool:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                logger.warning("tv worker did not stop within %.1fs", timeout)
                return False
            self._thread = None
        return True

    def trim_cache(self, keep: int = 8) -> int:
        """Drop oldest param-cache entries under memory pressure. Files stay on disk."""
        keep = max(0, int(keep))
        with self._lock:
            extra = max(0, len(self._cache) - keep)
            if extra:
                for key in list(self._cache.keys())[:extra]:
                    self._cache.pop(key, None)
            return extra

    def snapshot(self) -> Dict[str, Any]:
        counts: Dict[str, int] = {}
        for job in self._jobs.values():
            counts[job.status] = counts.get(job.status, 0) + 1
        return {
            "running": bool(self._thread and self._thread.is_alive()),
            "concurrency": bp.TV_MAX_CONCURRENCY,
            "queued": self._queue.qsize(),
            "cache_entries": len(self._cache),
            "counts": counts,
            "jobs_dir": self.jobs_dir,
            "export_dir": self.export_dir,
        }


_queue_singleton: Optional[TvJobQueue] = None


def get_tv_queue(config: Optional[SigmaConfig] = None) -> TvJobQueue:
    global _queue_singleton
    if _queue_singleton is None:
        _queue_singleton = TvJobQueue(config)
    return _queue_singleton


def main() -> None:  # pragma: no cover - Dienst-Entrypoint
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    q = get_tv_queue()
    q.start()
    logger.info("sigma-tv-worker ready — jobs at %s", q.jobs_dir)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        q.stop()


if __name__ == "__main__":  # pragma: no cover
    main()
