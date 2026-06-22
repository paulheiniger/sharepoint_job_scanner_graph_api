from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import create_engine, text

from jobscan import maply_export as me


def create_maply_schema(engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE dashboard_jobs (
                    job_id TEXT PRIMARY KEY,
                    division TEXT,
                    pipeline_status TEXT,
                    status TEXT,
                    customer TEXT,
                    job_name TEXT,
                    job_type TEXT,
                    site_address TEXT,
                    city TEXT,
                    state TEXT,
                    zip_code TEXT,
                    estimate_date TEXT,
                    estimated_value NUMERIC,
                    folder_url TEXT,
                    source_year INTEGER,
                    scan_root TEXT,
                    warnings TEXT
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE estimate_template_rows (
                    template_row_id TEXT PRIMARY KEY,
                    job_id TEXT,
                    template_bucket TEXT,
                    row_label TEXT,
                    selected_item_name TEXT,
                    cell_values TEXT
                )
                """
            )
        )


def insert_template_header(conn, job_id: str, bucket: str, cell_ref: str, value: str) -> None:
    conn.execute(
        text(
            """
            INSERT INTO estimate_template_rows (
                template_row_id, job_id, template_bucket, row_label, selected_item_name, cell_values
            )
            VALUES (:id, :job_id, :bucket, NULL, NULL, :cell_values)
            """
        ),
        {
            "id": f"{job_id}-{bucket}",
            "job_id": job_id,
            "bucket": bucket,
            "cell_values": json.dumps({cell_ref: value}),
        },
    )


def seed_maply_data(engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO dashboard_jobs (
                    job_id, division, pipeline_status, status, customer, job_name, job_type,
                    site_address, city, state, zip_code, estimate_date, estimated_value,
                    folder_url, source_year, scan_root, warnings
                )
                VALUES
                    (
                        'JOB1', 'Roofing', 'Contracted', 'Open', 'Acme', NULL, 'Roof Coating',
                        NULL, NULL, NULL, NULL, NULL, 125000,
                        'https://sharepoint.example/job1', 2025, '2025 MASTER FILES', ''
                    ),
                    (
                        'JOB2', 'Insulation', 'Completed', 'Completed', 'Beta', 'Beta Plant', 'Foam',
                        '100 Industrial Way', 'Louisville', 'KY', '40202', '2025-04-02', 82000,
                        'https://sharepoint.example/job2', 2025, '2025 MASTER FILES', ''
                    ),
                    (
                        'JOB3', 'Roofing', 'Completed', 'Completed', 'Old', 'Old Job', 'Repair',
                        '1 Past St', 'Lexington', 'KY', '40502', '2024-01-02', 1000,
                        'https://sharepoint.example/job3', 2024, '2024 MASTER FILES', ''
                    )
                """
            )
        )
        insert_template_header(conn, "JOB1", "job_name", "C2", "Acme Roof")
        insert_template_header(conn, "JOB1", "site_address", "C4", "123 Main St")
        insert_template_header(conn, "JOB1", "city_state_zip", "C5", "Shelbyville, KY 40065")
        insert_template_header(conn, "JOB1", "email", "C8", "pm@example.com")
        insert_template_header(conn, "JOB1", "phone", "C9", "555-0100")
        insert_template_header(conn, "JOB1", "estimate_date", "C1", "2025-03-01")


def test_maply_export_uses_template_rows_as_fallback() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    create_maply_schema(engine)
    seed_maply_data(engine)

    df = me.build_export_dataframe(engine, year=2025, divisions=["Roofing"], statuses=["Contracted"])

    assert list(df.columns) == me.MAPLY_COLUMNS
    assert len(df) == 1
    row = df.iloc[0]
    assert row["Name"] == "Acme Roof"
    assert row["Address"] == "123 Main St"
    assert row["Zip/Postal Code"] == "40065"
    assert row["City"] == "Shelbyville"
    assert row["State"] == "KY"
    assert row["Email"] == "pm@example.com"
    assert row["Phone"] == "555-0100"
    assert row["Estimate Date"] == "2025-03-01"
    assert row["Lat"] == ""
    assert row["Lng"] == ""
    assert "Missing address" not in row["Remarks"]


def test_maply_export_flags_missing_address_and_zip() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    create_maply_schema(engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO dashboard_jobs (
                    job_id, division, pipeline_status, status, customer, job_name,
                    source_year, estimated_value
                )
                VALUES ('JOB4', 'Roofing', 'Contracted', 'Open', 'No Address Co', NULL, 2025, 10)
                """
            )
        )

    df = me.build_export_dataframe(engine, year=2025, divisions=["Roofing"], statuses=["Contracted"])

    assert len(df) == 1
    assert df.iloc[0]["Name"] == "No Address Co"
    assert "Missing address" in df.iloc[0]["Remarks"]
    assert "Missing zip" in df.iloc[0]["Remarks"]


def test_standard_export_specs_use_expected_names(tmp_path: Path) -> None:
    specs = me.standard_export_specs(2025, tmp_path)

    assert [spec.output_path.name for spec in specs] == [
        "roofing_contracted_2025.csv",
        "roofing_completed_2025.csv",
        "insulation_contracted_2025.csv",
        "insulation_completed_2025.csv",
    ]


def test_dedupe_sites_keeps_highest_value() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    create_maply_schema(engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO dashboard_jobs (
                    job_id, division, pipeline_status, status, customer, job_name,
                    site_address, city, state, zip_code, source_year, estimated_value
                )
                VALUES
                    ('LOW', 'Roofing', 'Contracted', 'Open', 'A', 'Low', '1 Main', 'City', 'KY', '40000', 2025, 10),
                    ('HIGH', 'Roofing', 'Contracted', 'Open', 'A', 'High', '1 Main', 'City', 'KY', '40000', 2025, 20)
                """
            )
        )

    df = me.build_export_dataframe(engine, year=2025, divisions=["Roofing"], statuses=["Contracted"], dedupe=True)

    assert len(df) == 1
    assert df.iloc[0]["Job ID"] == "HIGH"
