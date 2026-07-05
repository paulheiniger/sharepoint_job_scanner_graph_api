from __future__ import annotations

import json

import pandas as pd

from jobscan.estimator.schemas import EstimatorData
from jobscan.estimator.template_catalog_qa import build_template_catalog_qa_report, write_template_catalog_qa_report


def test_template_catalog_qa_reports_coverage_and_missing_fields(tmp_path) -> None:
    data = EstimatorData(
        template_row_catalog=pd.DataFrame(
            [
                {
                    "template_type": "roofing",
                    "row_number": 26,
                    "template_bucket": "coating",
                    "line_item_kind": "material",
                    "row_label": "Coating",
                },
                {
                    "template_type": "roofing",
                    "row_number": 122,
                    "template_bucket": "labor_base",
                    "line_item_kind": "labor",
                    "row_label": "Base Coat Labor",
                },
            ]
        ),
        template_selector_maps=pd.DataFrame(
            [
                {
                    "template_type": "roofing",
                    "row_number": 26,
                    "template_bucket": "coating",
                    "selector_code": "11",
                    "resolved_item_name": "Gaco Silicone",
                },
                {
                    "template_type": "roofing",
                    "row_number": 39,
                    "template_bucket": "primer",
                    "selector_code": "",
                    "resolved_item_name": "",
                },
            ]
        ),
        template_product_options=pd.DataFrame(
            [
                {
                    "template_type": "roofing",
                    "row_number": 26,
                    "template_bucket": "coating",
                    "product_name": "Gaco Silicone Pail",
                    "source_values_json": {"unit": "pail", "unit_price": 77},
                }
            ]
        ),
        template_labor_options=pd.DataFrame(
            [
                {
                    "template_type": "roofing",
                    "row_number": 122,
                    "labor_package": "labor_base",
                    "lookup_key": "5",
                    "source_values_json": {"crew_size": 5, "daily_rate": 3600},
                }
            ]
        ),
        template_rows=pd.DataFrame(
            [
                {
                    "template_type": "roofing",
                    "row_number": 26,
                    "template_bucket": "coating",
                    "line_item_kind": "material",
                    "selected_item_name": "Historical Silicone",
                    "job_id": "J1",
                    "unit_price": 70,
                },
                {
                    "template_type": "roofing",
                    "row_number": 999,
                    "template_bucket": "unknown",
                    "line_item_kind": "material",
                    "row_label": "Mystery",
                    "selected_item_name": "Mystery Item",
                    "job_id": "J2",
                },
            ]
        ),
    )

    report = build_template_catalog_qa_report(data)
    coverage = {(row["row_number"], row["template_bucket"]): row for row in report["row_option_coverage"]}

    assert coverage[(26, "coating")]["coverage_status"] == "ok"
    assert coverage[(122, "labor_base")]["coverage_status"] == "ok"
    assert report["missing_catalog_field_summary"] == {"template_selector_maps": 1}
    assert report["unknown_row_count"] == 1
    assert report["unknown_group_count"] == 1
    assert any(row["selected_item_name"] == "Historical Silicone" for row in report["historical_product_candidates"])

    paths = write_template_catalog_qa_report(report, tmp_path)
    summary = json.loads(paths["summary_json"].read_text(encoding="utf-8"))
    assert summary["table_counts"]["template_row_catalog"] == 2
    assert summary["unknown_row_count"] == 1
    assert paths["row_option_coverage_csv"].exists()


def test_template_catalog_qa_flags_rows_without_options() -> None:
    data = EstimatorData(
        template_row_catalog=pd.DataFrame(
            [
                {
                    "template_type": "insulation",
                    "row_number": 41,
                    "template_bucket": "caulk_sealant",
                    "line_item_kind": "material",
                    "row_label": "Caulk",
                },
                {
                    "template_type": "insulation",
                    "row_number": 86,
                    "template_bucket": "labor_foam",
                    "line_item_kind": "labor",
                    "row_label": "Foam Labor",
                },
            ]
        )
    )

    report = build_template_catalog_qa_report(data)
    missing = [row for row in report["row_option_coverage"] if row["coverage_status"] == "missing"]

    assert len(missing) == 2
    assert any("no_product_options_or_historical_items" in row["missing_reasons"] for row in missing)
    assert any("no_labor_options" in row["missing_reasons"] for row in missing)


def test_template_catalog_qa_does_not_require_selector_options_for_freeform_cost_rows() -> None:
    data = EstimatorData(
        template_row_catalog=pd.DataFrame(
            [
                {
                    "template_type": "roofing",
                    "row_number": 47,
                    "template_bucket": "seams_misc",
                    "line_item_kind": "material",
                    "formula_model": "units_rate_cost",
                    "cell_roles_json": {"A": "item_name", "E": "unit_price"},
                },
                {
                    "template_type": "roofing",
                    "row_number": 26,
                    "template_bucket": "coating",
                    "line_item_kind": "material",
                    "formula_model": "selector_lookup",
                    "cell_roles_json": {"A": "selector_code", "F": "product"},
                },
            ]
        ),
        template_product_options=pd.DataFrame(
            [
                {
                    "template_type": "roofing",
                    "row_number": 47,
                    "template_bucket": "seams_misc",
                    "product_name": "Historical Seam Material",
                    "source_values_json": {},
                },
                {
                    "template_type": "roofing",
                    "row_number": 26,
                    "template_bucket": "coating",
                    "product_name": "Gaco Silicone",
                    "source_values_json": {},
                },
            ]
        ),
    )

    report = build_template_catalog_qa_report(data)
    coverage = {(row["row_number"], row["template_bucket"]): row for row in report["row_option_coverage"]}

    assert coverage[(47, "seams_misc")]["coverage_status"] == "ok"
    assert coverage[(26, "coating")]["coverage_status"] == "missing"
    assert coverage[(26, "coating")]["missing_reasons"] == "no_selector_options"
