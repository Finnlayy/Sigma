"""§35 — Exact TradingView CSV Roundtrip Protocol (Header-Freeze & Noir-Gate)."""
from __future__ import annotations

import json
import os

import pytest

from app.core import blueprint as bp
from app.optimizer.exact_csv_serializer import (CsvHeaderMismatch,
                                                ExactTradingViewCSVHandler,
                                                baseline_path, emit_optimized,
                                                ingest_tv_export, load_handler,
                                                optimized_path, sniff_delimiter,
                                                upload_properties_csv_to_tv)

ORIGINAL_NAME = "CISD_Scalper_v6_properties.csv"
TV_EXPORT = (
    "Strategy Input,Value\r\n"
    "trendFastEma,12\r\n"
    "trendSlowEma,60\r\n"
    "atrStopMultiplier,1.5\r\n"
    "useTrailing,true\r\n"
)
TV_EXPORT_SEMICOLON = (
    "Strategy Inputs;Default Value\n"
    "rsiPeriod;14\n"
    "atrPeriod;14\n"
)


@pytest.fixture()
def strategy_dir(tmp_path):
    return str(tmp_path / "strategies" / "cisd_scalper_v6")


def _ingest(strategy_dir, text=TV_EXPORT, name=ORIGINAL_NAME):
    return ingest_tv_export(strategy_dir, text, name)


# ------------------------------------------------------------ Header-Freeze --

def test_header_row_and_delimiter_are_frozen(strategy_dir):
    handler, path = _ingest(strategy_dir)
    assert handler.original_filename == ORIGINAL_NAME
    assert handler.exact_header_row == "Strategy Input,Value"
    assert handler.delimiter == ","
    assert handler.header_fields == ["Strategy Input", "Value"]
    assert path.endswith(os.path.join(bp.CSV_BASELINE_DIR, ORIGINAL_NAME))


def test_semicolon_delimiter_is_preserved(strategy_dir):
    handler, _ = _ingest(strategy_dir, TV_EXPORT_SEMICOLON, "Strategy_properties.csv")
    assert handler.delimiter == ";"
    out = handler.serialize_optimized_values({"rsiPeriod": 21})
    assert out.splitlines()[0] == "Strategy Inputs;Default Value"
    assert "rsiPeriod;21" in out
    assert sniff_delimiter("a;b") == ";" and sniff_delimiter("a,b") == ","


def test_parameters_are_typed(strategy_dir):
    handler, _ = _ingest(strategy_dir)
    params = handler.parameters()
    assert params == {"trendFastEma": 12, "trendSlowEma": 60,
                      "atrStopMultiplier": 1.5, "useTrailing": True}


def test_serialization_keeps_header_and_mutates_only_values(strategy_dir):
    handler, _ = _ingest(strategy_dir)
    text = handler.serialize_optimized_values({"trendFastEma": 21,
                                               "atrStopMultiplier": 2.25})
    lines = text.splitlines()
    assert lines[0] == handler.exact_header_row
    assert lines[1] == "trendFastEma,21"
    assert "atrStopMultiplier,2.25" in lines
    assert "trendSlowEma,60" in lines            # unveraenderte Werte bleiben
    assert "useTrailing,true" in lines           # bool-Formatierung wie TV


# -------------------------------------------------------- Verzeichnislayout --

def test_baseline_and_optimized_share_the_filename(strategy_dir):
    handler, base = _ingest(strategy_dir)
    optimized = handler.save_versioned_csv(strategy_dir, {"trendFastEma": 21})
    assert os.path.basename(base) == os.path.basename(optimized) == ORIGINAL_NAME
    assert base == baseline_path(strategy_dir, ORIGINAL_NAME)
    assert optimized == optimized_path(strategy_dir, ORIGINAL_NAME)
    assert os.path.exists(base) and os.path.exists(optimized)


def test_meta_json_records_contract(strategy_dir):
    handler, _ = _ingest(strategy_dir)
    handler.write_meta(strategy_dir)
    with open(os.path.join(strategy_dir, bp.CSV_META_FILE), encoding="utf-8") as fh:
        meta = json.load(fh)
    assert set(bp.CSV_META_FIELDS) <= set(meta)
    assert meta["original_csv_filename"] == ORIGINAL_NAME
    assert meta["exact_csv_header"] == ["Strategy Input", "Value"]
    assert meta["delimiter"] == ","


def test_handler_can_be_reloaded_from_meta(strategy_dir):
    _ingest(strategy_dir)
    reloaded = load_handler(strategy_dir)
    assert reloaded.exact_header_row == "Strategy Input,Value"
    assert reloaded.parameters()["trendSlowEma"] == 60


def test_load_handler_without_meta_fails(tmp_path):
    with pytest.raises(CsvHeaderMismatch):
        load_handler(str(tmp_path))


# ------------------------------------------------------------- Noir-Gate ----

def test_assertion_accepts_identical_header(strategy_dir):
    handler, _ = _ingest(strategy_dir)
    handler.assert_header_matches(handler.serialize_optimized_values({}),
                                  filename=ORIGINAL_NAME)


def test_assertion_rejects_modified_header(strategy_dir):
    handler, _ = _ingest(strategy_dir)
    tampered = "Input,Value\ntrendFastEma,21\n"
    with pytest.raises(CsvHeaderMismatch) as exc:
        handler.assert_header_matches(tampered)
    assert exc.value.code == bp.CSV_HEADER_MISMATCH_CODE == "CSV_HEADER_MISMATCH"


def test_assertion_rejects_renamed_file(strategy_dir):
    handler, _ = _ingest(strategy_dir)
    with pytest.raises(CsvHeaderMismatch):
        handler.assert_header_matches(handler.serialize_optimized_values({}),
                                      filename="parameters_optimized.csv")


def test_forbidden_filename_cannot_be_ingested(strategy_dir):
    with pytest.raises(CsvHeaderMismatch):
        _ingest(strategy_dir, TV_EXPORT, "parameters_optimized.csv")
    assert "parameters_optimized.csv" in bp.CSV_FORBIDDEN_FILENAMES


def test_empty_export_is_rejected(tmp_path):
    path = tmp_path / "empty.csv"
    path.write_text("")
    with pytest.raises(CsvHeaderMismatch):
        ExactTradingViewCSVHandler(str(path))


# ---------------------------------------------------------- GA-Integration --

def test_emit_optimized_writes_and_validates(strategy_dir):
    _ingest(strategy_dir)
    audit = emit_optimized(strategy_dir, {"trendFastEma": 34})
    assert audit["ok"] is True
    assert audit["filename"] == ORIGINAL_NAME
    assert audit["rows"] == 4
    with open(audit["path"], encoding="utf-8") as fh:
        assert fh.read().splitlines()[0] == "Strategy Input,Value"


def test_gene_schema_consumes_baseline_parameters(strategy_dir):
    from app.optimizer.gene_schema import GeneSchema

    handler, _ = _ingest(strategy_dir)
    schema = GeneSchema.from_params(handler.parameters())
    assert "trendFastEma" in schema.names()
    mutated = schema.genes_to_pine_inputs({"trendFastEma": 21})
    text = handler.serialize_optimized_values(mutated)
    assert text.splitlines()[0] == handler.exact_header_row


class _RecordingDriver:
    def __init__(self) -> None:
        self.uploads = []

    def upload_properties_csv(self, strategy_id, csv_file_path, filename):
        self.uploads.append((strategy_id, csv_file_path, filename))
        return {"applied": True}


def test_upload_uses_original_filename_after_assertion(strategy_dir):
    handler, _ = _ingest(strategy_dir)
    target = handler.save_versioned_csv(strategy_dir, {"trendFastEma": 21})
    driver = _RecordingDriver()
    result = upload_properties_csv_to_tv(driver, "cisd_scalper_v6", target,
                                         handler=handler)
    assert result["uploaded"] is True
    assert driver.uploads[0][2] == ORIGINAL_NAME


def test_upload_blocks_individual_on_header_mismatch(strategy_dir, tmp_path):
    handler, _ = _ingest(strategy_dir)
    rogue = tmp_path / ORIGINAL_NAME
    rogue.write_text("Input,Value\ntrendFastEma,21\n")
    with pytest.raises(CsvHeaderMismatch):
        upload_properties_csv_to_tv(_RecordingDriver(), "cisd_scalper_v6",
                                    str(rogue), handler=handler)


def test_section_35_is_no_longer_pending():
    assert not any(s.startswith("35 ") for s in bp.DOCS_PENDING_SECTIONS)
    assert bp.CSV_KEEP_ORIGINAL_FILENAME and bp.CSV_HEADER_MUST_MATCH_BYTEWISE


# -------------------------------------------------------------------- API ---

def test_csv_api_meta_diff_and_write(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import app.server.routes_sigma as routes
    from app.server.main import app

    root = tmp_path / "strategies"
    monkeypatch.setattr(routes, "_strategy_dir",
                        lambda sid: str(root / sid))
    _ingest(str(root / "csv_strat"))
    client = TestClient(app)

    meta = client.get("/api/strategies/csv_strat/csv/meta")
    assert meta.status_code == 200
    assert meta.json()["original_csv_filename"] == ORIGINAL_NAME

    empty = client.get("/api/strategies/csv_strat/csv/diff").json()
    assert empty["has_optimized"] is False

    written = client.post("/api/strategies/csv_strat/csv/optimized",
                          json={"params": {"trendFastEma": 34}})
    assert written.status_code == 200 and written.json()["ok"] is True

    diff = client.get("/api/strategies/csv_strat/csv/diff").json()
    assert diff["has_optimized"] is True
    assert diff["changed"]["trendFastEma"] == {"baseline": 12, "optimized": 34}

    missing = client.get("/api/strategies/ghost/csv/meta")
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == bp.CSV_HEADER_MISMATCH_CODE
