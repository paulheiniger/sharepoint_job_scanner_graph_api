from __future__ import annotations

import json

import pandas as pd
import pytest

from jobscan.estimator.data_loader import load_estimator_data
from jobscan.estimator.decision_tree import evaluate_decision_tree
from jobscan.estimator.estimate import build_estimate
from jobscan.estimator.labor import estimate_labor, estimate_travel_impact
from jobscan.estimator.materials import classify_line_item, coating_gallons, estimate_materials
from jobscan.estimator.rules import extract_scope, parse_sqft
from jobscan.estimator.schemas import EstimatorData
from jobscan.estimator.similarity import find_similar_jobs


def write_json(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows), encoding="utf-8")


def sample_data() -> EstimatorData:
    jobs = pd.DataFrame(
        [
            {
                "job_id": "J1",
                "customer": "Acme",
                "job_name": "Acme metal roof silicone",
                "division": "ROOFING",
                "pipeline_status": "Completed",
                "status": "Completed",
                "job_type": "roof coating",
                "estimated_sqft": 10000,
                "estimated_value": 95000,
                "price_per_sqft": 9.5,
                "city": "Louisville",
            },
            {
                "job_id": "J2",
                "customer": "Warehouse",
                "job_name": "Warehouse wall foam",
                "division": "WALLS",
                "job_type": "wall insulation",
                "estimated_sqft": 9000,
            },
        ]
    )
    estimates = pd.DataFrame(
        [
            {
                "job_id": "J1",
                "estimate_id": "E1",
                "estimate_file": "estimate.xlsx",
                "coating_type": "silicone",
                "coating_required": True,
                "estimated_sqft": 10000,
                "labor_subtotal": 14400,
                "estimated_labor_hours": 180,
                "estimated_duration_days": 5,
                "estimated_crew_size": 4,
            }
        ]
    )
    line_items = pd.DataFrame(
        [
            {"job_id": "J1", "section": "Materials", "line_item_name": "Silicone coating", "extended_cost": 18000, "unit_price": 38, "unit": "gal"},
            {"job_id": "J1", "section": "Labor", "line_item_name": "Crew labor", "extended_cost": 12000, "labor_hours": 180},
            {"job_id": "J1", "section": "Equipment", "line_item_name": "Lift rental", "extended_cost": 1500},
        ]
    )
    pricing = pd.DataFrame(
        [
            {
                "pricing_item_id": "P1",
                "product_name": "GAF High Solids Silicone",
                "category": "Coatings",
                "unit_price": 190,
                "price_per_gallon": 38,
                "price_per_sqft": None,
                "status": "active",
                "is_current": "true",
                "needs_review": "false",
            }
        ]
    )
    tracking = pd.DataFrame([{"job_id": "J1", "actual_labor_hours": 190}])
    return EstimatorData(jobs=jobs, estimates=estimates, line_items=line_items, tracking_summary=tracking, pricing=pricing)


def test_loads_staging_files_when_present(tmp_path) -> None:
    write_json(tmp_path / "output/job_index.json", [{"job_id": "J1"}])
    write_json(tmp_path / "output/estimate_summary.json", [{"estimate_id": "E1"}])
    write_json(tmp_path / "output/estimate_line_items.json", [{"line_item_name": "Coating"}])
    write_json(tmp_path / "output/job_tracking_summary.json", [{"job_id": "J1"}])
    write_json(tmp_path / "output/job_tracking_daily_entries.json", [{"job_id": "J1"}])
    pricing_path = tmp_path / "output/pricing/pricing_catalog_current_cleaned.csv"
    pricing_path.parent.mkdir(parents=True)
    pricing_path.write_text("pricing_item_id,product_name,is_current,status,needs_review\nP1,Silicone,true,active,false\n", encoding="utf-8")

    data = load_estimator_data(tmp_path)

    assert len(data.jobs) == 1
    assert len(data.pricing) == 1
    assert "output/job_index.json" in data.source_files_used


def test_missing_staging_files_are_graceful(tmp_path) -> None:
    data = load_estimator_data(tmp_path)

    assert data.jobs.empty
    assert data.warnings


def test_parse_sqft_and_detect_scope_fields() -> None:
    scope = extract_scope("Metal roof, about 12,000 sqft, rusted fasteners, silicone coating, medium access in Louisville")

    assert parse_sqft("about 12,000 sqft") == 12000
    assert scope["substrate"] == "metal"
    assert scope["coating_type"] == "silicone"
    assert scope["roof_condition"] == "poor/rusted"
    assert scope["access_complexity"] == "medium"


def test_similar_job_ranking() -> None:
    data = sample_data()
    scope = extract_scope("Metal roof 12,000 sqft silicone coating in Louisville")

    similar = find_similar_jobs(data, scope)

    assert similar.iloc[0]["job_id"] == "J1"
    assert similar.iloc[0]["similarity_score"] > 40


def test_line_item_category_grouping() -> None:
    assert classify_line_item({"line_item_name": "Silicone coating"}) == "materials"
    assert classify_line_item({"section": "Labor", "line_item_name": "Crew days"}) == "labor"
    assert classify_line_item({"line_item_name": "Lift rental"}) == "equipment"


def test_coating_gallons_formula() -> None:
    gallons = coating_gallons(12000, 24, 0.12)

    assert round(gallons, 1) == 201.1


def test_current_pricing_preferred_over_historical() -> None:
    data = sample_data()
    scope = extract_scope("Metal roof 12,000 sqft silicone coating")

    materials = estimate_materials(scope, data.pricing, data.line_items)

    assert materials["material_items"][0]["price_source_type"] == "current_pricing"
    assert materials["material_items"][0]["unit_price"] == 38
    assert materials["needs_pricing_review"] is False


def test_historical_fallback_marked_for_review() -> None:
    data = sample_data()
    scope = extract_scope("Metal roof 12,000 sqft silicone coating")

    materials = estimate_materials(scope, pd.DataFrame(), data.line_items)

    assert materials["material_items"][0]["price_source_type"] == "historical_fallback"
    assert materials["material_items"][0]["needs_pricing_review"] is True


def test_labor_hours_inferred_from_labor_subtotal() -> None:
    similar = pd.DataFrame([{"job_id": "J1", "labor_subtotal": 7200}])

    labor = estimate_labor(extract_scope("roof 5000 sqft"), similar, pd.DataFrame())

    assert labor["labor_hours_inferred"] is True
    assert labor["estimated_labor_hours_low"] > 0


def test_crew_duration_estimate_generated() -> None:
    labor = estimate_labor(extract_scope("roof coating 12000 sqft"), pd.DataFrame(), pd.DataFrame())

    assert labor["recommended_crew_size"] >= 1
    assert labor["estimated_duration_days_high"] >= labor["estimated_duration_days_low"] >= 1


def test_missing_sqft_triggers_human_review() -> None:
    scope = extract_scope("metal roof silicone coating in Louisville")

    assert "surface_area_sqft" in scope["missing_info"]
    assert scope["human_review_required"] is True


def test_estimate_range_generation() -> None:
    result = build_estimate("Metal roof, about 12,000 sqft, silicone coating in Louisville", sample_data())

    assert result["estimate_range"]["estimate_high"] > result["estimate_range"]["estimate_low"] > 0
    assert result["similar_jobs"].iloc[0]["job_id"] == "J1"


def test_build_estimate_uses_template_rows_when_classifications_missing() -> None:
    data = EstimatorData(
        jobs=pd.DataFrame(
            [
                {
                    "job_id": "J1",
                    "division": "Roofing",
                    "job_name": "Metal Roof Silicone",
                    "job_type": "roof coating",
                    "estimated_sqft": 10000,
                    "estimated_value": 100000,
                }
            ]
        ),
        template_rows=pd.DataFrame(
            [
                {
                    "template_row_id": "T1",
                    "job_id": "J1",
                    "template_bucket": "coating",
                    "template_section": "materials",
                    "line_item_kind": "material",
                    "selected_item_name": "Silicone",
                    "estimated_cost": 1200,
                    "needs_review": False,
                }
            ]
        ),
        pricing=pd.DataFrame(),
    )

    result = build_estimate("Roofing metal roof 10,000 sqft silicone coating", data)
    bucket_summary = result["template_line_item_summary"]["bucket_summary"]

    assert not bucket_summary.empty
    assert bucket_summary.iloc[0]["template_bucket"] == "coating"


def test_missing_address_triggers_travel_review() -> None:
    travel = estimate_travel_impact({}, recommended_crew_size=4, estimated_work_days=3)

    assert travel["needs_travel_review"] is True
    assert travel["travel_distance_bucket"] == "unknown"


def test_local_city_has_low_travel_impact() -> None:
    travel = estimate_travel_impact({"location": "Shelbyville, KY"}, recommended_crew_size=4, estimated_work_days=3)

    assert travel["travel_distance_bucket"] == "local"
    assert travel["estimated_round_trip_miles"] == 0


def test_distant_city_triggers_lodging_review() -> None:
    travel = estimate_travel_impact({"location": "Indianapolis, IN"}, recommended_crew_size=4, estimated_work_days=3)

    assert travel["lodging_required_possible"] is True
    assert travel["needs_travel_review"] is True


def test_round_trip_miles_and_labor_scale_with_crew_not_production_days() -> None:
    one = estimate_travel_impact({"location": "Louisville, KY"}, recommended_crew_size=2, estimated_work_days=1)
    two = estimate_travel_impact({"location": "Louisville, KY"}, recommended_crew_size=4, estimated_work_days=2)

    assert one["estimated_round_trip_miles"] == one["estimated_one_way_miles"] * 2
    assert two["travel_labor_hours"] == pytest.approx(one["travel_labor_hours"] * 2, abs=0.2)


def test_address_route_mileage_uses_mapbox_before_city_fallback(monkeypatch) -> None:
    from jobscan.estimator import labor

    calls = []

    def fake_mapbox_one_way(origin, destination):
        calls.append((origin, destination))
        return 123.4

    monkeypatch.setattr(labor, "mapbox_one_way_miles", fake_mapbox_one_way)

    travel = labor.estimate_travel_impact(
        {"site_address": "314 E Aberdeen Drive, Trenton, OH"},
        recommended_crew_size=2,
        estimated_work_days=1,
    )

    assert calls == [("1132 Equity Street, Shelbyville, KY", "314 E Aberdeen Drive, Trenton, OH")]
    assert travel["estimated_one_way_miles"] == 123.4
    assert travel["estimated_round_trip_miles"] == 246.8


def test_no_insulation_increases_foam_review() -> None:
    scope = extract_scope("metal roof 10000 sqft no insulation condensation concern silicone coating")

    decision = evaluate_decision_tree(scope)

    assert decision["condition_flags"]["insulation_missing"] is True
    assert "Foam or insulation design review required" in decision["human_review_flags"]
    assert decision["material_assumptions"]["foam_thickness_inches"] == 1.5


def test_warranty_target_changes_coating_assumptions() -> None:
    ten_year = evaluate_decision_tree(extract_scope("metal roof 10000 sqft silicone coating 10 year warranty"))
    twenty_year = evaluate_decision_tree(extract_scope("metal roof 10000 sqft silicone coating 20 year warranty"))

    assert ten_year["material_assumptions"]["coating_wet_mils"] == 24
    assert twenty_year["material_assumptions"]["coating_wet_mils"] == 30
    assert "20-year warranty target" in " ".join(twenty_year["recommended_scope"])


def test_poor_condition_triggers_tearoff_review() -> None:
    decision = evaluate_decision_tree(extract_scope("poor leaking metal roof 12000 sqft silicone coating"))

    assert "Tear-off or substrate repair review required" in decision["human_review_flags"]
    assert decision["labor_modifiers"]["prep_condition_multiplier"] > 1


def test_high_access_increases_labor_setup() -> None:
    low = evaluate_decision_tree(extract_scope("metal roof 12000 sqft silicone coating easy access"))
    high = evaluate_decision_tree(extract_scope("metal roof 12000 sqft silicone coating difficult access"))

    assert high["labor_modifiers"]["access_multiplier"] > low["labor_modifiers"]["access_multiplier"]
    assert high["labor_modifiers"]["combined_labor_multiplier"] > low["labor_modifiers"]["combined_labor_multiplier"]
    assert "difficult access" in " ".join(high["recommended_scope"]).lower()


def test_many_penetrations_increases_detail_labor() -> None:
    decision = evaluate_decision_tree(extract_scope("roof coating 8000 sqft many penetrations silicone"))

    assert decision["labor_modifiers"]["penetration_detail_multiplier"] > 1
    assert "penetrations" in " ".join(decision["recommended_scope"]).lower()


def test_metal_roof_rust_triggers_seam_fastener_primer_recommendation() -> None:
    decision = evaluate_decision_tree(extract_scope("metal roof 12000 sqft rusted fasteners silicone coating"))

    scope_text = " ".join(decision["recommended_scope"]).lower()
    assert "fastener" in scope_text
    assert "seam" in scope_text
    assert "primer" in scope_text
    assert decision["material_assumptions"]["primer_allowance_recommended"] is True


def test_historical_jobs_are_evidence_not_only_estimate_driver() -> None:
    result = build_estimate("metal roof 20000 sqft no insulation condensation concern silicone coating 20 year warranty difficult access", sample_data())

    assert result["similar_jobs"].iloc[0]["job_id"] == "J1"
    assert result["decision_tree"]["human_review_flags"]
    assert result["decision_tree"]["material_assumptions"]["coating_wet_mils"] == 30
    assert result["decision_tree"]["labor_modifiers"]["combined_labor_multiplier"] > 1
    assert result["calibration"]["evidence_job_count"] > 0
