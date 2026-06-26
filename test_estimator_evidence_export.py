from __future__ import annotations

import json
from types import SimpleNamespace

from openpyxl import load_workbook

from jobscan.estimator.evidence_export import build_estimator_evidence_export, write_estimator_evidence_export
from jobscan.estimator.field_estimator import estimate_from_field_notes
from test_field_estimator import field_data


SAMPLE_NOTE = (
    "Roof coating estimate. Metal roof in Louisville KY. Main roof is 120 ft by 80 ft. "
    "Deduct two skylight areas, each 4 ft by 8 ft. Roof condition is fair with some rusted fasteners. "
    "Customer wants a 10-year silicone coating system. Access is easy. Few penetrations."
)


def test_build_estimator_evidence_export_contains_expected_sheets() -> None:
    data = field_data()
    recommendation = estimate_from_field_notes(SAMPLE_NOTE, {"estimated_sqft": 0}, data=data)

    export = build_estimator_evidence_export(recommendation, data=data, notes=SAMPLE_NOTE)

    assert export["run_summary"]["estimated_sqft"] == 9536
    for sheet in [
        "README",
        "parsed_scope",
        "material_plan",
        "material_evidence",
        "labor_plan",
        "labor_evidence",
        "labor_diagnostics",
        "similar_jobs",
        "relationship_rows",
        "rejected_evidence",
        "estimate_rollup",
    ]:
        assert sheet in export["sheets"]
        assert export["sheets"][sheet]
    assert any(row.get("task") == "labor_prep" for row in export["sheets"]["labor_diagnostics"])
    assert any(row.get("included_in_total") is True for row in export["sheets"]["material_plan"])


def test_write_estimator_evidence_export_creates_parseable_json_and_xlsx(tmp_path) -> None:
    data = field_data()
    recommendation = estimate_from_field_notes(SAMPLE_NOTE, {"estimated_sqft": 0}, data=data)

    paths = write_estimator_evidence_export(recommendation, data=data, notes=SAMPLE_NOTE, output_dir=tmp_path, base_filename="sample")

    assert paths["json"].exists()
    assert paths["xlsx"].exists()
    parsed = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert parsed["run_summary"]["material_rows"] >= 1
    workbook = load_workbook(paths["xlsx"], read_only=True)
    assert {
        "README",
        "parsed_scope",
        "material_plan",
        "labor_plan",
        "labor_diagnostics",
        "estimate_rollup",
    }.issubset(set(workbook.sheetnames))


def test_rejected_material_evidence_is_exported() -> None:
    recommendation = SimpleNamespace(
        parsed_fields={"estimated_sqft": 10000, "project_type": "roof coating"},
        recommended_scope=[],
        material_plan=[
            {
                "item": "Primer allowance",
                "category": "primer",
                "quantity": 999,
                "unit": "pail",
                "unit_price": 1,
                "estimated_cost": 999,
                "selected_price_source": "rejected_historical_quantity_ratio",
                "sanity_check_status": "blocked_implausible_quantity",
                "rejected_reason": "Historical cost allowance was not a physical quantity.",
                "needs_review": True,
            }
        ],
        labor_plan=[],
        travel_plan={},
        historical_calibration={},
        similar_examples=[],
        estimate_low=0,
        estimate_target=0,
        estimate_high=0,
        review_flags=[],
        human_review_required=True,
        draft_workbook_inputs={"header": {"C12_estimated_sqft": 10000}},
        debug={},
    )

    export = build_estimator_evidence_export(recommendation)
    material_row = export["sheets"]["material_plan"][0]
    rejected_rows = export["sheets"]["rejected_evidence"]

    assert material_row["included_in_total"] is False
    assert any(row.get("package") == "primer" and row.get("severity") == "blocker" for row in rejected_rows)

