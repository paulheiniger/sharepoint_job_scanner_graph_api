from __future__ import annotations

import json

import pandas as pd
from sqlalchemy import create_engine, inspect

from jobscan.repair_estimator.profiler import profile_repairs
from jobscan.repair_estimator.vsimple_loader import (
    load_vsimple_repair_export,
    write_repair_tables,
    write_repair_tables_to_database,
)


def write_sample_vsimple_export(path) -> None:
    pd.DataFrame(
        [
            {
                "id": 1001,
                "Name": "Acme leaking seam repair",
                "companycustomer_name": "Acme",
                "Status Name": "Invoiced",
                "type_of_repair": "Billable Repair",
                "roof_type": "Metal",
                "scope_of_work": "Repair leaking standing seam.",
                "work_performed_long_text": "Cleaned seam, installed fabric, and sealed leaking seam with NP1.",
                "special_notes": "Small repair from field notes.",
                "materials_used": "2 tubes NP1\n1 roll fabric",
                "total_labor_hours": 4.5,
                "labor_cost": 315,
                "technician_1_name": "Tech One",
                "technician_1_hours": 2.5,
                "technician_1_cost": 175,
                "technician_2_name": "Tech Two",
                "technician_2_hours": 2.0,
                "technician_2_cost": 140,
                "np1_est": 2,
                "np1_cost": 9,
                "np1_total": 18,
                "fabric_est": 1,
                "fabric_cost": 125,
                "fabric_total": 125,
                "total_bill_amount": 1200,
                "invoice_amount": 1200,
                "gross_profit": 450,
                "URL": "https://app.vsimple.com/spray-tecwo/pre-orders/1001",
                "repair_address": "1 Main St",
                "city": "Louisville",
                "state": "KY",
                "zip": "40202",
            },
            {
                "id": 1002,
                "Name": "Beta drain leak repair",
                "companycustomer_name": "Beta",
                "Status Name": "Repair Complete",
                "type_of_repair": "Billable Repair",
                "roof_type": "TPO",
                "scope_of_work": "Patch drain leak.",
                "work_performed_long_text": "Repaired drain flashing and patched membrane puncture.",
                "materials_used": "1 roll fleece tape",
                "total_labor_hours": 6,
                "labor_cost": 420,
                "fleece_tape_50_est": 1,
                "fleece_tape_50_cost": 74,
                "fleece_tape_50_total": 74,
                "total_bill_amount": 1800,
                "invoice_amount": 1750,
                "gross_profit": 600,
                "URL": "https://app.vsimple.com/spray-tecwo/pre-orders/1002",
                "repair_address": "2 Main St",
                "city": "Louisville",
                "state": "KY",
                "zip": "40202",
            },
        ]
    ).to_excel(path, sheet_name="Export", index=False)


def test_vsimple_loader_builds_normalized_repair_tables(tmp_path) -> None:
    workbook = tmp_path / "data.xlsx"
    write_sample_vsimple_export(workbook)

    tables = load_vsimple_repair_export(workbook)

    assert len(tables.repair_jobs) == 2
    assert set(tables.repair_jobs["repair_id"]) == {"1001", "1002"}
    assert {"repair_id", "job_name", "status", "type_of_repair", "roof_type", "url"}.issubset(tables.repair_jobs.columns)

    materials = tables.repair_material_usage
    assert {"caulk_sealant", "fabric_reinforcement"}.issubset(set(materials["material_package"]))
    assert materials[materials["repair_id"] == "1001"]["quantity"].notna().any()

    labor = tables.repair_labor_usage
    assert {"aggregate", "technician"}.issubset(set(labor["labor_role"]))
    assert labor[labor["repair_id"] == "1001"]["labor_hours"].sum() >= 4.5

    scope = tables.repair_scope_text.set_index("repair_id")
    patterns = json.loads(scope.loc["1001", "work_phrase_patterns"])
    assert "seam" in patterns
    assert "fabric_reinforcement" in patterns

    outcomes = tables.repair_outcomes.set_index("repair_id")
    assert outcomes.loc["1002", "invoice_amount"] == 1750


def test_repair_tables_write_csv_and_database(tmp_path) -> None:
    workbook = tmp_path / "data.xlsx"
    write_sample_vsimple_export(workbook)
    tables = load_vsimple_repair_export(workbook)

    paths = write_repair_tables(tables, tmp_path / "normalized")
    assert set(paths) == {
        "repair_jobs",
        "repair_material_usage",
        "repair_labor_usage",
        "repair_scope_text",
        "repair_outcomes",
    }
    assert all(path.exists() for path in paths.values())

    engine = create_engine(f"sqlite:///{tmp_path / 'repairs.db'}")
    write_repair_tables_to_database(tables, engine)
    inspector = inspect(engine)
    for table_name in paths:
        assert inspector.has_table(table_name)


def test_repair_profiler_groups_repair_history(tmp_path) -> None:
    workbook = tmp_path / "data.xlsx"
    write_sample_vsimple_export(workbook)
    tables = load_vsimple_repair_export(workbook)

    paths = profile_repairs(tables, tmp_path / "profile", min_job_count=1)

    expected = {
        "repair_profile_summary.csv",
        "repair_material_package_profile.csv",
        "repair_work_phrase_profile.csv",
        "repair_estimator_rule_suggestions.json",
    }
    assert expected == set(paths)
    assert all(path.exists() for path in paths.values())

    summary = pd.read_csv(paths["repair_profile_summary.csv"])
    assert {"type_of_repair", "roof_type", "repair_count", "median_labor_hours", "median_invoice_amount"}.issubset(summary.columns)
    assert not summary.empty

    material_profile = pd.read_csv(paths["repair_material_package_profile.csv"])
    assert "caulk_sealant" in set(material_profile["material_package"])

    phrase_profile = pd.read_csv(paths["repair_work_phrase_profile.csv"])
    assert {"seam", "drain"}.intersection(set(phrase_profile["work_phrase_pattern"]))

    suggestions = json.loads(paths["repair_estimator_rule_suggestions.json"].read_text())
    assert suggestions["repair_type_defaults"]
    assert suggestions["material_package_defaults"]
