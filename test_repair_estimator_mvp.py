from __future__ import annotations

import importlib

import pandas as pd

from jobscan.repair_estimator.estimator import (
    estimate_repair_from_notes,
    find_similar_repairs,
)
from jobscan.repair_estimator.scope_parser import parse_repair_notes
from jobscan.repair_estimator.vsimple_loader import RepairTables


def sample_repair_tables() -> RepairTables:
    return RepairTables(
        repair_jobs=pd.DataFrame(
            [
                {
                    "repair_id": "R1",
                    "customer": "Acme",
                    "job_name": "Pipe boot leak repair",
                    "status": "Invoiced",
                    "type_of_repair": "Billable Repair",
                    "roof_type": "TPO",
                    "url": "https://example.test/R1",
                },
                {
                    "repair_id": "R2",
                    "customer": "Beta",
                    "job_name": "Open seam metal roof",
                    "status": "Repair Complete",
                    "type_of_repair": "Billable Repair",
                    "roof_type": "Metal",
                    "url": "https://example.test/R2",
                },
                {
                    "repair_id": "R3",
                    "customer": "Gamma",
                    "job_name": "Skylight curb leak",
                    "status": "Invoiced",
                    "type_of_repair": "Billable Repair",
                    "roof_type": "Coated Roof",
                    "url": "https://example.test/R3",
                },
            ]
        ),
        repair_material_usage=pd.DataFrame(
            [
                {"repair_material_usage_id": "M1", "repair_id": "R1", "material_package": "caulk_sealant", "material_name": "NP1", "quantity": 2, "unit": "tube", "unit_cost": 9, "total_cost": 18},
                {"repair_material_usage_id": "M2", "repair_id": "R1", "material_package": "fabric_reinforcement", "material_name": "Fabric", "quantity": 1, "unit": "roll", "unit_cost": 125, "total_cost": 125},
                {"repair_material_usage_id": "M3", "repair_id": "R2", "material_package": "caulk_sealant", "material_name": "Sealant", "quantity": 4, "unit": "tube", "unit_cost": 10, "total_cost": 40},
                {"repair_material_usage_id": "M4", "repair_id": "R2", "material_package": "fabric_reinforcement", "material_name": "Fabric", "quantity": 1, "unit": "roll", "unit_cost": 125, "total_cost": 125},
                {"repair_material_usage_id": "M5", "repair_id": "R3", "material_package": "flashing_edge_metal", "material_name": "Flashing", "quantity": 1, "unit": "ea", "unit_cost": 75, "total_cost": 75},
            ]
        ),
        repair_labor_usage=pd.DataFrame(
            [
                {"repair_labor_usage_id": "L1", "repair_id": "R1", "labor_role": "aggregate", "labor_hours": 4, "labor_cost": 320, "total_labor_hours": 4},
                {"repair_labor_usage_id": "L2", "repair_id": "R2", "labor_role": "aggregate", "labor_hours": 6, "labor_cost": 480, "total_labor_hours": 6},
                {"repair_labor_usage_id": "L3", "repair_id": "R3", "labor_role": "aggregate", "labor_hours": 5, "labor_cost": 400, "total_labor_hours": 5},
            ]
        ),
        repair_scope_text=pd.DataFrame(
            [
                {
                    "repair_id": "R1",
                    "scope_of_work": "Pipe boot leak on TPO roof",
                    "work_performed_long_text": "Sealed one pipe boot with NP1 and reinforced with fabric.",
                    "special_notes": "",
                    "materials_used": "2 tubes NP1; 1 roll fabric",
                    "combined_scope_text": "pipe boot leak tpo roof sealed fabric np1",
                    "work_phrase_patterns": '["leak", "caulk", "fabric_reinforcement"]',
                },
                {
                    "repair_id": "R2",
                    "scope_of_work": "Open seam metal roof",
                    "work_performed_long_text": "Cleaned open seam and installed sealant with fabric.",
                    "special_notes": "",
                    "materials_used": "sealant and fabric",
                    "combined_scope_text": "open seam metal roof water leak sealant fabric",
                    "work_phrase_patterns": '["leak", "seam", "fabric_reinforcement"]',
                },
                {
                    "repair_id": "R3",
                    "scope_of_work": "Skylight curb leak",
                    "work_performed_long_text": "Repaired skylight curb flashing and sealant.",
                    "special_notes": "",
                    "materials_used": "flashing sealant",
                    "combined_scope_text": "skylight curb leak coated roof flashing sealant",
                    "work_phrase_patterns": '["leak", "skylight", "flashing"]',
                },
            ]
        ),
        repair_outcomes=pd.DataFrame(
            [
                {"repair_id": "R1", "status": "Invoiced", "invoice_amount": 1200, "total_bill_amount": 1200, "gross_profit": 450},
                {"repair_id": "R2", "status": "Repair Complete", "invoice_amount": 1800, "total_bill_amount": 1800, "gross_profit": 650},
                {"repair_id": "R3", "status": "Invoiced", "invoice_amount": 1500, "total_bill_amount": 1500, "gross_profit": 500},
            ]
        ),
    )


def test_repair_scope_parser_pipe_boot_leak() -> None:
    parsed = parse_repair_notes("Small active leak around one pipe boot on a TPO roof. Easy access. Seal with fabric.")

    assert parsed.issue_type == "pipe_boot_leak"
    assert parsed.roof_type == "tpo"
    assert parsed.leak_present is True
    assert parsed.penetration_count == 1
    assert parsed.access_complexity == "low"
    assert "fabric" in parsed.materials_mentioned


def test_similar_repair_retrieval_uses_text_and_scope() -> None:
    tables = sample_repair_tables()
    parsed = parse_repair_notes("Leak around one pipe boot on TPO roof. Seal and reinforce with fabric.")

    similar = find_similar_repairs(tables, parsed, "Leak around one pipe boot on TPO roof. Seal and reinforce with fabric.")

    assert not similar.empty
    assert similar.iloc[0]["repair_id"] == "R1"
    assert similar.iloc[0]["similarity_score"] > 0


def test_repair_estimator_output_schema_and_packages() -> None:
    result = estimate_repair_from_notes(
        "Open seam on metal roof, about 12 linear feet, water entering after rain. Need clean, fabric, and sealant repair.",
        sample_repair_tables(),
    )
    payload = result.to_dict()

    assert payload["parsed_scope"]["issue_type"] == "open_seam"
    assert payload["estimated_labor_hours_target"] is not None
    assert payload["estimated_material_cost_target"] is not None
    assert payload["estimated_invoice_target"] is not None
    assert payload["similar_repairs"]
    packages = {row["material_package"] for row in payload["selected_repair_packages"]}
    assert {"caulk_sealant", "fabric_reinforcement"}.issubset(packages)


def test_repair_estimator_low_evidence_fallback() -> None:
    tables = sample_repair_tables()
    tables.repair_jobs = tables.repair_jobs.iloc[0:0]
    result = estimate_repair_from_notes("Customer says there is a roof problem and wants someone to look at it.", tables)

    assert result.confidence == "low"
    assert result.estimated_invoice_target is not None
    assert any("Low historical evidence" in flag for flag in result.review_flags)


def test_dashboard_repair_estimator_import_safety() -> None:
    app = importlib.import_module("dashboard.app")

    assert hasattr(app, "repair_estimator_page")
    assert hasattr(app, "load_repair_history_cached")
