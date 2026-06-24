from __future__ import annotations

import csv
import io
import json
import zipfile

from training.bidscope_review_export import build_bidscope_review_export_zip


def _sample_payload() -> dict:
    return {
        "tool_name": "BidScope AI",
        "trade_type": "roofing",
        "trade_name": "Roofing",
        "analysis_mode": "Standard",
        "documents": [{"document_name": "A3.01.pdf"}],
        "manifest": [
            {
                "candidate_id": "candidate-1",
                "document_name": "A3.01.pdf",
                "source_path": "bid.zip:Plans/A3.01.pdf",
                "priority": "high",
                "compressed_size": 100,
                "uncompressed_size": 200,
                "status": "manifested",
            }
        ],
        "progress": {"pdf_count": 1, "estimated_total_pages": 2, "fast_scanned_pages": 2, "deep_analyzed_pages": 1},
        "scan_completeness": {
            "total_documents_discovered": 1,
            "total_pages_discovered": 2,
            "total_pages_sampled": 2,
            "total_pages_lightly_indexed": 2,
            "total_pages_deep_analyzed": 1,
            "processing_budget_hit": False,
            "budget_hit_reason": "",
            "analysis_mode": "Standard",
            "trade_type": "roofing",
            "trade_name": "Roofing",
            "high_confidence_seed_count": 1,
            "generic_candidate_count": 0,
            "resolved_reference_count": 1,
            "unresolved_reference_count": 1,
            "measurement_pages_with_resolved_paths": 1,
            "measurement_pages_without_resolved_paths": 0,
        },
        "selected_node_count_internal": 2,
        "exported_node_count": 2,
        "selected_nodes_exported": ["seed::page_1", "plan::page_1"],
        "pages": [
            {
                "global_page_id": "rejected::page_1",
                "document_name": "E1.01.pdf",
                "canonical_sheet_id": "E1-01",
                "role": "unknown",
                "seed_evidence_score": 0,
                "measurement_likelihood_score": 0,
                "final_selection_score": 0,
            }
        ],
        "reference_graph": {
            "nodes": [],
            "edges": [
                {
                    "from_sheet": "A6-01",
                    "from_document": "A6.01.pdf",
                    "reference": "A3.01",
                    "type": "sheet",
                    "to_sheet": "A3-01",
                },
                {
                    "from_sheet": "A6-01",
                    "from_document": "A6.01.pdf",
                    "reference": "A999",
                    "type": "unresolved_sheet",
                },
            ],
            "warnings": ["Reference A999 unresolved"],
        },
        "measurement_tree": {
            "selected_node_count": 2,
            "selected_node_count_internal": 2,
            "exported_node_count": 2,
            "nodes": [
                {
                    "node_id": "seed::page_1",
                    "global_page_id": "seed::page_1",
                    "document_name": "A6.01.pdf",
                    "original_document_name": "Plan Set.pdf",
                    "original_page_number": 6,
                    "canonical_sheet_id": "A6-01",
                    "sheet_id": "A6-01",
                    "sheet_title": "Roof Detail",
                    "page_type": "detail_reference",
                    "role": "detail_reference",
                    "foam_seed_level": "high",
                    "foam_specific_evidence": ["TPO"],
                    "generic_evidence": ["roof"],
                    "seed_evidence_score": 16,
                    "measurement_likelihood_score": 0,
                    "final_selection_score": 16,
                    "inclusion_path": ["A6-01"],
                    "measurement_guidance": "Review roofing scope.",
                },
                {
                    "node_id": "plan::page_1",
                    "global_page_id": "plan::page_1",
                    "document_name": "A3.01.pdf",
                    "canonical_sheet_id": "A3-01",
                    "sheet_id": "A3-01",
                    "sheet_title": "Roof Plan",
                    "page_type": "roof_plan",
                    "role": "measurement_page",
                    "foam_seed_level": "none",
                    "seed_evidence_score": 0,
                    "measurement_likelihood_score": 90,
                    "final_selection_score": 90,
                    "graph_distance_from_seed": 1,
                    "connected_seed_pages": ["A6-01"],
                    "inclusion_path": ["A6-01", "A3-01"],
                    "measurement_guidance": "Measure roofing scope on A3-01.",
                },
            ],
        },
        "warnings": ["test warning"],
    }


def _takeoff_eval() -> dict:
    return {
        "expected_measurement_pages": [
            {
                "match_key": "sheet:A3-01",
                "plan_name": "A3.01.pdf",
                "canonical_sheet_id": "A3-01",
                "takeoff_name": "Roof Area",
                "quantity": 1200,
                "unit": "Sq Ft",
            }
        ],
        "top_predicted_measurement_pages": [{"match_key": "sheet:A3-01", "canonical_sheet_id": "A3-01"}],
        "matched_pages": [{"match_key": "sheet:A3-01"}],
        "extra_pages": [],
        "recall": 1.0,
        "precision": 1.0,
    }


def test_bidscope_review_export_zip_contains_expected_files() -> None:
    payload = build_bidscope_review_export_zip(
        _sample_payload(),
        trade_profile={"trade_type": "roofing", "trade_name": "Roofing"},
        project_name="Demo Project",
        source_type="SharePoint folder URL",
        package_name="Demo Package",
        takeoff_evaluation=_takeoff_eval(),
    )

    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = set(archive.namelist())

    expected = {
        "run_summary.json",
        "input_manifest.csv",
        "trade_profile_used.json",
        "seed_pages.csv",
        "measurement_candidates.csv",
        "selected_pages.csv",
        "rejected_pages_sample.csv",
        "reference_paths.csv",
        "unresolved_references.csv",
        "takeoff_eval.csv",
        "warnings.json",
        "chatgpt_review_prompt.txt",
    }
    assert expected.issubset(names)
    assert not any(name.lower().endswith((".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff")) for name in names)


def test_bidscope_review_export_csvs_have_expected_columns_and_summary_json_is_valid() -> None:
    payload = build_bidscope_review_export_zip(
        _sample_payload(),
        trade_profile={"trade_type": "roofing", "trade_name": "Roofing"},
        project_name="Demo Project",
        source_type="SharePoint folder URL",
        package_name="Demo Package",
        takeoff_evaluation=_takeoff_eval(),
    )

    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        summary = json.loads(archive.read("run_summary.json"))
        seed_header = next(csv.reader(io.StringIO(archive.read("seed_pages.csv").decode("utf-8"))))
        measurement_header = next(csv.reader(io.StringIO(archive.read("measurement_candidates.csv").decode("utf-8"))))
        selected_header = next(csv.reader(io.StringIO(archive.read("selected_pages.csv").decode("utf-8"))))
        takeoff_header = next(csv.reader(io.StringIO(archive.read("takeoff_eval.csv").decode("utf-8"))))

    assert summary["project_name"] == "Demo Project"
    assert summary["trade_type"] == "roofing"
    assert summary["takeoff_eval_recall"] == 1.0
    assert {"page_id", "document_name", "canonical_sheet_id", "seed_evidence_score", "why_selected"}.issubset(seed_header)
    assert {"rank", "page_id", "measurement_likelihood_score", "best_reference_path", "why_candidate"}.issubset(measurement_header)
    assert {"page_id", "role", "reference_path", "measurement_guidance"}.issubset(selected_header)
    assert {"actual_plan_name", "actual_sheet_id", "takeoff_name", "predicted_rank", "match_type"}.issubset(takeoff_header)


def test_bidscope_review_export_stays_small() -> None:
    payload = build_bidscope_review_export_zip(
        _sample_payload(),
        trade_profile={"trade_type": "roofing", "trade_name": "Roofing"},
        project_name="Demo Project",
        source_type="SharePoint folder URL",
        package_name="Demo Package",
        takeoff_evaluation=_takeoff_eval(),
    )

    assert len(payload) < 100_000


def test_blank_measurement_sheet_paths_are_low_confidence() -> None:
    payload = _sample_payload()
    payload["measurement_tree"]["nodes"].append(
        {
            "node_id": "blank::page_1",
            "global_page_id": "blank::page_1",
            "document_name": "Unknown.pdf",
            "canonical_sheet_id": "",
            "sheet_id": "",
            "page_type": "floor_plan",
            "role": "measurement_page",
            "measurement_likelihood_score": 90,
            "final_selection_score": 90,
            "graph_distance_from_seed": 1,
            "inclusion_path": ["A6-01", ""],
        }
    )

    bundle = build_bidscope_review_export_zip(payload, trade_profile={"trade_type": "foam_insulation", "trade_name": "Foam Insulation"})
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        paths = list(csv.DictReader(io.StringIO(archive.read("reference_paths.csv").decode("utf-8"))))

    blank_paths = [row for row in paths if not row["measurement_sheet"]]
    assert blank_paths
    assert all(row["path_confidence"] == "low" for row in blank_paths)


def test_foam_selected_measurement_pages_are_capped_without_takeoff_answer_key_boost() -> None:
    payload = _sample_payload()
    payload["measurement_tree"]["nodes"] = []
    for index in range(30):
        sheet = f"A2-{index:02d}"
        payload["measurement_tree"]["nodes"].append(
            {
                "node_id": f"{sheet}::page_1",
                "global_page_id": f"{sheet}::page_1",
                "document_name": f"{sheet}.pdf",
                "canonical_sheet_id": sheet,
                "sheet_id": sheet,
                "page_type": "floor_plan",
                "role": "measurement_page",
                "measurement_likelihood_score": 80 - index,
                "final_selection_score": 80 - index,
                "graph_distance_from_seed": 1,
                "inclusion_path": ["A6-01", sheet],
            }
        )
    takeoff_eval = {
        "top_predicted_measurement_pages": [
            {"match_key": "sheet:A2-29", "match_keys": ["sheet:A2-29"], "canonical_sheet_id": "A2-29", "predicted_measurement_type": "perimeter"}
        ],
        "matched_pages": [{"match_key": "sheet:A2-29"}],
        "expected_measurement_pages": [{"match_key": "sheet:A2-29", "canonical_sheet_id": "A2-29"}],
        "extra_pages": [],
        "recall": 1,
        "precision": 1,
    }

    bundle = build_bidscope_review_export_zip(
        payload,
        trade_profile={"trade_type": "foam_insulation", "trade_name": "Foam Insulation", "max_final_measurement_pages": 25},
        takeoff_evaluation=takeoff_eval,
    )
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        selected = list(csv.DictReader(io.StringIO(archive.read("selected_pages.csv").decode("utf-8"))))

    likely = [row for row in selected if row["selection_tier"] == "likely_measurement_pages"]
    assert len(likely) == 25
    assert "A2-29" not in {row["canonical_sheet_id"] for row in likely}
