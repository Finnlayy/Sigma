"""Learning-progress paths must not be gitignored."""
from __future__ import annotations

import subprocess
from pathlib import Path

from app.core import blueprint as bp
from app.optimizer.academy_progress import persist_academy_snapshot


ROOT = Path(__file__).resolve().parents[1]


def _ignored(rel: str) -> bool:
    proc = subprocess.run(
        ["git", "check-ignore", "-q", rel],
        cwd=ROOT,
        check=False,
    )
    return proc.returncode == 0


def test_learning_paths_are_not_gitignored():
    assert not _ignored("data/academy/registry.json")
    assert not _ignored("data/academy/training_dataset.json")
    assert not _ignored("data/strategies/demo/parameters.csv")
    assert not _ignored("models/regime_classifier.onnx")
    assert not _ignored("models/model.onnx")


def test_runtime_secrets_and_lake_stay_gitignored():
    assert _ignored("data/secrets/tv_storage_state.json")
    assert _ignored("data/logs/orders.jsonl")
    assert _ignored("data/sigma.duckdb")
    assert _ignored("data/sigma.duckdb.wal")
    assert _ignored("data/duckdb/runtime.duckdb")
    assert _ignored("data/tv_exports/job/out.csv")


def test_persist_academy_snapshot_writes_json(tmp_path):
    persist_academy_snapshot(
        [{"id": "s1", "graduation_level": "CADET"}],
        training_rows=[{"strategy_id": "s1", "label_allowed": 1}],
        data_dir=str(tmp_path),
    )
    registry = (tmp_path / "academy" / Path(bp.PATH_ACADEMY_REGISTRY).name).read_text()
    training = (tmp_path / "academy" / Path(bp.PATH_ACADEMY_TRAINING).name).read_text()
    assert '"s1"' in registry and "CADET" in registry
    assert "label_allowed" in training
