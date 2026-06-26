from __future__ import annotations

import json

from evals.estimator import run_estimator_eval as runner


def test_field_notes_cases_json_validates() -> None:
    cases = runner.load_cases()
    assert cases
    assert {case["case_id"] for case in cases} >= {
        "roof_coating_basic_9536",
        "insulation_walls_with_deductions",
    }


def test_package_present_matches_multiple_row_fields() -> None:
    rows = [
        {"category": "materials", "item": "White silicone coating"},
        {"task": "labor_prep"},
    ]
    assert runner.package_present(rows, "coating")
    assert runner.package_present(rows, "labor_prep")
    assert not runner.package_present(rows, "infrared_scan")


def test_numeric_matches_with_small_tolerance() -> None:
    assert runner.numeric_matches(9536.1, 9536, tolerance=1)
    assert not runner.numeric_matches(9540, 9536, tolerance=1)


def test_review_text_collects_flags_and_notes() -> None:
    result = {
        "review_flags": ["Verify infrared moisture scan"],
        "draft_workbook_inputs": {"adders_review_rows": [{"flag": "manual review"}]},
        "material_plan": [{"notes": "Primer review due to rust"}],
        "labor_plan": [{"notes": "Labor review"}],
    }
    text = runner.review_text(result)
    assert "infrared" in text
    assert "primer" in text


def test_eval_case_report_shape_with_fake_result(monkeypatch) -> None:
    class FakeRecommendation:
        parsed_fields = {
            "estimated_sqft": 100,
            "dimension_summary": {"gross_area_sqft": 100, "deduction_area_sqft": 0, "net_area_sqft": 100},
            "project_type": "roof coating",
            "substrate": "metal",
            "coating_type": "silicone",
            "warranty_years": 10,
        }
        material_plan = [{"package": "coating", "item": "silicone coating"}]
        labor_plan = []
        travel_plan = {"travel_labor_hours": 0}
        review_flags = []
        draft_workbook_inputs = {"header": {"C12_estimated_sqft": 100}, "adders_review_rows": []}

    monkeypatch.setattr(runner, "estimate_from_field_notes", lambda *args, **kwargs: FakeRecommendation())
    report = runner.evaluate_case(
        {
            "case_id": "fake",
            "notes": "fake",
            "expected": {
                "estimated_sqft": 100,
                "gross_area_sqft": 100,
                "deduction_area_sqft": 0,
                "net_area_sqft": 100,
                "must_include_material_packages": ["coating"],
            },
        }
    )
    assert report["passed"]
    assert json.dumps(report)


def test_print_report_includes_audit_command_for_failures_or_warnings(capsys) -> None:
    runner.print_report(
        {
            "total_cases": 1,
            "passed_cases": 0,
            "failed_cases": 1,
            "results": [
                {
                    "case_id": "roof_coating_basic_9536",
                    "passed": False,
                    "failures": ["bad"],
                    "warnings": [],
                    "actual": {"header": {}, "parsed_fields": {}, "material_items": [], "labor_tasks": [], "review_flags": []},
                }
            ],
        }
    )

    output = capsys.readouterr().out
    assert "python -m jobscan.estimator.calibration_audit --case-id roof_coating_basic_9536" in output
    assert '--database-url "$NEON_DATABASE_URL"' in output
