from __future__ import annotations

import json
import csv
import io
import zipfile

from foamscope_ui import analyze_documents, build_export_payload
from ingest.package_ingest import ingest_uploaded_package
from training.bidscope_review_export import build_bidscope_review_export_zip
from takeoff.evaluation import (
    canonical_sheet_id_from_plan_name,
    compare_foamscope_output_to_takeoff_export,
    infer_measurement_type,
    original_page_number_from_plan_name,
    parse_stack_takeoff_csv,
)
from training.bidscope_review_export import build_bidscope_review_export_zip
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


def test_foam_a2_sheets_classify_as_floor_plans_even_with_spec_or_revision_text() -> None:
    package = ingest_uploaded_package(
        [
            FakeUpload("A2.00.pdf", make_pdf("A2.00 Floor Plan\nAddendum revision specification notes")),
            FakeUpload("A2.03.pdf", make_pdf("A2.03 Floor Plan\nProject manual references")),
            FakeUpload("A2.05.pdf", make_pdf("A2.05 Overall Floor Plan\nSpecification reference")),
        ]
    )

    result = analyze_documents(package.documents, depth=1, use_ocr=False, trade_type="foam_insulation")
    by_sheet = {page.sheet_id: page for page in result["pages"]}

    assert by_sheet["A2-00"].role == "floor_plan"
    assert by_sheet["A2-03"].role == "floor_plan"
    assert by_sheet["A2-05"].role == "floor_plan"


def test_foam_a4_sheets_classify_as_elevations_and_export_elevation_area() -> None:
    package = ingest_uploaded_package(
        [
            FakeUpload("A6.13.pdf", make_pdf("A6.13 Wall Section\nspray foam insulation. See A4.04 and A4.05.")),
            FakeUpload("A4.04.pdf", make_pdf("A4.04 Exterior Elevation\nNorth wall")),
            FakeUpload("A4.05.pdf", make_pdf("A4.05 Exterior Elevation\nSouth wall")),
        ]
    )
    result = analyze_documents(package.documents, depth=3, use_ocr=False, trade_type="foam_insulation")
    payload = _payload_from_result(package, result)
    rows = _review_csv_rows(payload, "measurement_candidates.csv")
    by_sheet = {page.sheet_id: page for page in result["pages"]}
    by_candidate = {row["canonical_sheet_id"]: row for row in rows}

    assert by_sheet["A4-04"].role == "measurement_page"
    assert by_sheet["A4-05"].role == "measurement_page"
    assert by_candidate["A4-04"]["predicted_measurement_type"] == "elevation_area"
    assert by_candidate["A4-05"]["predicted_measurement_type"] == "elevation_area"


def test_foam_a2_sheets_become_measurement_candidates_when_connected_to_seed() -> None:
    package = ingest_uploaded_package(
        [
            FakeUpload("A6.13.pdf", make_pdf("A6.13 Wall Section\nspray foam insulation. See A2.00, A2.03, and A2.05.")),
            FakeUpload("A2.00.pdf", make_pdf("A2.00 Floor Plan\nFirst floor layout")),
            FakeUpload("A2.03.pdf", make_pdf("A2.03 Floor Plan\nThird floor layout")),
            FakeUpload("A2.05.pdf", make_pdf("A2.05 Floor Plan\nFifth floor layout")),
        ]
    )

    result = analyze_documents(package.documents, depth=3, use_ocr=False, trade_type="foam_insulation")
    by_sheet = {page.sheet_id: page for page in result["pages"]}

    for sheet_id in ("A2-00", "A2-03", "A2-05"):
        assert by_sheet[sheet_id].role == "measurement_page"
        assert by_sheet[sheet_id].measurement_likelihood_score >= 80
        assert by_sheet[sheet_id].inclusion_path


def test_penalized_discipline_sheets_do_not_become_final_measurement_pages_without_direct_foam() -> None:
    package = ingest_uploaded_package(
        [
            FakeUpload("A6.13.pdf", make_pdf("A6.13 Wall Section\nspray foam insulation. See E1.01, M1.01, P1.01, C1.01, L1.01, FP1.01.")),
            FakeUpload("E1.01.pdf", make_pdf("E1.01 Electrical Plan\nlighting layout")),
            FakeUpload("M1.01.pdf", make_pdf("M1.01 Mechanical Plan\nduct layout")),
            FakeUpload("P1.01.pdf", make_pdf("P1.01 Plumbing Plan\nfixture layout")),
            FakeUpload("C1.01.pdf", make_pdf("C1.01 Civil Plan\nsite layout")),
            FakeUpload("L1.01.pdf", make_pdf("L1.01 Landscape Plan\nplanting layout")),
            FakeUpload("FP1.01.pdf", make_pdf("FP1.01 Fire Protection Plan\nsprinklers")),
        ]
    )

    result = analyze_documents(package.documents, depth=3, use_ocr=False, trade_type="foam_insulation")
    by_sheet = {page.sheet_id: page for page in result["pages"]}

    for sheet_id in ("E1-01", "M1-01", "P1-01", "C1-01", "L1-01", "FP1-01"):
        assert by_sheet[sheet_id].role != "measurement_page"


def test_review_selected_pages_excludes_low_score_debug_connected_pages() -> None:
    package = ingest_uploaded_package(
        [
            FakeUpload("A6.13.pdf", make_pdf("A6.13 Wall Section\nspray foam insulation. See A2.00 and E1.01.")),
            FakeUpload("A2.00.pdf", make_pdf("A2.00 Floor Plan\nExterior wall layout")),
            FakeUpload("E1.01.pdf", make_pdf("E1.01 Electrical Plan\nlighting layout")),
        ]
    )
    result = analyze_documents(package.documents, depth=3, use_ocr=False, trade_type="foam_insulation")
    payload = _payload_from_result(package, result)
    selected_rows = _review_csv_rows(payload, "selected_pages.csv")
    rejected_rows = _review_csv_rows(payload, "rejected_pages_sample.csv")

    selected_sheets = {row["canonical_sheet_id"] for row in selected_rows}
    rejected_sheets = {row["canonical_sheet_id"] for row in rejected_rows}

    assert "A2-00" in selected_sheets
    assert "E1-01" not in selected_sheets
    assert "E1-01" in rejected_sheets


def test_depauw_takeoff_eval_expected_sheets_are_ranked_and_matched() -> None:
    package = ingest_uploaded_package(
        [
            FakeUpload("A6.13.pdf", make_pdf("A6.13 Wall Section\nspray foam insulation. See A2.00, A2.03, A2.05, A4.04, and A4.05.")),
            FakeUpload("A2.00.pdf", make_pdf("A2.00 Floor Plan\nFirst floor perimeter")),
            FakeUpload("A2.03.pdf", make_pdf("A2.03 Floor Plan\nThird floor perimeter")),
            FakeUpload("A2.05.pdf", make_pdf("A2.05 Floor Plan\nFifth floor perimeter")),
            FakeUpload("A4.04.pdf", make_pdf("A4.04 Exterior Elevation\nNorth elevation")),
            FakeUpload("A4.05.pdf", make_pdf("A4.05 Exterior Elevation\nSouth elevation")),
        ]
    )
    result = analyze_documents(package.documents, depth=4, use_ocr=False, trade_type="foam_insulation")
    payload = _payload_from_result(package, result)
    takeoff_csv = "\n".join(
        [
            "Takeoff Name,Takeoff Description,Sq Ft,Ln Ft,Cu Yd,EA,Drop Count,Takeoff Quantity,Takeoff Unit,Scale,Plan Name",
            "Perimeter,First floor perimeter,,100,,,,100,Ln Ft,1/8\"=1',A2.00.pdf",
            "Perimeter,Third floor perimeter,,100,,,,100,Ln Ft,1/8\"=1',A2.03.pdf",
            "Perimeter,Fifth floor perimeter,,100,,,,100,Ln Ft,1/8\"=1',A2.05.pdf",
            "Wall Area,North elevation,1200,,,,,1200,Sq Ft,1/8\"=1',A4.04.pdf",
            "Wall Area,South elevation,1200,,,,,1200,Sq Ft,1/8\"=1',A4.05.pdf",
        ]
    )

    evaluation = training_compare(json.dumps(payload, default=str), takeoff_csv, trade_type="foam_insulation")

    assert evaluation["counts"]["expected"] == 5
    assert evaluation["recall"] == 1
    assert evaluation["precision_at_10"] > 0
    assert not evaluation["missed_pages"]


def _payload_from_result(package, result):
    return build_export_payload(
        {
            "documents": [document.to_dict() for document in package.documents],
            "manifest": [],
            "progress": {},
            "scan_completeness": {"trade_type": result.get("trade_type"), "trade_name": result.get("trade_name")},
            "partial": False,
            "selected_node_count_internal": len(result["selected_nodes"]),
            "exported_node_count": len(result["tree"]["nodes"]),
            "selected_nodes_exported": [node["node_id"] for node in result["tree"]["nodes"]],
            **result,
        },
        result["pages"],
    )


def _review_csv_rows(payload: dict, filename: str) -> list[dict[str, str]]:
    bundle = build_bidscope_review_export_zip(
        payload,
        trade_profile={"trade_type": "foam_insulation", "trade_name": "Foam Insulation"},
        takeoff_evaluation=None,
    )
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        return list(csv.DictReader(io.StringIO(archive.read(filename).decode("utf-8"))))
