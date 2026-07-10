from __future__ import annotations

import json

import pandas as pd
from sqlalchemy import create_engine, inspect

from relationship_profiler import (
    build_job_package_summary,
    build_labor_rates,
    build_material_qty_ratios,
    build_missing_job_context,
    build_rule_suggestions,
    material_qty_ratios_from_summary,
    normalize_raw_line_items,
    profile_relationships,
    profile_relationships_from_database,
    sanitize_frame_for_sql,
    write_table,
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


def test_write_table_preserves_existing_table_object_dependencies(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'writer.db'}")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE existing_rows (id TEXT, name TEXT)")
        conn.exec_driver_sql("CREATE INDEX existing_rows_name_idx ON existing_rows(name)")
        conn.exec_driver_sql("CREATE VIEW existing_rows_view AS SELECT id, name FROM existing_rows")
        conn.exec_driver_sql("INSERT INTO existing_rows (id, name) VALUES ('old', 'Old Row')")

    write_table(
        engine,
        "existing_rows",
        pd.DataFrame([{"id": "new", "name": "New Row", "new_metric": 12.5}]),
    )

    with engine.connect() as conn:
        rows = conn.exec_driver_sql("SELECT id, name, new_metric FROM existing_rows").fetchall()
        view_rows = conn.exec_driver_sql("SELECT id, name FROM existing_rows_view").fetchall()
        index_rows = conn.exec_driver_sql("PRAGMA index_list(existing_rows)").fetchall()

    assert rows == [("new", "New Row", 12.5)]
    assert view_rows == [("new", "New Row")]
    assert any(row[1] == "existing_rows_name_idx" for row in index_rows)


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


def test_template_rows_use_estimated_units_as_material_quantity() -> None:
    raw = pd.DataFrame(
        [
            {
                "line_item_id": "T1",
                "source_type_table": "estimate_template_rows",
                "job_id": "J1",
                "template_type": "roofing",
                "template_bucket": "coating",
                "line_item_kind": "material",
                "selected_item_name": "Gaco Silicone",
                "quantity": 10000,
                "estimated_units": 125,
                "unit": None,
                "unit_cost": 42,
                "extended_cost": 5250,
            }
        ]
    )

    normalized = normalize_raw_line_items(raw)
    row = normalized.iloc[0]

    assert row["package"] == "coating"
    assert row["quantity"] == 125
    assert row["scope_quantity"] == 10000
    assert row["unit"] == "gal"
    assert row["source_type"] == "physical_quantity"
    assert bool(row["physical_quantity_valid"]) is True


def test_job_package_summary_preserves_context_and_hours_per_sqft() -> None:
    normalized = pd.DataFrame(
        [
            {
                "normalized_line_item_id": "N1",
                "job_id": "J1",
                "package": "labor_foam",
                "line_type": "labor",
                "labor_hours": 48,
                "labor_days": 2,
                "crew_size": 3,
                "total_cost": 2400,
                "quantity": None,
                "unit": "",
                "physical_quantity_valid": False,
                "source_type": "labor_budget",
                "review_required": False,
            }
        ]
    )
    jobs = pd.DataFrame(
        [
            {
                "job_id": "J1",
                "source_year": 2026,
                "division": "Insulation",
                "pipeline_status": "Completed",
                "status": "Completed",
                "template_type": "insulation",
                "project_type": "wall insulation",
                "substrate": "wall",
                "area_sqft": 1200,
            }
        ]
    )

    summary = build_job_package_summary(normalized, jobs)

    row = summary.iloc[0]
    assert row["package"] == "labor_foam"
    assert row["template_type"] == "insulation"
    assert row["area_sqft"] == 1200
    assert row["total_hours"] == 48
    assert row["hours_per_sqft"] == 0.04


def test_specific_labor_buckets_generate_labor_rates() -> None:
    summary = pd.DataFrame(
        [
            {
                "job_id": "J1",
                "source_year": 2026,
                "division": "Insulation",
                "template_type": "insulation",
                "project_type": "wall insulation",
                "substrate": "wall",
                "package": "labor_foam",
                "unit": "",
                "area_sqft": 1200,
                "total_hours": 48,
                "total_cost": 2400,
                "total_days": 2,
                "crew_size": 3,
                "is_labor": True,
            }
        ]
    )

    rates = build_labor_rates(summary)

    assert not rates.empty
    assert rates.iloc[0]["package"] == "labor_foam"
    assert rates.iloc[0]["median_hours_per_sqft"] == 0.04
    assert rates.iloc[0]["median_total_hours"] == 48
    assert rates.iloc[0]["median_crew_size"] == 3
    assert rates.iloc[0]["evidence_count"] == 1


def test_labor_rates_fallback_hours_from_days_and_crew_is_mask_aligned() -> None:
    summary = pd.DataFrame(
        [
            {
                "job_id": "J1",
                "source_year": 2026,
                "division": "Roofing",
                "template_type": "roofing",
                "project_type": "roofing",
                "substrate": "metal",
                "package": "labor_top_coat",
                "unit": "",
                "area_sqft": 1000,
                "total_hours": None,
                "labor_hours": None,
                "labor_days": 0.5,
                "crew_size": 4,
                "total_cost": 1000,
                "is_labor": True,
            },
            {
                "job_id": "J2",
                "source_year": 2026,
                "division": "Roofing",
                "template_type": "roofing",
                "project_type": "roofing",
                "substrate": "metal",
                "package": "labor_top_coat",
                "unit": "",
                "area_sqft": 1000,
                "total_hours": 24,
                "labor_hours": None,
                "labor_days": 1,
                "crew_size": 3,
                "total_cost": 1500,
                "is_labor": True,
            },
        ]
    )

    rates = build_labor_rates(summary)

    assert not rates.empty
    assert rates.iloc[0]["median_total_hours"] == 20


def test_missing_area_does_not_crash_and_appears_in_diagnostics() -> None:
    summary = pd.DataFrame(
        [
            {
                "job_id": "J1",
                "package": "labor_foam",
                "total_hours": 48,
                "area_sqft": None,
            }
        ]
    )

    rates = build_labor_rates(summary)
    diagnostics = build_missing_job_context(summary)

    assert rates.empty
    assert not diagnostics.empty
    assert "area_sqft" in diagnostics.iloc[0]["missing_context_fields"]


def test_build_rule_suggestions_accepts_evidence_count_without_job_count() -> None:
    cooccurrence = pd.DataFrame(
        [
            {
                "project_type": "roof coating",
                "substrate": "metal",
                "package_a": "coating",
                "package_b": "seam_treatment",
                "co_occurrence_rate": 0.75,
                "evidence_count": 4,
            }
        ]
    )

    suggestions = build_rule_suggestions(
        warranty=pd.DataFrame(),
        cooccurrence=cooccurrence,
        material_ratios=pd.DataFrame(),
        labor_rates=pd.DataFrame(),
        anomalies=pd.DataFrame(),
    )

    assert suggestions["project_substrate_likely_work_packages"]
    assert suggestions["project_substrate_likely_work_packages"][0]["job_count"] == 4


def test_build_rule_suggestions_handles_empty_frames() -> None:
    suggestions = build_rule_suggestions(
        warranty=pd.DataFrame(),
        cooccurrence=pd.DataFrame(),
        material_ratios=pd.DataFrame(),
        labor_rates=pd.DataFrame(),
        anomalies=pd.DataFrame(),
    )

    assert suggestions["diagnostics"]
    assert suggestions["warranty_years_to_wet_mils"] == []
    assert suggestions["project_substrate_likely_work_packages"] == []
    assert suggestions["default_production_rates_by_labor_package"] == []


def test_build_rule_suggestions_handles_sparse_optional_columns() -> None:
    suggestions = build_rule_suggestions(
        warranty=pd.DataFrame([{"evidence_count": 2}]),
        cooccurrence=pd.DataFrame([{"support": 0.8, "count": 3}]),
        material_ratios=pd.DataFrame([{"package": "primer", "evidence_count": 2}]),
        labor_rates=pd.DataFrame([{"package": "labor_foam", "median_hours_per_1000_sqft": 12, "evidence_count": 3}]),
        anomalies=pd.DataFrame([{"anomaly_type": "unit_cost_suspicious"}]),
    )

    assert suggestions["project_substrate_likely_work_packages"][0]["job_count"] == 3
    assert suggestions["primer_inclusion_triggers"][0]["job_count"] == 2
    assert suggestions["default_production_rates_by_labor_package"][0]["labor_package"] == "labor_foam"
    assert suggestions["anomaly_summary"] == {"unit_cost_suspicious": 1}


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


def test_database_pipeline_prefers_template_rows_and_preserves_specific_labor_packages(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'template_relationships.db'}")
    out_dir = tmp_path / "template_relationships"
    pd.DataFrame(
        [
            {
                "job_id": "J1",
                "source_year": 2026,
                "division": "Insulation",
                "pipeline_status": "Completed",
                "status": "Completed",
                "customer": "Acme",
                "job_name": "Acme insulation",
                "job_type": "wall insulation",
                "estimated_sqft": 1200,
            }
        ]
    ).to_sql("jobs", engine, index=False)
    pd.DataFrame(
        [
            {
                "template_row_id": "T1",
                "document_id": "D1",
                "job_id": "J1",
                "source_file": "Estimate Insulation.xlsx",
                "template_type": "insulation",
                "sheet_name": "Estimate",
                "row_number": 86,
                "template_bucket": "labor_foam",
                "template_section": "labor",
                "line_item_kind": "labor",
                "selected_item_name": "Foam",
                "days": 2,
                "crew_size": 3,
                "total_hours": 48,
                "estimated_cost": 2400,
                "needs_review": False,
            },
            {
                "template_row_id": "T2",
                "document_id": "D1",
                "job_id": "J1",
                "source_file": "Estimate Insulation.xlsx",
                "template_type": "insulation",
                "sheet_name": "Estimate",
                "row_number": 19,
                "template_bucket": "foam",
                "template_section": "materials",
                "line_item_kind": "material",
                "selected_item_name": "Closed cell foam",
                "quantity": 4,
                "unit": "unit",
                "unit_price": 1000,
                "estimated_cost": 4000,
                "needs_review": False,
            },
        ]
    ).to_sql("estimate_template_rows", engine, index=False)

    paths = profile_relationships_from_database(
        engine=engine,
        out_dir=out_dir,
        source_year="2026",
        division="Insulation",
        status="Completed",
        min_job_count=1,
    )

    package_summary = pd.read_sql_table("job_package_summary", engine)
    assert {"area_sqft", "hours_per_sqft", "template_type", "division"}.issubset(package_summary.columns)
    assert "labor_foam" in set(package_summary["package"])
    labor_row = package_summary[package_summary["package"] == "labor_foam"].iloc[0]
    assert labor_row["area_sqft"] == 1200
    assert labor_row["hours_per_sqft"] == 0.04

    labor_rates = pd.read_csv(paths["relationship_labor_rates.csv"])
    assert "labor_foam" in set(labor_rates["package"])
    assert labor_rates.iloc[0]["median_hours_per_sqft"] == 0.04
    assert paths["relationship_input_diagnostics.csv"].exists()
    assert paths["package_normalization_diagnostics.csv"].exists()
    assert paths["missing_job_context.csv"].exists()
    assert paths["labor_rate_diagnostics.csv"].exists()


def test_database_pipeline_mines_flooring_template_relationships(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'flooring_relationships.db'}")
    out_dir = tmp_path / "flooring_relationships"
    pd.DataFrame(
        [
            {
                "job_id": "F1",
                "source_year": 2026,
                "division": "Flooring",
                "pipeline_status": "Completed",
                "status": "Completed",
                "customer": "Lee",
                "job_name": "Lee Sporting Shop flooring",
                "job_type": "floor system",
                "estimated_sqft": 2400,
            },
            {
                "job_id": "F2",
                "source_year": 2026,
                "division": "Flooring",
                "pipeline_status": "Completed",
                "status": "Completed",
                "customer": "Beta",
                "job_name": "Beta floor coating",
                "job_type": "floor system",
                "estimated_sqft": 3000,
            },
        ]
    ).to_sql("jobs", engine, index=False)
    pd.DataFrame(
        [
            {
                "template_row_id": "F1_BASE",
                "document_id": "DF1",
                "job_id": "F1",
                "source_file": "Estimate Flooring - Lee Sporting Shop.xlsx",
                "template_type": "flooring",
                "sheet_name": "Estimate",
                "row_number": 26,
                "template_bucket": "floor_base_coat",
                "template_section": "materials",
                "line_item_kind": "material",
                "selected_item_name": "NPI Epoxy 707 - Black",
                "quantity": 2400,
                "estimated_units": 26.4,
                "unit_price": 45,
                "estimated_cost": 1188,
                "needs_review": False,
            },
            {
                "template_row_id": "F1_TOP",
                "document_id": "DF1",
                "job_id": "F1",
                "source_file": "Estimate Flooring - Lee Sporting Shop.xlsx",
                "template_type": "flooring",
                "sheet_name": "Estimate",
                "row_number": 27,
                "template_bucket": "floor_topcoat",
                "template_section": "materials",
                "line_item_kind": "material",
                "selected_item_name": "Polyaspartic",
                "quantity": 2400,
                "estimated_units": 15.84,
                "unit_price": 77.1,
                "estimated_cost": 1221.264,
                "needs_review": False,
            },
            {
                "template_row_id": "F1_FLAKE",
                "document_id": "DF1",
                "job_id": "F1",
                "source_file": "Estimate Flooring - Lee Sporting Shop.xlsx",
                "template_type": "flooring",
                "sheet_name": "Estimate",
                "row_number": 177,
                "template_bucket": "floor_flake",
                "template_section": "estimate_adders",
                "line_item_kind": "material",
                "selected_item_name": "Flake",
                "estimated_cost": 1320,
                "needs_review": False,
            },
            {
                "template_row_id": "F1_LABOR_BASE",
                "document_id": "DF1",
                "job_id": "F1",
                "source_file": "Estimate Flooring - Lee Sporting Shop.xlsx",
                "template_type": "flooring",
                "sheet_name": "Estimate",
                "row_number": 120,
                "template_bucket": "labor_floor_prep_base",
                "template_section": "labor",
                "line_item_kind": "labor",
                "selected_item_name": "Prep & Base 707",
                "days": 0.5,
                "crew_size": 3,
                "total_hours": 12,
                "estimated_cost": 2528.66,
                "needs_review": False,
            },
            {
                "template_row_id": "F1_LABOR_TOP",
                "document_id": "DF1",
                "job_id": "F1",
                "source_file": "Estimate Flooring - Lee Sporting Shop.xlsx",
                "template_type": "flooring",
                "sheet_name": "Estimate",
                "row_number": 130,
                "template_bucket": "labor_floor_topcoat",
                "template_section": "labor",
                "line_item_kind": "labor",
                "selected_item_name": "Trip #3 Top Coat",
                "days": 0.5,
                "crew_size": 3,
                "total_hours": 12,
                "estimated_cost": 2528.66,
                "needs_review": False,
            },
            {
                "template_row_id": "F2_BASE",
                "document_id": "DF2",
                "job_id": "F2",
                "source_file": "Estimate Flooring - Beta.xlsx",
                "template_type": "flooring",
                "sheet_name": "Estimate",
                "row_number": 26,
                "template_bucket": "floor_base_coat",
                "template_section": "materials",
                "line_item_kind": "material",
                "selected_item_name": "NPI Epoxy 707 - Gray",
                "quantity": 3000,
                "estimated_units": 33,
                "unit_price": 45,
                "estimated_cost": 1485,
                "needs_review": False,
            },
            {
                "template_row_id": "F2_TOP",
                "document_id": "DF2",
                "job_id": "F2",
                "source_file": "Estimate Flooring - Beta.xlsx",
                "template_type": "flooring",
                "sheet_name": "Estimate",
                "row_number": 27,
                "template_bucket": "floor_topcoat",
                "template_section": "materials",
                "line_item_kind": "material",
                "selected_item_name": "Polyaspartic",
                "quantity": 3000,
                "estimated_units": 19.8,
                "unit_price": 77.1,
                "estimated_cost": 1526.58,
                "needs_review": False,
            },
            {
                "template_row_id": "F2_LABOR_BASE",
                "document_id": "DF2",
                "job_id": "F2",
                "source_file": "Estimate Flooring - Beta.xlsx",
                "template_type": "flooring",
                "sheet_name": "Estimate",
                "row_number": 120,
                "template_bucket": "labor_floor_prep_base",
                "template_section": "labor",
                "line_item_kind": "labor",
                "selected_item_name": "Prep & Base 707",
                "days": 0.75,
                "crew_size": 3,
                "total_hours": 18,
                "estimated_cost": 3792.99,
                "needs_review": False,
            },
        ]
    ).to_sql("estimate_template_rows", engine, index=False)

    paths = profile_relationships_from_database(
        engine=engine,
        out_dir=out_dir,
        source_year="2026",
        division="Flooring",
        status="Completed",
        min_job_count=1,
    )

    package_summary = pd.read_sql_table("job_package_summary", engine)
    assert {"floor_base_coat", "floor_topcoat", "labor_floor_prep_base"}.issubset(set(package_summary["package"]))
    base = package_summary[(package_summary["job_id"] == "F1") & (package_summary["package"] == "floor_base_coat")].iloc[0]
    assert base["unit"] == "gal"
    assert round(base["qty_per_sqft"], 4) == 0.011

    material_ratios = pd.read_csv(paths["relationship_material_qty_ratios.csv"])
    assert {"floor_base_coat", "floor_topcoat"}.issubset(set(material_ratios["package"]))
    assert set(material_ratios[material_ratios["package"] == "floor_base_coat"]["template_type"]) == {"flooring"}

    labor_rates = pd.read_csv(paths["relationship_labor_rates.csv"])
    prep_base = labor_rates[labor_rates["package"] == "labor_floor_prep_base"].iloc[0]
    assert prep_base["median_hours_per_sqft"] == 0.0055
    assert prep_base["template_type"] == "flooring"

    cooccurrence = pd.read_csv(paths["relationship_package_cooccurrence.csv"])
    pairs = {tuple(sorted((row.package_a, row.package_b))) for row in cooccurrence.itertuples()}
    assert ("floor_base_coat", "floor_topcoat") in pairs
