from __future__ import annotations

import argparse
import csv
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text

from .product_catalog import slugify

DOCUMENT_EXTENSIONS = {".pdf", ".txt", ".md"}


def _document_type_from_name(path: Path, default: str = "PDS") -> str:
    text_value = path.name.lower()
    if "sds" in text_value or "safety" in text_value:
        return "SDS"
    if "application" in text_value or "guide" in text_value:
        return "application_guide"
    if "pds" in text_value or "data" in text_value:
        return "PDS"
    return default


def discover_product_documents(
    root: str | Path,
    *,
    manufacturer_hint: str | None = None,
    document_type: str = "PDS",
) -> list[dict[str, Any]]:
    root_path = Path(root)
    rows: list[dict[str, Any]] = []
    for path in sorted(root_path.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in DOCUMENT_EXTENSIONS:
            continue
        resolved_type = _document_type_from_name(path, document_type)
        rows.append(
            {
                "queue_id": slugify(f"{path.resolve()}"),
                "source_path": str(path),
                "source_type": "local_file",
                "manufacturer_hint": manufacturer_hint or "",
                "document_type": resolved_type,
                "discovered_at": datetime.now(UTC).isoformat(),
                "ingest_status": "pending",
                "product_id": "",
                "catalog_path": "",
                "validation_warnings": [],
                "notes": "Discovered from local product document folder.",
            }
        )
    return rows


def write_queue_csv(rows: list[dict[str, Any]], out: str | Path) -> Path:
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "queue_id",
        "source_path",
        "source_type",
        "manufacturer_hint",
        "document_type",
        "discovered_at",
        "ingest_status",
        "product_id",
        "catalog_path",
        "validation_warnings",
        "notes",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            out_row = dict(row)
            out_row["validation_warnings"] = json.dumps(out_row.get("validation_warnings") or [])
            writer.writerow({column: out_row.get(column, "") for column in columns})
    return path


def upsert_document_queue(db_url: str, rows: list[dict[str, Any]]) -> int:
    engine = create_engine(db_url, future=True)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS product_document_queue (
                    queue_id TEXT PRIMARY KEY,
                    source_path TEXT NOT NULL UNIQUE,
                    source_type TEXT DEFAULT 'local_file',
                    manufacturer_hint TEXT,
                    document_type TEXT,
                    discovered_at TIMESTAMPTZ DEFAULT now(),
                    ingest_status TEXT DEFAULT 'pending',
                    product_id TEXT,
                    catalog_path TEXT,
                    validation_warnings JSONB DEFAULT '[]'::jsonb,
                    notes TEXT
                )
                """
            )
        )
        for row in rows:
            connection.execute(
                text(
                    """
                    INSERT INTO product_document_queue (
                        queue_id, source_path, source_type, manufacturer_hint, document_type,
                        discovered_at, ingest_status, product_id, catalog_path, validation_warnings, notes
                    )
                    VALUES (
                        :queue_id, :source_path, :source_type, :manufacturer_hint, :document_type,
                        :discovered_at, :ingest_status, :product_id, :catalog_path,
                        CAST(:validation_warnings AS JSONB), :notes
                    )
                    ON CONFLICT (source_path) DO UPDATE SET
                        manufacturer_hint = EXCLUDED.manufacturer_hint,
                        document_type = EXCLUDED.document_type,
                        ingest_status = COALESCE(product_document_queue.ingest_status, EXCLUDED.ingest_status),
                        validation_warnings = EXCLUDED.validation_warnings,
                        notes = EXCLUDED.notes
                    """
                ),
                {
                    **row,
                    "product_id": row.get("product_id") or None,
                    "catalog_path": row.get("catalog_path") or None,
                    "validation_warnings": json.dumps(row.get("validation_warnings") or []),
                },
            )
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a manual product document discovery queue from local files.")
    parser.add_argument("--pdf-dir", required=True, help="Directory containing local product PDFs/text docs.")
    parser.add_argument("--out", default="output/product_document_queue.csv", help="CSV output path.")
    parser.add_argument("--manufacturer-hint", default="", help="Optional manufacturer hint for queued documents.")
    parser.add_argument("--document-type", default="PDS", help="Default document type when filename does not identify one.")
    parser.add_argument("--db-url", default="", help="Optional database URL for upserting product_document_queue.")
    parser.add_argument("--write-db", action="store_true", help="Upsert discovered queue rows to the database.")
    args = parser.parse_args(argv)

    rows = discover_product_documents(
        args.pdf_dir,
        manufacturer_hint=args.manufacturer_hint or None,
        document_type=args.document_type,
    )
    out = write_queue_csv(rows, args.out)
    if args.write_db:
        if not args.db_url:
            raise SystemExit("--write-db requires --db-url")
        upsert_document_queue(args.db_url, rows)
    print(f"Wrote product document queue: {out} ({len(rows)} documents)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
