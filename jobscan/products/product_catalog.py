from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd


CATALOG_KEYS = (
    "product_catalog",
    "product_aliases",
    "product_documents",
    "product_properties",
    "product_rules",
    "product_decision_links",
)


@dataclass
class ProductKnowledge:
    product_catalog: list[dict[str, Any]] = field(default_factory=list)
    product_aliases: list[dict[str, Any]] = field(default_factory=list)
    product_documents: list[dict[str, Any]] = field(default_factory=list)
    product_properties: list[dict[str, Any]] = field(default_factory=list)
    product_rules: list[dict[str, Any]] = field(default_factory=list)
    product_decision_links: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).replace("\xa0", " ").strip().split())


def slugify(value: Any, fallback: str = "product") -> str:
    text = clean_text(value).lower()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text or fallback


def normalize_product_name(value: Any) -> str:
    text = clean_text(value).lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    stop_words = {"the", "and", "with", "for", "product", "data", "sheet", "safety"}
    return " ".join(part for part in text.split() if part not in stop_words)


def product_id_for(manufacturer: str | None, product_name: str, sku: str | None = None) -> str:
    parts = [manufacturer or "", sku or "", product_name]
    return slugify("_".join(part for part in parts if clean_text(part)), "product")


def merge_product_knowledge(items: list[ProductKnowledge]) -> ProductKnowledge:
    merged = ProductKnowledge()
    seen_by_key: dict[str, set[str]] = {key: set() for key in CATALOG_KEYS}
    id_fields = {
        "product_catalog": "product_id",
        "product_aliases": "alias_id",
        "product_documents": "document_id",
        "product_properties": "property_id",
        "product_rules": "rule_id",
        "product_decision_links": "link_id",
    }
    for item in items:
        payload = item.to_dict()
        for key in CATALOG_KEYS:
            target = getattr(merged, key)
            id_field = id_fields[key]
            for row in payload.get(key) or []:
                row_id = str(row.get(id_field) or row)
                if row_id in seen_by_key[key]:
                    continue
                seen_by_key[key].add(row_id)
                target.append(row)
    return merged


def write_product_catalog_json(knowledge: ProductKnowledge, out: str | Path) -> Path:
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(knowledge.to_dict(), indent=2, sort_keys=True, default=str), encoding="utf-8")
    return path


def load_product_catalog_json(path: str | Path) -> ProductKnowledge:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return ProductKnowledge(**{key: payload.get(key) or [] for key in CATALOG_KEYS})


def _frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(rows or [])


def export_product_catalog_xlsx(knowledge: ProductKnowledge, out: str | Path) -> Path:
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for sheet_name, rows in (
            ("Products", knowledge.product_catalog),
            ("Aliases", knowledge.product_aliases),
            ("Documents", knowledge.product_documents),
            ("Properties", knowledge.product_properties),
            ("Rules", knowledge.product_rules),
            ("Decision Links", knowledge.product_decision_links),
        ):
            _frame(rows).to_excel(writer, sheet_name=sheet_name[:31], index=False)
    return path
