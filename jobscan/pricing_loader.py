"""Load normalized pricing rows into the pricing_catalog table."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from jobscan.db_connections import create_resilient_engine
from jobscan.pricing.core import extract_pricing_file, parse_float, pdf_pages


PRICING_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS pricing_catalog (
    pricing_item_id TEXT PRIMARY KEY,
    vendor TEXT,
    category TEXT,
    product_name TEXT NOT NULL,
    product_name_normalized TEXT,
    description TEXT,
    unit_price NUMERIC,
    unit_of_measure TEXT,
    package_size TEXT,
    price_basis TEXT,
    price_per_gallon NUMERIC,
    price_per_sqft NUMERIC,
    price_per_unit NUMERIC,
    vendor_item_no TEXT,
    source_file TEXT,
    source_type TEXT,
    source_sheet TEXT,
    source_page INTEGER,
    effective_date DATE,
    expiration_date DATE,
    is_current BOOLEAN DEFAULT TRUE,
    status TEXT DEFAULT 'active',
    needs_review BOOLEAN DEFAULT FALSE,
    review_notes TEXT,
    notes TEXT,
    raw_row_json JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
)
"""


SOURCE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS pricing_source_files (
    source_file_id TEXT PRIMARY KEY,
    file_name TEXT,
    source_type TEXT,
    vendor TEXT,
    effective_date DATE,
    loaded_at TIMESTAMPTZ,
    row_count INTEGER,
    notes TEXT,
    metadata_json JSONB
)
"""


INDEX_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_pricing_catalog_product_name_normalized ON pricing_catalog(product_name_normalized)",
    "CREATE INDEX IF NOT EXISTS idx_pricing_catalog_vendor ON pricing_catalog(vendor)",
    "CREATE INDEX IF NOT EXISTS idx_pricing_catalog_category ON pricing_catalog(category)",
    "CREATE INDEX IF NOT EXISTS idx_pricing_catalog_status ON pricing_catalog(status)",
    "CREATE INDEX IF NOT EXISTS idx_pricing_catalog_is_current ON pricing_catalog(is_current)",
    "CREATE INDEX IF NOT EXISTS idx_pricing_catalog_effective_date ON pricing_catalog(effective_date)",
    "CREATE INDEX IF NOT EXISTS idx_pricing_catalog_needs_review ON pricing_catalog(needs_review)",
]

SUPPORTED_INPUT_EXTS = {".csv", ".xlsx", ".xlsm", ".xls", ".pdf"}

PRICING_EXPORT_COLUMNS = [
    "pricing_item_id",
    "vendor",
    "category",
    "product_name",
    "description",
    "unit_price",
    "unit_of_measure",
    "package_size",
    "price_basis",
    "price_per_gallon",
    "price_per_sqft",
    "price_per_unit",
    "effective_date",
    "status",
    "is_current",
    "needs_review",
    "source_file",
    "notes",
]


@dataclass
class PricingLoadResult:
    rows_read: int = 0
    rows_inserted: int = 0
    rows_updated: int = 0
    rows_skipped: int = 0
    rows_needing_review: int = 0
    pdf_files_discovered: int = 0
    pdf_files_parsed: int = 0
    pdf_pages_read: int = 0
    pdf_rows_extracted: int = 0
    pdf_rows_loaded: int = 0
    pdf_rows_needing_review: int = 0
    pdf_files_skipped: list[str] | None = None


def blank(value: Any) -> bool:
    if value is None:
        return True
    text_value = str(value).strip()
    return not text_value or text_value.lower() in {"nan", "none", "null", "n/a"}


def clean_text(value: Any) -> str | None:
    if blank(value):
        return None
    return " ".join(str(value).replace("\xa0", " ").strip().split())


def normalize_product_name(value: Any) -> str:
    text_value = clean_text(value) or ""
    text_value = text_value.lower()
    text_value = re.sub(r"[^a-z0-9]+", " ", text_value)
    return " ".join(text_value.split())


def safe_date(value: Any) -> str | None:
    if blank(value):
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date().isoformat()


def safe_number(value: Any) -> float | None:
    parsed = parse_float(value)
    return None if parsed is None else float(parsed)


def stable_pricing_item_id(row: dict[str, Any]) -> str:
    if str(row.get("source_type") or "").lower() == "pdf":
        fields = [
            row.get("source_file"),
            row.get("source_page"),
            row.get("product_name_normalized"),
            row.get("unit_of_measure"),
            row.get("unit_price"),
        ]
    else:
        fields = [
            row.get("vendor"),
            row.get("category"),
            row.get("product_name_normalized"),
            row.get("unit_of_measure"),
            row.get("package_size"),
            row.get("source_file"),
        ]
    key = "||".join("" if value is None else str(value).strip().lower() for value in fields)
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]
    return f"price-{digest}"


def source_file_id(path: Path) -> str:
    digest = hashlib.sha1(str(path.name).encode("utf-8")).hexdigest()[:20]
    return f"pricesource-{digest}"


def dataframe_raw_rows(path: Path) -> dict[int, list[Any]]:
    if path.suffix.lower() == ".pdf":
        return {}
    if path.suffix.lower() == ".csv":
        df = pd.read_csv(path, header=None, dtype=object)
    else:
        df = pd.read_excel(path, header=None, dtype=object)
    rows: dict[int, list[Any]] = {}
    for index, row in df.iterrows():
        rows[index + 1] = [None if pd.isna(value) else value for value in row.tolist()]
    return rows


def details_json(row: dict[str, Any]) -> dict[str, Any]:
    details = row.get("details")
    if isinstance(details, dict):
        return details
    if isinstance(details, str) and details.strip():
        try:
            parsed = json.loads(details)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {"details": details}
    return {}


def source_page_from_details(row: dict[str, Any]) -> int | None:
    details = details_json(row)
    try:
        return int(details.get("page_number")) if details.get("page_number") not in (None, "") else None
    except (TypeError, ValueError):
        return None


def source_sheet_from_details(row: dict[str, Any]) -> str | None:
    details = details_json(row)
    return clean_text(details.get("sheet_name") or details.get("source_sheet"))


def raw_payload_for(path: Path, extracted_row: dict[str, Any], raw_rows: dict[int, list[Any]]) -> dict[str, Any]:
    details = details_json(extracted_row)
    row_number = details.get("row_number")
    raw_row = None
    try:
        raw_row = raw_rows.get(int(row_number)) if row_number is not None else None
    except (TypeError, ValueError):
        raw_row = None
    return {
        "source_file": path.name,
        "source_type": path.suffix.lower().lstrip("."),
        "source_details": details,
        "raw_row": raw_row,
        "extracted_row": extracted_row,
    }


def prepare_pricing_row(
    extracted_row: dict[str, Any],
    *,
    source_path: Path,
    raw_rows: dict[int, list[Any]],
    vendor: str | None = None,
    category: str | None = None,
    source_file: str | None = None,
    effective_date: str | None = None,
    mark_current: bool = False,
) -> dict[str, Any] | None:
    product_name = clean_text(extracted_row.get("product_name"))
    unit_price = safe_number(extracted_row.get("unit_price"))
    normalized = normalize_product_name(product_name)
    if not product_name:
        return None
    row = {
        "vendor": clean_text(vendor) or clean_text(extracted_row.get("vendor")),
        "category": clean_text(category) or clean_text(extracted_row.get("category")),
        "product_name": product_name,
        "product_name_normalized": normalized,
        "description": clean_text(extracted_row.get("description")),
        "unit_price": unit_price,
        "unit_of_measure": clean_text(extracted_row.get("unit_of_measure")),
        "package_size": clean_text(extracted_row.get("package_size")),
        "price_basis": clean_text(extracted_row.get("price_basis")),
        "price_per_gallon": safe_number(extracted_row.get("price_per_gallon")),
        "price_per_sqft": safe_number(extracted_row.get("price_per_sqft")),
        "price_per_unit": safe_number(extracted_row.get("price_per_unit")) or unit_price,
        "vendor_item_no": clean_text(extracted_row.get("vendor_item_no")),
        "source_file": clean_text(source_file) or source_path.name,
        "source_type": clean_text(extracted_row.get("source_type")) or source_path.suffix.lower().lstrip("."),
        "source_sheet": source_sheet_from_details(extracted_row),
        "source_page": source_page_from_details(extracted_row),
        "effective_date": safe_date(effective_date) or safe_date(extracted_row.get("effective_date")),
        "expiration_date": None,
        "is_current": bool(mark_current) or True,
        "status": "active",
        "needs_review": bool(extracted_row.get("needs_review")) or unit_price is None or not normalized,
        "review_notes": None,
        "notes": clean_text(extracted_row.get("notes")),
        "raw_row_json": json.dumps(raw_payload_for(source_path, extracted_row, raw_rows), default=str),
    }
    row["pricing_item_id"] = stable_pricing_item_id(row)
    return row


def ensure_pricing_tables(conn: Connection) -> None:
    conn.execute(text(PRICING_TABLE_SQL))
    conn.execute(text(SOURCE_TABLE_SQL))
    for statement in INDEX_SQL:
        conn.execute(text(statement))


def load_input_rows(
    path: Path,
    *,
    vendor: str | None = None,
    category: str | None = None,
    source_file: str | None = None,
    effective_date: str | None = None,
    mark_current: bool = False,
) -> tuple[list[dict[str, Any]], int]:
    if path.suffix.lower() not in SUPPORTED_INPUT_EXTS:
        return [], 0
    extracted_rows = extract_pricing_file(path)
    raw_rows = dataframe_raw_rows(path)
    prepared: list[dict[str, Any]] = []
    skipped = 0
    for extracted in extracted_rows:
        row = prepare_pricing_row(
            extracted,
            source_path=path,
            raw_rows=raw_rows,
            vendor=vendor,
            category=category,
            source_file=source_file,
            effective_date=effective_date,
            mark_current=mark_current,
        )
        if row is None:
            skipped += 1
        else:
            prepared.append(row)
    return prepared, skipped


def pdf_page_count(path: Path) -> int:
    if path.suffix.lower() != ".pdf":
        return 0
    try:
        return len(pdf_pages(path))
    except Exception:
        return 0


def input_paths(input_path: Path | None, input_dir: Path | None) -> list[Path]:
    paths: list[Path] = []
    if input_path:
        paths.append(input_path)
    if input_dir:
        paths.extend(sorted(path for path in input_dir.iterdir() if path.is_file() and path.suffix.lower() in SUPPORTED_INPUT_EXTS))
    return paths


def existing_pricing_ids(conn: Connection, ids: list[str]) -> set[str]:
    if not ids:
        return set()
    rows = conn.execute(
        text("SELECT pricing_item_id FROM pricing_catalog WHERE pricing_item_id = ANY(:ids)"),
        {"ids": ids},
    ).fetchall()
    return {str(row[0]) for row in rows}


def upsert_pricing_rows(conn: Connection, rows: list[dict[str, Any]]) -> tuple[int, int]:
    if not rows:
        return 0, 0
    deduped = {row["pricing_item_id"]: row for row in rows}
    rows = list(deduped.values())
    existing = existing_pricing_ids(conn, [row["pricing_item_id"] for row in rows])
    statement = text(
        """
        INSERT INTO pricing_catalog (
            pricing_item_id, vendor, category, product_name, product_name_normalized, description,
            unit_price, unit_of_measure, package_size, price_basis, price_per_gallon, price_per_sqft,
            price_per_unit, vendor_item_no, source_file, source_type, source_sheet, source_page,
            effective_date, expiration_date, is_current, status, needs_review, review_notes, notes,
            raw_row_json, created_at, updated_at
        )
        VALUES (
            :pricing_item_id, :vendor, :category, :product_name, :product_name_normalized, :description,
            :unit_price, :unit_of_measure, :package_size, :price_basis, :price_per_gallon, :price_per_sqft,
            :price_per_unit, :vendor_item_no, :source_file, :source_type, :source_sheet, :source_page,
            :effective_date, :expiration_date, :is_current, :status, :needs_review, :review_notes, :notes,
            CAST(:raw_row_json AS JSONB), NOW(), NOW()
        )
        ON CONFLICT (pricing_item_id) DO UPDATE SET
            vendor = EXCLUDED.vendor,
            category = EXCLUDED.category,
            product_name = EXCLUDED.product_name,
            product_name_normalized = EXCLUDED.product_name_normalized,
            description = EXCLUDED.description,
            unit_price = EXCLUDED.unit_price,
            unit_of_measure = EXCLUDED.unit_of_measure,
            package_size = EXCLUDED.package_size,
            price_basis = EXCLUDED.price_basis,
            price_per_gallon = EXCLUDED.price_per_gallon,
            price_per_sqft = EXCLUDED.price_per_sqft,
            price_per_unit = EXCLUDED.price_per_unit,
            vendor_item_no = EXCLUDED.vendor_item_no,
            source_file = EXCLUDED.source_file,
            source_type = EXCLUDED.source_type,
            source_sheet = EXCLUDED.source_sheet,
            source_page = EXCLUDED.source_page,
            effective_date = EXCLUDED.effective_date,
            expiration_date = EXCLUDED.expiration_date,
            is_current = EXCLUDED.is_current,
            status = EXCLUDED.status,
            needs_review = EXCLUDED.needs_review,
            notes = EXCLUDED.notes,
            raw_row_json = EXCLUDED.raw_row_json,
            updated_at = NOW()
        """
    )
    conn.execute(statement, rows)
    inserted = sum(1 for row in rows if row["pricing_item_id"] not in existing)
    updated = len(rows) - inserted
    return inserted, updated


def upsert_source_file(conn: Connection, path: Path, rows: list[dict[str, Any]], loaded_at: datetime, *, vendor: str | None = None, effective_date: str | None = None) -> None:
    metadata = {
        "pricing_item_ids": [row["pricing_item_id"] for row in rows],
        "source_path": str(path),
    }
    conn.execute(
        text(
            """
            INSERT INTO pricing_source_files (
                source_file_id, file_name, source_type, vendor, effective_date, loaded_at, row_count, metadata_json
            )
            VALUES (
                :source_file_id, :file_name, :source_type, :vendor, :effective_date, :loaded_at, :row_count,
                CAST(:metadata_json AS JSONB)
            )
            ON CONFLICT (source_file_id) DO UPDATE SET
                file_name = EXCLUDED.file_name,
                source_type = EXCLUDED.source_type,
                vendor = EXCLUDED.vendor,
                effective_date = EXCLUDED.effective_date,
                loaded_at = EXCLUDED.loaded_at,
                row_count = EXCLUDED.row_count,
                metadata_json = EXCLUDED.metadata_json
            """
        ),
        {
            "source_file_id": source_file_id(path),
            "file_name": path.name,
            "source_type": path.suffix.lower().lstrip("."),
            "vendor": vendor,
            "effective_date": safe_date(effective_date),
            "loaded_at": loaded_at,
            "row_count": len(rows),
            "metadata_json": json.dumps(metadata, default=str),
        },
    )


def load_pricing(
    engine: Engine,
    paths: list[Path],
    *,
    vendor: str | None = None,
    category: str | None = None,
    source_file: str | None = None,
    effective_date: str | None = None,
    mark_current: bool = False,
) -> PricingLoadResult:
    result = PricingLoadResult()
    result.pdf_files_skipped = []
    loaded_at = datetime.now(timezone.utc)
    with engine.begin() as conn:
        ensure_pricing_tables(conn)
        for path in paths:
            is_pdf = path.suffix.lower() == ".pdf"
            if is_pdf:
                result.pdf_files_discovered += 1
                result.pdf_pages_read += pdf_page_count(path)
            try:
                rows, skipped = load_input_rows(
                    path,
                    vendor=vendor,
                    category=category,
                    source_file=source_file,
                    effective_date=effective_date,
                    mark_current=mark_current,
                )
            except Exception as exc:
                if is_pdf:
                    result.pdf_files_skipped.append(f"{path.name}: {exc}")
                    continue
                raise
            result.rows_read += len(rows) + skipped
            result.rows_skipped += skipped
            result.rows_needing_review += sum(1 for row in rows if row.get("needs_review"))
            inserted, updated = upsert_pricing_rows(conn, rows)
            result.rows_inserted += inserted
            result.rows_updated += updated
            if is_pdf:
                result.pdf_files_parsed += 1
                result.pdf_rows_extracted += len(rows) + skipped
                result.pdf_rows_loaded += inserted + updated
                result.pdf_rows_needing_review += sum(1 for row in rows if row.get("needs_review"))
                if not rows:
                    result.pdf_files_skipped.append(f"{path.name}: no pricing rows extracted")
            upsert_source_file(conn, path, rows, loaded_at, vendor=vendor, effective_date=effective_date)
    return result


def current_pricing_rows(conn: Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        text(
            """
            SELECT
                pricing_item_id,
                vendor,
                category,
                product_name,
                description,
                unit_price,
                unit_of_measure,
                package_size,
                price_basis,
                price_per_gallon,
                price_per_sqft,
                price_per_unit,
                effective_date,
                status,
                is_current,
                needs_review,
                source_file,
                notes
            FROM pricing_catalog
            WHERE COALESCE(is_current, false) IS TRUE
            ORDER BY vendor NULLS LAST, category NULLS LAST, product_name
            """
        )
    ).mappings().all()
    return [dict(row) for row in rows]


def write_pricing_export(rows: list[dict[str, Any]], out_path: Path) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=PRICING_EXPORT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def export_current_pricing_catalog(engine: Engine, out_path: Path) -> int:
    with engine.begin() as conn:
        ensure_pricing_tables(conn)
        rows = current_pricing_rows(conn)
    return write_pricing_export(rows, out_path)


def database_url_from_env(value: str | None) -> str:
    if value:
        return value
    env_value = os.getenv("DATABASE_URL") or os.getenv("NEON_DATABASE_URL")
    if not env_value:
        raise SystemExit("Set --database-url, DATABASE_URL, or NEON_DATABASE_URL.")
    return env_value


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Load pricing CSV/XLSX/PDF rows into pricing_catalog.")
    parser.add_argument("--input", type=Path, help="Pricing CSV/XLSX/PDF file to load.")
    parser.add_argument("--input-dir", type=Path, help="Folder of pricing CSV/XLSX/PDF files to load.")
    parser.add_argument("--export-current", action="store_true", help="Export current pricing_catalog rows to --out.")
    parser.add_argument("--out", type=Path, help="Output CSV path for --export-current.")
    parser.add_argument("--database-url", help="Postgres/Neon database URL.")
    parser.add_argument("--mark-current", action="store_true", help="Mark loaded rows as current. Does not demote unrelated rows.")
    parser.add_argument("--vendor", help="Override vendor for loaded rows.")
    parser.add_argument("--category", help="Override category for loaded rows.")
    parser.add_argument("--source-file", help="Override source_file for loaded rows.")
    parser.add_argument("--effective-date", help="Override effective date for loaded rows.")
    args = parser.parse_args()

    database_url = database_url_from_env(args.database_url)
    engine = create_resilient_engine(database_url)

    if args.export_current:
        if not args.out:
            raise SystemExit("Set --out when using --export-current.")
        count = export_current_pricing_catalog(engine, args.out)
        print(f"Current pricing rows exported: {count}")
        print(f"Wrote: {args.out}")
        print("Source files are read-only; no source pricing files were modified.")
        return

    paths = input_paths(args.input, args.input_dir)
    if not paths:
        raise SystemExit("Provide --input or --input-dir with at least one CSV/XLSX/PDF file, or use --export-current --out.")

    result = load_pricing(
        engine,
        paths,
        vendor=args.vendor,
        category=args.category,
        source_file=args.source_file,
        effective_date=args.effective_date,
        mark_current=args.mark_current,
    )
    print(f"Rows read: {result.rows_read}")
    print(f"Rows inserted: {result.rows_inserted}")
    print(f"Rows updated: {result.rows_updated}")
    print(f"Rows skipped: {result.rows_skipped}")
    print(f"Rows needing review: {result.rows_needing_review}")
    print(f"PDF files discovered: {result.pdf_files_discovered}")
    print(f"PDF files parsed: {result.pdf_files_parsed}")
    print(f"PDF pages read: {result.pdf_pages_read}")
    print(f"PDF rows extracted: {result.pdf_rows_extracted}")
    print(f"PDF rows loaded: {result.pdf_rows_loaded}")
    print(f"PDF rows needing review: {result.pdf_rows_needing_review}")
    if result.pdf_files_skipped:
        print("PDF files skipped with reason:")
        for reason in result.pdf_files_skipped:
            print(f"  {reason}")
    print("Source files are read-only; no source pricing files were modified.")


if __name__ == "__main__":
    main()
