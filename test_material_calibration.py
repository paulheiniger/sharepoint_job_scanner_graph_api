from __future__ import annotations

import pandas as pd

from jobscan.estimator.material_calibration import build_material_calibration
from jobscan.estimator.schemas import EstimatorData


def calibration_data() -> EstimatorData:
    jobs = pd.DataFrame(
        [
            {"job_id": "J1", "estimated_sqft": 10000},
            {"job_id": "J2", "estimated_sqft": 12000},
            {"job_id": "J3", "estimated_sqft": 8000},
        ]
    )
    template_rows = pd.DataFrame(
        [
            {"job_id": "J1", "selected_item_name": "Rust primer", "line_item_kind": "material", "quantity": 20, "unit": "gal", "unit_price": 42, "estimated_cost": 840},
            {"job_id": "J2", "selected_item_name": "Epoxy primer", "line_item_kind": "material", "quantity": 24, "unit": "gal", "unit_price": 40, "estimated_cost": 960},
            {"job_id": "J3", "selected_item_name": "Primer", "line_item_kind": "material", "quantity": 16, "unit": "gal", "unit_price": 44, "estimated_cost": 704},
            {"job_id": "J1", "selected_item_name": "Seam tape", "line_item_kind": "material", "quantity": 800, "unit": "lf", "unit_price": 2.5, "estimated_cost": 2000},
            {"job_id": "J2", "selected_item_name": "Seam sealer", "line_item_kind": "material", "quantity": 960, "unit": "lf", "unit_price": 2.75, "estimated_cost": 2640},
            {"job_id": "J3", "selected_item_name": "Detail tape", "line_item_kind": "material", "quantity": 640, "unit": "lf", "unit_price": 3, "estimated_cost": 1920},
            {"job_id": "J1", "selected_item_name": "Fastener screws", "line_item_kind": "material", "quantity": 500, "unit": "ea", "unit_price": 1.5, "estimated_cost": 750},
            {"job_id": "J2", "selected_item_name": "Rusted fasteners", "line_item_kind": "material", "quantity": 600, "unit": "ea", "unit_price": 1.6, "estimated_cost": 960},
            {"job_id": "J3", "selected_item_name": "Washer fastener detail", "line_item_kind": "material", "quantity": 400, "unit": "ea", "unit_price": 1.4, "estimated_cost": 560},
            {"job_id": "J1", "template_bucket": "labor_prep", "line_item_kind": "labor", "quantity": 99, "estimated_cost": 9999},
        ]
    )
    pricing = pd.DataFrame(
        [
            {"pricing_item_id": "P1", "product_name": "Rust Primer", "category": "Primer", "unit_price": 45, "status": "active", "is_current": True, "needs_review": False},
            {"pricing_item_id": "P2", "product_name": "Seam Sealer", "category": "Seam", "unit_price": 3, "status": "active", "is_current": True, "needs_review": False},
            {"pricing_item_id": "P3", "product_name": "Fastener Dab", "category": "Fastener", "price_per_unit": 1.75, "status": "active", "is_current": True, "needs_review": False},
        ]
    )
    return EstimatorData(jobs=jobs, template_rows=template_rows, pricing=pricing, pricing_catalog=pricing)


def test_build_material_calibration_calculates_median_ratios() -> None:
    calibration = build_material_calibration(calibration_data(), {"surface_area_sqft": 9536})

    assert calibration["primer"]["evidence_count"] == 3
    assert calibration["primer"]["matching_historical_rows"] == 3
    assert calibration["primer"]["median_quantity_per_sqft"] == 0.002
    assert calibration["primer"]["median_cost_per_sqft"] == 0.084
    assert calibration["primer"]["selected_current_unit_price"] == 45

    assert calibration["seam_treatment"]["median_quantity_per_sqft"] == 0.08
    assert calibration["seam_treatment"]["median_cost_per_sqft"] == 0.22

    assert calibration["fastener_treatment"]["median_quantity_per_sqft"] == 0.05
    assert calibration["fastener_treatment"]["median_cost_per_sqft"] == 0.075
    assert calibration["fastener_treatment"]["selected_current_unit_price"] == 1.75
