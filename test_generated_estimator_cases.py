from __future__ import annotations

import json

import pandas as pd

from jobscan.estimator import generated_cases
from jobscan.estimator.generated_cases import (
    build_ai_case_prompt,
    build_case_facts,
    build_proposal_scope_case_facts,
    generate_cases,
    generate_proposal_scope_cases,
    select_historical_proposal_candidates,
    select_historical_candidates,
    validate_ai_case_output,
    write_generated_case_outputs,
)
from jobscan.estimator.schemas import EstimatorData
from scripts.evaluate_generated_case_reviewed_notes import _row_overlap, _scope_checks


def generated_case_data() -> EstimatorData:
    rows = []
    jobs = []
    for index in range(6):
        job_id = f"R{index}"
        area = 9000 + index * 750
        jobs.append(
            {
                "job_id": job_id,
                "customer": f"Roof Customer {index}",
                "job_name": f"Roof Job {index}",
                "site_address": f"{100 + index} Metal Roof Way",
                "division": "Roofing",
                "project_type": "roof coating",
                "substrate": "metal",
                "estimated_sqft": area,
                "coating_type": "silicone",
                "warranty_years": 10,
            }
        )
        rows.extend(
            [
                {
                    "job_id": job_id,
                    "source_file": f"sharepoint/roofing/{job_id}.xlsx",
                    "template_type": "roofing",
                    "division": "Roofing",
                    "sheet_name": "Estimate",
                    "row_number": 26,
                    "template_bucket": "coating",
                    "line_item_kind": "material",
                    "selector_code": 11,
                    "resolved_item_name": "Gaco Silicone",
                    "area_sqft": area,
                    "gal_per_100_sqft": 1.5,
                    "warranty_years": 10,
                    "unit_price": 42,
                    "estimated_cost": 5400,
                },
                {
                    "job_id": job_id,
                    "source_file": f"sharepoint/roofing/{job_id}.xlsx",
                    "template_type": "roofing",
                    "division": "Roofing",
                    "sheet_name": "Estimate",
                    "row_number": 34,
                    "template_bucket": "primer",
                    "line_item_kind": "material",
                    "selector_code": 2,
                    "resolved_item_name": "Rust Inhibitive Primer",
                    "area_sqft": area,
                    "unit_price": 85,
                    "estimated_cost": 1700,
                },
                {
                    "job_id": job_id,
                    "source_file": f"sharepoint/roofing/{job_id}.xlsx",
                    "template_type": "roofing",
                    "division": "Roofing",
                    "sheet_name": "Estimate",
                    "row_number": 122,
                    "template_bucket": "labor_base",
                    "line_item_kind": "labor",
                    "days": 2,
                    "crew_size": 4,
                    "total_hours": 80,
                    "hourly_rate": 55,
                    "estimated_cost": 4400,
                },
            ]
        )
    for index in range(6):
        job_id = f"I{index}"
        area = 2400 + index * 350
        jobs.append(
            {
                "job_id": job_id,
                "customer": f"Insulation Customer {index}",
                "job_name": f"Insulation Job {index}",
                "site_address": f"{200 + index} Foam Building Rd",
                "division": "Insulation",
                "project_type": "spray foam insulation",
                "building_type": "metal building",
                "estimated_sqft": area,
            }
        )
        rows.extend(
            [
                {
                    "job_id": job_id,
                    "source_file": f"sharepoint/insulation/{job_id}.xlsx",
                    "template_type": "insulation",
                    "division": "Insulation",
                    "sheet_name": "Estimate",
                    "row_number": 19,
                    "template_bucket": "foam",
                    "line_item_kind": "material",
                    "selector_code": 11,
                    "resolved_item_name": "Gaco 2.0 lb.",
                    "area_sqft": area,
                    "thickness_inches": 2.0,
                    "yield_or_coverage": 13500,
                    "unit_price": 1600,
                    "estimated_cost": 3200,
                },
                {
                    "job_id": job_id,
                    "source_file": f"sharepoint/insulation/{job_id}.xlsx",
                    "template_type": "insulation",
                    "division": "Insulation",
                    "sheet_name": "Estimate",
                    "row_number": 30,
                    "template_bucket": "thermal_barrier_coating",
                    "line_item_kind": "material",
                    "selector_code": 1,
                    "resolved_item_name": "DC315",
                    "area_sqft": area,
                    "gal_per_100_sqft": 1.0,
                    "unit_price": 120,
                    "estimated_cost": 2400,
                },
                {
                    "job_id": job_id,
                    "source_file": f"sharepoint/insulation/{job_id}.xlsx",
                    "template_type": "insulation",
                    "division": "Insulation",
                    "sheet_name": "Estimate",
                    "row_number": 86,
                    "template_bucket": "labor_foam",
                    "line_item_kind": "labor",
                    "days": 1,
                    "crew_size": 3,
                    "total_hours": 30,
                    "hourly_rate": 60,
                    "estimated_cost": 1800,
                },
            ]
        )
    return EstimatorData(template_rows=pd.DataFrame(rows), jobs=pd.DataFrame(jobs))


def generated_case_data_with_proposal_scopes() -> EstimatorData:
    data = generated_case_data()
    extra_rows = pd.DataFrame(
        [
            {
                "job_id": "R0",
                "source_file": "sharepoint/roofing/R0.xlsx",
                "template_type": "roofing",
                "division": "Roofing",
                "sheet_name": "Estimate",
                "row_number": 1,
                "template_bucket": "job_name",
                "line_item_kind": "header",
                "row_label": "Job Name",
                "selected_item_name": "Roof Job 0",
            },
            {
                "job_id": "R0",
                "source_file": "sharepoint/roofing/R0.xlsx",
                "template_type": "roofing",
                "division": "Roofing",
                "sheet_name": "Estimate",
                "row_number": 163,
                "template_bucket": "total_job_cost",
                "line_item_kind": "total",
                "row_label": "Total Job Cost",
                "estimated_cost": 20000,
            },
        ]
    )
    data.template_rows = pd.concat([data.template_rows, extra_rows], ignore_index=True)
    data.historical_scope_texts = pd.DataFrame(
        [
            {
                "job_id": "R0",
                "document_id": "proposal-r0",
                "file_name": "Proposal - Roof Job 0.pdf",
                "document_type": "proposal",
                "sharepoint_url": "https://example.invalid/proposal-r0",
                "source_year": 2025,
                "content_row_count": 4,
                "scope_text": (
                    "Scope of Work: Clean and prepare the existing metal roof. Treat rusted fasteners, "
                    "open seams, and penetrations. Apply primer where needed and install a silicone roof "
                    "restoration coating system over the prepared roof surface."
                ),
            },
            {
                "job_id": "I0",
                "document_id": "proposal-i0-plastic",
                "file_name": "Proposal - Plastic Sheeting.pdf",
                "document_type": "proposal",
                "sharepoint_url": "https://example.invalid/proposal-i0-plastic",
                "source_year": 2025,
                "content_row_count": 8,
                "scope_text": (
                    "Scope: Provide labor, materials, and equipment to install 6mil plastic sheeting "
                    "to cover exterior masonry walls along entire perimeter."
                ),
            },
            {
                "job_id": "I0",
                "document_id": "proposal-i0",
                "file_name": "Insulation Proposal I0.pdf",
                "document_type": "proposal",
                "sharepoint_url": "https://example.invalid/proposal-i0",
                "source_year": 2025,
                "content_row_count": 3,
                "scope_text": (
                    "Project Description: Spray foam insulation at the metal building walls and roof deck. "
                    "Owner wants foam on exterior walls and ceiling with normal jobsite access."
                ),
            },
        ]
    )
    return data


def test_candidate_selection_returns_roofing_insulation_mix() -> None:
    candidates = select_historical_candidates(generated_case_data(), limit=10, seed=1)

    assert len(candidates) == 10
    counts = pd.Series([candidate["template_type"] for candidate in candidates]).value_counts().to_dict()
    assert counts["roofing"] == 5
    assert counts["insulation"] == 5
    assert all(candidate["expected_decisions"] for candidate in candidates)
    assert all(candidate["source_file"] for candidate in candidates)


def test_historical_proposal_candidates_require_scope_text_and_decisions() -> None:
    candidates = select_historical_proposal_candidates(generated_case_data_with_proposal_scopes(), limit=4, seed=1)

    assert {candidate["template_type"] for candidate in candidates} == {"roofing", "insulation"}
    assert all(candidate["proposal_scope_text"] for candidate in candidates)
    assert all(candidate["proposal_document_id"] for candidate in candidates)
    assert all(candidate["expected_decisions"] for candidate in candidates)


def test_proposal_scope_case_uses_real_scope_text_and_address_not_synthetic_dimensions() -> None:
    candidate = select_historical_proposal_candidates(
        generated_case_data_with_proposal_scopes(),
        limit=1,
        template_types=("roofing",),
        seed=1,
    )[0]

    facts = build_proposal_scope_case_facts(candidate)

    assert facts["case_source"] == "historical_proposal_scope"
    assert "Site address: 100 Metal Roof Way" in facts["generated_notes"]
    assert "Scope of Work: Clean and prepare" in facts["generated_notes"]
    assert "Roof dimensions:" not in facts["generated_notes"]
    assert facts["ai_generation_metadata"]["generation_method"] == "historical_proposal_scope"


def test_proposal_scope_candidate_rejects_non_foam_insulation_proposals() -> None:
    candidate = select_historical_proposal_candidates(
        generated_case_data_with_proposal_scopes(),
        limit=1,
        template_types=("insulation",),
        seed=1,
    )[0]

    assert candidate["proposal_document_id"] == "proposal-i0"
    assert "plastic sheeting" not in candidate["proposal_scope_text"].lower()


def test_proposal_scope_answer_key_excludes_headers_and_totals() -> None:
    cases = generate_proposal_scope_cases(
        generated_case_data_with_proposal_scopes(),
        limit=1,
        template_types=("roofing",),
        seed=2,
        validate=False,
    )

    assert len(cases) == 1
    row_kinds = {row.get("line_item_kind") for row in cases[0]["expected_decisions"]}
    workbook_rows = {row.get("workbook_row") for row in cases[0]["expected_decisions"]}
    assert "header" not in row_kinds
    assert "total" not in row_kinds
    assert 1 not in workbook_rows
    assert 163 not in workbook_rows


def test_proposal_scope_answer_key_excludes_overhead_profit_buckets() -> None:
    data = generated_case_data_with_proposal_scopes()
    overhead_rows = pd.DataFrame(
        [
            {
                "job_id": "R0",
                "source_file": "sharepoint/roofing/R0.xlsx",
                "template_type": "roofing",
                "division": "Roofing",
                "sheet_name": "Estimate",
                "row_number": 177,
                "template_bucket": "overhead",
                "line_item_kind": "other",
                "selected_item_name": "Overhead",
                "estimated_cost": 1200,
            },
            {
                "job_id": "R0",
                "source_file": "sharepoint/roofing/R0.xlsx",
                "template_type": "roofing",
                "division": "Roofing",
                "sheet_name": "Estimate",
                "row_number": 178,
                "template_bucket": "profit",
                "line_item_kind": "other",
                "selected_item_name": "Profit",
                "estimated_cost": 2500,
            },
        ]
    )
    data.template_rows = pd.concat([data.template_rows, overhead_rows], ignore_index=True)

    cases = generate_proposal_scope_cases(data, limit=1, template_types=("roofing",), seed=2, validate=False)
    buckets = {str(row.get("template_bucket") or "").lower() for row in cases[0]["expected_decisions"]}

    assert "overhead" not in buckets
    assert "profit" not in buckets


def test_deterministic_area_synthesis_matches_source_area() -> None:
    candidates = select_historical_candidates(generated_case_data(), limit=2, seed=2)

    for candidate in candidates:
        facts = build_case_facts(candidate)
        expected_area = float(facts["expected_scope_fields"]["estimated_sqft"])
        source_area = float(candidate["area_sqft"])
        assert abs(expected_area - source_area) / source_area < 0.02
        assert facts["area_trace"]["net_area_sqft"] == expected_area


def test_deterministic_area_synthesis_includes_deductions() -> None:
    candidates = select_historical_candidates(generated_case_data(), limit=2, seed=2)

    for candidate in candidates:
        facts = build_case_facts(candidate)
        area_trace = facts["area_trace"]
        source_area = float(candidate["area_sqft"])
        assert area_trace["gross_area_sqft"] > area_trace["net_area_sqft"]
        assert area_trace["deduction_area_sqft"] > 0
        assert "deduct" in area_trace["formula"].lower()
        assert area_trace["net_area_sqft"] == source_area


def test_ai_prompt_separates_explicit_facts_from_inference_clues() -> None:
    candidate = select_historical_candidates(generated_case_data(), limit=1, template_types=("roofing",))[0]
    facts = build_case_facts(candidate)
    prompt = build_ai_case_prompt(facts)
    payload = json.loads(prompt)

    assert "explicit_note_facts" in payload
    assert "inference_clues" in payload
    assert "hidden_expected_decisions_do_not_list" in payload
    assert "Do not mention selector codes" in " ".join(payload["hard_rules"])


def test_ai_prompt_filters_and_dedupes_expected_decision_context() -> None:
    candidate = select_historical_candidates(generated_case_data(), limit=1, template_types=("roofing",))[0]
    facts = build_case_facts(candidate)
    facts["expected_decisions"].extend(
        [
            {
                "decision_id": "roofing_coating_system",
                "template_bucket": "coating",
                "line_item_kind": "material",
                "workbook_row": 26,
                "resolved_item_name": "Duplicate Coating",
            },
            {"decision_id": "customer", "template_bucket": "customer", "line_item_kind": "other", "workbook_row": 1},
            {"decision_id": "total_job_cost", "template_bucket": "total_job_cost", "line_item_kind": "total", "workbook_row": 163},
        ]
    )

    payload = json.loads(build_ai_case_prompt(facts))
    context = payload["expected_decision_summary_for_context_only"]
    keys = [(row.get("decision_id"), row.get("template_bucket"), row.get("workbook_row")) for row in context]

    assert len(keys) == len(set(keys))
    assert ("customer", "customer", 1) not in keys
    assert ("total_job_cost", "total_job_cost", 163) not in keys
    assert sum(1 for key in keys if key == ("roofing_coating_system", "coating", 26)) == 1
    assert "Filtered and deduped" in payload["expected_decision_context_note"]


def test_expected_decisions_from_historical_rows_are_deduped() -> None:
    data = generated_case_data()
    duplicate = data.template_rows.iloc[[0]].copy()
    duplicate.loc[:, "resolved_item_name"] = ""
    data.template_rows = pd.concat([data.template_rows, duplicate], ignore_index=True)

    candidate = select_historical_candidates(data, limit=1, template_types=("roofing",), seed=1)[0]
    keys = [
        (row.get("decision_id"), row.get("template_bucket"), row.get("workbook_row"))
        for row in candidate["expected_decisions"]
    ]

    assert len(keys) == len(set(keys))


def test_expected_decisions_filter_mismatched_template_layout_rows() -> None:
    data = generated_case_data()
    bad_rows = pd.DataFrame(
        [
            {
                "job_id": "I0",
                "source_file": "sharepoint/insulation/I0.xlsx",
                "template_type": "insulation",
                "division": "Insulation",
                "sheet_name": "Estimate",
                "row_number": 126,
                "template_bucket": "labor_caulk",
                "line_item_kind": "labor",
                "days": 1,
                "crew_size": 4,
                "total_hours": 42,
            },
            {
                "job_id": "I0",
                "source_file": "sharepoint/insulation/I0.xlsx",
                "template_type": "insulation",
                "division": "Insulation",
                "sheet_name": "Estimate",
                "row_number": 30,
                "template_bucket": "thermal_barrier_coating",
                "line_item_kind": "material",
                "selected_item_name": "Margin %",
                "unit_price": 30,
            },
        ]
    )
    data.template_rows = pd.concat([data.template_rows, bad_rows], ignore_index=True)

    candidate = select_historical_candidates(data, limit=1, template_types=("insulation",), seed=1)[0]
    keys = {
        (row.get("template_bucket"), row.get("workbook_row"), row.get("selected_item_name"))
        for row in candidate["expected_decisions"]
    }

    assert not any(key[0] == "labor_caulk" and key[1] == 126 for key in keys)
    assert not any(key[0] == "thermal_barrier_coating" and key[2] == "Margin %" for key in keys)
    assert any(row.get("template_bucket") == "foam" for row in candidate["expected_decisions"])


def test_ai_output_validator_rejects_changed_area_and_decision_leakage() -> None:
    candidate = select_historical_candidates(generated_case_data(), limit=1, template_types=("roofing",))[0]
    facts = build_case_facts(candidate)
    facts["expected_decisions"].extend(
        [
            {"resolved_item_name": "Secret Fabric"},
            {"resolved_item_name": "Secret Granules"},
        ]
    )
    bad = {
        "generated_notes": (
            "Roof area is 99999 sqft. Use Gaco Silicone, Rust Inhibitive Primer, Secret Fabric, and Secret Granules."
        )
    }

    result = validate_ai_case_output(bad, facts)

    assert not result["ok"]
    assert any("conflicts with deterministic area" in error for error in result["errors"])
    assert any("too many hidden expected" in error for error in result["errors"])


def test_generate_cases_deterministic_without_openai() -> None:
    cases = generate_cases(generated_case_data(), limit=10, seed=3, use_ai=False, validate=False)

    assert len(cases) == 10
    assert {case["template_type"] for case in cases} == {"roofing", "insulation"}
    assert all(case["generated_notes"] for case in cases)
    assert all(case["ai_generation_metadata"]["generation_method"] == "deterministic_template" for case in cases)
    assert all(case["validation_result"]["status"] == "not_validated" for case in cases)


def test_outputs_include_jsonl_xlsx_and_per_case_files(tmp_path) -> None:
    cases = generate_cases(generated_case_data(), limit=3, seed=4, use_ai=False, validate=False)

    paths = write_generated_case_outputs(cases, tmp_path)

    assert paths["jsonl"].exists()
    assert paths["xlsx"].exists()
    assert paths["eval_candidates"].exists()
    rows = [json.loads(line) for line in paths["jsonl"].read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 3
    first_dir = paths["cases_dir"] / rows[0]["case_id"]
    assert (first_dir / "notes.txt").exists()
    assert (first_dir / "source_decisions.json").exists()
    assert (first_dir / "validation.json").exists()


def test_reviewed_notes_evaluator_filters_decision_row_overlap_from_scaffolding() -> None:
    case = {
        "expected_workbook_rows": [1, 26, 27, 116, 163, 173],
        "expected_decisions": [
            {"workbook_row": 1, "line_item_kind": "header"},
            {"workbook_row": 26, "line_item_kind": "material"},
            {"workbook_row": 27, "line_item_kind": "material", "template_bucket": "coating"},
            {"workbook_row": 116, "line_item_kind": "labor"},
            {"workbook_row": 163, "line_item_kind": "total"},
            {"workbook_row": 173, "line_item_kind": "other"},
        ],
    }
    validation = {"actual_workbook_rows": [26, 116], "notes": "Customer requested coating."}

    result = _row_overlap(case, validation)

    assert result["raw_expected_row_count"] == 6
    assert result["raw_overlap_ratio"] == 2 / 6
    assert result["decision_expected_row_count"] == 3
    assert result["decision_row_overlap_ratio"] == 2 / 3
    assert result["scaffold_expected_row_count"] == 3
    assert result["decision_missing_rows"] == [27]
    assert result["raw_expected_rows"] == [1, 26, 27, 116, 163, 173]
    assert result["missing_decision_rows_by_reason"]["prompt_evidenced"] == [27]
    assert result["prompt_evidenced_decision_pass"] is False
    assert result["baseline_required_decision_pass"] is True
    assert result["duplicate_decision_row_pass"] is True


def test_reviewed_notes_evaluator_tracks_duplicate_decision_rows_and_historical_only_rows() -> None:
    case = {
        "template_type": "roofing",
        "expected_workbook_rows": [39, 79],
        "expected_decisions": [
            {"workbook_row": 39, "line_item_kind": "material", "template_bucket": "primer"},
            {"workbook_row": 79, "line_item_kind": "material", "template_bucket": "fabric"},
        ],
    }
    validation = {
        "actual_workbook_rows": [],
        "notes": "Review primer for rust. No fabric is mentioned.",
        "duplicate_decision_row_count": 2,
    }

    result = _row_overlap(case, validation)

    assert result["missing_decision_rows_by_reason"]["conditional_review"] == [39]
    assert result["missing_decision_rows_by_reason"]["historical_only"] == [79]
    assert result["conditional_review_decision_pass"] is False
    assert result["hidden_historical_only_count"] == 1
    assert result["duplicate_decision_row_count"] == 2
    assert result["duplicate_decision_row_pass"] is False


def test_reviewed_notes_evaluator_treats_hidden_warranty_as_not_evidenced() -> None:
    case = {
        "expected_scope_fields": {
            "estimated_sqft": 10000,
            "project_type": "roof coating",
            "warranty_years": 15,
        }
    }
    validation = {
        "notes": "Metal roof/coating restoration seems possible if the roof can qualify.",
        "parsed_scope": {
            "estimated_sqft": 10000,
            "project_type": "roof coating",
            "coating_required": True,
            "coating_path_review": True,
            "warranty_target_years": None,
        },
    }

    result = _scope_checks(case, validation)

    assert result["scope_area_pass"] is True
    assert result["coating_path_pass"] is True
    assert result["explicit_warranty_pass"] is True
    assert result["warranty_evaluation_reason"] == "not_evidenced_in_reviewed_notes"


def test_reviewed_notes_evaluator_checks_warranty_when_reviewed_note_states_duration() -> None:
    case = {
        "expected_scope_fields": {
            "estimated_sqft": 10000,
            "project_type": "roof coating",
            "warranty_years": 15,
        }
    }
    validation = {
        "notes": "Customer requests a 15-year silicone coating warranty.",
        "parsed_scope": {
            "estimated_sqft": 10000,
            "project_type": "roof coating",
            "coating_required": True,
            "warranty_target_years": None,
        },
    }

    result = _scope_checks(case, validation)

    assert result["explicit_warranty_pass"] is False
    assert result["warranty_evaluation_reason"] == "explicit_in_reviewed_notes"


def test_cli_dry_run_writes_ten_cases_without_openai(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(generated_cases, "load_estimator_data", lambda *args, **kwargs: generated_case_data())

    exit_code = generated_cases.main(
        [
            "--db-url",
            "postgresql://example/test",
            "--out-dir",
            str(tmp_path),
            "--limit",
            "10",
            "--dry-run",
            "--skip-validation",
        ]
    )

    assert exit_code == 0
    rows = [json.loads(line) for line in (tmp_path / "generated_live_cases.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 10
    assert not any(row["ai_generation_metadata"]["generation_method"] == "openai_responses" for row in rows)
