from __future__ import annotations

import json

import pandas as pd
from sqlalchemy import create_engine, inspect

from relationship_profiler import (
    build_material_qty_ratios,
    material_qty_ratios_from_summary,
    profile_relationships,
    profile_relationships_from_database,
    sanitize_frame_for_sql,
)


def assert_no_nested_sql_values(frame: pd.DataFrame) -> None:
    values = frame.to_numpy().ravel()
    assert not any(isinstance(value, (dict, list, tuple, set)) for value in values)


def test_sanitize_estimate_line_items_raw_drops_raw_dict_when_raw_json_exists() -> None:
    raw_payload = {"job_id": "J1", "notes": None, "unit": None, "nested": {"package": "coating"}}
    frame = pd.DataFrame(
        [
            {
                "line_item_id": "L1",
                "raw": raw_payload,
                "raw_json": json.dumps(raw_payload, default=str, sort_keys=True),
            }
        ]
    )

    cleaned = sanitize_frame_for_sql(frame, "estimate_line_items_raw")

    assert "raw" not in cleaned.columns
    assert "raw_json" in cleaned.columns
    assert cleaned.loc[0, "raw_json"] == json.dumps(raw_payload, default=str, sort_keys=True)
    assert_no_nested_sql_values(cleaned)


def test_sanitize_generic_object_columns_serializes_nested_values() -> None:
    frame = pd.DataFrame(
        [
            {
                "row_id": "R1",
                "payload": {"package": "primer", "quantity": 5},
                "source_ids": ["L1", "L2"],
                "tags": {"review", "allowance"},
            }
        ]
    )

    cleaned = sanitize_frame_for_sql(frame, "relationship_debug")

    assert json.loads(cleaned.loc[0, "payload"]) == {"package": "primer", "quantity": 5}
    assert json.loads(cleaned.loc[0, "source_ids"]) == ["L1", "L2"]
    assert isinstance(cleaned.loc[0, "tags"], str)
    assert_no_nested_sql_values(cleaned)


def test_material_qty_ratios_from_summary_tolerates_missing_warranty_years() -> None:
    summary = pd.DataFrame(
        [
            {
                "job_id": "J1",
                "package": "coating",
                "total_quantity": 120,
                "unit": "gal",
                "total_cost": 4560,
                "area_sqft": 8000,
                "division": "Roofing",
                "project_type": "roof coating",
                "substrate": "membrane",
                "coating_type": "silicone",
            }
        ]
    )

    ratios = material_qty_ratios_from_summary(summary)

    assert "warranty_years" in ratios.columns
    assert not ratios.empty
    assert pd.isna(ratios.loc[0, "warranty_years"])


def test_build_material_qty_ratios_groups_without_warranty_years() -> None:
    rows = pd.DataFrame(
        [
            {
                "job_id": "J1",
                "package": "seam_treatment",
                "quantity": 900,
                "unit": "lf",
                "total_cost": 2700,
                "area_sqft": 12000,
                "division": "Roofing",
                "project_type": "roof coating",
                "substrate": "metal",
                "coating_type": "silicone",
                "is_material": True,
            }
        ]
    )

    ratios = build_material_qty_ratios(rows)

    assert not ratios.empty
    assert "warranty_years" in ratios.columns
    assert ratios.loc[0, "package"] == "seam_treatment"
    assert pd.isna(ratios.loc[0, "warranty_years"])


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


def test_relationship_profiler_database_pipeline_uses_normalized_tables(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'relationships.db'}")
    out_dir = tmp_path / "db_relationships"
    pd.DataFrame(
        [
            {
                "job_id": "J1",
                "source_year": 2026,
                "division": "Roofing",
                "pipeline_status": "Completed",
                "status": "Completed",
                "customer": "Acme",
                "job_name": "Acme roof",
                "job_type": "roof coating",
                "estimated_sqft": 12000,
                "invoice_amount": 100000,
            },
            {
                "job_id": "J2",
                "source_year": 2026,
                "division": "Roofing",
                "pipeline_status": "Completed",
                "status": "Completed",
                "customer": "Beta",
                "job_name": "Beta roof",
                "job_type": "roof coating",
                "estimated_sqft": 8000,
                "invoice_amount": 70000,
            },
        ]
    ).to_sql("jobs", engine, index=False)
    pd.DataFrame(
        [
            {"estimate_id": "E1", "job_id": "J1", "project_type": "roof coating", "substrate": "metal", "estimated_sqft": 12000, "coating_type": "silicone", "warranty_years": 10, "final_price": 100000},
            {"estimate_id": "E2", "job_id": "J2", "project_type": "roof coating", "substrate": "membrane", "estimated_sqft": 8000, "coating_type": "silicone", "warranty_years": 10, "final_price": 70000},
        ]
    ).to_sql("estimates", engine, index=False)
    pd.DataFrame(
        [
            {"line_item_id": "L1", "estimate_id": "E1", "job_id": "J1", "estimate_file": "Estimate 1.xlsx", "source_sheet": "Estimate", "source_row": 26, "section": "Materials", "line_item_name": "Silicone coating", "quantity": 180, "unit": "gal", "unit_cost": 38, "extended_cost": 6840},
            {"line_item_id": "L2", "estimate_id": "E1", "job_id": "J1", "estimate_file": "Estimate 1.xlsx", "source_sheet": "Estimate", "source_row": 39, "section": "Materials", "line_item_name": "Epoxy Primer", "quantity": 40, "unit": "pail", "unit_cost": 5, "extended_cost": 200},
            {"line_item_id": "L3", "estimate_id": "E1", "job_id": "J1", "estimate_file": "Estimate 1.xlsx", "source_sheet": "Estimate", "source_row": 116, "section": "Labor / Subcontractor", "line_item_name": "Prime", "labor_days": 1, "crew_size": 4, "labor_hours": 32, "extended_cost": 2500},
            {"line_item_id": "L4", "estimate_id": "E2", "job_id": "J2", "estimate_file": "Estimate 2.xlsx", "source_sheet": "Estimate", "source_row": 26, "section": "Materials", "line_item_name": "Silicone coating", "quantity": 120, "unit": "gal", "unit_cost": 38, "extended_cost": 4560},
            {"line_item_id": "L5", "estimate_id": "E2", "job_id": "J2", "estimate_file": "Estimate 2.xlsx", "source_sheet": "Estimate", "source_row": 173, "section": "Materials", "line_item_name": "Misc allowance", "quantity": 1, "unit": "allowance", "unit_cost": 500, "extended_cost": 500},
            {"line_item_id": "L6", "estimate_id": "E2", "job_id": "J2", "estimate_file": "Estimate 2.xlsx", "source_sheet": "Estimate", "source_row": 116, "section": "Labor / Subcontractor", "line_item_name": "Prime", "labor_days": 1, "crew_size": 4, "labor_hours": 32, "extended_cost": 2500},
        ]
    ).to_sql("estimate_line_items", engine, index=False)

    paths = profile_relationships_from_database(
        engine=engine,
        out_dir=out_dir,
        source_year="2026",
        division="Roofing",
        status="Completed",
        min_job_count=1,
        write_review_sheet=True,
    )

    inspector = inspect(engine)
    for table in [
        "source_documents",
        "estimate_line_items_raw",
        "estimate_line_items_normalized",
        "estimate_jobs",
        "job_package_summary",
        "relationship_warranty_coating",
        "relationship_package_cooccurrence",
    ]:
        assert inspector.has_table(table)

    normalized = pd.read_sql_table("estimate_line_items_normalized", engine)
    assert {"raw_line_item_id", "source_document_id", "source_type", "physical_quantity_valid", "normalization_reason"}.issubset(normalized.columns)
    assert "cost_allowance" in set(normalized["source_type"])

    package_summary = pd.read_sql_table("job_package_summary", engine)
    assert {"job_id", "package", "qty_per_sqft", "cost_per_sqft", "evidence_line_item_ids"}.issubset(package_summary.columns)

    anomalies = pd.read_csv(paths["relationship_anomalies.csv"])
    assert "primer_pails_implausible" in set(anomalies["anomaly_type"])
    assert "allowance_as_quantity" in set(anomalies["anomaly_type"])
    assert "primer_labor_without_primer_material" in set(anomalies["anomaly_type"])
    assert paths["relationship_review_sheet.xlsx"].exists()
