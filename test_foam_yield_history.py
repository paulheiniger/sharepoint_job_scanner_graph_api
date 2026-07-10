from __future__ import annotations

import pandas as pd

from jobscan.estimator.data_loader import normalize_estimator_data
from jobscan.estimator.foam_yield_history import build_foam_yield_history_digest, build_foam_yield_history_table
from jobscan.estimator.schemas import EstimatorData


def foam_history_data() -> EstimatorData:
    return EstimatorData(
        template_rows=pd.DataFrame(
            [
                {
                    "template_row_id": "roof-foam-1",
                    "job_id": "R1",
                    "source_file": "roofing.xlsx",
                    "template_type": "roofing",
                    "row_number": 19,
                    "template_bucket": "foam",
                    "line_item_kind": "material",
                    "resolved_item_name": "Gaco Roof 2.7",
                    "area_sqft": 9600,
                    "thickness_inches": 1.5,
                    "yield_or_coverage": 17058.8,
                    "estimated_units": 844.44,
                    "estimated_sets": 0.84444,
                    "unit_price": 2.1,
                    "formula_model": "foam_sets_from_area_thickness_yield",
                },
                {
                    "template_row_id": "insulation-foam-1",
                    "job_id": "I1",
                    "source_file": "insulation.xlsx",
                    "template_type": "insulation",
                    "row_number": 19,
                    "template_bucket": "foam",
                    "line_item_kind": "material",
                    "resolved_item_name": "Gaco 0.5 lb.",
                    "area_sqft": 2226,
                    "thickness_inches": 5.5,
                    "yield_or_coverage": 4500,
                    "estimated_units": 2720.6667,
                    "unit_price": 1.9,
                    "formula_model": "foam_sets_from_area_thickness_yield",
                },
            ]
        )
    )


def test_foam_yield_history_table_mines_roofing_and_insulation_rows() -> None:
    history = build_foam_yield_history_table(foam_history_data())

    assert set(history["template_type"]) == {"roofing", "insulation"}
    assert {
        "product",
        "thickness_inches",
        "square_feet",
        "estimated_yield",
        "estimated_sets",
    }.issubset(history.columns)

    roofing = history.loc[history["template_type"] == "roofing"].iloc[0]
    assert roofing["product"] == "Gaco Roof 2.7"
    assert roofing["thickness_inches"] == 1.5
    assert roofing["square_feet"] == 9600
    assert roofing["estimated_yield"] == 17058.8
    assert roofing["estimated_sets"] == 0.84444

    insulation = history.loc[history["template_type"] == "insulation"].iloc[0]
    assert insulation["product"] == "Gaco 0.5 lb."
    assert insulation["estimated_sets"] == 2.720667


def test_normalize_estimator_data_derives_foam_yield_history_from_template_rows() -> None:
    data = normalize_estimator_data(foam_history_data())

    assert not data.foam_yield_history.empty
    assert set(data.foam_yield_history["template_type"]) == {"roofing", "insulation"}


def test_roofing_foam_yield_digest_uses_mined_history_examples() -> None:
    data = normalize_estimator_data(foam_history_data())

    digest = build_foam_yield_history_digest(
        data,
        scope={"template_type": "roofing", "foam_thickness_inches": 1.5, "raw_input_notes": "roof SPF with Gaco Roof 2.7"},
        template_type="roofing",
    )

    assert digest
    assert digest[0]["template_option"] == "Gaco Roof 2.7"
    assert digest[0]["median_square_feet"] == 9600
    assert digest[0]["median_estimated_sets"] == 0.84444
    assert digest[0]["examples"][0]["square_feet"] == 9600
    assert digest[0]["examples"][0]["estimated_sets"] == 0.84444
