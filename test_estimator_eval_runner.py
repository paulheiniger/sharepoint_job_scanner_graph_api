from __future__ import annotations

import json

import pytest

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


def test_eval_fails_when_historical_cost_ratio_is_priced(monkeypatch) -> None:
    class FakeRecommendation:
        parsed_fields = {"estimated_sqft": 10000, "project_type": "roof coating", "substrate": "metal", "coating_type": "silicone"}
        recommended_scope = ["roof coating"]
        material_plan = [
            {"category": "coating", "item": "silicone", "estimated_cost": 10000},
            {
                "category": "primer",
                "item": "Primer historical cost",
                "selected_price_source": "historical_cost_ratio_fallback",
                "estimated_cost": 4000,
            },
        ]
        labor_plan = []
        travel_plan = {"travel_labor_hours": 0}
        review_flags = []
        draft_workbook_inputs = {"header": {"C12_estimated_sqft": 10000}, "adders_review_rows": []}

    monkeypatch.setattr(runner, "estimate_from_field_notes", lambda *args, **kwargs: FakeRecommendation())
    report = runner.evaluate_case({"case_id": "fake", "notes": "fake", "expected": {"estimated_sqft": 10000}})

    assert not report["passed"]
    assert any("historical_cost_ratio_fallback" in failure for failure in report["failures"])


def test_eval_fails_when_simple_roof_labor_hours_are_extreme(monkeypatch) -> None:
    class FakeRecommendation:
        parsed_fields = {"estimated_sqft": 10000, "project_type": "roof coating", "substrate": "metal", "coating_type": "silicone"}
        recommended_scope = ["roof coating"]
        material_plan = [{"category": "coating", "item": "silicone", "estimated_cost": 10000}]
        labor_plan = [{"task": "labor_prep", "total_hours": 900, "estimated_cost": 65000}]
        travel_plan = {"travel_labor_hours": 0}
        review_flags = []
        draft_workbook_inputs = {"header": {"C12_estimated_sqft": 10000}, "adders_review_rows": []}

    monkeypatch.setattr(runner, "estimate_from_field_notes", lambda *args, **kwargs: FakeRecommendation())
    report = runner.evaluate_case({"case_id": "fake", "notes": "fake", "expected": {"estimated_sqft": 10000}})

    assert not report["passed"]
    assert any("labor hours per 1000 sqft" in failure for failure in report["failures"])


def test_parse_args_requires_neon_database_url_not_database_url(monkeypatch) -> None:
    monkeypatch.delenv("NEON_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://local.example/test")

    args = runner.parse_args([])

    assert args.database_url is None


def test_load_data_for_eval_requires_neon_url() -> None:
    with pytest.raises(RuntimeError, match="NEON_DATABASE_URL is required"):
        runner.load_data_for_eval(None)


def test_load_data_for_eval_prints_database_preflight(monkeypatch, capsys) -> None:
    class FakeData:
        pass

    monkeypatch.setattr(
        runner,
        "estimator_database_preflight",
        lambda _database_url: {
            "database_engine": "postgresql+psycopg2",
            "database_host": "example-pooler.neon.tech",
            "database_name": "spraytec",
            "counts": {
                "estimate_template_rows": 66570,
                "relationship_material_qty_ratios": 12,
                "relationship_labor_rates": 34,
            },
        },
    )
    monkeypatch.setattr(runner, "load_estimator_data", lambda *args, **kwargs: FakeData())

    data = runner.load_data_for_eval("postgresql://user:secret@example-pooler.neon.tech/spraytec")

    output = capsys.readouterr().out
    assert isinstance(data, FakeData)
    assert "database engine: postgresql+psycopg2" in output
    assert "database host: example-pooler.neon.tech" in output
    assert "database name: spraytec" in output
    assert "estimate_template_rows count: 66570" in output
    assert "relationship_material_qty_ratios count: 12" in output
    assert "relationship_labor_rates count: 34" in output
    assert "secret" not in output
