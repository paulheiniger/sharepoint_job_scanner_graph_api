from __future__ import annotations

import pandas as pd

from jobscan.estimator.line_items import (
    classify_line_items,
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
