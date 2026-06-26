from __future__ import annotations

import json

import pandas as pd
from sqlalchemy import create_engine

from evals.relationship_mining import run_relationship_eval as runner


def test_relationship_checks_json_validates() -> None:
    checks = runner.load_checks()
    assert "job_package_summary" in checks["required_tables"]
    assert "relationship_labor_rates" in checks["required_tables"]


def test_diagnostic_files_status_reports_presence(tmp_path) -> None:
    (tmp_path / "relationship_input_diagnostics.csv").write_text("metric,value\nrows,1\n")
    rows = runner.diagnostic_files_status(
        tmp_path,
        ["relationship_input_diagnostics.csv", "missing_job_context.csv"],
    )
    assert rows[0]["exists"] is True
    assert rows[1]["exists"] is False


def test_relationship_eval_with_sqlite_tables(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'relationships.db'}")
    checks = runner.load_checks()
    pd.DataFrame(
        [
            {
                "job_id": "J1",
                "package": "labor_foam",
                "area_sqft": 1200,
                "cost_per_sqft": 2,
                "hours_per_sqft": 0.04,
                "template_type": "insulation",
                "total_hours": 48,
            },
            {
                "job_id": "J1",
                "package": "foam",
                "area_sqft": 1200,
                "cost_per_sqft": 3,
                "hours_per_sqft": None,
                "template_type": "insulation",
                "total_hours": None,
            },
        ]
    ).to_sql("job_package_summary", engine, index=False)
    pd.DataFrame([{"package": "foam", "evidence_count": 1}]).to_sql("relationship_material_qty_ratios", engine, index=False)
    pd.DataFrame([{"package": "labor_foam", "evidence_count": 1}]).to_sql("relationship_labor_rates", engine, index=False)

    report = runner.evaluate_relationships(engine, checks)

    assert report["passed"]
    assert report["row_counts"]["job_package_summary"] == 2
    assert any(row["package"] == "labor_foam" for row in report["package_summary"])
    assert json.dumps(report, default=str)


def test_relationship_eval_fails_required_missing_table(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    report = runner.evaluate_relationships(engine, runner.load_checks())
    assert not report["passed"]
    assert any("Required table missing" in failure for failure in report["failures"])


def _seed_required_relationship_tables(engine) -> None:
    pd.DataFrame(
        [
            {
                "job_id": "J1",
                "package": "labor",
                "area_sqft": 1200,
                "cost_per_sqft": 2,
                "hours_per_sqft": 0.04,
                "template_type": "roofing",
                "total_hours": 48,
            }
        ]
    ).to_sql("job_package_summary", engine, index=False)
    pd.DataFrame([{"package": "coating", "evidence_count": 1}]).to_sql("relationship_material_qty_ratios", engine, index=False)
    pd.DataFrame([{"package": "labor", "evidence_count": 1}]).to_sql("relationship_labor_rates", engine, index=False)


def test_relationship_eval_warns_when_roofing_labor_like_rows_are_unmapped(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'relationships.db'}")
    _seed_required_relationship_tables(engine)
    pd.DataFrame(
        [
            {"template_type": "roofing", "template_bucket": "unknown", "line_item_kind": "unknown", "row_label": "Pwash/Prep", "row_number": 116},
            {"template_type": "roofing", "template_bucket": "unknown", "line_item_kind": "unknown", "row_label": "Top Coat", "row_number": 124},
            {"template_type": "roofing", "template_bucket": "coating", "line_item_kind": "material", "row_label": "Silicone", "row_number": 26},
        ]
    ).to_sql("estimate_template_rows", engine, index=False)

    report = runner.evaluate_relationships(engine, runner.load_checks())

    assert report["passed"]
    assert report["roofing_labor_health"]["labor_like_unknown_rows"] == 2
    assert any("roofing labor-like labels" in warning for warning in report["warnings"])


def test_relationship_eval_strict_fails_when_roofing_labor_health_bad(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'relationships.db'}")
    _seed_required_relationship_tables(engine)
    pd.DataFrame(
        [
            {"template_type": "roofing", "template_bucket": "unknown", "line_item_kind": "unknown", "row_label": "Pwash/Prep", "row_number": 116},
            {"template_type": "roofing", "template_bucket": "unknown", "line_item_kind": "unknown", "row_label": "Set Up/Safety", "row_number": 118},
        ]
    ).to_sql("estimate_template_rows", engine, index=False)

    report = runner.evaluate_relationships(engine, runner.load_checks(), strict=True)

    assert not report["passed"]
    assert any("standard roofing labor buckets are very low" in failure for failure in report["failures"])
