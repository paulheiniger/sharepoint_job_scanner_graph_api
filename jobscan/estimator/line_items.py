from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.engine import Connection, Engine

from jobscan.db_loader import ensure_primary_id, load_json_records
from .rules import to_float

CLASSIFIER_VERSION = "template-bucket-v1"

TEMPLATE_BUCKETS = [
    "foam",
    "coating",
    "thinner",
    "granules",
    "primer",
    "caulk_sealant",
    "seams_misc",
    "penetrations",
    "hvac_units",
    "drains",
    "board_stock",
    "fasteners",
    "plates",
    "dumpsters",
    "lift",
    "delivery_fee",
    "fabric",
    "edge_metal",
    "gutter",
    "downspouts",
    "roof_hatch",
    "scuppers",
    "curbs",
    "ladders",
    "pitch_pockets",
    "generator",
    "freight",
    "sales_inspection_trips",
    "truck_expense",
    "labor_prep",
    "labor_prime",
    "labor_seam_sealer",
    "labor_base",
    "labor_top_coat",
    "labor_details",
    "labor_cleanup",
    "labor_loading",
    "labor_traveling",
    "meals_lodging",
    "overhead_profit",
    "other",
    "unknown",
]

BUCKET_SECTIONS = {
    "foam": "materials",
    "coating": "materials",
    "thinner": "materials",
    "granules": "materials",
    "primer": "materials",
    "caulk_sealant": "materials",
    "seams_misc": "materials",
    "penetrations": "materials",
    "hvac_units": "materials",
    "drains": "materials",
    "board_stock": "materials",
    "fasteners": "materials",
    "plates": "materials",
    "dumpsters": "equipment",
    "lift": "equipment",
    "delivery_fee": "equipment",
    "fabric": "materials",
    "edge_metal": "materials",
    "gutter": "materials",
    "downspouts": "materials",
    "roof_hatch": "materials",
    "scuppers": "materials",
    "curbs": "materials",
    "ladders": "materials",
    "pitch_pockets": "materials",
    "generator": "equipment",
    "freight": "travel",
    "sales_inspection_trips": "travel",
    "truck_expense": "travel",
    "labor_prep": "labor",
    "labor_prime": "labor",
    "labor_seam_sealer": "labor",
    "labor_base": "labor",
    "labor_top_coat": "labor",
    "labor_details": "labor",
    "labor_cleanup": "labor",
    "labor_loading": "labor",
    "labor_traveling": "labor",
    "meals_lodging": "travel",
    "overhead_profit": "overhead_profit",
    "other": "other",
    "unknown": "unknown",
}

BUCKET_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("meals_lodging", ("lodging", "hotel", "meal", "per diem")),
    ("overhead_profit", ("overhead", "profit", "markup")),
    ("labor_loading", ("loading", "load out", "load-in", "load in")),
    ("labor_traveling", ("traveling", "travel labor")),
    ("labor_prep", ("prep", "pressure wash", "pwash", "grind", "patch")),
    ("labor_prime", ("prime", "primer labor")),
    ("labor_seam_sealer", ("seam sealer", "seam labor")),
    ("labor_base", ("base coat", "base ")),
    ("labor_top_coat", ("top coat", "finish coat")),
    ("labor_cleanup", ("cleanup", "clean up", "touch")),
    ("labor_details", ("detail", "caulk/sf")),
    ("sales_inspection_trips", ("sales trip", "inspection", "inspect")),
    ("truck_expense", ("truck", "mileage", "miles")),
    ("freight", ("freight",)),
    ("delivery_fee", ("delivery",)),
    ("dumpsters", ("dumpster",)),
    ("lift", ("lift", "boom", "scissor", "forklift", "articulating")),
    ("generator", ("generator",)),
    ("roof_hatch", ("roof hatch",)),
    ("pitch_pockets", ("pitch pocket", "pitch pan", "pipe boot")),
    ("penetrations", ("penetration", "pipe boot", "pipe flashing")),
    ("hvac_units", ("hvac", "unit curb", "unit")),
    ("curbs", ("curb",)),
    ("drains", ("drain",)),
    ("scuppers", ("scupper",)),
    ("ladders", ("ladder",)),
    ("downspouts", ("downspout",)),
    ("gutter", ("gutter",)),
    ("edge_metal", ("edge metal", "coping", "termination bar")),
    ("fasteners", ("fastener", "screw")),
    ("plates", ("plate",)),
    ("board_stock", ("board", "iso", "cover board", "dens deck", "hd board", "gypsum")),
    ("fabric", ("fabric", "stitchbond", "roll")),
    ("caulk_sealant", ("caulk", "sealant", "flashing grade", "buttergrade", "sausage")),
    ("seams_misc", ("seam", "seams", "tape")),
    ("primer", ("primer", "bleed block", "red zinc", "e-5320")),
    ("granules", ("granule", "granules")),
    ("thinner", ("thinner", "solvent", "xylene", "naphtha", "mineral spirits")),
    ("foam", ("spray foam", "polyurethane foam", "spf", " foam", "gaco roof 2.7", "basf roof")),
    ("coating", ("silicone", "acrylic", "urethane", "coating", "topcoat", "top coat")),
]

SOURCE_ROW_BUCKETS = {
    116: "labor_prep",
    118: "labor_prime",
    120: "labor_seam_sealer",
    122: "labor_base",
    124: "labor_top_coat",
    126: "labor_details",
    128: "labor_details",
    130: "labor_top_coat",
    132: "labor_cleanup",
    134: "other",
    137: "labor_loading",
    139: "labor_traveling",
    145: "meals_lodging",
}

IMPORTANT_MATERIAL_BUCKETS = {
    "foam",
    "coating",
    "thinner",
    "granules",
    "primer",
    "caulk_sealant",
    "fabric",
    "board_stock",
    "fasteners",
    "plates",
}


@dataclass(frozen=True)
class ClassificationResult:
    template_bucket: str
    template_section: str
    classification_confidence: float
    classification_reason: str
    needs_review: bool


def _row_text(row: dict[str, Any] | pd.Series) -> str:
    return " ".join(
        str(row.get(column) or "")
        for column in (
            "section",
            "line_item_category",
            "line_item_name",
            "item_name",
            "description",
            "unit",
            "notes",
            "source_sheet",
        )
    ).lower()


def _line_total(row: dict[str, Any] | pd.Series) -> float | None:
    for column in ("line_total", "extended_cost", "total", "amount"):
        value = to_float(row.get(column))
        if value is not None:
            return value
    return None


def _has_missing_quantity_for_material(row: dict[str, Any] | pd.Series, bucket: str) -> bool:
    if bucket not in IMPORTANT_MATERIAL_BUCKETS:
        return False
    return to_float(row.get("quantity")) is None and to_float(row.get("labor_hours")) is None


def classify_template_line_item(row: dict[str, Any] | pd.Series) -> ClassificationResult:
    text = _row_text(row)
    matches: list[tuple[str, str]] = []
    source_row = int(to_float(row.get("source_row")) or -1)
    labor_context = "labor" in str(row.get("section") or "").lower() or "subcontractor" in str(row.get("section") or "").lower() or "labor" in text
    if source_row in SOURCE_ROW_BUCKETS and "labor" in text:
        bucket = SOURCE_ROW_BUCKETS[source_row]
        return ClassificationResult(bucket, BUCKET_SECTIONS[bucket], 0.9, f"matched template labor source row {source_row}", False)

    for bucket, keywords in BUCKET_KEYWORDS:
        if bucket.startswith("labor_") and not labor_context:
            continue
        matched = [keyword for keyword in keywords if keyword in text]
        if matched:
            matches.append((bucket, matched[0]))

    if "labor" in text and not any(bucket.startswith("labor_") for bucket, _keyword in matches):
        matches.append(("labor_details", "labor"))

    if not matches:
        total = _line_total(row)
        needs_review = total is not None and total != 0
        return ClassificationResult("unknown", "unknown", 0.0, "no keyword or template-row match", needs_review)

    primary_bucket, primary_keyword = matches[0]
    strong_buckets = {bucket for bucket, _keyword in matches[:3]}
    needs_review = len(strong_buckets) > 1 and primary_bucket not in {"fabric", "seams_misc", "caulk_sealant"}
    if _has_missing_quantity_for_material(row, primary_bucket):
        needs_review = True
    confidence = 0.9 if len(strong_buckets) == 1 else 0.65
    if needs_review:
        confidence = min(confidence, 0.7)
    reason = f"matched keyword '{primary_keyword}'"
    if len(strong_buckets) > 1:
        reason += f"; competing buckets: {', '.join(sorted(strong_buckets))}"
    if _has_missing_quantity_for_material(row, primary_bucket):
        reason += "; missing quantity/unit for material bucket"
    return ClassificationResult(primary_bucket, BUCKET_SECTIONS[primary_bucket], confidence, reason, needs_review)


def classify_line_items(line_items: pd.DataFrame) -> pd.DataFrame:
    if line_items.empty:
        return pd.DataFrame()
    rows = []
    for _, row in line_items.iterrows():
        result = classify_template_line_item(row)
        out = row.to_dict()
        out.update(
            {
                "template_bucket": result.template_bucket,
                "template_section": result.template_section,
                "classification_confidence": result.classification_confidence,
                "classification_reason": result.classification_reason,
                "needs_review": result.needs_review,
                "line_total": _line_total(row),
            }
        )
        rows.append(out)
    return pd.DataFrame(rows)


def summarize_classified_by_job(classified: pd.DataFrame, job_sqft: dict[str, float] | None = None) -> dict[str, Any]:
    job_sqft = job_sqft or {}
    if classified.empty:
        return {"job_bucket_summary": pd.DataFrame(), "total_by_section": {}}
    df = classified.copy()
    df["line_total"] = pd.to_numeric(df.get("line_total"), errors="coerce").fillna(0)
    summary = (
        df.groupby(["job_id", "template_section", "template_bucket"], dropna=False, as_index=False)
        .agg(
            total_cost=("line_total", "sum"),
            line_count=("template_bucket", "size"),
            review_count=("needs_review", "sum"),
        )
        .sort_values(["job_id", "template_section", "template_bucket"])
    )
    if job_sqft:
        summary["estimated_sqft"] = summary["job_id"].astype(str).map(job_sqft)
        summary["cost_per_sqft"] = summary.apply(
            lambda row: row["total_cost"] / row["estimated_sqft"] if row.get("estimated_sqft") else None,
            axis=1,
        )
    total_by_section = df.groupby("template_section")["line_total"].sum().to_dict()
    return {"job_bucket_summary": summary, "total_by_section": total_by_section}


def summarize_similar_job_buckets(
    line_items: pd.DataFrame,
    similar_jobs: pd.DataFrame,
    *,
    job_sqft: dict[str, float] | None = None,
) -> dict[str, Any]:
    if line_items.empty or similar_jobs.empty or "job_id" not in line_items.columns or "job_id" not in similar_jobs.columns:
        return {
            "classified_rows": pd.DataFrame(),
            "bucket_summary": pd.DataFrame(),
            "common_items": pd.DataFrame(),
        }
    similar_ids = [str(job_id) for job_id in similar_jobs["job_id"].dropna().astype(str)]
    filtered = line_items[line_items["job_id"].astype(str).isin(similar_ids)].copy()
    if filtered.empty:
        return {
            "classified_rows": pd.DataFrame(),
            "bucket_summary": pd.DataFrame(),
            "common_items": pd.DataFrame(),
        }
    if job_sqft is None:
        job_sqft = {
            str(row.get("job_id")): float(row.get("estimated_sqft"))
            for _, row in similar_jobs.iterrows()
            if to_float(row.get("estimated_sqft"))
        }
    if {"template_bucket", "template_section", "classification_confidence", "needs_review"}.issubset(filtered.columns):
        classified = filtered.copy()
        if "line_total" not in classified.columns:
            classified["line_total"] = classified.apply(_line_total, axis=1)
    else:
        classified = classify_line_items(filtered)
    classified["line_total"] = pd.to_numeric(classified["line_total"], errors="coerce").fillna(0)
    classified["estimated_sqft"] = classified["job_id"].astype(str).map(job_sqft)
    classified["cost_per_sqft"] = classified.apply(
        lambda row: row["line_total"] / row["estimated_sqft"] if row.get("estimated_sqft") else None,
        axis=1,
    )
    by_job_bucket = (
        classified.groupby(["job_id", "template_bucket"], dropna=False, as_index=False)
        .agg(
            bucket_cost=("line_total", "sum"),
            cost_per_sqft=("cost_per_sqft", "sum"),
            needs_review=("needs_review", "max"),
        )
    )
    bucket_summary = (
        by_job_bucket.groupby("template_bucket", dropna=False, as_index=False)
        .agg(
            frequency=("job_id", "nunique"),
            median_cost_per_sqft=("cost_per_sqft", "median"),
            low_cost_per_sqft=("cost_per_sqft", lambda values: values.quantile(0.25)),
            high_cost_per_sqft=("cost_per_sqft", lambda values: values.quantile(0.75)),
            median_total_cost=("bucket_cost", "median"),
            needs_review_count=("needs_review", "sum"),
        )
        .sort_values(["frequency", "median_total_cost"], ascending=[False, False])
    )
    name_col = "line_item_name" if "line_item_name" in classified.columns else "item_name"
    common_items = (
        classified.groupby(["template_bucket", name_col], dropna=False, as_index=False)
        .agg(
            count=(name_col, "size"),
            median_line_total=("line_total", "median"),
            needs_review=("needs_review", "max"),
        )
        .rename(columns={name_col: "raw_item_name"})
        .sort_values(["count", "median_line_total"], ascending=[False, False])
        .head(50)
    )
    return {
        "classified_rows": classified,
        "bucket_summary": bucket_summary,
        "common_items": common_items,
    }


def _blank_to_none(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    text_value = str(value).strip()
    if not text_value or text_value.lower() in {"nan", "none", "null"}:
        return None
    return value


def _to_int(value: Any) -> int | None:
    number = to_float(value)
    if number is None:
        return None
    return int(number)


def _normalize_item_name(value: Any) -> str | None:
    value = _blank_to_none(value)
    if value is None:
        return None
    return " ".join(str(value).strip().lower().split())


def _line_item_kind(template_section: str, row: dict[str, Any] | None = None) -> str:
    if row:
        item_text = " ".join(
            str(row.get(column) or "")
            for column in ("line_item_category", "line_item_name", "item_name", "description", "vendor", "notes")
        ).lower()
        if "subcontract" in item_text:
            return "subcontractor"
    return {
        "materials": "material",
        "labor": "labor",
        "equipment": "equipment",
        "travel": "travel",
        "overhead_profit": "overhead_profit",
        "other": "other",
        "unknown": "unknown",
    }.get(template_section, "unknown")


def _template_row_hint(row: dict[str, Any]) -> str | None:
    sheet_name = _blank_to_none(row.get("source_sheet") or row.get("sheet_name"))
    row_number = _to_int(row.get("source_row") or row.get("row_number"))
    if sheet_name and row_number is not None:
        return f"{sheet_name}!{row_number}"
    if row_number is not None:
        return str(row_number)
    return None


def classification_row_from_line_item(row: dict[str, Any] | pd.Series) -> dict[str, Any]:
    record = row.to_dict() if isinstance(row, pd.Series) else dict(row)
    record = ensure_primary_id("line_items", record)
    result = classify_template_line_item(record)
    raw_item_name = _blank_to_none(record.get("line_item_name") or record.get("item_name"))
    source_file = _blank_to_none(record.get("source_file") or record.get("estimate_file") or record.get("source_path"))
    unit_price = to_float(record.get("unit_price"))
    if unit_price is None:
        unit_price = to_float(record.get("unit_cost"))
    line_total = _line_total(record)
    return {
        "line_item_id": str(record["line_item_id"]),
        "job_id": _blank_to_none(record.get("job_id")),
        "estimate_id": _blank_to_none(record.get("estimate_id")),
        "source_file": source_file,
        "sheet_name": _blank_to_none(record.get("source_sheet") or record.get("sheet_name")),
        "row_number": _to_int(record.get("source_row") or record.get("row_number")),
        "raw_item_name": raw_item_name,
        "raw_description": _blank_to_none(record.get("description")),
        "normalized_item_name": _normalize_item_name(raw_item_name),
        "template_bucket": result.template_bucket,
        "template_section": result.template_section,
        "template_row_hint": _template_row_hint(record),
        "line_item_kind": _line_item_kind(result.template_section, record),
        "quantity": to_float(record.get("quantity")),
        "unit": _blank_to_none(record.get("unit")),
        "unit_price": unit_price,
        "line_total": line_total,
        "classification_confidence": result.classification_confidence,
        "classification_reason": result.classification_reason,
        "needs_review": bool(result.needs_review),
        "classifier_version": CLASSIFIER_VERSION,
    }


def classification_rows_from_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [classification_row_from_line_item(record) for record in records]


CLASSIFICATION_COLUMNS = [
    "line_item_id",
    "job_id",
    "estimate_id",
    "source_file",
    "sheet_name",
    "row_number",
    "raw_item_name",
    "raw_description",
    "normalized_item_name",
    "template_bucket",
    "template_section",
    "template_row_hint",
    "line_item_kind",
    "quantity",
    "unit",
    "unit_price",
    "line_total",
    "classification_confidence",
    "classification_reason",
    "needs_review",
    "classifier_version",
]


UPSERT_CLASSIFICATION_SQL = text(
    """
    INSERT INTO estimate_line_item_classifications (
        line_item_id,
        job_id,
        estimate_id,
        source_file,
        sheet_name,
        row_number,
        raw_item_name,
        raw_description,
        normalized_item_name,
        template_bucket,
        template_section,
        template_row_hint,
        line_item_kind,
        quantity,
        unit,
        unit_price,
        line_total,
        classification_confidence,
        classification_reason,
        needs_review,
        classifier_version
    )
    VALUES (
        :line_item_id,
        :job_id,
        :estimate_id,
        :source_file,
        :sheet_name,
        :row_number,
        :raw_item_name,
        :raw_description,
        :normalized_item_name,
        :template_bucket,
        :template_section,
        :template_row_hint,
        :line_item_kind,
        :quantity,
        :unit,
        :unit_price,
        :line_total,
        :classification_confidence,
        :classification_reason,
        :needs_review,
        :classifier_version
    )
    ON CONFLICT (line_item_id) DO UPDATE SET
        job_id = excluded.job_id,
        estimate_id = excluded.estimate_id,
        source_file = excluded.source_file,
        sheet_name = excluded.sheet_name,
        row_number = excluded.row_number,
        raw_item_name = excluded.raw_item_name,
        raw_description = excluded.raw_description,
        normalized_item_name = excluded.normalized_item_name,
        template_bucket = excluded.template_bucket,
        template_section = excluded.template_section,
        template_row_hint = excluded.template_row_hint,
        line_item_kind = excluded.line_item_kind,
        quantity = excluded.quantity,
        unit = excluded.unit,
        unit_price = excluded.unit_price,
        line_total = excluded.line_total,
        classification_confidence = excluded.classification_confidence,
        classification_reason = excluded.classification_reason,
        needs_review = excluded.needs_review,
        classifier_version = excluded.classifier_version,
        updated_at = CURRENT_TIMESTAMP
    """
)


def upsert_classification_rows(conn: Connection, rows: list[dict[str, Any]], batch_size: int = 1000) -> int:
    if not rows:
        return 0
    total = 0
    batch_size = max(batch_size, 1)
    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        conn.execute(UPSERT_CLASSIFICATION_SQL, batch)
        total += len(batch)
    return total


def fetch_estimate_line_item_records(conn: Connection, limit: int | None = None) -> list[dict[str, Any]]:
    statement = """
        SELECT
            line_item_id,
            estimate_id,
            job_id,
            estimate_file,
            section,
            line_item_category,
            line_item_name,
            description,
            quantity,
            unit,
            unit_cost,
            unit_price,
            extended_cost,
            labor_days,
            crew_size,
            labor_hours,
            vendor,
            notes,
            source_sheet,
            source_row
        FROM estimate_line_items
        ORDER BY job_id NULLS LAST, estimate_id NULLS LAST, source_sheet NULLS LAST, source_row NULLS LAST, line_item_id
    """
    params: dict[str, Any] = {}
    if limit is not None:
        statement += " LIMIT :limit"
        params["limit"] = limit
    return [dict(row) for row in conn.execute(text(statement), params).mappings().all()]


def classification_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    bucket_counts = {key: count for key, count in sorted(pd.Series([row["template_bucket"] for row in rows]).value_counts().items())} if rows else {}
    return {
        "rows_classified": len(rows),
        "rows_needing_review": sum(1 for row in rows if row.get("needs_review")),
        "unknown_rows": sum(1 for row in rows if row.get("template_bucket") == "unknown"),
        "by_bucket": bucket_counts,
    }


def classify_existing_line_items(engine: Engine, *, limit: int | None = None, batch_size: int = 1000) -> dict[str, Any]:
    with engine.connect() as conn:
        source_rows = fetch_estimate_line_item_records(conn, limit=limit)
    classification_rows = classification_rows_from_records(source_rows)
    with engine.begin() as conn:
        rows_upserted = upsert_classification_rows(conn, classification_rows, batch_size=batch_size)
    summary = classification_summary(classification_rows)
    summary.update({"rows_read": len(source_rows), "rows_upserted": rows_upserted})
    return summary


def load_classified_line_items_for_job(engine: Engine, job_id: str) -> pd.DataFrame:
    statement = text(
        """
        SELECT *
        FROM estimate_line_item_classifications
        WHERE job_id = :job_id
        ORDER BY estimate_id, sheet_name, row_number, line_item_id
        """
    )
    with engine.connect() as conn:
        return pd.read_sql_query(statement, conn, params={"job_id": job_id})


def load_classified_line_items_for_jobs(engine: Engine, job_ids: list[str]) -> pd.DataFrame:
    clean_ids = [str(job_id) for job_id in job_ids if str(job_id).strip()]
    if not clean_ids:
        return pd.DataFrame()
    statement = text(
        """
        SELECT *
        FROM estimate_line_item_classifications
        WHERE job_id IN :job_ids
        ORDER BY job_id, estimate_id, sheet_name, row_number, line_item_id
        """
    ).bindparams(bindparam("job_ids", expanding=True))
    with engine.connect() as conn:
        return pd.read_sql_query(statement, conn, params={"job_ids": clean_ids})


def load_classification_table_status(engine: Engine) -> dict[str, int]:
    statement = text(
        """
        SELECT
            COUNT(*) AS row_count,
            COUNT(*) FILTER (WHERE needs_review) AS review_count,
            COUNT(*) FILTER (WHERE template_bucket = 'unknown') AS unknown_count
        FROM estimate_line_item_classifications
        """
    )
    with engine.connect() as conn:
        row = conn.execute(statement).mappings().first()
    if not row:
        return {"row_count": 0, "review_count": 0, "unknown_count": 0}
    return {key: int(row.get(key) or 0) for key in ("row_count", "review_count", "unknown_count")}


def bucket_summary_for_similar_jobs(classified: pd.DataFrame, similar_jobs: pd.DataFrame) -> dict[str, Any]:
    return summarize_similar_job_buckets(classified, similar_jobs)


def classify_file_to_dataframe(path: Path) -> pd.DataFrame:
    records = load_json_records(path)
    rows = classification_rows_from_records(records)
    return pd.DataFrame(rows, columns=CLASSIFICATION_COLUMNS)


def print_summary(summary: dict[str, Any]) -> None:
    print(f"Rows read: {summary.get('rows_read', summary.get('rows_classified', 0))}")
    print(f"Rows classified: {summary.get('rows_classified', 0)}")
    if "rows_upserted" in summary:
        print(f"Rows upserted: {summary['rows_upserted']}")
    print(f"Rows needing review: {summary.get('rows_needing_review', 0)}")
    print(f"Unknown rows: {summary.get('unknown_rows', 0)}")
    print("Rows by bucket:")
    for bucket, count in (summary.get("by_bucket") or {}).items():
        print(f"  {bucket}: {count}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Classify Spray-Tec estimate line items into template buckets.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--classify-existing", action="store_true", help="Classify existing estimate_line_items rows in Postgres.")
    mode.add_argument("--classify-file", type=Path, help="Classify a local output/estimate_line_items.json file.")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL") or os.getenv("NEON_DATABASE_URL"), help="Postgres URL for --classify-existing.")
    parser.add_argument("--out", type=Path, help="CSV output path for --classify-file.")
    parser.add_argument("--limit", type=int, help="Optional row limit for testing.")
    parser.add_argument("--batch-size", type=int, default=1000, help="Rows per classification upsert batch.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.classify_existing:
        if not args.database_url:
            raise SystemExit("Set --database-url, DATABASE_URL, or NEON_DATABASE_URL.")
        engine = create_engine(args.database_url, future=True)
        summary = classify_existing_line_items(engine, limit=args.limit, batch_size=args.batch_size)
        print_summary(summary)
        return 0

    output_path = args.out
    if output_path is None:
        raise SystemExit("--out is required with --classify-file.")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df = classify_file_to_dataframe(args.classify_file)
    if args.limit is not None:
        df = df.head(args.limit)
    df.to_csv(output_path, index=False)
    summary = classification_summary(df.to_dict(orient="records"))
    summary["rows_read"] = len(df)
    print_summary(summary)
    print(f"Wrote classification CSV: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
