from __future__ import annotations

import json

import pandas as pd

from jobscan.estimator.schemas import EstimatorData
from jobscan.estimator.template_catalog_backfill import (
    build_template_catalog_backfill,
    load_template_intelligence_files,
    write_backfill_preview,
)


def _sample_intelligence_doc() -> dict:
    return {
        "template_type": "roofing",
        "template_name": "Roofing Template.xlsx",
        "selector_maps": [
            {
                "sheet_name": "Estimate",
                "row_number": 26,
                "formula_cell": "F26",
                "selector_cell": "A26",
                "template_bucket": "coating",
                "selector_code": "11",
                "resolved_item_name": "Gaco Silicone",
                "formula": "=IF(A26=11,\"Gaco\",\"\")",
            }
        ],
        "pricing_product_references": [
            {
                "source_type": "pricing_lookup",
                "source_table": "Pricing",
                "template_bucket": "coating",
                "row_number": 26,
                "selector_code": "11",
                "product_name": "Gaco S20 Silicone",
                "unit": "pail",
                "unit_price": 225,
            }
        ],
        "people_rate_table": [
            {
                "table_name": "people_daily_rate_selector",
                "selector_code": "3",
                "crew_size": 3,
                "daily_rate": 2160,
                "hours_per_day": 10,
            }
        ],
        "row_catalog": [
            {
                "sheet_name": "Estimate",
                "row_number": 26,
                "section": "Materials",
                "template_bucket": "coating",
                "line_item_kind": "material",
                "formula_model": "selector_lookup",
                "cell_roles": {"A": "selector_code", "F": "product"},
            },
            {
                "sheet_name": "Estimate",
                "row_number": 122,
                "section": "Labor",
                "template_bucket": "labor_base",
                "line_item_kind": "labor",
                "formula_model": "mixed_hours_or_daily",
                "cell_roles": {"B": "hours", "D": "days", "G": "daily_rate", "J": "hourly_rate"},
            },
        ],
    }


def test_backfill_builds_catalog_rows_from_template_intelligence_and_history() -> None:
    data = EstimatorData(
        template_rows=pd.DataFrame(
            [
                {
                    "job_id": "J-1",
                    "template_type": "roofing",
                    "row_number": 26,
                    "template_bucket": "coating",
                    "line_item_kind": "material",
                    "selected_item_name": "Historical Silicone",
                    "unit": float("nan"),
                    "selector_code": "12",
                    "unit_price": 210,
                },
                {
                    "job_id": "J-2",
                    "template_type": "roofing",
                    "row_number": 999,
                    "template_bucket": "unknown",
                    "line_item_kind": "material",
                    "selected_item_name": "Unresolved Item",
                },
            ]
        )
    )

    rows_by_table = build_template_catalog_backfill(intelligence_docs=[_sample_intelligence_doc()], data=data)

    assert len(rows_by_table["template_selector_maps"]) == 1
    assert {row["row_number"] for row in rows_by_table["template_row_catalog"]} == {26, 122}
    assert any(row["product_name"] == "Gaco S20 Silicone" for row in rows_by_table["template_product_options"])
    assert any(row["product_name"] == "Historical Silicone" for row in rows_by_table["template_product_options"])
    assert not any(row["product_name"] == "Unresolved Item" for row in rows_by_table["template_product_options"])

    labor_options = rows_by_table["template_labor_options"]
    assert len(labor_options) == 1
    assert labor_options[0]["row_number"] == 122
    assert labor_options[0]["labor_package"] == "labor_base"
    assert json.loads(labor_options[0]["source_values_json"])["crew_size"] == 3
    historical_option = next(
        row for row in rows_by_table["template_product_options"] if row["product_name"] == "Historical Silicone"
    )
    assert json.loads(historical_option["source_values_json"])["unit"] is None


def test_backfill_derives_product_options_from_selector_maps_when_pricing_refs_missing() -> None:
    doc = {
        "template_type": "insulation",
        "template_name": "Insulation Template.xlsx",
        "selector_maps": [
            {
                "sheet_name": "Estimate",
                "row_number": 37,
                "template_bucket": "closed_cell_foam",
                "selector_code": "2",
                "resolved_item_name": "Closed Cell Foam",
            }
        ],
        "row_catalog": [],
    }

    rows_by_table = build_template_catalog_backfill(
        intelligence_docs=[doc],
        include_historical_products=False,
    )

    product_options = rows_by_table["template_product_options"]
    assert len(product_options) == 1
    assert product_options[0]["template_type"] == "insulation"
    assert product_options[0]["template_bucket"] == "closed_cell_foam"
    assert product_options[0]["product_name"] == "Closed Cell Foam"


def test_backfill_loads_intelligence_files_and_writes_preview(tmp_path) -> None:
    source_path = tmp_path / "template_intelligence.json"
    source_path.write_text(json.dumps(_sample_intelligence_doc()), encoding="utf-8")

    docs = load_template_intelligence_files([source_path])
    rows_by_table = build_template_catalog_backfill(
        intelligence_docs=docs,
        include_historical_products=False,
    )
    paths = write_backfill_preview(rows_by_table, tmp_path / "preview")

    summary = json.loads(paths["summary"].read_text(encoding="utf-8"))
    assert summary["template_selector_maps"] == 1
    assert paths["template_product_options"].exists()
    assert paths["template_labor_options"].exists()
