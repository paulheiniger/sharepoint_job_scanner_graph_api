from __future__ import annotations

from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

from jobscan.estimator import build_estimate, estimate_from_field_notes, load_estimator_data


def test_estimator_package_exports_dashboard_imports() -> None:
    assert callable(build_estimate)
    assert callable(load_estimator_data)
    assert callable(estimate_from_field_notes)


def test_estimator_package_exports_field_notes_estimator() -> None:
    assert build_estimate is not None
    assert estimate_from_field_notes is not None
    assert load_estimator_data is not None


def test_load_estimator_data_accepts_database_url_keyword() -> None:
    load_estimator_data(Path.cwd(), database_url=None)


def test_load_estimator_data_accepts_database_url_keyword_with_missing_files(tmp_path: Path) -> None:
    data = load_estimator_data(tmp_path, database_url=None)

    assert data.jobs.empty
    assert data.warnings


def test_load_estimator_data_accepts_database_url_positional(tmp_path: Path) -> None:
    data = load_estimator_data(tmp_path, None)

    assert data.jobs.empty


def test_load_estimator_data_falls_back_when_database_unavailable(tmp_path: Path) -> None:
    output = tmp_path / "output"
    output.mkdir()
    (output / "job_index.json").write_text('[{"job_id": "J1"}]', encoding="utf-8")

    data = load_estimator_data(tmp_path, database_url="sqlite:///:memory:", prefer_database=True)

    assert len(data.jobs) == 1
    assert any("Database estimator load failed" in warning for warning in data.warnings)


def test_load_estimator_data_loads_template_rows_and_pricing_from_database(tmp_path: Path) -> None:
    db_path = tmp_path / "estimator.sqlite"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE dashboard_jobs (
                    job_id TEXT PRIMARY KEY,
                    job_name TEXT,
                    division TEXT,
                    estimated_sqft NUMERIC
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE estimate_template_rows (
                    template_row_id TEXT PRIMARY KEY,
                    document_id TEXT,
                    job_id TEXT,
                    source_file TEXT,
                    sheet_name TEXT,
                    row_number INTEGER,
                    template_bucket TEXT,
                    template_section TEXT,
                    line_item_kind TEXT,
                    row_label TEXT,
                    selected_item_name TEXT,
                    quantity NUMERIC,
                    unit TEXT,
                    unit_price NUMERIC,
                    estimated_units NUMERIC,
                    estimated_cost NUMERIC,
                    days NUMERIC,
                    crew_size NUMERIC,
                    total_hours NUMERIC,
                    daily_rate NUMERIC,
                    trips NUMERIC,
                    round_trip_miles NUMERIC,
                    cost_per_mile NUMERIC,
                    warranty_years NUMERIC,
                    overhead_pct NUMERIC,
                    profit_pct NUMERIC,
                    needs_review BOOLEAN
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE pricing_catalog (
                    pricing_item_id TEXT PRIMARY KEY,
                    product_name TEXT,
                    unit_price NUMERIC,
                    is_current BOOLEAN
                )
                """
            )
        )
        conn.execute(text("INSERT INTO dashboard_jobs VALUES ('J1', 'DB Job', 'Roofing', 10000)"))
        conn.execute(
            text(
                """
                INSERT INTO estimate_template_rows (
                    template_row_id, document_id, job_id, source_file, sheet_name, row_number,
                    template_bucket, template_section, line_item_kind, row_label, selected_item_name,
                    estimated_cost, needs_review
                )
                VALUES ('T1', 'D1', 'J1', 'Estimate.xlsx', 'Estimate', 26, 'coating', 'materials', 'material',
                        'Coating', 'Silicone', 1200, false)
                """
            )
        )
        conn.execute(text("INSERT INTO pricing_catalog VALUES ('P1', 'Silicone', 42, true)"))
        conn.execute(text("INSERT INTO pricing_catalog VALUES ('P2', 'Old Silicone', 30, false)"))

    data = load_estimator_data(tmp_path, database_url=f"sqlite:///{db_path}")

    assert len(data.jobs) == 1
    assert len(data.template_rows) == 1
    assert len(data.pricing) == 1
    assert len(data.pricing_catalog) == 1
    assert len(data.line_item_classifications) == 0
    assert "database: estimate_template_rows" in data.source_files_used
    assert "database: pricing_catalog" in data.source_files_used
    assert "output/job_index.json" not in data.source_files_used
    assert any("estimate_line_item_classifications table not found" in warning for warning in data.warnings)


def test_load_estimator_data_missing_pricing_catalog_does_not_crash(tmp_path: Path) -> None:
    db_path = tmp_path / "estimator.sqlite"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE estimate_template_rows (
                    template_row_id TEXT PRIMARY KEY,
                    document_id TEXT,
                    job_id TEXT,
                    template_bucket TEXT
                )
                """
            )
        )
        conn.execute(text("INSERT INTO estimate_template_rows VALUES ('T1', 'D1', 'J1', 'coating')"))

    data = load_estimator_data(tmp_path, database_url=f"sqlite:///{db_path}")

    assert len(data.template_rows) == 1
    assert data.pricing_catalog.empty
    assert any("pricing_catalog table not found" in warning for warning in data.warnings)


def test_field_notes_estimator_import_works_from_package() -> None:
    result = estimate_from_field_notes(
        "Metal roof about 12000 sqft silicone coating Louisville KY",
        data=__import__("jobscan.estimator.schemas", fromlist=["EstimatorData"]).EstimatorData(pricing=pd.DataFrame()),
    )

    assert result.estimate_high >= result.estimate_low


def test_dashboard_optional_field_notes_estimator_helper_loads() -> None:
    import dashboard.app as app

    estimator_fn, warning = app.optional_field_notes_estimator()

    assert warning is None
    assert callable(estimator_fn)
