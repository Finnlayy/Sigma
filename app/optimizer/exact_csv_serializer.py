"""
=========================================================
Datei:      app/optimizer/exact_csv_serializer.py
Zweck:      §35 / Axiom 13 — Exact TradingView CSV Roundtrip Protocol
System:     Manas: Ciel Core Matrix — Projekt:Sigma
Knoten:     Jaune (Carrera-Engine) / Loop B
=========================================================

TradingViews Properties-Import ist strikt: **falscher Dateiname oder
abweichender Header = Schema-Mismatch**. Sigma spiegelt TV 1:1:

* Dateiname exakt wie der Export (``CISD_Scalper_v6_properties.csv``)
* Zeile 1 byte-identisch (Header wird nie neu erfunden)
* Delimiter (``,`` oder ``;``) aus dem Original uebernommen
* Versionierung ueber Ordner (``baseline/`` vs ``optimized/``), nie ueber
  Umbenennung — ``parameters_optimized.csv`` ist verboten

Vor jedem Playwright-Re-Upload laeuft die Noir-Gate-Assertion; bei
Abweichung: ``CSV_HEADER_MISMATCH`` und das GA-Individuum wird verworfen.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Tuple

from app.core import blueprint as bp

logger = logging.getLogger("app.optimizer.exact_csv")


class CsvHeaderMismatch(ValueError):
    """§35.4 — Header oder Dateiname weichen vom TV-Original ab."""

    code = bp.CSV_HEADER_MISMATCH_CODE

    def __init__(self, reason: str) -> None:
        super().__init__(f"{self.code}: {reason}")
        self.reason = reason


def sniff_delimiter(header_line: str) -> str:
    """Erkennt den Delimiter des Originals (nur ``,`` und ``;`` sind kanonisch)."""
    counts = {delim: header_line.count(delim) for delim in bp.CSV_ALLOWED_DELIMITERS}
    delimiter = max(counts, key=lambda d: counts[d])
    if counts[delimiter] == 0:
        return ","
    return delimiter


def split_line(line: str, delimiter: str) -> List[str]:
    return [cell.strip() for cell in line.rstrip("\r\n").split(delimiter)]


def _coerce(value: str) -> Any:
    token = value.strip()
    low = token.lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return int(token)
    except ValueError:
        pass
    try:
        return float(token)
    except ValueError:
        return token


def _format(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        text = f"{value:.10f}".rstrip("0").rstrip(".")
        return text or "0"
    return str(value)


@dataclass
class CsvMeta:
    """Inhalt von ``meta.json`` (§35.2)."""

    original_csv_filename: str
    exact_csv_header: List[str]
    delimiter: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "original_csv_filename": self.original_csv_filename,
            "exact_csv_header": list(self.exact_csv_header),
            "delimiter": self.delimiter,
        }


class ExactTradingViewCSVHandler:
    """Haelt Dateiname, Header und Delimiter eines TV-Exports eingefroren."""

    def __init__(self, original_csv_path: str) -> None:
        if not os.path.exists(original_csv_path):
            raise FileNotFoundError(original_csv_path)
        self.original_csv_path = original_csv_path
        self.original_filename = os.path.basename(original_csv_path)
        if self.original_filename in bp.CSV_FORBIDDEN_FILENAMES:
            raise CsvHeaderMismatch(
                f"{self.original_filename} ist ein erfundener Name — "
                "TV-Originalnamen verwenden")
        with open(original_csv_path, encoding="utf-8", newline="") as fh:
            content = fh.read()
        lines = content.splitlines()
        if not lines:
            raise CsvHeaderMismatch(f"{self.original_filename} ist leer")
        self.exact_header_row = lines[0]
        self.delimiter = sniff_delimiter(self.exact_header_row)
        self.header_fields = split_line(self.exact_header_row, self.delimiter)
        self.line_ending = "\r\n" if "\r\n" in content else "\n"
        self._rows = [split_line(line, self.delimiter) for line in lines[1:] if line.strip()]

    # ------------------------------------------------------------- lesen ---
    def parameters(self) -> Dict[str, Any]:
        """Werte-Spalte als Dict; Header-Zeile wird uebersprungen (§35.6)."""
        params: Dict[str, Any] = {}
        for row in self._rows:
            if not row or not row[0]:
                continue
            value = row[1] if len(row) > 1 else ""
            params[row[0]] = _coerce(value)
        return params

    def meta(self) -> CsvMeta:
        return CsvMeta(self.original_filename, list(self.header_fields), self.delimiter)

    # ---------------------------------------------------------- schreiben ---
    def serialize_optimized_values(self, optimized_params: Mapping[str, Any]) -> str:
        """Zeile 1 = Original-Header, danach nur mutierte Werte."""
        lines = [self.exact_header_row]
        known = self.parameters()
        for name in known:
            value = optimized_params.get(name, known[name])
            lines.append(f"{name}{self.delimiter}{_format(value)}")
        for name, value in optimized_params.items():
            if name not in known:
                logger.warning("unbekannter Parameter %s — nicht in TV-Original", name)
        return self.line_ending.join(lines) + self.line_ending

    def save_versioned_csv(self, strategy_dir: str, params: Mapping[str, Any],
                           *, is_baseline: bool = False) -> str:
        """Schreibt ``baseline/`` oder ``optimized/`` — immer gleicher Dateiname."""
        subdir = bp.CSV_BASELINE_DIR if is_baseline else bp.CSV_OPTIMIZED_DIR
        target_dir = os.path.join(strategy_dir, subdir)
        os.makedirs(target_dir, exist_ok=True)
        target = os.path.join(target_dir, self.original_filename)
        with open(target, "w", encoding="utf-8", newline="") as fh:
            fh.write(self.serialize_optimized_values(params))
        self.write_meta(strategy_dir)
        return target

    def write_meta(self, strategy_dir: str) -> str:
        os.makedirs(strategy_dir, exist_ok=True)
        path = os.path.join(strategy_dir, bp.CSV_META_FILE)
        existing: Dict[str, Any] = {}
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    existing = json.load(fh) or {}
            except (OSError, json.JSONDecodeError):
                existing = {}
        existing.update(self.meta().as_dict())
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(existing, fh, indent=2)
        return path

    # ------------------------------------------------------- Noir-Gate ------
    def assert_header_matches(self, candidate_csv: str,
                              *, filename: Optional[str] = None) -> None:
        """§35.4 — Pre-Upload-Assertion. Wirft ``CsvHeaderMismatch``."""
        if filename is not None and os.path.basename(filename) != self.original_filename:
            raise CsvHeaderMismatch(
                f"Dateiname {os.path.basename(filename)!r} != "
                f"{self.original_filename!r}")
        first_line = (candidate_csv or "").splitlines()[0] if candidate_csv else ""
        if first_line != self.exact_header_row:
            raise CsvHeaderMismatch(
                f"Header {first_line!r} != Original {self.exact_header_row!r}")

    def validate_upload(self, csv_file_path: str) -> Dict[str, Any]:
        """Assertion gegen eine Datei auf Platte; liefert Audit-Payload."""
        with open(csv_file_path, encoding="utf-8", newline="") as fh:
            content = fh.read()
        self.assert_header_matches(content, filename=csv_file_path)
        return {
            "ok": True, "filename": os.path.basename(csv_file_path),
            "header": self.exact_header_row, "delimiter": self.delimiter,
            "rows": len([ln for ln in content.splitlines()[1:] if ln.strip()]),
        }


# =============================================================================
# GA-Anbindung (§35.6)
# =============================================================================

def baseline_path(strategy_dir: str, filename: str) -> str:
    return os.path.join(strategy_dir, bp.CSV_BASELINE_DIR, filename)


def optimized_path(strategy_dir: str, filename: str) -> str:
    return os.path.join(strategy_dir, bp.CSV_OPTIMIZED_DIR, filename)


def load_handler(strategy_dir: str) -> ExactTradingViewCSVHandler:
    """Rekonstruiert den Handler aus ``meta.json`` + ``baseline/``."""
    meta_path = os.path.join(strategy_dir, bp.CSV_META_FILE)
    if not os.path.exists(meta_path):
        raise CsvHeaderMismatch(f"{bp.CSV_META_FILE} fehlt in {strategy_dir}")
    with open(meta_path, encoding="utf-8") as fh:
        meta = json.load(fh)
    filename = meta.get("original_csv_filename")
    if not filename:
        raise CsvHeaderMismatch("original_csv_filename fehlt in meta.json")
    return ExactTradingViewCSVHandler(baseline_path(strategy_dir, filename))


def ingest_tv_export(strategy_dir: str, csv_text: str, original_filename: str
                     ) -> Tuple[ExactTradingViewCSVHandler, str]:
    """Frischen TV-Export als Baseline einfrieren (Dateiname bleibt erhalten)."""
    if original_filename in bp.CSV_FORBIDDEN_FILENAMES:
        raise CsvHeaderMismatch(f"{original_filename} ist verboten (§35.1)")
    target_dir = os.path.join(strategy_dir, bp.CSV_BASELINE_DIR)
    os.makedirs(target_dir, exist_ok=True)
    target = os.path.join(target_dir, original_filename)
    with open(target, "w", encoding="utf-8", newline="") as fh:
        fh.write(csv_text)
    handler = ExactTradingViewCSVHandler(target)
    handler.write_meta(strategy_dir)
    return handler, target


def emit_optimized(strategy_dir: str, params: Mapping[str, Any]) -> Dict[str, Any]:
    """GA-Ergebnis schreiben und sofort gegen den Header pruefen."""
    handler = load_handler(strategy_dir)
    path = handler.save_versioned_csv(strategy_dir, params)
    audit = handler.validate_upload(path)
    audit["path"] = path
    audit["baseline"] = baseline_path(strategy_dir, handler.original_filename)
    return audit


def upload_properties_csv_to_tv(driver: Any, strategy_id: str, csv_file_path: str,
                                *, handler: Optional[ExactTradingViewCSVHandler] = None,
                                ) -> Dict[str, Any]:
    """§35.5 — Re-Upload mit Original-Dateinamen nach bestandener Assertion."""
    if handler is not None:
        handler.validate_upload(csv_file_path)
    uploader = getattr(driver, "upload_properties_csv", None)
    if uploader is None:
        raise CsvHeaderMismatch("Driver unterstuetzt keinen Properties-Upload")
    result = uploader(strategy_id=strategy_id, csv_file_path=csv_file_path,
                      filename=os.path.basename(csv_file_path))
    return {"uploaded": True, "filename": os.path.basename(csv_file_path),
            "driver_result": result}
