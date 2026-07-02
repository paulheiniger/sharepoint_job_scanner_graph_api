from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import pandas as pd

from .ai_document_parser import is_bad_source_excerpt, is_suspicious_product_name
from .product_catalog import ProductKnowledge, load_product_catalog_json


def _rows(value: Any) -> list[dict[str, Any]]:
    return [row for row in (value or []) if isinstance(row, dict)]


def validate_product_catalog(knowledge: ProductKnowledge) -> dict[str, list[dict[str, Any]]]:
    warnings: list[dict[str, Any]] = []
    product_rows = _rows(knowledge.product_catalog)
    property_rows = _rows(knowledge.product_properties)
    rule_rows = _rows(knowledge.product_rules)
    link_rows = _rows(knowledge.product_decision_links)

    product_ids = [str(row.get("product_id") or "") for row in product_rows]
    duplicate_ids = {product_id for product_id, count in Counter(product_ids).items() if product_id and count > 1}
    for row in product_rows:
        product_id = row.get("product_id")
        product_name = row.get("product_name")
        if not product_name:
            warnings.append(
                {
                    "severity": "high",
                    "issue_type": "missing_product_name",
                    "product_id": product_id,
                    "message": "Product is missing product_name.",
                }
            )
        elif is_suspicious_product_name(product_name):
            warnings.append(
                {
                    "severity": "high",
                    "issue_type": "suspicious_product_name",
                    "product_id": product_id,
                    "product_name": product_name,
                    "message": "Product name looks like a website, phone number, footer, or contact header.",
                }
            )
        if product_id in duplicate_ids:
            warnings.append(
                {
                    "severity": "high",
                    "issue_type": "duplicated_product_id",
                    "product_id": product_id,
                    "message": "Duplicate product_id appears in product_catalog.",
                }
            )

    for row in [*property_rows, *rule_rows]:
        table = "product_rules" if "rule_type" in row else "product_properties"
        confidence = row.get("confidence")
        try:
            confidence_number = float(confidence)
        except Exception:
            confidence_number = 1.0
        if confidence_number < 0.5:
            warnings.append(
                {
                    "severity": "medium",
                    "issue_type": "low_confidence_fact",
                    "source_table": table,
                    "product_id": row.get("product_id"),
                    "field": row.get("rule_type") or row.get("property_name"),
                    "message": "Fact confidence is below 0.5.",
                }
            )
        source_text = row.get("source_text")
        if not source_text:
            warnings.append(
                {
                    "severity": "medium",
                    "issue_type": "missing_source_evidence",
                    "source_table": table,
                    "product_id": row.get("product_id"),
                    "field": row.get("rule_type") or row.get("property_name"),
                    "message": "Fact is missing source_text evidence.",
                }
            )
        elif is_bad_source_excerpt(source_text):
            warnings.append(
                {
                    "severity": "medium",
                    "issue_type": "section_heading_source_text",
                    "source_table": table,
                    "product_id": row.get("product_id"),
                    "field": row.get("rule_type") or row.get("property_name"),
                    "source_text": source_text,
                    "message": "Source evidence is only a section heading or generic label.",
                }
            )

    links_by_product: dict[str, set[str]] = defaultdict(set)
    for row in link_rows:
        links_by_product[str(row.get("product_id") or "")].add(str(row.get("decision_id") or ""))
    for product_id, decision_ids in links_by_product.items():
        cleaned = {decision_id for decision_id in decision_ids if decision_id}
        if len(cleaned) > 4:
            warnings.append(
                {
                    "severity": "medium",
                    "issue_type": "too_many_decision_links",
                    "product_id": product_id,
                    "decision_count": len(cleaned),
                    "decision_ids": ", ".join(sorted(cleaned)),
                    "message": "Product is linked to many decision nodes; review whether links are too broad.",
                }
            )

    summary = [
        {"metric": "products", "value": len(product_rows)},
        {"metric": "properties", "value": len(property_rows)},
        {"metric": "rules", "value": len(rule_rows)},
        {"metric": "decision_links", "value": len(link_rows)},
        {"metric": "warnings", "value": len(warnings)},
    ]
    return {
        "Summary": summary,
        "Validation Warnings": warnings or [{"severity": "ok", "issue_type": "", "message": "No validation warnings."}],
        "Products": product_rows,
        "Properties": property_rows,
        "Rules": rule_rows,
        "Decision Links": link_rows,
    }


def write_validation_workbook(report: dict[str, list[dict[str, Any]]], out: str | Path) -> Path:
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for sheet_name, rows in report.items():
            pd.DataFrame(rows or []).to_excel(writer, sheet_name=sheet_name[:31], index=False)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate product knowledge catalog quality.")
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    knowledge = load_product_catalog_json(args.catalog)
    report = validate_product_catalog(knowledge)
    path = write_validation_workbook(report, args.out)
    warning_count = sum(1 for row in report["Validation Warnings"] if row.get("severity") != "ok")
    print(f"Wrote product catalog validation workbook: {path} ({warning_count} warnings)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
