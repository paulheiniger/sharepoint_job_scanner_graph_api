from __future__ import annotations

import argparse
import csv
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import create_engine, text

from .product_catalog import slugify

DOCUMENT_EXTENSIONS = {".pdf", ".txt", ".md"}
DEFAULT_APPROVED_DOMAINS = {
    "3m.com",
    "www.3m.com",
    "accufoam.com",
    "www.accufoam.com",
    "aldo-coatings.com",
    "www.aldo-coatings.com",
    "envergesprayfoam.com",
    "www.envergesprayfoam.com",
    "gaco.com",
    "www.gaco.com",
    "gaf.com",
    "www.gaf.com",
    "icynene.com",
    "www.icynene.com",
    "huntsmanbuildingsolutions.com",
    "www.huntsmanbuildingsolutions.com",
    "ncfi.com",
    "www.ncfi.com",
    "sescoproducts.com",
    "www.sescoproducts.com",
}


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
    decision_nodes: list[str] | None = None,
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
                "source_url": "",
                "source_type": "local_file",
                "source_domain": "",
                "domain_approved": True,
                "approved_for_ingest": True,
                "review_status": "approved",
                "discovery_method": "local_folder",
                "manufacturer_hint": manufacturer_hint or "",
                "document_type": resolved_type,
                "discovered_at": datetime.now(UTC).isoformat(),
                "ingest_status": "pending",
                "product_id": "",
                "catalog_path": "",
                "content_hash": "",
                "decision_nodes": decision_nodes or [],
                "lookup_ids": [],
                "source_page_url": "",
                "link_text": "",
                "scrape_score": 0.0,
                "priority": 100,
                "validation_warnings": [],
                "notes": "Discovered from local product document folder.",
            }
        )
    return rows


def source_domain(source_url: str) -> str:
    parsed = urlparse(source_url)
    return (parsed.netloc or "").lower().split("@")[-1].split(":")[0]


def is_approved_document_url(source_url: str, approved_domains: set[str] | list[str] | None = None) -> bool:
    domain = source_domain(source_url)
    if not domain:
        return False
    domains = {str(item).lower() for item in (approved_domains or DEFAULT_APPROVED_DOMAINS)}
    return domain in domains or any(domain.endswith(f".{approved}") for approved in domains)


def queue_product_document_url(
    source_url: str,
    *,
    manufacturer_hint: str | None = None,
    document_type: str = "PDS",
    approved_domains: set[str] | list[str] | None = None,
    decision_nodes: list[str] | None = None,
    notes: str = "",
    priority: int = 50,
) -> dict[str, Any]:
    """Create a reviewable queue row for a known product document URL.

    This intentionally does not fetch or scrape the URL. It only records enough
    metadata for a controlled review/ingest workflow.
    """

    domain = source_domain(source_url)
    approved = is_approved_document_url(source_url, approved_domains)
    return {
        "queue_id": slugify(source_url),
        "source_path": "",
        "source_url": source_url,
        "source_type": "approved_url" if approved else "url_needs_domain_review",
        "source_domain": domain,
        "domain_approved": approved,
        "approved_for_ingest": approved,
        "review_status": "approved" if approved else "needs_domain_approval",
        "discovery_method": "manual_url",
        "manufacturer_hint": manufacturer_hint or "",
        "document_type": document_type,
        "discovered_at": datetime.now(UTC).isoformat(),
        "ingest_status": "pending" if approved else "blocked_domain_review",
        "product_id": "",
        "catalog_path": "",
        "content_hash": "",
        "decision_nodes": decision_nodes or [],
        "lookup_ids": [],
        "source_page_url": "",
        "link_text": "",
        "scrape_score": 0.0,
        "priority": priority,
        "validation_warnings": [] if approved else [f"Domain not approved for product document ingestion: {domain}"],
        "notes": notes or "Manual product document URL queued for controlled review.",
    }


def write_queue_csv(rows: list[dict[str, Any]], out: str | Path) -> Path:
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "queue_id",
        "source_path",
        "source_url",
        "source_type",
        "source_domain",
        "domain_approved",
        "approved_for_ingest",
        "review_status",
        "discovery_method",
        "manufacturer_hint",
        "document_type",
        "discovered_at",
        "ingest_status",
        "product_id",
        "catalog_path",
        "content_hash",
        "decision_nodes",
        "lookup_ids",
        "source_page_url",
        "link_text",
        "scrape_score",
        "priority",
        "fetched_at",
        "last_checked_at",
        "validation_warnings",
        "notes",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            out_row = dict(row)
            out_row["validation_warnings"] = json.dumps(out_row.get("validation_warnings") or [])
            out_row["decision_nodes"] = json.dumps(out_row.get("decision_nodes") or [])
            out_row["lookup_ids"] = json.dumps(out_row.get("lookup_ids") or [])
            writer.writerow({column: out_row.get(column, "") for column in columns})
    return path


def upsert_document_queue(db_url: str, rows: list[dict[str, Any]]) -> int:
    engine = create_engine(db_url, future=True)
    dialect = str(getattr(engine.dialect, "name", "") or "")
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS product_document_queue (
                    queue_id TEXT PRIMARY KEY,
                    source_path TEXT,
                    source_url TEXT,
                    source_type TEXT DEFAULT 'local_file',
                    source_domain TEXT,
                    domain_approved BOOLEAN DEFAULT false,
                    approved_for_ingest BOOLEAN DEFAULT false,
                    review_status TEXT DEFAULT 'pending_review',
                    discovery_method TEXT DEFAULT 'manual',
                    manufacturer_hint TEXT,
                    document_type TEXT,
                    discovered_at TIMESTAMPTZ DEFAULT now(),
                    ingest_status TEXT DEFAULT 'pending',
                    product_id TEXT,
                    catalog_path TEXT,
                    content_hash TEXT,
                    decision_nodes JSONB DEFAULT '[]'::jsonb,
                    lookup_ids JSONB DEFAULT '[]'::jsonb,
                    source_page_url TEXT,
                    link_text TEXT,
                    scrape_score NUMERIC,
                    priority INTEGER DEFAULT 100,
                    fetched_at TIMESTAMPTZ,
                    last_checked_at TIMESTAMPTZ,
                    validation_warnings JSONB DEFAULT '[]'::jsonb,
                    notes TEXT,
                    UNIQUE(source_path),
                    UNIQUE(source_url)
                )
                """
            )
        )
        if dialect.startswith("postgres"):
            for statement in [
                "ALTER TABLE product_document_queue ALTER COLUMN source_path DROP NOT NULL",
                "ALTER TABLE product_document_queue ADD COLUMN IF NOT EXISTS source_url TEXT",
                "ALTER TABLE product_document_queue ADD COLUMN IF NOT EXISTS source_domain TEXT",
                "ALTER TABLE product_document_queue ADD COLUMN IF NOT EXISTS domain_approved BOOLEAN DEFAULT false",
                "ALTER TABLE product_document_queue ADD COLUMN IF NOT EXISTS approved_for_ingest BOOLEAN DEFAULT false",
                "ALTER TABLE product_document_queue ADD COLUMN IF NOT EXISTS review_status TEXT DEFAULT 'pending_review'",
                "ALTER TABLE product_document_queue ADD COLUMN IF NOT EXISTS discovery_method TEXT DEFAULT 'manual'",
                "ALTER TABLE product_document_queue ADD COLUMN IF NOT EXISTS content_hash TEXT",
                "ALTER TABLE product_document_queue ADD COLUMN IF NOT EXISTS decision_nodes JSONB DEFAULT '[]'::jsonb",
                "ALTER TABLE product_document_queue ADD COLUMN IF NOT EXISTS lookup_ids JSONB DEFAULT '[]'::jsonb",
                "ALTER TABLE product_document_queue ADD COLUMN IF NOT EXISTS source_page_url TEXT",
                "ALTER TABLE product_document_queue ADD COLUMN IF NOT EXISTS link_text TEXT",
                "ALTER TABLE product_document_queue ADD COLUMN IF NOT EXISTS scrape_score NUMERIC",
                "ALTER TABLE product_document_queue ADD COLUMN IF NOT EXISTS priority INTEGER DEFAULT 100",
                "ALTER TABLE product_document_queue ADD COLUMN IF NOT EXISTS fetched_at TIMESTAMPTZ",
                "ALTER TABLE product_document_queue ADD COLUMN IF NOT EXISTS last_checked_at TIMESTAMPTZ",
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_product_document_queue_source_url ON product_document_queue(source_url) WHERE source_url IS NOT NULL AND source_url <> ''",
            ]:
                connection.execute(text(statement))
        for row in rows:
            connection.execute(
                text(
                    """
                    INSERT INTO product_document_queue (
                        queue_id, source_path, source_url, source_type, source_domain,
                        domain_approved, approved_for_ingest, review_status, discovery_method,
                        manufacturer_hint, document_type, discovered_at, ingest_status,
                        product_id, catalog_path, content_hash, decision_nodes, lookup_ids,
                        source_page_url, link_text, scrape_score, priority, fetched_at,
                        last_checked_at,
                        validation_warnings, notes
                    )
                    VALUES (
                        :queue_id, :source_path, :source_url, :source_type, :source_domain,
                        :domain_approved, :approved_for_ingest, :review_status, :discovery_method,
                        :manufacturer_hint, :document_type, :discovered_at, :ingest_status,
                        :product_id, :catalog_path, :content_hash, CAST(:decision_nodes AS JSONB),
                        CAST(:lookup_ids AS JSONB), :source_page_url, :link_text,
                        :scrape_score, :priority, :fetched_at, :last_checked_at,
                        CAST(:validation_warnings AS JSONB), :notes
                    )
                    ON CONFLICT (queue_id) DO UPDATE SET
                        source_path = EXCLUDED.source_path,
                        source_url = EXCLUDED.source_url,
                        source_domain = EXCLUDED.source_domain,
                        domain_approved = EXCLUDED.domain_approved,
                        approved_for_ingest = EXCLUDED.approved_for_ingest,
                        review_status = EXCLUDED.review_status,
                        discovery_method = EXCLUDED.discovery_method,
                        manufacturer_hint = EXCLUDED.manufacturer_hint,
                        document_type = EXCLUDED.document_type,
                        ingest_status = COALESCE(product_document_queue.ingest_status, EXCLUDED.ingest_status),
                        content_hash = EXCLUDED.content_hash,
                        decision_nodes = EXCLUDED.decision_nodes,
                        lookup_ids = EXCLUDED.lookup_ids,
                        source_page_url = EXCLUDED.source_page_url,
                        link_text = EXCLUDED.link_text,
                        scrape_score = EXCLUDED.scrape_score,
                        priority = EXCLUDED.priority,
                        fetched_at = EXCLUDED.fetched_at,
                        last_checked_at = EXCLUDED.last_checked_at,
                        validation_warnings = EXCLUDED.validation_warnings,
                        notes = EXCLUDED.notes
                    """
                ),
                {
                    **row,
                    "source_path": row.get("source_path") or None,
                    "source_url": row.get("source_url") or None,
                    "product_id": row.get("product_id") or None,
                    "catalog_path": row.get("catalog_path") or None,
                    "content_hash": row.get("content_hash") or None,
                    "decision_nodes": json.dumps(row.get("decision_nodes") or []),
                    "lookup_ids": json.dumps(row.get("lookup_ids") or []),
                    "source_page_url": row.get("source_page_url") or None,
                    "link_text": row.get("link_text") or None,
                    "scrape_score": row.get("scrape_score") or None,
                    "priority": int(row.get("priority") or 100),
                    "fetched_at": row.get("fetched_at") or None,
                    "last_checked_at": row.get("last_checked_at") or None,
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
    parser.add_argument("--source-url", action="append", default=[], help="Known product document URL to add to the controlled queue. Can be repeated.")
    parser.add_argument("--approved-domain", action="append", default=[], help="Approved manufacturer domain for queued URLs. Can be repeated.")
    parser.add_argument("--decision-node", action="append", default=[], help="Decision graph node this document may inform. Can be repeated.")
    parser.add_argument("--db-url", default="", help="Optional database URL for upserting product_document_queue.")
    parser.add_argument("--write-db", action="store_true", help="Upsert discovered queue rows to the database.")
    args = parser.parse_args(argv)

    rows = discover_product_documents(
        args.pdf_dir,
        manufacturer_hint=args.manufacturer_hint or None,
        document_type=args.document_type,
        decision_nodes=args.decision_node or None,
    )
    approved_domains = set(args.approved_domain or DEFAULT_APPROVED_DOMAINS)
    for source_url in args.source_url or []:
        rows.append(
            queue_product_document_url(
                source_url,
                manufacturer_hint=args.manufacturer_hint or None,
                document_type=args.document_type,
                approved_domains=approved_domains,
                decision_nodes=args.decision_node or None,
            )
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
