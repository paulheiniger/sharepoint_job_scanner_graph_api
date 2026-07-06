from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text

from .document_queue import DEFAULT_APPROVED_DOMAINS, is_approved_document_url, queue_product_document_url, write_queue_csv
from .product_catalog import clean_text, slugify

DEFAULT_LOOKUP_PATH = Path(__file__).with_name("product_family_lookup_seed.csv")

LOOKUP_COLUMNS = [
    "lookup_id",
    "vendor",
    "canonical_product_family",
    "template_option",
    "product_type",
    "cell_type",
    "density_class",
    "application_hint",
    "lookup_priority",
    "lookup_terms",
    "preferred_documents",
    "document_priority",
    "official_vendor_url",
    "vendor_product_url",
    "source_domain",
    "domain_approved",
    "decision_nodes",
    "mapping_status",
    "alias_policy",
    "search_strategy",
    "document_collection_status",
    "knowledge_status",
    "replacement_for",
    "priority",
    "active",
    "notes",
]

LOOKUP_PRIORITY_TO_QUEUE_PRIORITY = {
    "high": 20,
    "medium": 50,
    "low": 80,
}


def _source_domain(source_url: str) -> str:
    from .document_queue import source_domain

    return source_domain(source_url)


def infer_decision_nodes(vendor: str, product_family: str, lookup_terms: str) -> list[str]:
    text_value = f"{vendor} {product_family} {lookup_terms}".lower()
    nodes: list[str] = []

    def add(node: str) -> None:
        if node not in nodes:
            nodes.append(node)

    if any(term in text_value for term in ("thermal barrier", "dc315", "dc 315", "no-burn", "ignition barrier")):
        add("insulation_thermal_barrier")
    if any(term in text_value for term in ("spray foam", "closed cell", "open cell", "insulation", "foam")):
        add("insulation_foam_system")
    if "roof foam" in text_value or "roofing foam" in text_value:
        add("roofing_coating_system")
    if any(term in text_value for term in ("silicone", "acrylic", "urethane", "polyurea", "roof coating", "hydrostop", "unisil", "base coat", "top coat")):
        add("roofing_coating_system")
    if "primer" in text_value:
        add("roofing_primer")
        add("insulation_primer")
    if any(term in text_value for term in ("mastic", "sealant", "seam", "flashing", "caulk", "aldo 399")):
        add("roofing_seam_treatment")
        add("roofing_caulk_detail")
        add("insulation_caulk_sealant")
    if "fabric" in text_value:
        add("roofing_fabric")
        add("roofing_seam_treatment")
    if "granule" in text_value:
        add("roofing_granules")
    if "cleaner" in text_value or "wash" in text_value:
        add("roofing_coating_system")
    return nodes


def _priority_from_lookup_priority(value: Any) -> int:
    text_value = clean_text(value).lower()
    return LOOKUP_PRIORITY_TO_QUEUE_PRIORITY.get(text_value, 50)


def normalize_lookup_row(row: dict[str, Any], *, approved_domains: set[str] | list[str] | None = None) -> dict[str, Any]:
    vendor = clean_text(row.get("vendor"))
    family = clean_text(row.get("canonical_product_family"))
    lookup_terms = clean_text(row.get("lookup_terms"))
    template_option = clean_text(row.get("template_option"))
    product_type = clean_text(row.get("product_type"))
    cell_type = clean_text(row.get("cell_type"))
    density_class = clean_text(row.get("density_class"))
    application_hint = clean_text(row.get("application_hint"))
    lookup_priority = clean_text(row.get("lookup_priority"))
    preferred_documents = clean_text(row.get("preferred_documents"))
    document_priority = clean_text(row.get("document_priority"))
    official_url = clean_text(row.get("official_vendor_url"))
    vendor_product_url = clean_text(row.get("vendor_product_url"))
    domain = _source_domain(official_url)
    nodes = row.get("decision_nodes")
    if isinstance(nodes, str):
        try:
            nodes = json.loads(nodes)
        except Exception:
            for separator in ("|", ";", ","):
                if separator in nodes:
                    nodes = [part.strip() for part in nodes.split(separator) if part.strip()]
                    break
            else:
                nodes = [nodes.strip()] if nodes.strip() else []
    if not nodes:
        node_text = " ".join(
            part
            for part in (lookup_terms, template_option, cell_type, density_class, application_hint, preferred_documents)
            if part
        )
        nodes = infer_decision_nodes(vendor, family, node_text)
    return {
        "lookup_id": clean_text(row.get("lookup_id")) or slugify(f"{vendor}_{family}", "product_family"),
        "vendor": vendor,
        "canonical_product_family": family,
        "template_option": template_option,
        "product_type": product_type,
        "cell_type": cell_type,
        "density_class": density_class,
        "application_hint": application_hint,
        "lookup_priority": lookup_priority,
        "lookup_terms": lookup_terms,
        "preferred_documents": preferred_documents,
        "document_priority": document_priority,
        "official_vendor_url": official_url,
        "vendor_product_url": vendor_product_url,
        "source_domain": domain,
        "domain_approved": is_approved_document_url(official_url, approved_domains or DEFAULT_APPROVED_DOMAINS),
        "decision_nodes": nodes,
        "mapping_status": clean_text(row.get("mapping_status")),
        "alias_policy": clean_text(row.get("alias_policy")),
        "search_strategy": clean_text(row.get("search_strategy")),
        "document_collection_status": clean_text(row.get("document_collection_status")),
        "knowledge_status": clean_text(row.get("knowledge_status")),
        "replacement_for": clean_text(row.get("replacement_for")),
        "priority": int(row.get("priority") or _priority_from_lookup_priority(lookup_priority)),
        "active": row.get("active", True) is not False and str(row.get("active", "true")).lower() not in {"false", "0", "no"},
        "notes": clean_text(row.get("notes")),
        "status": clean_text(row.get("status")),
    }


def load_product_family_lookup(
    path: str | Path = DEFAULT_LOOKUP_PATH,
    *,
    approved_domains: set[str] | list[str] | None = None,
) -> list[dict[str, Any]]:
    with Path(path).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return [normalize_lookup_row(row, approved_domains=approved_domains) for row in reader]


def write_product_family_lookup(rows: list[dict[str, Any]], out: str | Path) -> Path:
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=LOOKUP_COLUMNS)
        writer.writeheader()
        for row in rows:
            out_row = dict(row)
            out_row["decision_nodes"] = json.dumps(out_row.get("decision_nodes") or [])
            writer.writerow({column: out_row.get(column, "") for column in LOOKUP_COLUMNS})
    return path


def build_document_queue_from_lookup(
    rows: list[dict[str, Any]],
    *,
    approved_domains: set[str] | list[str] | None = None,
) -> list[dict[str, Any]]:
    """Create one controlled discovery queue row per official URL.

    Multiple families intentionally collapse to one URL row when a vendor only
    supplied a homepage. The detailed search terms remain in product_family_lookup.
    """

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if not row.get("active", True):
            continue
        url = clean_text(row.get("official_vendor_url"))
        if not url:
            continue
        grouped.setdefault(url, []).append(row)

    queue_rows: list[dict[str, Any]] = []
    for url, family_rows in sorted(grouped.items()):
        vendor = clean_text(family_rows[0].get("vendor"))
        nodes: list[str] = []
        for row in family_rows:
            for node in row.get("decision_nodes") or []:
                if node not in nodes:
                    nodes.append(node)
        ordered_family_rows = sorted(
            family_rows,
            key=lambda row: (int(row.get("priority") or 50), str(row.get("canonical_product_family") or "")),
        )
        family_labels = [
            (
                f"{row.get('canonical_product_family')}"
                + (f" [{row.get('template_option')}]" if row.get("template_option") else "")
                + f" (terms: {row.get('lookup_terms')}"
                + (f"; preferred docs: {row.get('preferred_documents')}" if row.get("preferred_documents") else "")
                + (f"; mapping: {row.get('mapping_status')}" if row.get("mapping_status") else "")
                + (f"; alias: {row.get('alias_policy')}" if row.get("alias_policy") else "")
                + ")"
            )
            for row in ordered_family_rows[:12]
        ]
        if len(ordered_family_rows) > 12:
            family_labels.append(f"...and {len(ordered_family_rows) - 12} more product families")
        queue_rows.append(
            queue_product_document_url(
                url,
                manufacturer_hint=vendor,
                document_type="product_family_page",
                approved_domains=approved_domains or DEFAULT_APPROVED_DOMAINS,
                decision_nodes=nodes,
                notes="Product-family seed. Search/download relevant PDS from this official vendor page for: "
                + "; ".join(family_labels),
                priority=min(int(row.get("priority") or 50) for row in family_rows),
            )
        )
        queue_rows[-1]["discovery_method"] = "product_family_lookup"
        queue_rows[-1]["lookup_ids"] = [row.get("lookup_id") for row in family_rows]
    return queue_rows


def upsert_product_family_lookup(db_url: str, rows: list[dict[str, Any]]) -> int:
    engine = create_engine(db_url, future=True)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS product_family_lookup (
                    lookup_id TEXT PRIMARY KEY,
                    vendor TEXT,
                    canonical_product_family TEXT,
                    template_option TEXT,
                    product_type TEXT,
                    cell_type TEXT,
                    density_class TEXT,
                    application_hint TEXT,
                    lookup_priority TEXT,
                    lookup_terms TEXT,
                    preferred_documents TEXT,
                    document_priority TEXT,
                    official_vendor_url TEXT,
                    vendor_product_url TEXT,
                    source_domain TEXT,
                    domain_approved BOOLEAN DEFAULT false,
                    decision_nodes JSONB DEFAULT '[]'::jsonb,
                    mapping_status TEXT,
                    alias_policy TEXT,
                    search_strategy TEXT,
                    document_collection_status TEXT,
                    knowledge_status TEXT,
                    replacement_for TEXT,
                    priority INTEGER DEFAULT 50,
                    active BOOLEAN DEFAULT true,
                    notes TEXT,
                    created_at TIMESTAMPTZ DEFAULT now(),
                    updated_at TIMESTAMPTZ DEFAULT now()
                )
                """
            )
        )
        for statement in [
            "ALTER TABLE product_family_lookup ADD COLUMN IF NOT EXISTS source_domain TEXT",
            "ALTER TABLE product_family_lookup ADD COLUMN IF NOT EXISTS template_option TEXT",
            "ALTER TABLE product_family_lookup ADD COLUMN IF NOT EXISTS product_type TEXT",
            "ALTER TABLE product_family_lookup ADD COLUMN IF NOT EXISTS cell_type TEXT",
            "ALTER TABLE product_family_lookup ADD COLUMN IF NOT EXISTS density_class TEXT",
            "ALTER TABLE product_family_lookup ADD COLUMN IF NOT EXISTS application_hint TEXT",
            "ALTER TABLE product_family_lookup ADD COLUMN IF NOT EXISTS lookup_priority TEXT",
            "ALTER TABLE product_family_lookup ADD COLUMN IF NOT EXISTS preferred_documents TEXT",
            "ALTER TABLE product_family_lookup ADD COLUMN IF NOT EXISTS document_priority TEXT",
            "ALTER TABLE product_family_lookup ADD COLUMN IF NOT EXISTS vendor_product_url TEXT",
            "ALTER TABLE product_family_lookup ADD COLUMN IF NOT EXISTS mapping_status TEXT",
            "ALTER TABLE product_family_lookup ADD COLUMN IF NOT EXISTS alias_policy TEXT",
            "ALTER TABLE product_family_lookup ADD COLUMN IF NOT EXISTS search_strategy TEXT",
            "ALTER TABLE product_family_lookup ADD COLUMN IF NOT EXISTS document_collection_status TEXT",
            "ALTER TABLE product_family_lookup ADD COLUMN IF NOT EXISTS knowledge_status TEXT",
            "ALTER TABLE product_family_lookup ADD COLUMN IF NOT EXISTS replacement_for TEXT",
            "ALTER TABLE product_family_lookup ADD COLUMN IF NOT EXISTS status TEXT",
            "ALTER TABLE product_family_lookup ADD COLUMN IF NOT EXISTS domain_approved BOOLEAN DEFAULT false",
            "ALTER TABLE product_family_lookup ADD COLUMN IF NOT EXISTS decision_nodes JSONB DEFAULT '[]'::jsonb",
            "ALTER TABLE product_family_lookup ADD COLUMN IF NOT EXISTS priority INTEGER DEFAULT 50",
            "ALTER TABLE product_family_lookup ADD COLUMN IF NOT EXISTS active BOOLEAN DEFAULT true",
            "ALTER TABLE product_family_lookup ADD COLUMN IF NOT EXISTS notes TEXT",
            "ALTER TABLE product_family_lookup ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now()",
            "CREATE INDEX IF NOT EXISTS idx_product_family_lookup_vendor ON product_family_lookup(vendor)",
            "CREATE INDEX IF NOT EXISTS idx_product_family_lookup_domain ON product_family_lookup(source_domain)",
        ]:
            connection.execute(text(statement))
        for row in rows:
            connection.execute(
                text(
                    """
                    INSERT INTO product_family_lookup (
                        lookup_id, vendor, canonical_product_family, template_option,
                        product_type, cell_type, density_class, application_hint, lookup_priority,
                        lookup_terms, preferred_documents, document_priority, official_vendor_url,
                        vendor_product_url,
                        source_domain, domain_approved, decision_nodes,
                        mapping_status, alias_policy, search_strategy,
                        document_collection_status, knowledge_status, replacement_for,
                        priority, active, notes, status, updated_at
                    )
                    VALUES (
                        :lookup_id, :vendor, :canonical_product_family, :template_option,
                        :product_type, :cell_type, :density_class, :application_hint, :lookup_priority,
                        :lookup_terms, :preferred_documents, :document_priority, :official_vendor_url,
                        :vendor_product_url,
                        :source_domain, :domain_approved,
                        CAST(:decision_nodes AS JSONB),
                        :mapping_status, :alias_policy, :search_strategy,
                        :document_collection_status, :knowledge_status, :replacement_for,
                        :priority, :active, :notes, :status, now()
                    )
                    ON CONFLICT (lookup_id) DO UPDATE SET
                        vendor = EXCLUDED.vendor,
                        canonical_product_family = EXCLUDED.canonical_product_family,
                        template_option = EXCLUDED.template_option,
                        product_type = EXCLUDED.product_type,
                        cell_type = EXCLUDED.cell_type,
                        density_class = EXCLUDED.density_class,
                        application_hint = EXCLUDED.application_hint,
                        lookup_priority = EXCLUDED.lookup_priority,
                        lookup_terms = EXCLUDED.lookup_terms,
                        preferred_documents = EXCLUDED.preferred_documents,
                        document_priority = EXCLUDED.document_priority,
                        official_vendor_url = EXCLUDED.official_vendor_url,
                        vendor_product_url = EXCLUDED.vendor_product_url,
                        source_domain = EXCLUDED.source_domain,
                        domain_approved = EXCLUDED.domain_approved,
                        decision_nodes = EXCLUDED.decision_nodes,
                        mapping_status = EXCLUDED.mapping_status,
                        alias_policy = EXCLUDED.alias_policy,
                        search_strategy = EXCLUDED.search_strategy,
                        document_collection_status = EXCLUDED.document_collection_status,
                        knowledge_status = EXCLUDED.knowledge_status,
                        replacement_for = EXCLUDED.replacement_for,
                        priority = EXCLUDED.priority,
                        active = EXCLUDED.active,
                        notes = EXCLUDED.notes,
                        status = EXCLUDED.status,
                        updated_at = now()
                    """
                ),
                {**row, "decision_nodes": json.dumps(row.get("decision_nodes") or [])},
            )
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage product-family lookup seeds for controlled document discovery.")
    parser.add_argument("--lookup", default=str(DEFAULT_LOOKUP_PATH), help="Input product family lookup CSV.")
    parser.add_argument("--out", default="", help="Optional normalized lookup CSV output path.")
    parser.add_argument("--queue-out", default="", help="Optional product document queue CSV output path.")
    parser.add_argument("--approved-domain", action="append", default=[], help="Approved vendor domain. Can be repeated.")
    parser.add_argument("--db-url", default="", help="Optional database URL.")
    parser.add_argument("--write-db", action="store_true", help="Upsert lookup rows to product_family_lookup.")
    parser.add_argument("--write-queue-db", action="store_true", help="Also upsert generated queue rows to product_document_queue.")
    args = parser.parse_args(argv)

    approved_domains = set(args.approved_domain or DEFAULT_APPROVED_DOMAINS)
    rows = load_product_family_lookup(args.lookup, approved_domains=approved_domains)
    if args.out:
        out = write_product_family_lookup(rows, args.out)
        print(f"Wrote normalized product family lookup: {out} ({len(rows)} rows)")
    queue_rows = []
    if args.queue_out or args.write_queue_db:
        queue_rows = build_document_queue_from_lookup(rows, approved_domains=approved_domains)
        if args.queue_out:
            queue_out = write_queue_csv(queue_rows, args.queue_out)
            print(f"Wrote product family document queue: {queue_out} ({len(queue_rows)} URLs)")
    if args.write_db or args.write_queue_db:
        if not args.db_url:
            raise SystemExit("--write-db/--write-queue-db requires --db-url")
        if args.write_db:
            upsert_product_family_lookup(args.db_url, rows)
        if args.write_queue_db:
            from .document_queue import upsert_document_queue

            upsert_document_queue(args.db_url, queue_rows)
    if not any([args.out, args.queue_out, args.write_db, args.write_queue_db]):
        print(f"Loaded product family lookup: {len(rows)} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
