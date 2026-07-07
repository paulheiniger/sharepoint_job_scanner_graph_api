from __future__ import annotations

from dataclasses import dataclass

from jobscan.estimator.photo_evidence import (
    analyze_selected_photos_with_ai,
    apply_photo_record_edits,
    apply_photo_scope_context,
    build_photo_scope_context,
    classify_photo,
    combine_notes_with_photo_context,
    merge_photo_ai_analysis,
    select_representative_images,
    stage_uploaded_images,
)
from jobscan.estimator.schemas import EstimateRecommendation, EstimatorData
from jobscan.estimator.workbench import build_estimating_workbench


@dataclass
class UploadedBytes:
    name: str
    payload: bytes

    def getvalue(self) -> bytes:
        return self.payload


def recommendation(parsed_fields: dict) -> EstimateRecommendation:
    return EstimateRecommendation(
        parsed_fields=parsed_fields,
        recommended_scope=[],
        material_plan=[],
        labor_plan=[],
        travel_plan={},
        historical_calibration={},
        similar_examples=[],
        estimate_low=None,
        estimate_target=None,
        estimate_high=None,
        review_flags=[],
        human_review_required=False,
        draft_workbook_inputs={},
    )


def test_stage_uploaded_images_hashes_classifies_and_selects(tmp_path) -> None:
    files = [
        UploadedBytes("roof_open_seam_drain_ponding.jpg", b"not a real image but still stored"),
        UploadedBytes("roof_open_seam_drain_ponding_duplicate.jpg", b"not a real image but still stored"),
        UploadedBytes("rear_ladder_access.png", b"other image bytes"),
    ]

    records = stage_uploaded_images(files, upload_key="test-job", storage_root=tmp_path)

    assert len(records) == 3
    assert records[0]["stored_path"]
    assert "open_seams" in records[0]["signals"]
    assert "ponding" in records[0]["signals"]
    assert records[1]["duplicate"] is True
    assert any(record["selected"] for record in records)


def test_photo_scope_context_creates_roofing_decision_proposals() -> None:
    records = [
        {
            "image_id": "img1",
            "content_hash": "hash1",
            "file_name": "open seam ponding drain.jpg",
            "category": "drains",
            "signals": ["open_seams", "ponding"],
            "quality_flags": [],
            "duplicate": False,
        },
        {
            "image_id": "img2",
            "content_hash": "hash2",
            "file_name": "rusted fasteners metal roof.jpg",
            "category": "fasteners_rust",
            "signals": ["rusted_fasteners", "metal_roof"],
            "quality_flags": [],
            "duplicate": False,
        },
    ]

    context = build_photo_scope_context(records, selected_hashes=["hash1", "hash2"], template_type="roofing")
    scope = apply_photo_scope_context({"template_type": "roofing", "division": "Roofing", "estimated_sqft": 10000}, context)

    assert "photo_decision_proposals" in scope
    decision_ids = {proposal["decision_id"] for proposal in scope["photo_decision_proposals"]}
    assert "roofing_seams_misc_row_47" in decision_ids
    assert "roofing_primer_system_row_39" in decision_ids
    assert scope["defects"]["open_seams"] is True
    assert "Photo-visible issues" in combine_notes_with_photo_context("Roof review.", context)


def test_empty_selected_hashes_means_no_photo_evidence() -> None:
    context = build_photo_scope_context(
        [
            {
                "image_id": "img1",
                "content_hash": "hash1",
                "file_name": "open seam ponding drain.jpg",
                "category": "drains",
                "signals": ["open_seams", "ponding"],
                "quality_flags": [],
                "duplicate": False,
            }
        ],
        selected_hashes=[],
        template_type="roofing",
    )

    assert context["selected_image_count"] == 0
    assert context["photo_decision_proposals"] == []


def test_photo_record_edits_turn_generic_uploads_into_decision_evidence() -> None:
    records = [
        {
            "image_id": "img1",
            "content_hash": "hash1",
            "file_name": "IMG_1234.jpg",
            "category": "unknown",
            "signals": [],
            "quality_flags": [],
            "duplicate": False,
        },
        {
            "image_id": "img2",
            "content_hash": "hash2",
            "file_name": "IMG_1235.jpg",
            "category": "unknown",
            "signals": [],
            "quality_flags": [],
            "duplicate": False,
        },
    ]

    edited = apply_photo_record_edits(
        records,
        [
            {"image_id": "img1", "category": "seams", "signals": ""},
            {"image_id": "img2", "category": "drains", "signals": "ponding"},
        ],
    )
    context = build_photo_scope_context(edited, selected_hashes=["hash1", "hash2"], template_type="roofing")

    assert edited[0]["signals"] == ["open_seams"]
    assert edited[0]["classification_source"] == "estimator_review"
    assert "open_seams" in context["signals"]
    assert "ponding" in context["signals"]
    decision_ids = {proposal["decision_id"] for proposal in context["photo_decision_proposals"]}
    assert "roofing_seams_misc_row_47" in decision_ids
    assert "roofing_fabric_row_79" in decision_ids


def test_photo_evidence_applies_to_workbench_as_review_required() -> None:
    context = build_photo_scope_context(
        [
            {
                "image_id": "img1",
                "content_hash": "hash1",
                "file_name": "open seam ponding drain.jpg",
                "category": "drains",
                "signals": ["open_seams", "ponding"],
                "quality_flags": [],
                "duplicate": False,
            }
        ],
        selected_hashes=["hash1"],
        template_type="roofing",
    )
    parsed = apply_photo_scope_context(
        {
            "division": "Roofing",
            "template_type": "roofing",
            "project_type": "roof restoration review",
            "estimated_sqft": 10000,
            "net_sqft": 10000,
        },
        context,
    )

    workbench = build_estimating_workbench(recommendation(parsed), EstimatorData())
    seams = next(row for row in workbench["roofing_detail_quantity_template_decisions"] if row["workbook_row"] == "47")

    assert seams["include"] is True
    assert seams["proposal_source"] == "photo_evidence"
    assert seams["proposal_review_required"] is True
    assert any("Photo evidence" in reason for reason in seams["proposal_review_reasons"])


def test_ai_photo_analysis_is_explicit_bounded_and_cached(tmp_path) -> None:
    image_path = tmp_path / "open-seam.jpg"
    image_path.write_bytes(b"fake image bytes")
    records = [
        {
            "image_id": "img1",
            "content_hash": "hash1",
            "file_name": "open seam.jpg",
            "stored_path": str(image_path),
            "category": "seams",
            "signals": ["open_seams"],
            "quality_flags": [],
            "duplicate": False,
        }
    ]
    calls = []

    def provider(messages, model):
        calls.append((messages, model))
        return {
            "roof_condition": "weathered but serviceable",
            "existing_system": "metal roof",
            "visible_issues": ["open seams"],
            "recommended_scope_notes": ["treat seams before coating"],
            "risk_flags": ["confirm adhesion"],
            "missing_photos": ["wide overview of full roof"],
            "confidence": 0.72,
        }

    analysis = analyze_selected_photos_with_ai(
        records,
        selected_hashes=["hash1"],
        template_type="roofing",
        notes="Roof restoration review.",
        cache_dir=tmp_path / "cache",
        provider=provider,
    )
    cached = analyze_selected_photos_with_ai(
        records,
        selected_hashes=["hash1"],
        template_type="roofing",
        notes="Roof restoration review.",
        cache_dir=tmp_path / "cache",
        provider=lambda *_args: (_ for _ in ()).throw(AssertionError("provider should not run")),
    )

    assert len(calls) == 1
    assert calls[0][1] == "gpt-4o-mini"
    assert analysis["cache_hit"] is False
    assert cached["cache_hit"] is True
    assert cached["visible_issues"] == ["open seams"]


def test_merge_ai_photo_analysis_adds_decision_evidence() -> None:
    records = [
        {
            "image_id": "img1",
            "content_hash": "hash1",
            "file_name": "ai-open-seam.jpg",
            "category": "seams",
            "signals": [],
            "quality_flags": [],
            "duplicate": False,
        }
    ]
    context = build_photo_scope_context(records, selected_hashes=["hash1"], template_type="roofing")
    merged = merge_photo_ai_analysis(
        context,
        {
            "visible_issues": ["open seams", "coating wear"],
            "recommended_scope_notes": ["treat seams", "review coating restoration path"],
            "risk_flags": ["confirm substrate qualification"],
            "missing_photos": [],
            "confidence": 0.74,
            "selected_hashes": ["hash1"],
        },
        records=records,
    )

    decision_ids = {proposal["decision_id"] for proposal in merged["photo_decision_proposals"]}
    assert "roofing_seams_misc_row_47" in decision_ids
    assert "roofing_coating_system_row_26" in decision_ids
    assert merged["ai_photo_analysis_used"] is True
    assert "photo_ai" in merged["photo_decision_proposals"][0]["evidence"]


def test_select_representative_images_prefers_category_coverage() -> None:
    records = [
        {"content_hash": "a", "file_name": "a.jpg", "category": "seams", "signals": ["open_seams"], "quality_flags": []},
        {"content_hash": "b", "file_name": "b.jpg", "category": "seams", "signals": ["open_seams"], "quality_flags": []},
        {"content_hash": "c", "file_name": "c.jpg", "category": "access", "signals": ["access_constraints"], "quality_flags": []},
    ]

    selected = select_representative_images(records, max_images=2)

    assert len(selected) == 2
    assert "c" in selected


def test_classify_photo_uses_filename_signals() -> None:
    category, signals = classify_photo(file_name="metal roof rusted fastener access ladder.jpg")

    assert category in {"access", "fasteners_rust"}
    assert "rusted_fasteners" in signals
    assert "metal_roof" in signals
