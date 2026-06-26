from __future__ import annotations

import pandas as pd
from sqlalchemy import create_engine, text

from jobscan.estimator.data_loader import load_estimator_data
from jobscan.estimator.line_items import (
    classification_row_from_line_item,
    classify_existing_line_items,
    classify_line_items,
    load_classified_line_items_for_job,
    classify_template_line_item,
    summarize_classified_by_job,
    summarize_similar_job_buckets,
)


def row(name: str, **kwargs):
    base = {
        "job_id": "J1",
        "line_item_name": name,
        "description": "",
        "section": "Materials",
        "quantity": 10,
        "unit": "ea",
        "unit_price": 5,
        "extended_cost": 50,
        "source_sheet": "Estimate",
        "source_row": None,
    }
    base.update(kwargs)
    return base


def bucket(name: str, **kwargs) -> str:
    return classify_template_line_item(row(name, **kwargs)).template_bucket


def test_coating_classification() -> None:
    assert bucket("High solids silicone coating") == "coating"
    assert bucket("Acrylic top coat") == "coating"


def test_foam_classification() -> None:
    assert bucket("SPF polyurethane foam") == "foam"


def test_primer_classification() -> None:
    assert bucket("Bleed Block primer") == "primer"


def test_sealant_classification() -> None:
    assert bucket("Buttergrade flashing grade sealant") == "caulk_sealant"


def test_fabric_and_seam_classification() -> None:
    assert bucket("6 inch stitchbond fabric roll") == "fabric"
    assert bucket("Seam treatment") == "seams_misc"


def test_board_fastener_plate_classification() -> None:
    assert bucket("ISO cover board") == "board_stock"
    assert bucket("Metal screws fasteners") == "fasteners"
    assert bucket("Carlisle plates") == "plates"


def test_lift_and_dumpster_classification() -> None:
    assert bucket("Boom lift rental") == "lift"
    assert bucket("40 yard dumpster") == "dumpsters"


def test_travel_and_lodging_classification() -> None:
    assert bucket("Truck mileage") == "truck_expense"
    assert bucket("Meals and hotel lodging") == "meals_lodging"
    assert bucket("Sales inspection trip") == "sales_inspection_trips"


def test_labor_task_classification() -> None:
    assert bucket("Pressure wash prep labor", section="Labor / Subcontractor") == "labor_prep"
    assert bucket("Top Coat", section="Labor / Subcontractor", source_row=130) == "labor_top_coat"
    assert bucket("Traveling", section="Labor / Subcontractor", source_row=139) == "labor_traveling"


def test_ambiguous_line_marked_review() -> None:
    result = classify_template_line_item(row("Fabric seam coating", quantity=None, unit=None, extended_cost=200))

    assert result.needs_review is True
    assert result.classification_confidence < 0.8


def test_aggregation_by_bucket_and_cost_per_sqft() -> None:
    classified = classify_line_items(
        pd.DataFrame(
            [
                row("Silicone coating", job_id="J1", extended_cost=1000),
                row("Boom lift rental", job_id="J1", extended_cost=500),
                row("Prep labor", job_id="J1", section="Labor / Subcontractor", extended_cost=800),
            ]
        )
    )

    summary = summarize_classified_by_job(classified, {"J1": 1000})
    job_summary = summary["job_bucket_summary"]

    assert set(job_summary["template_bucket"]) >= {"coating", "lift", "labor_prep"}
    assert job_summary[job_summary["template_bucket"] == "coating"].iloc[0]["cost_per_sqft"] == 1


def test_similar_job_bucket_summary() -> None:
    line_items = pd.DataFrame(
        [
            row("Silicone coating", job_id="J1", extended_cost=1000),
            row("Silicone coating", job_id="J2", extended_cost=1200),
            row("Boom lift rental", job_id="J1", extended_cost=400),
        ]
    )
    similar = pd.DataFrame(
        [
            {"job_id": "J1", "estimated_sqft": 1000},
            {"job_id": "J2", "estimated_sqft": 1200},
        ]
    )

    summary = summarize_similar_job_buckets(line_items, similar)
    bucket_summary = summary["bucket_summary"]

    coating = bucket_summary[bucket_summary["template_bucket"] == "coating"].iloc[0]
    assert coating["frequency"] == 2
    assert coating["median_cost_per_sqft"] == 1
    assert not summary["classified_rows"].empty


def test_similar_job_bucket_summary_uses_selected_item_name_without_item_name() -> None:
    line_items = pd.DataFrame(
        [
            {
                "job_id": "J1",
                "template_bucket": "coating",
                "template_section": "materials",
                "line_item_kind": "material",
                "selected_item_name": "High Solids Silicone",
                "estimated_cost": 1200,
                "needs_review": False,
            }
        ]
    )
    similar = pd.DataFrame([{"job_id": "J1", "estimated_sqft": 1200}])

    summary = summarize_similar_job_buckets(line_items, similar)
    common = summary["common_items"].iloc[0]
    bucket = summary["bucket_summary"].iloc[0]

    assert common["item_display_name"] == "High Solids Silicone"
    assert common["raw_item_name"] == "High Solids Silicone"
    assert common["count"] == 1
    assert common["median_line_total"] == 1200
    assert bucket["median_total_cost"] == 1200


def test_similar_job_bucket_summary_falls_back_to_row_label() -> None:
    line_items = pd.DataFrame(
        [
            {
                "job_id": "J1",
                "template_bucket": "labor_prep",
                "template_section": "labor",
                "line_item_kind": "labor",
                "selected_item_name": "",
                "row_label": "Prep Days",
                "estimated_cost": 800,
                "needs_review": False,
            }
        ]
    )
    similar = pd.DataFrame([{"job_id": "J1", "estimated_sqft": 1000}])

    summary = summarize_similar_job_buckets(line_items, similar)

    assert summary["common_items"].iloc[0]["item_display_name"] == "Prep Days"


def test_similar_job_bucket_summary_falls_back_to_template_bucket() -> None:
    line_items = pd.DataFrame(
        [
            {
                "job_id": "J1",
                "template_bucket": "primer",
                "template_section": "materials",
                "line_item_kind": "material",
                "estimated_cost": 300,
                "needs_review": False,
            }
        ]
    )
    similar = pd.DataFrame([{"job_id": "J1", "estimated_sqft": 1000}])

    summary = summarize_similar_job_buckets(line_items, similar)

    assert summary["common_items"].iloc[0]["item_display_name"] == "primer"


def test_similar_job_bucket_summary_prefers_total_cost_when_present() -> None:
    line_items = pd.DataFrame(
        [
            {
                "job_id": "J1",
                "template_bucket": "coating",
                "template_section": "materials",
                "line_item_kind": "material",
                "selected_item_name": "Silicone",
                "line_total": float("nan"),
                "estimated_cost": 200,
                "total_cost": 500,
                "needs_review": False,
            }
        ]
    )
    similar = pd.DataFrame([{"job_id": "J1", "estimated_sqft": 1000}])

    summary = summarize_similar_job_buckets(line_items, similar)

    assert summary["common_items"].iloc[0]["median_total_cost"] == 500
    assert summary["bucket_summary"].iloc[0]["median_total_cost"] == 500


def test_similar_job_bucket_summary_empty_inputs_return_stable_columns() -> None:
    summary = summarize_similar_job_buckets(pd.DataFrame(), pd.DataFrame([{"job_id": "J1"}]))

    assert summary["bucket_summary"].empty
    assert "median_total_cost" in summary["bucket_summary"].columns
    assert summary["common_items"].empty
    assert "item_display_name" in summary["common_items"].columns
    assert "median_line_total" in summary["common_items"].columns


def test_similar_job_bucket_summary_no_matching_jobs_return_stable_columns() -> None:
    line_items = pd.DataFrame(
        [
            {
                "job_id": "J1",
                "template_bucket": "coating",
                "selected_item_name": "Silicone",
                "estimated_cost": 100,
            }
        ]
    )
    similar = pd.DataFrame([{"job_id": "J2", "estimated_sqft": 1000}])

    summary = summarize_similar_job_buckets(line_items, similar)

    assert summary["bucket_summary"].empty
    assert "template_bucket" in summary["bucket_summary"].columns
    assert summary["common_items"].empty
    assert "item_display_name" in summary["common_items"].columns


def test_classification_row_has_template_fields() -> None:
    classified = classification_row_from_line_item(
        row(
            "High solids silicone coating",
            line_item_id="LI1",
            estimate_id="E1",
            source_sheet="Estimate",
            source_row=28,
            extended_cost=123.45,
        )
    )

    assert classified["line_item_id"] == "LI1"
    assert classified["template_bucket"] == "coating"
    assert classified["line_item_kind"] == "material"
    assert classified["template_row_hint"] == "Estimate!28"
    assert classified["line_total"] == 123.45


def test_subcontractor_line_item_kind() -> None:
    classified = classification_row_from_line_item(row("Subcontractor crane assist", description="subcontracted lift support"))

    assert classified["line_item_kind"] == "subcontractor"


def create_sqlite_classification_schema(engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE estimate_line_items (
                    line_item_id TEXT PRIMARY KEY,
                    estimate_id TEXT,
                    job_id TEXT,
                    estimate_file TEXT,
                    section TEXT,
                    line_item_category TEXT,
                    line_item_name TEXT,
                    description TEXT,
                    quantity NUMERIC,
                    unit TEXT,
                    unit_cost NUMERIC,
                    unit_price NUMERIC,
                    extended_cost NUMERIC,
                    labor_days NUMERIC,
                    crew_size NUMERIC,
                    labor_hours NUMERIC,
                    vendor TEXT,
                    notes TEXT,
                    source_sheet TEXT,
                    source_row INTEGER,
                    raw TEXT
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE estimate_line_item_classifications (
                    line_item_id TEXT PRIMARY KEY,
                    job_id TEXT,
                    estimate_id TEXT,
                    source_file TEXT,
                    sheet_name TEXT,
                    row_number INTEGER,
                    raw_item_name TEXT,
                    raw_description TEXT,
                    normalized_item_name TEXT,
                    template_bucket TEXT,
                    template_section TEXT,
                    template_row_hint TEXT,
                    line_item_kind TEXT,
                    quantity NUMERIC,
                    unit TEXT,
                    unit_price NUMERIC,
                    line_total NUMERIC,
                    classification_confidence NUMERIC,
                    classification_reason TEXT,
                    needs_review BOOLEAN DEFAULT FALSE,
                    classifier_version TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )


def test_database_upsert_preserves_raw_line_items_and_is_idempotent() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    create_sqlite_classification_schema(engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO estimate_line_items (
                    line_item_id, estimate_id, job_id, estimate_file, section,
                    line_item_name, quantity, unit, unit_price, extended_cost,
                    source_sheet, source_row, raw
                )
                VALUES (
                    'LI1', 'E1', 'J1', 'Estimate.xlsx', 'Materials',
                    'Silicone coating', 10, 'gal', 30, 300,
                    'Estimate', 28, '{"original": true}'
                )
                """
            )
        )

    first = classify_existing_line_items(engine)
    second = classify_existing_line_items(engine)

    assert first["rows_upserted"] == 1
    assert second["rows_upserted"] == 1
    with engine.connect() as conn:
        raw_value = conn.execute(text("SELECT raw FROM estimate_line_items WHERE line_item_id = 'LI1'")).scalar_one()
        count = conn.execute(text("SELECT COUNT(*) FROM estimate_line_item_classifications")).scalar_one()
        bucket = conn.execute(text("SELECT template_bucket FROM estimate_line_item_classifications")).scalar_one()
    assert raw_value == '{"original": true}'
    assert count == 1
    assert bucket == "coating"


def test_load_classified_line_items_for_job() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    create_sqlite_classification_schema(engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO estimate_line_item_classifications (
                    line_item_id, job_id, estimate_id, raw_item_name, template_bucket,
                    template_section, line_item_kind, line_total, needs_review
                )
                VALUES ('LI1', 'J1', 'E1', 'Primer', 'primer', 'materials', 'material', 120, false)
                """
            )
        )

    df = load_classified_line_items_for_job(engine, "J1")

    assert len(df) == 1
    assert df.iloc[0]["template_bucket"] == "primer"


def test_database_loader_falls_back_to_local_files_when_not_strict(tmp_path) -> None:
    output = tmp_path / "output"
    output.mkdir()
    (output / "job_index.json").write_text('[{"job_id": "J1", "job_name": "Fallback Job"}]', encoding="utf-8")
    (output / "estimate_line_items.json").write_text(
        '[{"job_id": "J1", "line_item_name": "Silicone coating", "extended_cost": 100}]',
        encoding="utf-8",
    )

    data = load_estimator_data(tmp_path, database_url="sqlite:///:memory:")

    assert len(data.jobs) == 1
    assert len(data.line_items) == 1
    assert len(data.classified_line_items) == 1
    assert any("using local staging files" in warning for warning in data.warnings)
