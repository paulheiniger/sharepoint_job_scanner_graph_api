from __future__ import annotations

import pandas as pd
from sqlalchemy import create_engine, text

from jobscan.estimator.data_loader import (
    _load_template_example_source_links,
    normalize_estimator_data,
    normalize_estimator_dataframe,
    normalize_numeric_columns,
)
from jobscan.estimator.schemas import EstimatorData


def test_normalize_numeric_columns_coerces_invalid_values_without_dropping_rows() -> None:
    df = pd.DataFrame(
        [
            {"estimated_cost": "1250.50", "crew_size": "4", "selected_item_name": "Prep"},
            {"estimated_cost": "not a number", "crew_size": "", "selected_item_name": "Bad historical row"},
        ]
    )

    normalized = normalize_numeric_columns(df, ["estimated_cost", "crew_size"])

    assert len(normalized) == 2
    assert normalized.iloc[0]["estimated_cost"] == 1250.50
    assert normalized.iloc[0]["crew_size"] == 4
    assert pd.isna(normalized.iloc[1]["estimated_cost"])
    assert pd.isna(normalized.iloc[1]["crew_size"])
    assert normalized.iloc[1]["selected_item_name"] == "Bad historical row"


def test_normalize_estimator_dataframe_handles_database_style_columns() -> None:
    df = pd.DataFrame(
        [
            {
                "estimated_cost": "500",
                "median_crew_size": "nan",
                "evidence_count": "bad",
                "estimated_sqft": "9536",
                "template_bucket": "labor_prep",
            }
        ]
    )

    normalized = normalize_estimator_dataframe(df)

    assert normalized.iloc[0]["estimated_cost"] == 500
    assert normalized.iloc[0]["estimated_sqft"] == 9536
    assert pd.isna(normalized.iloc[0]["median_crew_size"])
    assert pd.isna(normalized.iloc[0]["evidence_count"])
    assert normalized.iloc[0]["template_bucket"] == "labor_prep"


def test_normalize_estimator_data_keeps_pricing_and_classification_aliases() -> None:
    data = EstimatorData(
        pricing_catalog=pd.DataFrame([{"product_name": "Silicone", "price_per_gallon": "38", "is_current": True}]),
        latest_historical_unit_prices=pd.DataFrame(
            [{"template_bucket": "coating", "unit_price": "35", "historical_observation_count": "7"}]
        ),
        template_rows=pd.DataFrame([{"template_bucket": "labor_prep", "crew_size": "bad"}]),
        line_item_classifications=pd.DataFrame([{"template_bucket": "coating", "estimated_cost": "1200"}]),
        estimator_memory=pd.DataFrame(
            [
                {
                    "status": "Approved",
                    "priority": "High",
                    "template_type": "Insulation",
                    "template_bucket": "Labor Loading",
                    "guidance": "  Loading should usually be short. ",
                }
            ]
        ),
    )

    normalized = normalize_estimator_data(data)

    assert normalized.pricing.iloc[0]["price_per_gallon"] == 38
    assert normalized.pricing_catalog.iloc[0]["price_per_gallon"] == 38
    assert normalized.latest_historical_unit_prices.iloc[0]["unit_price"] == 35
    assert normalized.latest_historical_unit_prices.iloc[0]["historical_observation_count"] == 7
    assert pd.isna(normalized.template_rows.iloc[0]["crew_size"])
    assert normalized.classified_line_items.iloc[0]["estimated_cost"] == 1200
    assert normalized.line_item_classifications.iloc[0]["estimated_cost"] == 1200
    assert normalized.estimator_memory.iloc[0]["status"] == "approved"
    assert normalized.estimator_memory.iloc[0]["priority"] == "high"
    assert normalized.estimator_memory.iloc[0]["template_bucket"] == "labor_loading"
    assert normalized.estimator_memory.iloc[0]["guidance"] == "Loading should usually be short."


def test_template_example_source_links_use_document_and_drive_metadata() -> None:
    engine = create_engine("sqlite://")
    with engine.connect() as connection:
        connection.execute(text("ATTACH DATABASE ':memory:' AS analytics"))
        connection.execute(
            text(
                """
                CREATE TABLE analytics.estimator_template_examples (
                    example_id TEXT,
                    document_id TEXT,
                    job_id TEXT,
                    source_file TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE documents (
                    document_id TEXT,
                    job_id TEXT,
                    file_name TEXT,
                    drive_id TEXT,
                    drive_item_id TEXT,
                    sharepoint_url TEXT,
                    folder_path TEXT,
                    relative_path TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE sharepoint_drive_items (
                    drive_id TEXT,
                    drive_item_id TEXT,
                    web_url TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO analytics.estimator_template_examples
                VALUES ('EX-1', 'DOC-1', 'JOB-1', 'Estimate JOB-1.xlsx')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO documents
                VALUES (
                    'DOC-1',
                    'JOB-1',
                    'Estimate JOB-1.xlsx',
                    'DRIVE-1',
                    'ITEM-1',
                    'https://fallback.invalid/estimate',
                    'Jobs/2024/JOB-1/Estimates',
                    'Estimates/Estimate JOB-1.xlsx'
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO sharepoint_drive_items
                VALUES ('DRIVE-1', 'ITEM-1', 'https://sharepoint.invalid/estimate')
                """
            )
        )

        links = _load_template_example_source_links(
            connection,
            "analytics.estimator_template_examples",
        )

    assert links.to_dict(orient="records") == [
        {
            "example_id": "EX-1",
            "job_id": "JOB-1",
            "document_id": "DOC-1",
            "source_file": "Estimate JOB-1.xlsx",
            "file_name": "Estimate JOB-1.xlsx",
            "source_url": "https://sharepoint.invalid/estimate",
            "folder_path": "Jobs/2024/JOB-1/Estimates",
            "relative_path": "Estimates/Estimate JOB-1.xlsx",
        }
    ]
