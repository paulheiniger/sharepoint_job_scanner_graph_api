from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

from sqlalchemy import text

from jobscan.db_connections import create_resilient_engine
from jobscan.env import load_project_env

from .product_catalog import ProductKnowledge, load_product_catalog_json


def _json_param(value: Any) -> str:
    if value is None or value == "":
        return "[]"
    if isinstance(value, str):
        try:
            json.loads(value)
            return value
        except Exception:
            return json.dumps([value])
    return json.dumps(value, default=str)


def _none_if_blank(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return value


def _date_or_none(value: Any) -> str | None:
    text_value = str(value or "").strip()
    if not text_value:
        return None
    if re.match(r"^\d{4}-\d{2}-\d{2}$", text_value):
        return text_value
    if re.match(r"^\d{1,2}/\d{1,2}/\d{2,4}$", text_value):
        return text_value
    return None


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except Exception:
        return None


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except Exception:
        return None


def _bool_value(value: Any, default: bool = True) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "n"}


def _schema_statements(sql: str) -> list[str]:
    statements: list[str] = []
    current: list[str] = []
    for line in sql.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        current.append(line)
        if stripped.endswith(";"):
            statement = "\n".join(current).strip().rstrip(";")
            if statement:
                statements.append(statement)
            current = []
    tail = "\n".join(current).strip()
    if tail:
        statements.append(tail)
    return statements


def apply_product_knowledge_schema(db_url: str, schema_path: str | Path = "db/product_knowledge_schema.sql") -> int:
    path = Path(schema_path)
    if not path.exists():
        raise FileNotFoundError(f"Product knowledge schema not found: {path}")
    engine = create_resilient_engine(db_url)
    statements = _schema_statements(path.read_text(encoding="utf-8"))
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))
    return len(statements)


def _product_params(row: dict[str, Any]) -> dict[str, Any]:
    product_id = str(row.get("product_id") or "").strip()
    product_name = str(row.get("product_name") or row.get("product_family") or product_id or "Unnamed Product").strip()
    return {
        "product_id": product_id,
        "manufacturer": _none_if_blank(row.get("manufacturer")),
        "product_family": _none_if_blank(row.get("product_family")),
        "product_name": product_name,
        "sku": _none_if_blank(row.get("sku") or row.get("sku_or_model")),
        "category": _none_if_blank(row.get("category")),
        "subcategory": _none_if_blank(row.get("subcategory")),
        "unit": _none_if_blank(row.get("unit")),
        "aliases": _json_param(row.get("aliases") or []),
        "active": _bool_value(row.get("active"), True),
        "extraction_method": _none_if_blank(row.get("extraction_method")),
        "extraction_warnings": _json_param(row.get("extraction_warnings") or []),
    }


def _alias_params(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "alias_id": str(row.get("alias_id") or "").strip(),
        "product_id": _none_if_blank(row.get("product_id")),
        "alias": str(row.get("alias") or "").strip(),
        "alias_type": _none_if_blank(row.get("alias_type")),
        "confidence": _float_or_none(row.get("confidence")),
    }


def _document_params(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "document_id": str(row.get("document_id") or "").strip(),
        "product_id": _none_if_blank(row.get("product_id")),
        "document_type": _none_if_blank(row.get("document_type")),
        "source_type": _none_if_blank(row.get("source_type")),
        "source_path": _none_if_blank(row.get("source_path")),
        "revision_date": _date_or_none(row.get("revision_date")),
        "raw_text_hash": _none_if_blank(row.get("raw_text_hash")),
        "extraction_method": _none_if_blank(row.get("extraction_method")),
        "extraction_warnings": _json_param(row.get("extraction_warnings") or []),
    }


def _property_params(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "property_id": str(row.get("property_id") or "").strip(),
        "product_id": _none_if_blank(row.get("product_id")),
        "document_id": _none_if_blank(row.get("document_id")),
        "property_name": str(row.get("property_name") or "").strip(),
        "property_value": _none_if_blank(row.get("property_value")),
        "numeric_value": _float_or_none(row.get("numeric_value")),
        "numeric_min": _float_or_none(row.get("numeric_min")),
        "numeric_max": _float_or_none(row.get("numeric_max")),
        "unit": _none_if_blank(row.get("unit")),
        "source_page": _int_or_none(row.get("source_page")),
        "source_text": _none_if_blank(row.get("source_text")),
        "confidence": _float_or_none(row.get("confidence")),
    }


def _rule_params(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "rule_id": str(row.get("rule_id") or "").strip(),
        "product_id": _none_if_blank(row.get("product_id")),
        "document_id": _none_if_blank(row.get("document_id")),
        "rule_type": str(row.get("rule_type") or "").strip(),
        "rule_value": _none_if_blank(row.get("rule_value")),
        "source_page": _int_or_none(row.get("source_page")),
        "source_text": _none_if_blank(row.get("source_text")),
        "confidence": _float_or_none(row.get("confidence")),
        "severity": _none_if_blank(row.get("severity")),
    }


def _link_params(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "link_id": str(row.get("link_id") or "").strip(),
        "product_id": _none_if_blank(row.get("product_id")),
        "decision_id": str(row.get("decision_id") or "").strip(),
        "influence_type": _none_if_blank(row.get("influence_type")),
        "confidence": _float_or_none(row.get("confidence")),
        "reason": _none_if_blank(row.get("reason")),
    }


def _valid(rows: list[dict[str, Any]], id_field: str, required: list[str] | None = None) -> list[dict[str, Any]]:
    required = required or []
    clean: list[dict[str, Any]] = []
    for row in rows or []:
        if not str(row.get(id_field) or "").strip():
            continue
        if any(not str(row.get(field) or "").strip() for field in required):
            continue
        clean.append(row)
    return clean


def upsert_product_knowledge(
    db_url: str,
    knowledge: ProductKnowledge,
    *,
    catalog_path: str | Path | None = None,
    update_queue: bool = True,
) -> dict[str, int]:
    """Publish product knowledge JSON into the normalized product tables.

    The Estimating Assistant already reads these tables through
    ``load_estimator_data_from_database``; this function is the missing bridge
    from local PDF ingestion output to the workbench.
    """

    engine = create_resilient_engine(db_url)
    counts = {
        "product_catalog": 0,
        "product_aliases": 0,
        "product_documents": 0,
        "product_properties": 0,
        "product_rules": 0,
        "product_decision_links": 0,
        "product_document_queue": 0,
    }
    catalog_path_text = str(catalog_path) if catalog_path else None

    with engine.begin() as connection:
        for row in _valid(knowledge.product_catalog, "product_id"):
            params = _product_params(row)
            connection.execute(
                text(
                    """
                    INSERT INTO product_catalog (
                        product_id, manufacturer, product_family, product_name, sku,
                        category, subcategory, unit, aliases, active,
                        extraction_method, extraction_warnings
                    )
                    VALUES (
                        :product_id, :manufacturer, :product_family, :product_name, :sku,
                        :category, :subcategory, :unit, CAST(:aliases AS JSONB), :active,
                        :extraction_method, CAST(:extraction_warnings AS JSONB)
                    )
                    ON CONFLICT (product_id) DO UPDATE SET
                        manufacturer = EXCLUDED.manufacturer,
                        product_family = EXCLUDED.product_family,
                        product_name = EXCLUDED.product_name,
                        sku = EXCLUDED.sku,
                        category = EXCLUDED.category,
                        subcategory = EXCLUDED.subcategory,
                        unit = EXCLUDED.unit,
                        aliases = EXCLUDED.aliases,
                        active = EXCLUDED.active,
                        extraction_method = EXCLUDED.extraction_method,
                        extraction_warnings = EXCLUDED.extraction_warnings
                    """
                ),
                params,
            )
            counts["product_catalog"] += 1

        for row in _valid(knowledge.product_aliases, "alias_id", ["product_id", "alias"]):
            connection.execute(
                text(
                    """
                    INSERT INTO product_aliases (alias_id, product_id, alias, alias_type, confidence)
                    VALUES (:alias_id, :product_id, :alias, :alias_type, :confidence)
                    ON CONFLICT (alias_id) DO UPDATE SET
                        product_id = EXCLUDED.product_id,
                        alias = EXCLUDED.alias,
                        alias_type = EXCLUDED.alias_type,
                        confidence = EXCLUDED.confidence
                    """
                ),
                _alias_params(row),
            )
            counts["product_aliases"] += 1

        for row in _valid(knowledge.product_documents, "document_id"):
            params = _document_params(row)
            connection.execute(
                text(
                    """
                    INSERT INTO product_documents (
                        document_id, product_id, document_type, source_type, source_path,
                        revision_date, raw_text_hash, extraction_method, extraction_warnings
                    )
                    VALUES (
                        :document_id, :product_id, :document_type, :source_type, :source_path,
                        :revision_date, :raw_text_hash, :extraction_method,
                        CAST(:extraction_warnings AS JSONB)
                    )
                    ON CONFLICT (document_id) DO UPDATE SET
                        product_id = EXCLUDED.product_id,
                        document_type = EXCLUDED.document_type,
                        source_type = EXCLUDED.source_type,
                        source_path = EXCLUDED.source_path,
                        revision_date = EXCLUDED.revision_date,
                        raw_text_hash = EXCLUDED.raw_text_hash,
                        extraction_method = EXCLUDED.extraction_method,
                        extraction_warnings = EXCLUDED.extraction_warnings
                    """
                ),
                params,
            )
            counts["product_documents"] += 1

        for row in _valid(knowledge.product_properties, "property_id", ["product_id", "property_name"]):
            connection.execute(
                text(
                    """
                    INSERT INTO product_properties (
                        property_id, product_id, document_id, property_name, property_value,
                        numeric_value, numeric_min, numeric_max, unit, source_page,
                        source_text, confidence
                    )
                    VALUES (
                        :property_id, :product_id, :document_id, :property_name, :property_value,
                        :numeric_value, :numeric_min, :numeric_max, :unit, :source_page,
                        :source_text, :confidence
                    )
                    ON CONFLICT (property_id) DO UPDATE SET
                        product_id = EXCLUDED.product_id,
                        document_id = EXCLUDED.document_id,
                        property_name = EXCLUDED.property_name,
                        property_value = EXCLUDED.property_value,
                        numeric_value = EXCLUDED.numeric_value,
                        numeric_min = EXCLUDED.numeric_min,
                        numeric_max = EXCLUDED.numeric_max,
                        unit = EXCLUDED.unit,
                        source_page = EXCLUDED.source_page,
                        source_text = EXCLUDED.source_text,
                        confidence = EXCLUDED.confidence
                    """
                ),
                _property_params(row),
            )
            counts["product_properties"] += 1

        for row in _valid(knowledge.product_rules, "rule_id", ["product_id", "rule_type"]):
            connection.execute(
                text(
                    """
                    INSERT INTO product_rules (
                        rule_id, product_id, document_id, rule_type, rule_value,
                        source_page, source_text, confidence, severity
                    )
                    VALUES (
                        :rule_id, :product_id, :document_id, :rule_type, :rule_value,
                        :source_page, :source_text, :confidence, :severity
                    )
                    ON CONFLICT (rule_id) DO UPDATE SET
                        product_id = EXCLUDED.product_id,
                        document_id = EXCLUDED.document_id,
                        rule_type = EXCLUDED.rule_type,
                        rule_value = EXCLUDED.rule_value,
                        source_page = EXCLUDED.source_page,
                        source_text = EXCLUDED.source_text,
                        confidence = EXCLUDED.confidence,
                        severity = EXCLUDED.severity
                    """
                ),
                _rule_params(row),
            )
            counts["product_rules"] += 1

        for row in _valid(knowledge.product_decision_links, "link_id", ["product_id", "decision_id"]):
            connection.execute(
                text(
                    """
                    INSERT INTO product_decision_links (
                        link_id, product_id, decision_id, influence_type, confidence, reason
                    )
                    VALUES (:link_id, :product_id, :decision_id, :influence_type, :confidence, :reason)
                    ON CONFLICT (link_id) DO UPDATE SET
                        product_id = EXCLUDED.product_id,
                        decision_id = EXCLUDED.decision_id,
                        influence_type = EXCLUDED.influence_type,
                        confidence = EXCLUDED.confidence,
                        reason = EXCLUDED.reason
                    """
                ),
                _link_params(row),
            )
            counts["product_decision_links"] += 1

        if update_queue:
            for row in knowledge.product_documents:
                source_path = str(row.get("source_path") or "").strip()
                if not source_path:
                    continue
                result = connection.execute(
                    text(
                        """
                        UPDATE product_document_queue
                        SET product_id = :product_id,
                            catalog_path = COALESCE(:catalog_path, catalog_path),
                            ingest_status = 'catalog_loaded',
                            last_checked_at = now(),
                            validation_warnings = CAST(:extraction_warnings AS JSONB)
                        WHERE source_path = :source_path
                        """
                    ),
                    {
                        "product_id": row.get("product_id") or None,
                        "catalog_path": catalog_path_text,
                        "extraction_warnings": _json_param(row.get("extraction_warnings") or []),
                        "source_path": source_path,
                    },
                )
                counts["product_document_queue"] += int(result.rowcount or 0)

    return counts


def import_product_catalog_json(
    catalog_path: str | Path,
    db_url: str,
    *,
    update_queue: bool = True,
) -> dict[str, int]:
    knowledge = load_product_catalog_json(catalog_path)
    return upsert_product_knowledge(db_url, knowledge, catalog_path=catalog_path, update_queue=update_queue)


def main(argv: list[str] | None = None) -> int:
    load_project_env()
    parser = argparse.ArgumentParser(description="Publish product catalog JSON into Neon product knowledge tables.")
    parser.add_argument("--catalog", required=True, help="Input product catalog JSON from jobscan.products.ingest.")
    parser.add_argument("--db-url", default=os.getenv("NEON_DATABASE_URL") or os.getenv("DATABASE_URL"), help="Database URL.")
    parser.add_argument("--apply-schema", action="store_true", help="Apply db/product_knowledge_schema.sql before importing.")
    parser.add_argument("--schema", default="db/product_knowledge_schema.sql", help="Schema SQL to use with --apply-schema.")
    parser.add_argument("--skip-queue-update", action="store_true", help="Do not mark matching product_document_queue rows loaded.")
    args = parser.parse_args(argv)

    if not args.db_url:
        raise SystemExit("--db-url is required or NEON_DATABASE_URL/DATABASE_URL must be set")
    if args.apply_schema:
        statement_count = apply_product_knowledge_schema(args.db_url, args.schema)
        print(f"Applied product knowledge schema ({statement_count} statements).")
    counts = import_product_catalog_json(args.catalog, args.db_url, update_queue=not args.skip_queue_update)
    print("Published product catalog to database:")
    for key, value in counts.items():
        print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
