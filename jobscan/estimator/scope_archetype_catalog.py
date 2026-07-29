from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

import pandas as pd


CATALOG_SCHEMA_VERSION = "estimator_scope_archetype_catalog.v1"
CATALOG_RUNTIME_MODE = "shadow_only"


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not pd.isna(value):
        return bool(value)
    normalized = _text(value).lower()
    if normalized in {"true", "yes", "y", "1", "approved"}:
        return True
    if normalized in {"false", "no", "n", "0", "rejected"}:
        return False
    return None


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if not _text(value):
        return []
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not _text(value):
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if pd.notna(number) else None


def _approved_rows(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return []
    return [
        row
        for row in frame.to_dict(orient="records")
        if _as_bool(row.get("review_complete")) is True
        and _as_bool(row.get("review_approved")) is True
    ]


def build_approved_scope_catalog(
    archetype_review: pd.DataFrame,
    rule_review: pd.DataFrame,
    *,
    source_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    archetypes: list[dict[str, Any]] = []
    for row in _approved_rows(archetype_review):
        archetypes.append(
            {
                "archetype_id": _text(row.get("archetype_id")),
                "name": (
                    _text(row.get("review_proposed_name"))
                    or _text(row.get("ai_proposed_name"))
                    or _text(row.get("provisional_label"))
                ),
                "template_type": _text(row.get("template_type")).lower(),
                "base_system": _text(row.get("ai_base_system")),
                "core_decisions": [
                    str(value)
                    for value in _json_list(row.get("core_decisions_json"))
                ],
                "typical_decisions": [
                    str(value)
                    for value in _json_list(row.get("typical_decisions_json"))
                ],
                "conditional_modifiers": [
                    str(value)
                    for value in _json_list(
                        row.get("ai_conditional_modifiers_json")
                    )
                ],
                "required_signals": [
                    str(value)
                    for value in _json_list(row.get("ai_required_signals_json"))
                ],
                "likely_exclusions": [
                    str(value)
                    for value in _json_list(row.get("ai_likely_exclusions_json"))
                ],
                "review_confidence": _number(row.get("ai_review_confidence")),
                "reviewer_notes": _text(row.get("reviewer_notes")),
                "source_observation_count": int(
                    _number(row.get("observation_count")) or 0
                ),
            }
        )
    archetypes.sort(key=lambda row: (row["template_type"], row["name"], row["archetype_id"]))

    rules: list[dict[str, Any]] = []
    for row in _approved_rows(rule_review):
        if _text(row.get("validation_status")) != "stable_candidate":
            continue
        rules.append(
            {
                "rule_key": _text(row.get("rule_key")),
                "template_type": _text(row.get("segment_value")).lower(),
                "antecedent": _text(row.get("antecedent")),
                "consequent": _text(row.get("consequent")),
                "support_count": int(_number(row.get("full_support_count")) or 0),
                "confidence": _number(row.get("full_confidence")),
                "lift": _number(row.get("full_lift")),
                "sufficient_split_count": int(
                    _number(row.get("sufficient_split_count")) or 0
                ),
                "stable_split_count": int(
                    _number(row.get("stable_split_count")) or 0
                ),
                "reviewer_notes": _text(row.get("reviewer_notes")),
            }
        )
    rules.sort(
        key=lambda row: (
            row["template_type"],
            row["antecedent"],
            row["consequent"],
        )
    )

    version_payload = {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "runtime_mode": CATALOG_RUNTIME_MODE,
        "archetypes": archetypes,
        "rules": rules,
        "source_metadata": source_metadata or {},
    }
    digest = hashlib.sha256(
        json.dumps(
            version_payload,
            default=str,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return {
        **version_payload,
        "catalog_version": f"scope-archetypes-{digest[:16]}",
        "content_sha256": digest,
        "created_at": datetime.now(UTC).isoformat(),
        "approved_archetype_count": len(archetypes),
        "approved_rule_count": len(rules),
        "applied_automatically": False,
    }


def build_advisory_scope_catalog(
    archetype_review: pd.DataFrame,
    rule_validation: pd.DataFrame,
    *,
    source_metadata: dict[str, Any] | None = None,
    minimum_ai_confidence: float = 0.7,
) -> dict[str, Any]:
    """Build evidence-only catalog from AI-labeled archetypes and stable rules.

    Unlike the approved catalog, this artifact is allowed to contain unreviewed
    evidence because it cannot apply decisions. Each entry retains its review
    status so the estimator can weigh it appropriately.
    """

    archetypes: list[dict[str, Any]] = []
    if isinstance(archetype_review, pd.DataFrame) and not archetype_review.empty:
        for row in archetype_review.to_dict(orient="records"):
            confidence = _number(row.get("ai_review_confidence")) or 0.0
            name = (
                _text(row.get("review_proposed_name"))
                or _text(row.get("ai_proposed_name"))
                or _text(row.get("provisional_label"))
            )
            if not name or confidence < minimum_ai_confidence:
                continue
            human_approved = (
                _as_bool(row.get("review_complete")) is True
                and _as_bool(row.get("review_approved")) is True
            )
            archetypes.append(
                {
                    "archetype_id": _text(row.get("archetype_id")),
                    "name": name,
                    "template_type": _text(row.get("template_type")).lower(),
                    "base_system": _text(row.get("ai_base_system")),
                    "project_type_mode": _text(row.get("project_type_mode")),
                    "substrate_mode": _text(row.get("substrate_mode")),
                    "core_decisions": [
                        str(value)
                        for value in _json_list(row.get("core_decisions_json"))
                    ],
                    "typical_decisions": [
                        str(value)
                        for value in _json_list(row.get("typical_decisions_json"))
                    ],
                    "occasional_decisions": [
                        str(value)
                        for value in _json_list(row.get("occasional_decisions_json"))
                    ],
                    "decision_rates": _json_dict(
                        row.get("full_decision_rates_json")
                    ),
                    "conditional_modifiers": [
                        str(value)
                        for value in _json_list(
                            row.get("ai_conditional_modifiers_json")
                        )
                    ],
                    "required_signals": [
                        str(value)
                        for value in _json_list(row.get("ai_required_signals_json"))
                    ],
                    "likely_exclusions": [
                        str(value)
                        for value in _json_list(row.get("ai_likely_exclusions_json"))
                    ],
                    "ambiguities": [
                        str(value)
                        for value in _json_list(row.get("ai_ambiguities_json"))
                    ],
                    "review_confidence": confidence,
                    "reviewer_notes": _text(row.get("reviewer_notes")),
                    "source_observation_count": int(
                        _number(row.get("observation_count")) or 0
                    ),
                    "evidence_status": (
                        "human_approved"
                        if human_approved
                        else "ai_labeled_unreviewed"
                    ),
                }
            )
    archetypes.sort(
        key=lambda row: (
            row["template_type"],
            -int(row["source_observation_count"]),
            row["name"],
        )
    )

    rules: list[dict[str, Any]] = []
    if isinstance(rule_validation, pd.DataFrame) and not rule_validation.empty:
        for row in rule_validation.to_dict(orient="records"):
            if _text(row.get("validation_status")) != "stable_candidate":
                continue
            rules.append(
                {
                    "rule_key": _text(row.get("rule_key")),
                    "template_type": _text(row.get("segment_value")).lower(),
                    "antecedent": _text(row.get("antecedent")),
                    "consequent": _text(row.get("consequent")),
                    "support_count": int(
                        _number(row.get("full_support_count")) or 0
                    ),
                    "confidence": _number(row.get("full_confidence")),
                    "lift": _number(row.get("full_lift")),
                    "sufficient_split_count": int(
                        _number(row.get("sufficient_split_count")) or 0
                    ),
                    "stable_split_count": int(
                        _number(row.get("stable_split_count")) or 0
                    ),
                    "reviewer_notes": _text(row.get("reviewer_notes")),
                    "evidence_status": "holdout_validated_unreviewed",
                }
            )
    rules.sort(
        key=lambda row: (
            row["template_type"],
            -int(row["support_count"]),
            row["antecedent"],
            row["consequent"],
        )
    )

    metadata = {
        **(source_metadata or {}),
        "catalog_kind": "advisory_evidence",
        "minimum_ai_confidence": minimum_ai_confidence,
    }
    version_payload = {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "runtime_mode": CATALOG_RUNTIME_MODE,
        "archetypes": archetypes,
        "rules": rules,
        "source_metadata": metadata,
    }
    digest = hashlib.sha256(
        json.dumps(
            version_payload,
            default=str,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return {
        **version_payload,
        "catalog_version": f"scope-patterns-{digest[:16]}",
        "content_sha256": digest,
        "created_at": datetime.now(UTC).isoformat(),
        "approved_archetype_count": sum(
            row["evidence_status"] == "human_approved" for row in archetypes
        ),
        "approved_rule_count": 0,
        "advisory_archetype_count": len(archetypes),
        "advisory_rule_count": len(rules),
        "applied_automatically": False,
    }


def write_approved_scope_catalog(
    catalog: dict[str, Any],
    path: str | Path,
) -> Path:
    validate_scope_catalog(catalog)
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(catalog, indent=2, default=str, sort_keys=True),
        encoding="utf-8",
    )
    return output


def load_approved_scope_catalog(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    catalog = json.loads(source.read_text(encoding="utf-8"))
    validate_scope_catalog(catalog)
    return catalog


def validate_scope_catalog(catalog: dict[str, Any]) -> None:
    if not isinstance(catalog, dict):
        raise ValueError("Scope archetype catalog must be a JSON object.")
    if catalog.get("schema_version") != CATALOG_SCHEMA_VERSION:
        raise ValueError("Unsupported scope archetype catalog schema.")
    if catalog.get("runtime_mode") != CATALOG_RUNTIME_MODE:
        raise ValueError("Scope archetype catalog must remain shadow_only.")
    if catalog.get("applied_automatically") is not False:
        raise ValueError("Scope archetype catalog cannot apply decisions automatically.")
    for field in ("archetypes", "rules"):
        if not isinstance(catalog.get(field), list):
            raise ValueError(f"Scope archetype catalog {field} must be a list.")
    expected_payload = {
        key: catalog.get(key)
        for key in (
            "schema_version",
            "runtime_mode",
            "archetypes",
            "rules",
            "source_metadata",
        )
    }
    expected_digest = hashlib.sha256(
        json.dumps(
            expected_payload,
            default=str,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    if _text(catalog.get("content_sha256")) != expected_digest:
        raise ValueError("Scope archetype catalog content hash does not match.")


def _workbook_rows(value: Any) -> list[int]:
    text = _text(value)
    if not text:
        return []
    range_match = re.fullmatch(r"\s*(\d+)\s*-\s*(\d+)\s*", text)
    if range_match:
        start, end = (int(range_match.group(1)), int(range_match.group(2)))
        if start <= end and end - start <= 10:
            return list(range(start, end + 1))
    return [int(value) for value in re.findall(r"\d+", text)]


def workbench_decision_keys(workbench: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for section, rows in workbench.items():
        if not isinstance(rows, list) or not section.endswith("_decisions"):
            continue
        for row in rows:
            if not isinstance(row, dict) or row.get("include") is not True:
                continue
            explicit_key = _text(row.get("decision_key"))
            if explicit_key:
                keys.add(explicit_key)
            bucket = _text(
                row.get("template_bucket")
                or row.get("package")
                or row.get("package_or_labor_task")
            ).lower()
            bucket = re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", bucket)).strip("_")
            if not bucket:
                continue
            for workbook_row in _workbook_rows(row.get("workbook_row")):
                keys.add(f"{bucket}@row_{workbook_row}")
    return keys


def _decision_bucket(decision_key: Any) -> str:
    return _text(decision_key).split("@", 1)[0].lower()


def _scope_signal_buckets(scope: dict[str, Any]) -> set[str]:
    text = json.dumps(scope or {}, default=str).lower()
    signals = {
        "foam": ("foam", "spf", "polyurethane"),
        "coating": ("coating", "silicone", "acrylic", "urethane"),
        "thermal_barrier_coating": (
            "thermal barrier",
            "ignition barrier",
            "dc315",
            "dc 315",
            "intumescent",
        ),
        "primer": ("primer", "prime "),
        "fabric": ("fabric", "reinforcement"),
        "board_stock": ("iso board", "polyiso", "densdeck", "cover board"),
        "fasteners": ("fastener", "screw"),
        "plates": ("plate",),
        "edge_metal": ("edge metal", "foam stop", "foam-stop"),
        "gutter": ("gutter",),
        "downspouts": ("downspout",),
        "caulk_sealant": ("caulk", "sealant"),
        "granules": ("granule",),
        "dumpster": ("dumpster", "tear-off", "tear off", "removal"),
    }
    return {
        bucket
        for bucket, terms in signals.items()
        if any(term in text for term in terms)
    }


def build_scope_pattern_evidence(
    scope: dict[str, Any],
    catalog: dict[str, Any] | None,
    *,
    comparable_manifests: Iterable[dict[str, Any]] = (),
    archetype_limit: int = 1,
    rule_limit: int = 8,
) -> dict[str, Any]:
    """Match advisory patterns from scope signals and complete comparables."""

    if not catalog:
        return {
            "status": "catalog_unavailable",
            "runtime_mode": CATALOG_RUNTIME_MODE,
            "applied_automatically": False,
            "matched_archetypes": [],
            "validated_relationships": [],
        }
    validate_scope_catalog(catalog)
    template_type = _text(
        scope.get("template_type")
        or scope.get("division")
        or scope.get("project_type")
    ).lower()
    template_type = "insulation" if "insulation" in template_type else "roofing"
    scope_buckets = _scope_signal_buckets(scope)
    comparable_sets = [
        {
            str(value)
            for value in manifest.get("active_decision_keys") or []
            if _text(value)
        }
        for manifest in comparable_manifests
        if isinstance(manifest, dict)
    ]
    comparable_sets = [values for values in comparable_sets if values]

    matched: list[dict[str, Any]] = []
    for archetype in catalog.get("archetypes") or []:
        if _text(archetype.get("template_type")).lower() != template_type:
            continue
        core = {str(value) for value in archetype.get("core_decisions") or []}
        typical = {
            str(value) for value in archetype.get("typical_decisions") or []
        }
        pattern = core | typical
        pattern_buckets = {_decision_bucket(value) for value in pattern}
        semantic_overlap = sorted(scope_buckets & pattern_buckets)
        semantic_score = (
            len(semantic_overlap) / max(len(scope_buckets), 1)
            if scope_buckets
            else 0.0
        )
        comparable_score = 0.0
        matched_comparable_keys: list[str] = []
        for decision_keys in comparable_sets:
            overlap = pattern & decision_keys
            if not overlap:
                continue
            core_recall = len(core & decision_keys) / max(len(core), 1)
            pattern_recall = len(overlap) / max(len(pattern), 1)
            jaccard = len(overlap) / max(len(pattern | decision_keys), 1)
            score = 0.6 * core_recall + 0.25 * pattern_recall + 0.15 * jaccard
            if score > comparable_score:
                comparable_score = score
                matched_comparable_keys = sorted(overlap)
        score = max(comparable_score, semantic_score * 0.8)
        if score <= 0:
            continue
        rates = archetype.get("decision_rates")
        matched.append(
            {
                "archetype_id": archetype.get("archetype_id"),
                "name": archetype.get("name"),
                "match_score": round(score, 6),
                "match_basis": {
                    "scope_signal_buckets": semantic_overlap,
                    "comparable_decisions": matched_comparable_keys,
                },
                "base_system": archetype.get("base_system"),
                "core_decisions": sorted(core),
                "typical_decisions": sorted(typical),
                "decision_rates": rates if isinstance(rates, dict) else {},
                "conditional_modifiers": archetype.get("conditional_modifiers")
                or [],
                "required_signals": archetype.get("required_signals") or [],
                "likely_exclusions": archetype.get("likely_exclusions") or [],
                "ambiguities": archetype.get("ambiguities") or [],
                "source_observation_count": archetype.get(
                    "source_observation_count"
                ),
                "review_confidence": archetype.get("review_confidence"),
                "evidence_status": archetype.get("evidence_status"),
            }
        )
    matched.sort(
        key=lambda row: (
            -float(row["match_score"]),
            -int(row.get("source_observation_count") or 0),
            _text(row.get("name")),
        )
    )
    selected = matched[: max(0, archetype_limit)]

    anchor_keys = set().union(*comparable_sets) if comparable_sets else set()
    anchor_buckets = set(scope_buckets)
    for archetype in selected:
        anchor_keys.update(archetype.get("core_decisions") or [])
        anchor_keys.update(archetype.get("typical_decisions") or [])
        anchor_buckets.update(
            _decision_bucket(value)
            for value in (
                (archetype.get("core_decisions") or [])
                + (archetype.get("typical_decisions") or [])
            )
        )
    relationships: list[dict[str, Any]] = []
    for rule in catalog.get("rules") or []:
        if _text(rule.get("template_type")).lower() != template_type:
            continue
        antecedent = _text(rule.get("antecedent"))
        if (
            antecedent not in anchor_keys
            and _decision_bucket(antecedent) not in anchor_buckets
        ):
            continue
        relationships.append(
            {
                "rule_key": rule.get("rule_key"),
                "observed_decision": antecedent,
                "possible_companion": rule.get("consequent"),
                "confidence": rule.get("confidence"),
                "lift": rule.get("lift"),
                "support_count": rule.get("support_count"),
                "stable_split_count": rule.get("stable_split_count"),
                "sufficient_split_count": rule.get("sufficient_split_count"),
                "evidence_status": rule.get("evidence_status"),
                "applied_automatically": False,
            }
        )
    relationships.sort(
        key=lambda row: (
            -float(row.get("lift") or 0),
            -int(row.get("support_count") or 0),
            _text(row.get("possible_companion")),
        )
    )
    return {
        "status": "advisory_evidence_available",
        "runtime_mode": CATALOG_RUNTIME_MODE,
        "catalog_version": catalog.get("catalog_version"),
        "matched_archetypes": selected,
        "validated_relationships": relationships[: max(0, rule_limit)],
        "applied_automatically": False,
    }


def build_scope_archetype_shadow_evidence(
    workbench: dict[str, Any],
    catalog: dict[str, Any] | None,
    *,
    archetype_limit: int = 3,
    rule_limit: int = 20,
) -> dict[str, Any]:
    if not catalog:
        return {
            "status": "catalog_unavailable",
            "runtime_mode": CATALOG_RUNTIME_MODE,
            "applied_automatically": False,
            "matched_archetypes": [],
            "companion_hints": [],
        }
    validate_scope_catalog(catalog)
    scope = workbench.get("scope") or {}
    template_type = _text(scope.get("template_type")).lower()
    if not template_type:
        division = _text(scope.get("division")).lower()
        template_type = "insulation" if "insulation" in division else "roofing"
    current = workbench_decision_keys(workbench)

    matched: list[dict[str, Any]] = []
    for archetype in catalog.get("archetypes") or []:
        if _text(archetype.get("template_type")).lower() != template_type:
            continue
        core = {str(value) for value in archetype.get("core_decisions") or []}
        typical = {
            str(value) for value in archetype.get("typical_decisions") or []
        }
        core_overlap = sorted(current & core)
        typical_overlap = sorted(current & typical)
        if not core_overlap and not typical_overlap:
            continue
        core_recall = len(core_overlap) / max(len(core), 1)
        typical_recall = len(typical_overlap) / max(len(typical), 1)
        score = 0.8 * core_recall + 0.2 * typical_recall
        matched.append(
            {
                "archetype_id": archetype.get("archetype_id"),
                "name": archetype.get("name"),
                "match_score": round(score, 6),
                "matched_core_decisions": core_overlap,
                "missing_core_decisions": sorted(core - current),
                "matched_typical_decisions": typical_overlap,
                "conditional_modifiers": archetype.get("conditional_modifiers")
                or [],
                "required_signals": archetype.get("required_signals") or [],
                "likely_exclusions": archetype.get("likely_exclusions") or [],
                "source_observation_count": archetype.get(
                    "source_observation_count"
                ),
                "review_confidence": archetype.get("review_confidence"),
            }
        )
    matched.sort(
        key=lambda row: (
            -float(row["match_score"]),
            -int(row.get("source_observation_count") or 0),
            _text(row.get("name")),
        )
    )

    hints: list[dict[str, Any]] = []
    for rule in catalog.get("rules") or []:
        if _text(rule.get("template_type")).lower() != template_type:
            continue
        antecedent = _text(rule.get("antecedent"))
        consequent = _text(rule.get("consequent"))
        if antecedent not in current or consequent in current:
            continue
        hints.append(
            {
                "rule_key": rule.get("rule_key"),
                "observed_decision": antecedent,
                "possible_companion": consequent,
                "confidence": rule.get("confidence"),
                "lift": rule.get("lift"),
                "support_count": rule.get("support_count"),
                "reviewer_notes": rule.get("reviewer_notes"),
                "applied_automatically": False,
            }
        )
    hints.sort(
        key=lambda row: (
            -float(row.get("confidence") or 0),
            -int(row.get("support_count") or 0),
            _text(row.get("possible_companion")),
        )
    )
    return {
        "status": "shadow_evidence_available",
        "runtime_mode": CATALOG_RUNTIME_MODE,
        "catalog_version": catalog.get("catalog_version"),
        "current_decision_keys": sorted(current),
        "matched_archetypes": matched[: max(0, archetype_limit)],
        "companion_hints": hints[: max(0, rule_limit)],
        "applied_automatically": False,
    }
