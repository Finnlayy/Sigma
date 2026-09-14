"""
=========================================================
Datei:      app/execution/StorageUtils.py
Zweck:      Atomare Parquet-Schreibvorgänge (tmp + rename) für die L2-Lake
Knoten:     Jaune (Carrera-Engine)
=========================================================
"""
from __future__ import annotations

import os
import tempfile
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq


def write_parquet_atomically(target_file: str, table: pa.Table,
                             compression: str = "zstd") -> str:
    """Schreibt erst in .tmp, renamiert danach — niemals halbe Dateien."""
    os.makedirs(os.path.dirname(target_file) or ".", exist_ok=True)
    tmp_file = f"{target_file}.tmp"
    pq.write_table(table, tmp_file, compression=compression)
    os.replace(tmp_file, target_file)
    return target_file


def parquet_partition_path(base_dir: str, symbol: str, interval_sec: int,
                           day: str) -> str:
    safe_symbol = symbol.replace("/", "-")
    return os.path.join(base_dir, safe_symbol, f"i{interval_sec}s", f"{day}.parquet")


def read_parquet_file(path: str) -> pa.Table:
    return pq.read_table(path)


def flush_candles_to_parquet(base_dir: str, symbol: str, interval_sec: int,
                             candles: list, day_label: str) -> str:
    """Agregiert Candle-Batches partitioniert (Hive-Style) und schreibt atomar."""
    if not candles:
        raise ValueError("Keine Candles zum Schreiben vorhanden.")
    cols = {}
    cols["timestamp"] = pa.array([str(c["ts"]) for c in candles], type=pa.string())
    cols["open"] = pa.array([float(c["open"]) for c in candles], type=pa.float64())
    cols["high"] = pa.array([float(c["high"]) for c in candles], type=pa.float64())
    cols["low"] = pa.array([float(c["low"]) for c in candles], type=pa.float64())
    cols["close"] = pa.array([float(c["close"]) for c in candles], type=pa.float64())
    cols["volume"] = pa.array([float(c["volume"]) for c in candles], type=pa.float64())
    table = pa.table(cols)
    target = parquet_partition_path(base_dir, symbol, interval_sec, day_label)
    return write_parquet_atomically(target, table)
