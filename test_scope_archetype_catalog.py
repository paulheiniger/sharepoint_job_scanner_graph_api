from __future__ import annotations

import copy
import json

import pandas as pd
import pytest

from jobscan.estimator.data_loader import _attach_scope_archetype_catalog
from jobscan.estimator.schemas import EstimatorData
from jobscan.estimator.scope_archetype_catalog import (
    build_advisory_scope_catalog,
    build_approved_scope_catalog,
    build_scope_archetype_shadow_evidence,
    build_scope_pattern_evidence,
    load_approved_scope_catalog,
    validate_scope_catalog,
    write_approved_scope_catalog,
)


def _approved_catalog() -> dict:
    archetypes = pd.DataFrame(
        [
            {
                "archetype_id": "A1",
                "template_type": "roofing",
                "provisional_label": "Roof coating",
                "review_proposed_name": "Reviewed coating restoration",
                "review_approved": True,
                "review_complete": True,
                "core_decisions_json": json.dumps(["coating@row_26"]),
                "typical_decisions_json": json.dumps(["primer@row_39"]),
                "ai_conditional_modifiers_json": json.dumps(
                    ["Primer depends on substrate."]
                ),
                "ai_required_signals_json": json.dumps(["Coating scope"]),
                "ai_likely_exclusions_json": json.dumps(["Tear-off"]),
                "ai_review_confidence": 0.9,
                "observation_count": 24,
            },
            {
                "archetype_id": "A2",
                "template_type": "roofing",
                "provisional_label": "Unreviewed system",
                "review_approved": False,
                "review_complete": True,
                "core_decisions_json": json.dumps(["foam@row_19"]),
                "typical_decisions_json": "[]",
            },
        ]
    )
    rules = pd.DataFrame(
        [
            {
                "rule_key": "R1",
                "segment_value": "roofing",
                "antecedent": "coating@row_26",
                "consequent": "primer@row_39",
                "validation_status": "stable_candidate",
                "full_support_count": 20,
                "full_confidence": 0.91,
                "full_lift": 1.4,
                "sufficient_split_count": 5,
                "stable_split_count": 5,
                "review_approved": True,
                "review_complete": True,
            },
            {
                "rule_key": "R2",
                "segment_value": "roofing",
                "antecedent": "coating@row_26",
                "consequent": "fabric@row_79",
                "validation_status": "unstable_candidate",
                "review_approved": True,
                "review_complete": True,
            },
        ]
    )
    return build_approved_scope_catalog(archetypes, rules)


def test_catalog_contains_only_completed_approved_stable_evidence() -> None:
    catalog = _approved_catalog()

    assert catalog["runtime_mode"] == "shadow_only"
    assert catalog["applied_automatically"] is False
    assert catalog["approved_archetype_count"] == 1
    assert catalog["approved_rule_count"] == 1
    assert catalog["archetypes"][0]["archetype_id"] == "A1"
    assert catalog["rules"][0]["rule_key"] == "R1"


def test_catalog_round_trip_and_integrity_validation(tmp_path) -> None:
    catalog = _approved_catalog()
    path = write_approved_scope_catalog(catalog, tmp_path / "catalog.json")

    loaded = load_approved_scope_catalog(path)
    assert loaded["catalog_version"] == catalog["catalog_version"]

    unsafe = copy.deepcopy(loaded)
    unsafe["runtime_mode"] = "automatic"
    with pytest.raises(ValueError, match="shadow_only"):
        validate_scope_catalog(unsafe)


def test_shadow_matching_does_not_modify_workbench() -> None:
    catalog = _approved_catalog()
    workbench = {
        "scope": {"template_type": "roofing"},
        "roofing_coating_template_decisions": [
            {
                "include": True,
                "template_bucket": "coating",
                "workbook_row": 26,
            }
        ],
        "roofing_primer_template_decisions": [
            {
                "include": False,
                "template_bucket": "primer",
                "workbook_row": 39,
            }
        ],
    }
    before = copy.deepcopy(workbench)

    evidence = build_scope_archetype_shadow_evidence(workbench, catalog)

    assert workbench == before
    assert evidence["applied_automatically"] is False
    assert evidence["matched_archetypes"][0]["name"] == "Reviewed coating restoration"
    assert evidence["companion_hints"][0]["possible_companion"] == "primer@row_39"


def test_data_loader_attaches_only_valid_catalog(monkeypatch, tmp_path) -> None:
    path = write_approved_scope_catalog(
        _approved_catalog(),
        tmp_path / "catalog.json",
    )
    monkeypatch.setenv("ESTIMATOR_SCOPE_ARCHETYPE_CATALOG_PATH", str(path))

    data = _attach_scope_archetype_catalog(EstimatorData(), tmp_path)

    assert data.scope_archetype_catalog["approved_archetype_count"] == 1
    assert any("scope archetype catalog:" in source for source in data.source_files_used)


def test_advisory_catalog_keeps_ai_archetypes_and_only_stable_rules() -> None:
    archetypes = pd.DataFrame(
        [
            {
                "archetype_id": "A1",
                "template_type": "roofing",
                "provisional_label": "Roof coating",
                "ai_proposed_name": "Coated foam restoration",
                "ai_base_system": "SPF with coating",
                "ai_review_confidence": 0.86,
                "core_decisions_json": json.dumps(
                    ["foam@row_19", "coating@row_26"]
                ),
                "typical_decisions_json": json.dumps(["coating@row_27"]),
                "occasional_decisions_json": json.dumps(["primer@row_39"]),
                "full_decision_rates_json": json.dumps(
                    {
                        "foam@row_19": 0.95,
                        "coating@row_26": 1.0,
                        "coating@row_27": 0.72,
                    }
                ),
                "ai_required_signals_json": json.dumps(
                    ["Existing SPF roof receiving coating"]
                ),
                "ai_conditional_modifiers_json": json.dumps(
                    ["Primer depends on substrate."]
                ),
                "ai_likely_exclusions_json": json.dumps(
                    ["Tear-off without explicit scope."]
                ),
                "ai_ambiguities_json": json.dumps(
                    ["Warranty can change coating requirements."]
                ),
                "observation_count": 76,
            }
        ]
    )
    rules = pd.DataFrame(
        [
            {
                "rule_key": "R1",
                "segment_value": "roofing",
                "antecedent": "coating@row_26",
                "consequent": "coating@row_27",
                "validation_status": "stable_candidate",
                "full_support_count": 55,
                "full_confidence": 0.94,
                "full_lift": 1.8,
                "sufficient_split_count": 6,
                "stable_split_count": 5,
            },
            {
                "rule_key": "R2",
                "segment_value": "roofing",
                "antecedent": "coating@row_26",
                "consequent": "primer@row_39",
                "validation_status": "unstable_candidate",
            },
        ]
    )

    catalog = build_advisory_scope_catalog(archetypes, rules)

    assert catalog["advisory_archetype_count"] == 1
    assert catalog["advisory_rule_count"] == 1
    assert catalog["applied_automatically"] is False
    assert catalog["archetypes"][0]["evidence_status"] == "ai_labeled_unreviewed"
    assert catalog["rules"][0]["rule_key"] == "R1"


def test_scope_pattern_matching_uses_complete_comparable_manifest_as_advice() -> None:
    archetypes = pd.DataFrame(
        [
            {
                "archetype_id": "A1",
                "template_type": "roofing",
                "ai_proposed_name": "Coated foam restoration",
                "ai_review_confidence": 0.9,
                "core_decisions_json": json.dumps(
                    ["foam@row_19", "coating@row_26"]
                ),
                "typical_decisions_json": json.dumps(["coating@row_27"]),
                "full_decision_rates_json": json.dumps(
                    {
                        "foam@row_19": 0.95,
                        "coating@row_26": 1.0,
                        "coating@row_27": 0.72,
                    }
                ),
                "ai_required_signals_json": json.dumps(["Coated foam scope"]),
                "ai_conditional_modifiers_json": "[]",
                "ai_likely_exclusions_json": "[]",
                "ai_ambiguities_json": "[]",
                "observation_count": 76,
            }
        ]
    )
    rules = pd.DataFrame(
        [
            {
                "rule_key": "R1",
                "segment_value": "roofing",
                "antecedent": "coating@row_26",
                "consequent": "coating@row_27",
                "validation_status": "stable_candidate",
                "full_support_count": 55,
                "full_confidence": 0.94,
                "full_lift": 1.8,
                "sufficient_split_count": 6,
                "stable_split_count": 5,
            }
        ]
    )
    catalog = build_advisory_scope_catalog(archetypes, rules)

    evidence = build_scope_pattern_evidence(
        {
            "template_type": "roofing",
            "raw_input_notes": "Restore the existing coated foam roof.",
        },
        catalog,
        comparable_manifests=[
            {
                "manifest_complete": True,
                "active_decision_keys": [
                    "foam@row_19",
                    "coating@row_26",
                    "coating@row_27",
                ],
            }
        ],
    )

    assert evidence["applied_automatically"] is False
    assert evidence["matched_archetypes"][0]["archetype_id"] == "A1"
    assert evidence["matched_archetypes"][0]["decision_rates"][
        "coating@row_27"
    ] == 0.72
    assert evidence["validated_relationships"][0]["rule_key"] == "R1"
    assert evidence["validated_relationships"][0]["applied_automatically"] is False
