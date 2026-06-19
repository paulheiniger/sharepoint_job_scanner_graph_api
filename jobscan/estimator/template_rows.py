from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.engine import Connection, Engine

PARSER_VERSION = "document-content-template-v1"

HEADER_BUCKETS = {
    1: "estimate_date",
    2: "job_name",
    3: "job_type",
    4: "site_address",
    5: "city_state_zip",
    6: "contact",
    8: "email",
    9: "phone",
    12: "estimated_square_feet",
}

MATERIAL_BUCKETS = {
    19: "foam",
    20: "foam",
    21: "foam",
    26: "coating",
    27: "coating",
    28: "coating",
    33: "thinner",
    36: "granules",
    39: "primer",
    43: "caulk_sealant",
    45: "caulk_sealant",
    47: "seams_misc",
    49: "penetrations",
    51: "hvac_units",
    53: "drains",
    58: "board_stock",
    59: "board_stock",
    60: "board_stock",
    63: "fasteners",
    65: "plates",
    69: "dumpsters",
    73: "lift",
    74: "lift",
    76: "delivery_fee",
    79: "fabric",
    82: "edge_metal",
    84: "gutter",
    86: "downspouts",
    88: "roof_hatch",
    90: "scuppers",
    92: "curbs",
    94: "ladders",
    96: "pitch_pockets",
    99: "generator",
    101: "misc",
    103: "freight",
    106: "sales_inspection_trips",
    108: "truck_expense",
}

LABOR_BUCKETS = {
    116: "labor_prep",
    118: "labor_prime",
    120: "labor_seam_sealer",
    122: "labor_base",
    124: "labor_top_coat",
    126: "labor_caulk",
    128: "labor_details",
    130: "labor_top_coat_granules",
    132: "labor_cleanup",
    134: "labor_misc",
    137: "labor_loading",
    139: "labor_traveling",
    142: "infrared_scan",
    145: "meals_lodging",
}

TOTAL_BUCKETS = {
    154: "warranty",
    156: "misc_insurance",
    158: "permits",
    163: "total_job_cost",
    165: "overhead",
    167: "profit",
    169: "worksheet_price",
    170: "worksheet_price_adjusted",
}

ADDER_ROWS = set(range(173, 181))
ADDER_BUCKETS = {row_number: "estimate_adder" for row_number in ADDER_ROWS}

TEMPLATE_BUCKET_BY_ROW = {
    **HEADER_BUCKETS,
    **MATERIAL_BUCKETS,
    **LABOR_BUCKETS,
    **TOTAL_BUCKETS,
    **ADDER_BUCKETS,
}

EQUIPMENT_BUCKETS = {"dumpsters", "lift", "generator", "delivery_fee"}
TRAVEL_BUCKETS = {"sales_inspection_trips", "truck_expense", "labor_traveling", "meals_lodging", "freight"}
WARRANTY_BUCKETS = {"warranty", "misc_insurance", "permits"}
TOTAL_LINE_BUCKETS = {"total_job_cost", "worksheet_price", "worksheet_price_adjusted"}
ADDER_TEMPLATE_BUCKETS = {"estimate_adder", "estimate_adder_no_markup", "misc_materials", "misc_equipment"}
ADDER_AMOUNT_COLUMNS = ("F", "H", "G", "E")

CELL_FRAGMENT_RE = re.compile(r"^\s*([A-Z]{1,4}\d+)\s*:\s*(.*)\s*$")


def numeric_or_text(value: str) -> int | float | str:
    text_value = str(value).strip()
    if not text_value:
        return ""
    cleaned = text_value.replace("$", "").replace(",", "").strip()
    pct = cleaned.endswith("%")
    if pct:
        cleaned = cleaned[:-1].strip()
    try:
        number = float(cleaned)
    except ValueError:
        return text_value
    if pct:
        return number
    if number.is_integer():
        return int(number)
    return number


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text_value = str(value).strip()
    if not text_value or text_value.startswith("="):
        return None
    cleaned = text_value.replace("$", "").replace(",", "").replace("%", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def is_present(value: Any) -> bool:
    if value is None:
        return False
    text_value = str(value).strip()
    return bool(text_value) and text_value.lower() not in {"nan", "none", "null"}


def cell_key(column: str, row_number: int) -> str:
    return f"{column}{row_number}"


def parse_cell_labeled_text(text_content: str) -> tuple[dict[str, Any], dict[str, str], int]:
    cell_values: dict[str, Any] = {}
    formula_cells: dict[str, str] = {}
    malformed_count = 0
    for fragment in str(text_content or "").split("|"):
        fragment = fragment.strip()
        if not fragment:
            continue
        match = CELL_FRAGMENT_RE.match(fragment)
        if not match:
            malformed_count += 1
            continue
        cell_ref, raw_value = match.groups()
        raw_value = raw_value.strip()
        if raw_value.startswith("="):
            formula_cells[cell_ref] = raw_value
        else:
            converted = numeric_or_text(raw_value)
            if converted != "":
                cell_values[cell_ref] = converted
    return cell_values, formula_cells, malformed_count


def template_section_for_bucket(bucket: str) -> str:
    if bucket in HEADER_BUCKETS.values():
        return "job_header"
    if bucket in MATERIAL_BUCKETS.values():
        if bucket in TRAVEL_BUCKETS:
            return "travel"
        if bucket in EQUIPMENT_BUCKETS:
            return "materials"
        return "materials"
    if bucket in LABOR_BUCKETS.values():
        if bucket in TRAVEL_BUCKETS:
            return "travel"
        return "labor"
    if bucket in WARRANTY_BUCKETS:
        return "warranty_bonding_insurance"
    if bucket in {"overhead", "profit"}:
        return "overhead_profit"
    if bucket in TOTAL_LINE_BUCKETS:
        return "totals"
    if bucket in ADDER_TEMPLATE_BUCKETS:
        return "estimate_adders"
    return "other"


def line_item_kind_for_bucket(bucket: str) -> str:
    if bucket in HEADER_BUCKETS.values():
        return "header"
    if bucket in {"foam", "coating", "thinner", "granules", "primer", "caulk_sealant", "seams_misc", "penetrations", "hvac_units", "drains", "board_stock", "fasteners", "plates", "fabric", "edge_metal", "gutter", "downspouts", "roof_hatch", "scuppers", "curbs", "ladders", "pitch_pockets", "misc", "misc_materials"}:
        return "material"
    if bucket in EQUIPMENT_BUCKETS or bucket == "misc_equipment":
        return "equipment"
    if bucket in TRAVEL_BUCKETS:
        return "travel"
    if bucket in LABOR_BUCKETS.values():
        return "labor"
    if bucket == "warranty":
        return "warranty"
    if bucket == "misc_insurance":
        return "insurance"
    if bucket == "permits":
        return "permit"
    if bucket in {"overhead", "profit"}:
        return "overhead_profit"
    if bucket in TOTAL_LINE_BUCKETS:
        return "total"
    return "unknown" if bucket == "unknown" else "other"


def classify_estimate_adder(row_label: Any) -> tuple[str, str]:
    label = str(row_label or "").strip().lower()
    if "lift" in label:
        return "lift", "equipment"
    if "insurance" in label:
        return "misc_insurance", "insurance"
    if "material" in label:
        return "misc_materials", "material"
    if "equipment" in label:
        return "misc_equipment", "equipment"
    if "markup" in label or "w/o markup" in label or "without markup" in label:
        return "estimate_adder_no_markup", "other"
    return "estimate_adder", "other"


def is_placeholder_adder_label(row_label: Any) -> bool:
    label = str(row_label or "").strip().lower()
    return "additional amount" in label and ("markup" in label or "w/o" in label or "without" in label)


def adder_label_text(row_label: Any, cell_values: dict[str, Any], raw_text: str) -> str:
    parts = [str(row_label or ""), str(raw_text or "")]
    parts.extend(str(value or "") for value in cell_values.values())
    return " ".join(part for part in parts if part.strip())


def adder_estimated_cost(cell_values: dict[str, Any], row_number: int) -> float | None:
    for column in ADDER_AMOUNT_COLUMNS:
        amount = numeric_at(cell_values, row_number, column)
        if amount is not None:
            return amount
    return None


def is_unused_placeholder_adder_row(row: dict[str, Any] | pd.Series) -> bool:
    record = row.to_dict() if isinstance(row, pd.Series) else dict(row)
    row_number = int(record.get("row_number") or 0)
    if row_number not in ADDER_ROWS:
        return False
    raw_text = str(record.get("text_content") or "")
    if not re.search(r"\b[A-Z]{1,4}\d+\s*:", raw_text):
        return False
    cell_values, _formula_cells, _malformed_count = parse_cell_labeled_text(raw_text)
    row_label = value_at(cell_values, row_number, "A")
    label_text = adder_label_text(row_label, cell_values, raw_text)
    return is_placeholder_adder_label(label_text) and adder_estimated_cost(cell_values, row_number) is None


def template_row_id_for_content_row(row: dict[str, Any] | pd.Series) -> str:
    record = row.to_dict() if isinstance(row, pd.Series) else dict(row)
    return stable_template_row_id(record.get("document_id"), record.get("sheet_name"), record.get("row_number"), record.get("cell_range"))


def stable_template_row_id(document_id: Any, sheet_name: Any, row_number: Any, cell_range: Any) -> str:
    key = "||".join(str(part or "") for part in (document_id, sheet_name, row_number, cell_range))
    return f"templaterow-{hashlib.sha1(key.encode('utf-8')).hexdigest()[:28]}"


def value_at(cell_values: dict[str, Any], row_number: int, column: str) -> Any:
    return cell_values.get(cell_key(column, row_number))


def numeric_at(cell_values: dict[str, Any], row_number: int, column: str) -> float | None:
    return to_float(value_at(cell_values, row_number, column))


def parse_document_content_row(row: dict[str, Any] | pd.Series) -> dict[str, Any] | None:
    record = row.to_dict() if isinstance(row, pd.Series) else dict(row)
    sheet_name = str(record.get("sheet_name") or "")
    row_number = int(record.get("row_number") or 0)
    raw_text = str(record.get("text_content") or "")
    if sheet_name.lower() != "estimate" or row_number <= 0 or not raw_text:
        return None
    if not re.search(r"\b[A-Z]{1,4}\d+\s*:", raw_text):
        return None

    cell_values, formula_cells, malformed_count = parse_cell_labeled_text(raw_text)
    bucket = TEMPLATE_BUCKET_BY_ROW.get(row_number, "unknown")
    row_label = value_at(cell_values, row_number, "A")
    if row_number in ADDER_ROWS:
        label_text = adder_label_text(row_label, cell_values, raw_text)
        estimated_cost = adder_estimated_cost(cell_values, row_number)
        if is_placeholder_adder_label(label_text) and estimated_cost is None:
            return None
        bucket, kind = classify_estimate_adder(label_text)
        section = "estimate_adders"
    else:
        section = template_section_for_bucket(bucket)
        kind = line_item_kind_for_bucket(bucket)
    selected_item_name = value_at(cell_values, row_number, "B")
    parsed_confidence = 0.95 if bucket != "unknown" and malformed_count == 0 else 0.55
    needs_review = bucket == "unknown" or malformed_count > 0

    out: dict[str, Any] = {
        "template_row_id": stable_template_row_id(record.get("document_id"), sheet_name, row_number, record.get("cell_range")),
        "document_id": record.get("document_id"),
        "job_id": record.get("job_id"),
        "source_file": record.get("source_file") or record.get("file_name"),
        "sheet_name": sheet_name,
        "row_number": row_number,
        "cell_range": record.get("cell_range"),
        "template_bucket": bucket,
        "template_section": section,
        "line_item_kind": kind,
        "row_label": row_label,
        "raw_text": raw_text,
        "cell_values": cell_values,
        "formula_cells": formula_cells,
        "selected_item_name": selected_item_name,
        "quantity": None,
        "unit": None,
        "unit_price": None,
        "estimated_units": None,
        "estimated_cost": None,
        "days": None,
        "crew_size": None,
        "total_hours": None,
        "daily_rate": None,
        "trips": None,
        "round_trip_miles": None,
        "cost_per_mile": None,
        "warranty_years": None,
        "overhead_pct": None,
        "profit_pct": None,
        "parsed_confidence": parsed_confidence,
        "needs_review": needs_review,
        "parser_version": PARSER_VERSION,
    }

    if section == "materials":
        out["row_label"] = row_label if row_label not in (None, "") else selected_item_name
        out["quantity"] = numeric_at(cell_values, row_number, "C")
        out["unit_price"] = numeric_at(cell_values, row_number, "E")
        out["estimated_units"] = numeric_at(cell_values, row_number, "G")
        out["estimated_cost"] = numeric_at(cell_values, row_number, "H")
    if bucket in {"sales_inspection_trips", "truck_expense"}:
        out["trips"] = numeric_at(cell_values, row_number, "B")
        out["round_trip_miles"] = numeric_at(cell_values, row_number, "C")
        out["cost_per_mile"] = numeric_at(cell_values, row_number, "E")
        out["estimated_cost"] = numeric_at(cell_values, row_number, "H")
    if 116 <= row_number <= 134:
        out["days"] = numeric_at(cell_values, row_number, "B")
        out["crew_size"] = numeric_at(cell_values, row_number, "C")
        out["total_hours"] = numeric_at(cell_values, row_number, "D")
        out["estimated_cost"] = numeric_at(cell_values, row_number, "H")
        out["daily_rate"] = numeric_at(cell_values, row_number, "J")
    if row_number in {137, 139}:
        out["days"] = numeric_at(cell_values, row_number, "C")
        out["total_hours"] = numeric_at(cell_values, row_number, "C")
        out["crew_size"] = numeric_at(cell_values, row_number, "E")
        out["unit_price"] = numeric_at(cell_values, row_number, "G")
        out["estimated_cost"] = numeric_at(cell_values, row_number, "H")
    if row_number == 154:
        out["warranty_years"] = numeric_at(cell_values, row_number, "C")
        out["quantity"] = numeric_at(cell_values, row_number, "E")
        out["estimated_cost"] = numeric_at(cell_values, row_number, "H")
    if row_number == 165:
        out["overhead_pct"] = numeric_at(cell_values, row_number, "F")
        out["estimated_cost"] = numeric_at(cell_values, row_number, "H")
    if row_number == 167:
        out["profit_pct"] = numeric_at(cell_values, row_number, "F")
        out["estimated_cost"] = numeric_at(cell_values, row_number, "H")
    if row_number in {163, 169}:
        out["estimated_cost"] = numeric_at(cell_values, row_number, "H")
    if row_number == 170:
        out["estimated_cost"] = numeric_at(cell_values, row_number, "F") or numeric_at(cell_values, row_number, "H")
    if row_number in ADDER_ROWS:
        out["row_label"] = row_label
        out["selected_item_name"] = row_label
        out["estimated_cost"] = adder_estimated_cost(cell_values, row_number)
        has_label = is_present(row_label)
        has_numeric_amount = out["estimated_cost"] is not None
        has_formula_amount = any(cell_key(column, row_number) in formula_cells for column in ADDER_AMOUNT_COLUMNS)
        if has_label and has_numeric_amount:
            out["needs_review"] = malformed_count > 0
            out["parsed_confidence"] = 0.9 if malformed_count == 0 else 0.65
        elif is_placeholder_adder_label(adder_label_text(row_label, cell_values, raw_text)) and not has_numeric_amount and not has_formula_amount:
            out["needs_review"] = False
            out["parsed_confidence"] = 0.8
        elif has_label and not has_numeric_amount:
            out["needs_review"] = True
            out["parsed_confidence"] = 0.65
        elif has_numeric_amount and not has_label:
            out["needs_review"] = True
            out["parsed_confidence"] = 0.55
    return out


def parse_document_content_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    parsed: list[dict[str, Any]] = []
    for row in rows:
        parsed_row = parse_document_content_row(row)
        if parsed_row:
            parsed.append(parsed_row)
    return parsed


TEMPLATE_ROW_COLUMNS = [
    "template_row_id",
    "document_id",
    "job_id",
    "source_file",
    "sheet_name",
    "row_number",
    "cell_range",
    "template_bucket",
    "template_section",
    "line_item_kind",
    "row_label",
    "raw_text",
    "cell_values",
    "formula_cells",
    "selected_item_name",
    "quantity",
    "unit",
    "unit_price",
    "estimated_units",
    "estimated_cost",
    "days",
    "crew_size",
    "total_hours",
    "daily_rate",
    "trips",
    "round_trip_miles",
    "cost_per_mile",
    "warranty_years",
    "overhead_pct",
    "profit_pct",
    "parsed_confidence",
    "needs_review",
    "parser_version",
]


def db_row(row: dict[str, Any]) -> dict[str, Any]:
    out = {column: row.get(column) for column in TEMPLATE_ROW_COLUMNS}
    out["cell_values"] = json.dumps(out.get("cell_values") or {}, sort_keys=True)
    out["formula_cells"] = json.dumps(out.get("formula_cells") or {}, sort_keys=True)
    return out


UPSERT_TEMPLATE_ROW_SQL = text(
    """
    INSERT INTO estimate_template_rows (
        template_row_id, document_id, job_id, source_file, sheet_name, row_number,
        cell_range, template_bucket, template_section, line_item_kind, row_label,
        raw_text, cell_values, formula_cells, selected_item_name, quantity, unit,
        unit_price, estimated_units, estimated_cost, days, crew_size, total_hours,
        daily_rate, trips, round_trip_miles, cost_per_mile, warranty_years,
        overhead_pct, profit_pct, parsed_confidence, needs_review, parser_version
    )
    VALUES (
        :template_row_id, :document_id, :job_id, :source_file, :sheet_name, :row_number,
        :cell_range, :template_bucket, :template_section, :line_item_kind, :row_label,
        :raw_text, :cell_values, :formula_cells, :selected_item_name, :quantity, :unit,
        :unit_price, :estimated_units, :estimated_cost, :days, :crew_size, :total_hours,
        :daily_rate, :trips, :round_trip_miles, :cost_per_mile, :warranty_years,
        :overhead_pct, :profit_pct, :parsed_confidence, :needs_review, :parser_version
    )
    ON CONFLICT (template_row_id) DO UPDATE SET
        job_id = excluded.job_id,
        source_file = excluded.source_file,
        sheet_name = excluded.sheet_name,
        row_number = excluded.row_number,
        cell_range = excluded.cell_range,
        template_bucket = excluded.template_bucket,
        template_section = excluded.template_section,
        line_item_kind = excluded.line_item_kind,
        row_label = excluded.row_label,
        raw_text = excluded.raw_text,
        cell_values = excluded.cell_values,
        formula_cells = excluded.formula_cells,
        selected_item_name = excluded.selected_item_name,
        quantity = excluded.quantity,
        unit = excluded.unit,
        unit_price = excluded.unit_price,
        estimated_units = excluded.estimated_units,
        estimated_cost = excluded.estimated_cost,
        days = excluded.days,
        crew_size = excluded.crew_size,
        total_hours = excluded.total_hours,
        daily_rate = excluded.daily_rate,
        trips = excluded.trips,
        round_trip_miles = excluded.round_trip_miles,
        cost_per_mile = excluded.cost_per_mile,
        warranty_years = excluded.warranty_years,
        overhead_pct = excluded.overhead_pct,
        profit_pct = excluded.profit_pct,
        parsed_confidence = excluded.parsed_confidence,
        needs_review = excluded.needs_review,
        parser_version = excluded.parser_version,
        updated_at = CURRENT_TIMESTAMP
    """
)


def upsert_template_rows(conn: Connection, rows: list[dict[str, Any]], batch_size: int = 1000) -> int:
    if not rows:
        return 0
    total = 0
    batch_size = max(batch_size, 1)
    prepared = [db_row(row) for row in rows]
    for start in range(0, len(prepared), batch_size):
        batch = prepared[start : start + batch_size]
        conn.execute(UPSERT_TEMPLATE_ROW_SQL, batch)
        total += len(batch)
    return total


def delete_template_rows_by_id(conn: Connection, template_row_ids: list[str]) -> int:
    clean_ids = [template_row_id for template_row_id in template_row_ids if template_row_id]
    if not clean_ids:
        return 0
    statement = text("DELETE FROM estimate_template_rows WHERE template_row_id IN :template_row_ids").bindparams(
        bindparam("template_row_ids", expanding=True)
    )
    result = conn.execute(statement, {"template_row_ids": clean_ids})
    return int(result.rowcount or 0)


def fetch_document_content_rows(conn: Connection, document_id: str | None = None) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"document_id": document_id}
    statement = text(
        """
        SELECT
            c.document_id,
            c.job_id,
            COALESCE(d.file_name, c.document_id) AS source_file,
            c.sheet_name,
            c.row_number,
            c.cell_range,
            c.text_content
        FROM document_content c
        LEFT JOIN documents d ON d.document_id = c.document_id
        WHERE LOWER(COALESCE(c.sheet_name, '')) = 'estimate'
          AND c.row_number IS NOT NULL
          AND c.text_content ~ '[A-Z]{1,4}[0-9]+:'
          AND (:document_id IS NULL OR c.document_id = :document_id)
        ORDER BY c.document_id, c.row_number, c.cell_range
        """
    )
    try:
        rows = conn.execute(statement, params).mappings().all()
    except Exception:
        sqlite_statement = text(
            """
            SELECT
                c.document_id,
                c.job_id,
                COALESCE(d.file_name, c.document_id) AS source_file,
                c.sheet_name,
                c.row_number,
                c.cell_range,
                c.text_content
            FROM document_content c
            LEFT JOIN documents d ON d.document_id = c.document_id
            WHERE LOWER(COALESCE(c.sheet_name, '')) = 'estimate'
              AND c.row_number IS NOT NULL
              AND c.text_content LIKE '%:%'
              AND (:document_id IS NULL OR c.document_id = :document_id)
            ORDER BY c.document_id, c.row_number, c.cell_range
            """
        )
        rows = conn.execute(sqlite_statement, params).mappings().all()
    return [dict(row) for row in rows]


def parse_existing_document_content(engine: Engine, document_id: str | None = None, batch_size: int = 1000) -> dict[str, Any]:
    with engine.connect() as conn:
        source_rows = fetch_document_content_rows(conn, document_id=document_id)
    unused_placeholder_ids = [template_row_id_for_content_row(row) for row in source_rows if is_unused_placeholder_adder_row(row)]
    parsed_rows = parse_document_content_rows(source_rows)
    with engine.begin() as conn:
        rows_deleted = delete_template_rows_by_id(conn, unused_placeholder_ids)
        rows_upserted = upsert_template_rows(conn, parsed_rows, batch_size=batch_size)
    documents_considered = len({row.get("document_id") for row in source_rows})
    skipped = len(source_rows) - len(parsed_rows)
    bucket_counts = Counter(row.get("template_bucket") for row in parsed_rows)
    kind_counts = Counter(row.get("line_item_kind") for row in parsed_rows)
    return {
        "documents_considered": documents_considered,
        "rows_read": len(source_rows),
        "rows_parsed": len(parsed_rows),
        "rows_skipped": skipped,
        "rows_upserted": rows_upserted,
        "placeholder_rows_deleted": rows_deleted,
        "rows_needing_review": sum(1 for row in parsed_rows if row.get("needs_review")),
        "by_template_bucket": dict(sorted(bucket_counts.items())),
        "by_line_item_kind": dict(sorted(kind_counts.items())),
    }


def load_template_rows_for_document(engine: Engine, document_id: str) -> pd.DataFrame:
    with engine.connect() as conn:
        return pd.read_sql_query(
            text("SELECT * FROM estimate_template_rows WHERE document_id = :document_id ORDER BY row_number, cell_range"),
            conn,
            params={"document_id": document_id},
        )


def load_template_rows_for_job(engine: Engine, job_id: str) -> pd.DataFrame:
    with engine.connect() as conn:
        return pd.read_sql_query(
            text("SELECT * FROM estimate_template_rows WHERE job_id = :job_id ORDER BY document_id, row_number, cell_range"),
            conn,
            params={"job_id": job_id},
        )


def load_template_rows_for_jobs(engine: Engine, job_ids: list[str]) -> pd.DataFrame:
    clean_ids = [str(job_id) for job_id in job_ids if str(job_id).strip()]
    if not clean_ids:
        return pd.DataFrame()
    statement = text(
        "SELECT * FROM estimate_template_rows WHERE job_id IN :job_ids ORDER BY job_id, document_id, row_number, cell_range"
    ).bindparams(bindparam("job_ids", expanding=True))
    with engine.connect() as conn:
        return pd.read_sql_query(statement, conn, params={"job_ids": clean_ids})


def bucket_summary(template_rows: pd.DataFrame) -> pd.DataFrame:
    if template_rows.empty:
        return pd.DataFrame()
    df = template_rows.copy()
    df["estimated_cost"] = pd.to_numeric(df.get("estimated_cost"), errors="coerce").fillna(0)
    return (
        df.groupby(["template_bucket", "line_item_kind"], dropna=False, as_index=False)
        .agg(
            rows=("template_row_id", "count"),
            total_estimated_cost=("estimated_cost", "sum"),
            review_rows=("needs_review", "sum"),
        )
        .sort_values(["rows", "total_estimated_cost"], ascending=[False, False])
    )


def labor_task_summary(template_rows: pd.DataFrame) -> pd.DataFrame:
    if template_rows.empty:
        return pd.DataFrame()
    df = template_rows[template_rows["line_item_kind"] == "labor"].copy()
    if df.empty:
        return pd.DataFrame()
    for column in ("days", "crew_size", "total_hours", "estimated_cost"):
        df[column] = pd.to_numeric(df.get(column), errors="coerce")
    return (
        df.groupby("template_bucket", dropna=False, as_index=False)
        .agg(
            rows=("template_row_id", "count"),
            median_days=("days", "median"),
            median_crew_size=("crew_size", "median"),
            median_total_hours=("total_hours", "median"),
            median_estimated_cost=("estimated_cost", "median"),
        )
        .sort_values("rows", ascending=False)
    )


def material_equipment_travel_summary(template_rows: pd.DataFrame) -> pd.DataFrame:
    if template_rows.empty:
        return pd.DataFrame()
    df = template_rows[template_rows["line_item_kind"].isin(["material", "equipment", "travel"])].copy()
    if df.empty:
        return pd.DataFrame()
    for column in ("quantity", "unit_price", "estimated_units", "estimated_cost"):
        df[column] = pd.to_numeric(df.get(column), errors="coerce")
    return (
        df.groupby(["template_bucket", "line_item_kind"], dropna=False, as_index=False)
        .agg(
            rows=("template_row_id", "count"),
            median_quantity=("quantity", "median"),
            median_unit_price=("unit_price", "median"),
            median_estimated_units=("estimated_units", "median"),
            median_estimated_cost=("estimated_cost", "median"),
        )
        .sort_values("rows", ascending=False)
    )


def totals_for_document(template_rows: pd.DataFrame) -> dict[str, float | None]:
    if template_rows.empty:
        return {}
    out: dict[str, float | None] = {}
    for bucket in ("total_job_cost", "overhead", "profit", "worksheet_price", "worksheet_price_adjusted"):
        rows = template_rows[template_rows["template_bucket"] == bucket]
        out[bucket] = to_float(rows.iloc[0].get("estimated_cost")) if not rows.empty else None
    return out


def print_summary(summary: dict[str, Any]) -> None:
    print(f"Documents considered: {summary.get('documents_considered', 0)}")
    print(f"Rows read: {summary.get('rows_read', 0)}")
    print(f"Rows parsed: {summary.get('rows_parsed', 0)}")
    print(f"Rows skipped: {summary.get('rows_skipped', 0)}")
    print(f"Rows upserted: {summary.get('rows_upserted', 0)}")
    print(f"Rows needing review: {summary.get('rows_needing_review', 0)}")
    print("Counts by template_bucket:")
    for bucket, count in (summary.get("by_template_bucket") or {}).items():
        print(f"  {bucket}: {count}")
    print("Counts by line_item_kind:")
    for kind, count in (summary.get("by_line_item_kind") or {}).items():
        print(f"  {kind}: {count}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Parse document_content XLSX Estimate rows into structured template rows.")
    parser.add_argument("--parse-existing", action="store_true", help="Parse all existing document_content Estimate rows.")
    parser.add_argument("--document-id", help="Parse one document_id from document_content.")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL") or os.getenv("NEON_DATABASE_URL"))
    parser.add_argument("--batch-size", type=int, default=1000)
    args = parser.parse_args(argv)
    if not args.parse_existing and not args.document_id:
        parser.error("Use --parse-existing or --document-id.")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.database_url:
        raise SystemExit("Set --database-url, DATABASE_URL, or NEON_DATABASE_URL.")
    engine = create_engine(args.database_url, future=True)
    summary = parse_existing_document_content(engine, document_id=args.document_id, batch_size=args.batch_size)
    print_summary(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
