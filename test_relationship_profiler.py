from __future__ import annotations

import json

import pandas as pd

from relationship_profiler import profile_relationships


def test_relationship_profiler_writes_relationship_outputs(tmp_path) -> None:
    estimate_summary = tmp_path / "estimate_summary.csv"
    line_items = tmp_path / "estimate_line_items.csv"
    out_dir = tmp_path / "relationships"

    pd.DataFrame(
        [
            {
                "job_id": "J1",
                "project_type": "roof coating",
                "substrate": "metal",
                "estimated_sqft": 12000,
                "coating_type": "silicone",
                "warranty_years": 10,
                "roof_condition": "rusted",
                "final_price": 100000,
            },
            {
                "job_id": "J2",
                "project_type": "roof coating",
                "substrate": "membrane",
                "estimated_sqft": 8000,
                "coating_type": "silicone",
                "warranty_years": 10,
                "roof_condition": "fair",
                "final_price": 70000,
            },
            {
                "job_id": "J3",
                "project_type": "roof coating",
                "substrate": "metal",
                "estimated_sqft": 10000,
                "coating_type": "silicone",
                "warranty_years": 15,
                "roof_condition": "good",
                "final_price": 90000,
            },
        ]
    ).to_csv(estimate_summary, index=False)

    pd.DataFrame(
        [
            {"job_id": "J1", "section": "Materials", "line_item_name": "Silicone coating", "quantity": 180, "unit": "gal", "unit_cost": 38, "extended_cost": 6840},
            {"job_id": "J1", "section": "Materials", "line_item_name": "Epoxy Primer", "quantity": 40, "unit": "pail", "unit_cost": 5, "extended_cost": 200},
            {"job_id": "J1", "section": "Materials", "line_item_name": "Seam treatment", "quantity": 900, "unit": "lf", "unit_cost": 3, "extended_cost": 2700},
            {"job_id": "J1", "section": "Labor / Subcontractor", "line_item_name": "Prime", "labor_days": 1, "crew_size": 4, "labor_hours": 32, "extended_cost": 2500},
            {"job_id": "J2", "section": "Materials", "line_item_name": "Silicone coating", "quantity": 120, "unit": "gal", "unit_cost": 38, "extended_cost": 4560},
            {"job_id": "J2", "section": "Materials", "line_item_name": "Misc allowance", "quantity": 1, "unit": "allowance", "unit_cost": 500, "extended_cost": 500},
            {"job_id": "J2", "section": "Labor / Subcontractor", "line_item_name": "Prime", "labor_days": 1, "crew_size": 4, "labor_hours": 32, "extended_cost": 2500},
            {"job_id": "J3", "section": "Materials", "line_item_name": "Silicone coating", "quantity": 160, "unit": "gal", "unit_cost": 38, "extended_cost": 6080},
            {"job_id": "J3", "section": "Materials", "line_item_name": "Fastener screws", "quantity": 500, "unit": "ea", "unit_cost": 1.5, "extended_cost": 750},
            {"job_id": "J3", "section": "Labor / Subcontractor", "line_item_name": "Top coat", "labor_days": 2, "crew_size": 4, "labor_hours": 64, "extended_cost": 5200},
        ]
    ).to_csv(line_items, index=False)

    paths = profile_relationships(
        jobs_csv=None,
        estimate_summary_csv=estimate_summary,
        line_items_csv=line_items,
        out_dir=out_dir,
    )

    expected_files = {
        "relationship_warranty_coating.csv",
        "relationship_work_package_cooccurrence.csv",
        "relationship_material_qty_ratios.csv",
        "relationship_labor_rates.csv",
        "relationship_anomalies.csv",
        "estimator_rule_suggestions.json",
    }
    assert expected_files == set(paths)
    assert all(path.exists() for path in paths.values())

    warranty = pd.read_csv(paths["relationship_warranty_coating.csv"])
    assert {"coating_type", "warranty_years", "wet_mils", "median_gal_per_sqft", "job_count", "confidence"}.issubset(warranty.columns)
    assert not warranty.empty

    material_ratios = pd.read_csv(paths["relationship_material_qty_ratios.csv"])
    seam = material_ratios[material_ratios["package"] == "seam_treatment"].iloc[0]
    assert seam["median_qty_per_sqft"] > 0

    labor_rates = pd.read_csv(paths["relationship_labor_rates.csv"])
    assert "median_hours_per_1000_sqft" in labor_rates.columns
    assert not labor_rates.empty

    anomalies = pd.read_csv(paths["relationship_anomalies.csv"])
    assert "primer_pails_implausible" in set(anomalies["anomaly_type"])
    assert "allowance_as_quantity" in set(anomalies["anomaly_type"])
    assert "primer_labor_without_primer_material" in set(anomalies["anomaly_type"])

    suggestions = json.loads(paths["estimator_rule_suggestions.json"].read_text())
    assert "warranty_years_to_wet_mils" in suggestions
    assert "default_production_rates_by_labor_package" in suggestions
