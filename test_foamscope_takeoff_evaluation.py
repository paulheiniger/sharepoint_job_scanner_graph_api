from __future__ import annotations

import json

from foamscope_ui import analyze_documents, build_export_payload
from ingest.package_ingest import ingest_uploaded_package
from takeoff.evaluation import (
    canonical_sheet_id_from_plan_name,
    compare_foamscope_output_to_takeoff_export,
    infer_measurement_type,
    original_page_number_from_plan_name,
    parse_stack_takeoff_csv,
)
from training.completed_takeoff_parser import parse_stack_takeoff_csv as training_parse_stack_takeoff_csv
from training.foamscope_evaluator import compare_foamscope_output_to_takeoff_export as training_compare


class FakeUpload:
    def __init__(self, name: str, content: bytes) -> None:
        self.name = name
        self._content = content

    def getvalue(self) -> bytes:
        return self._content


def make_pdf(text: str) -> bytes:
    import fitz

    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), text, fontsize=11)
    payload = document.tobytes()
    document.close()
    return payload


def test_stack_plan_names_normalize_to_canonical_sheet_ids() -> None:
    assert canonical_sheet_id_from_plan_name("A2.00.pdf") == "A2-00"
    assert canonical_sheet_id_from_plan_name("A4.04") == "A4-04"
    assert canonical_sheet_id_from_plan_name("A4.05") == "A4-05"


def test_de_pauw_split_page_plan_name_extracts_original_page_number() -> None:
    assert original_page_number_from_plan_name("2026-DePauw Bid Set.pdf Page 131.pdf") == 131


def test_measurement_type_inference_from_stack_rows() -> None:
    assert infer_measurement_type({"Takeoff Name": "Exterior Perimeter", "Takeoff Unit": "Ln Ft"}) == "perimeter"
    assert infer_measurement_type({"Takeoff Description": "North Elevation", "Takeoff Unit": "Sq Ft"}) == "elevation_area"
    assert infer_measurement_type({"Takeoff Name": "Attic insulation", "Takeoff Unit": "Sq Ft"}) == "attic_area"
    assert infer_measurement_type({"Takeoff Unit": "Sq Ft"}) == "area"
    assert infer_measurement_type({"Takeoff Unit": "Ln Ft"}) == "perimeter"


def test_parse_stack_takeoff_csv_creates_positive_measurement_labels() -> None:
    labels = parse_stack_takeoff_csv(
        "\n".join(
            [
                "Takeoff Name,Takeoff Description,Sq Ft,Ln Ft,Cu Yd,EA,Drop Count,Takeoff Quantity,Takeoff Unit,Scale,Plan Name",
                "Wall Area,Exterior elevation,1200,,,,,1200,Sq Ft,1/8\"=1',A4.04.pdf",
                "Perimeter,Roof perimeter,,320,,,,320,Ln Ft,1/8\"=1',A2.00.pdf",
                "Attic,Attic insulation,500,,,,,500,Sq Ft,1/8\"=1',2026-DePauw Bid Set.pdf Page 131.pdf",
            ]
        )
    )

    by_key = {label.match_key: label for label in labels}
    assert by_key["sheet:A4-04"].measurement_type == "elevation_area"
    assert by_key["sheet:A2-00"].measurement_type == "perimeter"
    assert by_key["page:131"].measurement_type == "attic_area"


def test_compare_foamscope_output_to_takeoff_export_reports_precision_and_recall() -> None:
    foamscope_json = {
        "measurement_tree": {
            "nodes": [
                {
                    "role": "measurement_page",
                    "canonical_sheet_id": "A4-04",
                    "document_name": "A4.04.pdf",
                    "page_num": 1,
                    "sheet_title": "Exterior Elevations",
                    "inclusion_path": ["A6-13", "A4-04"],
                },
                {
                    "role": "measurement_page",
                    "canonical_sheet_id": "A2-00",
                    "document_name": "A2.00.pdf",
                    "page_num": 1,
                    "sheet_title": "Floor Plan",
                    "inclusion_path": ["A6-13", "A2-00"],
                },
                {
                    "role": "measurement_page",
                    "canonical_sheet_id": "A9-99",
                    "document_name": "A9.99.pdf",
                    "page_num": 1,
                    "sheet_title": "Extra Detail",
                    "inclusion_path": ["A6-13", "A9-99"],
                },
            ]
        }
    }
    takeoff_csv = "\n".join(
        [
            "Takeoff Name,Takeoff Description,Sq Ft,Ln Ft,Cu Yd,EA,Drop Count,Takeoff Quantity,Takeoff Unit,Scale,Plan Name",
            "Wall Area,Exterior elevation,1200,,,,,1200,Sq Ft,1/8\"=1',A4.04.pdf",
            "Perimeter,Roof perimeter,,320,,,,320,Ln Ft,1/8\"=1',A2.00.pdf",
            "Attic,Attic insulation,500,,,,,500,Sq Ft,1/8\"=1',2026-DePauw Bid Set.pdf Page 131.pdf",
        ]
    )

    result = compare_foamscope_output_to_takeoff_export(json.dumps(foamscope_json), takeoff_csv)

    assert result["counts"] == {"expected": 3, "selected": 3, "matched": 2, "missed": 1, "extra": 1}
    assert round(result["recall"], 3) == 0.667
    assert round(result["precision"], 3) == 0.667
    assert {page["match_key"] for page in result["missed_pages"]} == {"page:131"}
    assert {page["match_key"] for page in result["extra_selected_pages"]} == {"sheet:A9-99"}


def test_training_parser_alias_uses_completed_takeoff_parser() -> None:
    labels = training_parse_stack_takeoff_csv(
        "\n".join(
            [
                "Takeoff Name,Takeoff Description,Sq Ft,Ln Ft,Cu Yd,EA,Drop Count,Takeoff Quantity,Takeoff Unit,Scale,Plan Name",
                "Wall Area,Exterior elevation,1200,,,,,1200,Sq Ft,1/8\"=1',A4.04.pdf",
            ]
        ),
        project_id="depauw",
        trade_type="foam_insulation",
    )

    assert labels[0].canonical_sheet_id == "A4-04"
    assert labels[0].measurement_type == "elevation_area"
    assert labels[0].project_id == "depauw"
    assert labels[0].trade_type == "foam_insulation"


def test_seed_sheet_scores_high_but_is_not_measurement_page() -> None:
    package = ingest_uploaded_package([FakeUpload("A6.13.pdf", make_pdf("A6.13 Wall Section\nspray foam insulation. See A4.04."))])

    result = analyze_documents(package.documents, depth=2, use_ocr=False)
    page = result["pages"][0]

    assert page.seed_evidence_score > 0
    assert page.foam_seed_level == "high"
    assert page.role in {"assembly_definition", "detail_reference"}
    assert page.role != "measurement_page"
    assert page.measurement_likelihood_score == 0


def test_elevation_connected_to_seed_gets_high_measurement_likelihood() -> None:
    package = ingest_uploaded_package(
        [
            FakeUpload("A6.13.pdf", make_pdf("A6.13 Wall Section\nspray foam insulation. See A4.04.")),
            FakeUpload("A4.04.pdf", make_pdf("A4.04 Exterior Elevation\nNorth elevation wall area")),
        ]
    )

    result = analyze_documents(package.documents, depth=3, use_ocr=False)
    by_sheet = {page.sheet_id: page for page in result["pages"]}

    assert by_sheet["A4-04"].role == "measurement_page"
    assert by_sheet["A4-04"].measurement_likelihood_score >= 90
    assert by_sheet["A4-04"].graph_distance_from_seed is not None
    assert by_sheet["A4-04"].inclusion_path


def test_generic_insulation_only_does_not_dominate_final_selection() -> None:
    package = ingest_uploaded_package([FakeUpload("A2.00.pdf", make_pdf("A2.00 Floor Plan\ninsulation exterior wall partition type"))])

    result = analyze_documents(package.documents, depth=3, use_ocr=False)
    page = result["pages"][0]

    assert result["seed_nodes"] == []
    assert page.foam_seed_level == "generic_only"
    assert page.role == "candidate_only"
    assert page.measurement_likelihood_score == 0


def test_training_evaluator_returns_precision_at_k() -> None:
    package = ingest_uploaded_package(
        [
            FakeUpload("A6.13.pdf", make_pdf("A6.13 Wall Section\nspray foam insulation. See A4.04 and A2.00.")),
            FakeUpload("A4.04.pdf", make_pdf("A4.04 Exterior Elevation\nNorth elevation wall area")),
            FakeUpload("A2.00.pdf", make_pdf("A2.00 Floor Plan\nExterior wall layout")),
            FakeUpload("A9.99.pdf", make_pdf("A9.99 Schedule\nExtra unrelated schedule")),
        ]
    )
    result = analyze_documents(package.documents, depth=4, use_ocr=False)
    payload = build_export_payload(
        {
            "documents": [document.to_dict() for document in package.documents],
            "manifest": [],
            "progress": {},
            "scan_completeness": {},
            "partial": False,
            "selected_node_count_internal": len(result["selected_nodes"]),
            "exported_node_count": len(result["tree"]["nodes"]),
            "selected_nodes_exported": [node["node_id"] for node in result["tree"]["nodes"]],
            **result,
        },
        result["pages"],
    )
    takeoff_csv = "\n".join(
        [
            "Takeoff Name,Takeoff Description,Sq Ft,Ln Ft,Cu Yd,EA,Drop Count,Takeoff Quantity,Takeoff Unit,Scale,Plan Name",
            "Wall Area,Exterior elevation,1200,,,,,1200,Sq Ft,1/8\"=1',A4.04.pdf",
            "Floor Area,Exterior wall,800,,,,,800,Sq Ft,1/8\"=1',A2.00.pdf",
        ]
    )

    evaluation = training_compare(json.dumps(payload, default=str), takeoff_csv)

    assert evaluation["recall"] == 1
    assert evaluation["precision_at_10"] > 0
    assert evaluation["precision_at_25"] > 0
    assert evaluation["precision_at_50"] > 0
    assert evaluation["top_predicted_measurement_pages"][0]["why_selected"]


def test_roofing_profile_identifies_seed_and_connected_roof_plan() -> None:
    package = ingest_uploaded_package(
        [
            FakeUpload("A6.01.pdf", make_pdf("A6.01 Roof Detail\nTPO roof replacement and flashing. See A3.01.")),
            FakeUpload("A3.01.pdf", make_pdf("A3.01 Roof Plan\nRoof area, drains, curbs, and edge conditions")),
        ]
    )

    result = analyze_documents(package.documents, depth=3, use_ocr=False, trade_type="roofing")
    by_sheet = {page.sheet_id: page for page in result["pages"]}

    assert by_sheet["A6-01"].seed_evidence_score > 0
    assert by_sheet["A6-01"].role in {"detail_reference", "assembly_definition"}
    assert by_sheet["A6-01"].role != "measurement_page"
    assert by_sheet["A3-01"].role == "measurement_page"
    assert by_sheet["A3-01"].measurement_likelihood_score > 0
    assert by_sheet["A3-01"].inclusion_path
    assert result["trade_type"] == "roofing"


def test_roofing_evaluation_metrics_work() -> None:
    package = ingest_uploaded_package(
        [
            FakeUpload("A6.01.pdf", make_pdf("A6.01 Roof Detail\nmetal roof coating and flashing. See A3.01.")),
            FakeUpload("A3.01.pdf", make_pdf("A3.01 Roof Plan\nRoof plan area and drains")),
        ]
    )
    result = analyze_documents(package.documents, depth=3, use_ocr=False, trade_type="roofing")
    payload = build_export_payload(
        {
            "documents": [document.to_dict() for document in package.documents],
            "manifest": [],
            "progress": {},
            "scan_completeness": {"trade_type": "roofing", "trade_name": "Roofing"},
            "partial": False,
            "selected_node_count_internal": len(result["selected_nodes"]),
            "exported_node_count": len(result["tree"]["nodes"]),
            "selected_nodes_exported": [node["node_id"] for node in result["tree"]["nodes"]],
            **result,
        },
        result["pages"],
    )
    takeoff_csv = "\n".join(
        [
            "Takeoff Name,Takeoff Description,Sq Ft,Ln Ft,Cu Yd,EA,Drop Count,Takeoff Quantity,Takeoff Unit,Scale,Plan Name",
            "Roof Area,TPO roof area,1200,,,,,1200,Sq Ft,1/8\"=1',A3.01.pdf",
        ]
    )

    evaluation = training_compare(json.dumps(payload, default=str), takeoff_csv, trade_type="roofing")

    assert evaluation["counts"]["expected"] == 1
    assert evaluation["recall"] == 1
    assert evaluation["precision_at_10"] > 0
