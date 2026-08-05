from __future__ import annotations

from datetime import date

from sqlalchemy import create_engine, inspect

from jobscan.business import chart_history_service as history


def _fake_result(value: float) -> dict:
    return {
        "as_of": "2026-08-04T12:00:00Z",
        "headline_metrics": {"source_value": value},
        "source_tables": ["prepared_current_snapshot"],
        "data_freshness": {"prepared_as_of": "2026-08-04T11:00:00Z"},
        "coverage": {"source_rows": 5},
        "warnings": [],
    }


def test_daily_history_capture_is_idempotent_and_preserves_prior_days(
    monkeypatch,
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    current = {"value": 10.0}

    def loader(**_kwargs):
        return _fake_result(current["value"])

    monkeypatch.setattr(
        history,
        "CAPTURE_SPECS",
        (
            history.HistoryCaptureSpec(
                "source_dataset",
                loader,
                (("source_value", "metric_value"),),
            ),
        ),
    )
    monkeypatch.setattr(
        history,
        "HISTORY_DATASETS",
        {"history_dataset": "source_dataset"},
    )

    history.capture_daily_chart_history(
        engine=engine,
        snapshot_date=date(2026, 8, 3),
    )
    current["value"] = 12.0
    history.capture_daily_chart_history(
        engine=engine,
        snapshot_date=date(2026, 8, 3),
    )
    current["value"] = 15.0
    history.capture_daily_chart_history(
        engine=engine,
        snapshot_date=date(2026, 8, 4),
    )

    result = history.get_chart_history(
        "history_dataset",
        engine=engine,
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 4),
    )

    assert result["records"] == [
        {"snapshot_date": "2026-08-03", "metric_value": 12.0},
        {"snapshot_date": "2026-08-04", "metric_value": 15.0},
    ]
    assert result["staging"]["historical_series_available"] is True
    assert result["coverage"]["available_snapshot_days"] == 2
    assert history.chart_history_status(engine=engine)[0]["snapshot_days"] == 2


def test_daily_history_dry_run_does_not_create_storage(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    monkeypatch.setattr(
        history,
        "CAPTURE_SPECS",
        (
            history.HistoryCaptureSpec(
                "source_dataset",
                lambda **_kwargs: _fake_result(10),
                (("source_value", "metric_value"),),
            ),
        ),
    )

    result = history.capture_daily_chart_history(engine=engine, dry_run=True)

    assert result["datasets_written"] == 0
    assert history.CHART_HISTORY_RELATION not in inspect(engine).get_table_names()


def test_history_read_is_non_mutating_when_storage_is_missing(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    monkeypatch.setattr(
        history,
        "HISTORY_DATASETS",
        {"history_dataset": "source_dataset"},
    )

    result = history.get_chart_history("history_dataset", engine=engine)

    assert result["records"] == []
    assert any("not been initialized" in warning for warning in result["warnings"])
    assert history.CHART_HISTORY_RELATION not in inspect(engine).get_table_names()


def test_history_warns_until_two_daily_observations_exist(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    monkeypatch.setattr(
        history,
        "CAPTURE_SPECS",
        (
            history.HistoryCaptureSpec(
                "source_dataset",
                lambda **_kwargs: _fake_result(10),
                (("source_value", "metric_value"),),
            ),
        ),
    )
    monkeypatch.setattr(
        history,
        "HISTORY_DATASETS",
        {"history_dataset": "source_dataset"},
    )
    history.capture_daily_chart_history(
        engine=engine,
        snapshot_date=date(2026, 8, 4),
    )

    result = history.get_chart_history(
        "history_dataset",
        engine=engine,
        start_date=date(2026, 8, 4),
        end_date=date(2026, 8, 4),
    )

    assert result["staging"]["historical_series_available"] is False
    assert any("Fewer than two" in warning for warning in result["warnings"])
