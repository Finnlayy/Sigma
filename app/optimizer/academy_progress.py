"""Persist Loop-E learning snapshots under data/academy/ (git-tracked)."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core import blueprint as bp
from app.core.config import load_config

logger = logging.getLogger("app.optimizer.academy_progress")


def academy_dir(data_dir: Optional[str] = None) -> Path:
    root = Path(data_dir or load_config().data_dir)
    return (root / "academy").resolve()


def persist_academy_snapshot(
    entries: Optional[List[Dict[str, Any]]] = None,
    *,
    training_rows: Optional[List[Dict[str, Any]]] = None,
    data_dir: Optional[str] = None,
) -> Dict[str, str]:
    """Write academy registry (+ optional training dataset) as JSON.

    DuckDB remains the runtime SoT and stays gitignored; these files are the
    versioned learning trail.
    """
    dest = academy_dir(data_dir)
    dest.mkdir(parents=True, exist_ok=True)
    registry_path = dest / Path(bp.PATH_ACADEMY_REGISTRY).name
    training_path = dest / Path(bp.PATH_ACADEMY_TRAINING).name
    if entries is not None:
        _atomic_json(registry_path, entries)
    if training_rows is not None:
        _atomic_json(training_path, training_rows)
    return {"registry": str(registry_path), "training": str(training_path)}


def _atomic_json(path: Path, payload: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)
