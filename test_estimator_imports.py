from __future__ import annotations

from pathlib import Path

import pandas as pd

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
