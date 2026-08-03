from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping

from .chat_assistant import estimator_context_summary
from .data_loader import ESTIMATOR_LOAD_PROFILE_CHAT, load_estimator_data
from .planning_guidance import build_estimator_planning_guidance
from .schemas import EstimatorData
from .semantic_context import build_semantic_observations
from .scope_integrity import evaluate_roofing_scope_integrity


AGENT_CONTEXT_TARGET_BYTES = 68_000
AGENT_CONTEXT_PUBLIC_MAX_BYTES = 75_000
_BOUNDED_LIST_FIELDS = (
    "matched_comparables",
    "decision_evidence",
    "historical_material_usage",
    "historical_labor_performance",
    "historical_assemblies",
    "validated_relationships",
    "approved_memories",
    "pricing_candidates",
    "product_guidance",
    "foam_yield_history",
    "purchasing_guidance",
    "labor_plan_guidance",
    "logistics_guidance",
    "decision_concepts",
    "calculation_requirements",
    "source_links",
)

_ESTIMATOR_DATA_CACHE: dict[tuple[str, str], tuple[float, EstimatorData]] = {}
_ESTIMATOR_DATA_CACHE_LOCK = threading.Lock()


def build_copilot_estimator_context(
    *,
    scope: dict[str, Any],
    raw_notes: str = "",
    template_type_hint: str = "",
    site_address: str = "",
    reference_job_ids: list[str] | None = None,
    exclude_job_ids: list[str] | None = None,
    exclude_source_files: list[str] | None = None,
    data: EstimatorData | None = None,
    database_url: str | None = None,
    base_dir: Path | str | None = None,
    include_source_metadata: bool = False,
    focus: str = "full",
) -> dict[str, Any]:
    """Build the bounded, model-neutral context supplied to a Copilot agent.

    This function performs retrieval and context assembly only. It deliberately
    does not call an LLM. Copilot remains responsible for interpreting the
    current job and proposing decisions.
    """

    normalized_scope = dict(scope or {})
    if template_type_hint and not normalized_scope.get("template_type"):
        normalized_scope["template_type"] = template_type_hint
    if site_address and not normalized_scope.get("site_address"):
        normalized_scope["site_address"] = site_address
    if raw_notes and not normalized_scope.get("raw_input_notes"):
        normalized_scope["raw_input_notes"] = raw_notes
    requested_references = _unique_strings(reference_job_ids or [])
    if requested_references:
        normalized_scope["reference_job_ids"] = requested_references
    excluded_jobs = _unique_strings(
        [
            *(exclude_job_ids or []),
            *(normalized_scope.get("exclude_job_ids") or []),
            normalized_scope.get("target_job_id"),
        ]
    )
    excluded_sources = _unique_strings(
        [
            *(exclude_source_files or []),
            *(normalized_scope.get("exclude_source_files") or []),
            *(normalized_scope.get("target_source_files") or []),
            normalized_scope.get("target_source_file"),
        ]
    )
    if excluded_jobs:
        normalized_scope["exclude_job_ids"] = excluded_jobs
    if excluded_sources:
        normalized_scope["exclude_source_files"] = excluded_sources

    estimator_data = data or _cached_estimator_data(
        base_dir=base_dir,
        database_url=database_url,
    )
    full_context = estimator_context_summary(estimator_data, scope=normalized_scope)
    historical_packet = (
        full_context.get("historical_evidence_packet")
        if isinstance(full_context.get("historical_evidence_packet"), dict)
        else {}
    )
    decision_concepts = _decision_concepts(full_context)
    matched_comparables = _semantic_comparables(
        historical_packet.get("matched_comparables") or []
    )
    decision_evidence = _semantic_decision_evidence(
        historical_packet.get("decision_evidence") or [],
        template_type=full_context.get("template_type") or "",
    )
    source_links = _source_links(estimator_data, historical_packet)
    semantic_observations = build_semantic_observations(
        decision_evidence=historical_packet.get("decision_evidence") or [],
        matched_comparables=matched_comparables,
        source_links=source_links,
        template_type=full_context.get("template_type") or "",
    )
    scope_integrity = evaluate_roofing_scope_integrity(normalized_scope)
    planning_guidance = build_estimator_planning_guidance(
        scope=normalized_scope,
        data=estimator_data,
        historical_material_usage=semantic_observations.get(
            "historical_material_usage"
        )
        or [],
        historical_labor_performance=semantic_observations.get(
            "historical_labor_performance"
        )
        or [],
        route_mileage=full_context.get("route_mileage") or {},
    )
    response: dict[str, Any] = {
        "schema_version": "spraytec.copilot_estimator_context.v1",
        "focus": str(focus or "full").strip().lower(),
        "scope": _bounded_response_scope(normalized_scope),
        "template_type": full_context.get("template_type") or "",
        "route_mileage": full_context.get("route_mileage") or {},
        "scope_integrity": scope_integrity,
        "retrieval_exclusions": {
            "job_ids": excluded_jobs,
            "source_files": excluded_sources,
        },
        "matched_comparables": matched_comparables,
        "decision_evidence": decision_evidence,
        **semantic_observations,
        "matched_scope_pattern": historical_packet.get("matched_scope_pattern") or {},
        "validated_relationships": historical_packet.get("validated_relationships")
        or [],
        "approved_memories": full_context.get("estimator_memory_guidance") or [],
        "pricing_candidates": _public_pricing_candidates(
            full_context,
            scope=normalized_scope,
            planning_guidance=planning_guidance,
        ),
        "product_guidance": full_context.get("product_guidance_digest") or [],
        "foam_yield_history": _without_verbose_examples(
            full_context.get("foam_yield_history_digest") or []
        ),
        **planning_guidance,
        "commercial_guidance": _commercial_guidance(
            estimator_data,
            template_type=full_context.get("template_type") or template_type_hint,
        ),
        "decision_concepts": decision_concepts,
        "calculation_requirements": _calculation_requirements(decision_concepts),
        "source_links": source_links,
        "warnings": _unique_strings(estimator_data.warnings),
    }
    response["pricing_coverage"] = _pricing_coverage(
        response["pricing_candidates"],
        required_buckets=_pricing_target_buckets(
            normalized_scope,
            planning_guidance=planning_guidance,
        ),
    )
    response["labor_cost_summary"] = _labor_cost_summary(
        response.get("labor_plan_guidance") or []
    )
    response = _apply_context_focus(response, focus=focus)
    response = _bound_agent_context(response)
    response["retrieval_summary"] = _retrieval_summary(response)
    if include_source_metadata:
        response["source_metadata"] = {
            "sources": _unique_strings(estimator_data.source_files_used),
            "row_counts": {
                "jobs": len(estimator_data.jobs),
                "estimates": len(estimator_data.estimates),
                "template_examples": len(estimator_data.template_examples),
                "pricing_catalog": len(estimator_data.pricing_catalog),
                "product_catalog": len(estimator_data.product_catalog),
                "approved_memories": len(estimator_data.estimator_memory),
            },
        }
    return json_safe(response)


def _bound_agent_context(response: dict[str, Any]) -> dict[str, Any]:
    """Project the shared estimator context into an action-safe evidence packet."""

    original_counts = _list_counts(response)
    response = dict(response)
    response["decision_evidence"] = _compact_decision_evidence(
        response.get("decision_evidence") or [],
        semantic_concept_ids={
            str(row.get("concept_id") or "")
            for field in (
                "historical_material_usage",
                "historical_labor_performance",
            )
            for row in response.get(field) or []
            if isinstance(row, dict)
        },
    )
    for field in (
        "historical_material_usage",
        "historical_labor_performance",
        "historical_assemblies",
    ):
        response[field] = _compact_observation_sources(response.get(field) or [])
    response["approved_memories"] = _balanced_limit(
        response.get("approved_memories") or [],
        group_fields=("template_bucket", "decision_id", "priority"),
        per_group=2,
        total=8,
    )
    response["pricing_candidates"] = _balanced_limit(
        response.get("pricing_candidates") or [],
        group_fields=("template_bucket", "decision_bucket", "category"),
        per_group=3,
        total=24,
    )
    response["product_guidance"] = _balanced_limit(
        response.get("product_guidance") or [],
        group_fields=("category", "guidance_type", "product_id"),
        per_group=2,
        total=12,
    )

    response["response_budget"] = {
        "profile": "agent_compact_v1",
        "focus": response.get("focus") or "full",
        "target_bytes": AGENT_CONTEXT_TARGET_BYTES,
        "public_max_bytes": AGENT_CONTEXT_PUBLIC_MAX_BYTES,
        "original_counts": original_counts,
    }
    _trim_to_serialized_budget(response)
    _refresh_budget_metadata(response)
    return response


def _cached_estimator_data(
    *,
    base_dir: Path | str | None,
    database_url: str | None,
) -> EstimatorData:
    """Reuse the read-only chat evidence set for a short presentation-safe TTL."""

    try:
        ttl_seconds = int(os.getenv("ESTIMATOR_CONTEXT_DATA_CACHE_TTL_SECONDS") or "900")
    except ValueError:
        ttl_seconds = 900
    ttl_seconds = min(max(ttl_seconds, 0), 3600)
    if ttl_seconds == 0:
        return load_estimator_data(
            base_dir=base_dir,
            database_url=database_url,
            prefer_database=bool(database_url),
            load_profile=ESTIMATOR_LOAD_PROFILE_CHAT,
        )
    key = (str(base_dir or ""), str(database_url or ""))
    with _ESTIMATOR_DATA_CACHE_LOCK:
        now = time.monotonic()
        cached = _ESTIMATOR_DATA_CACHE.get(key)
        if cached and now - cached[0] < ttl_seconds:
            return cached[1]
        loaded = load_estimator_data(
            base_dir=base_dir,
            database_url=database_url,
            prefer_database=bool(database_url),
            load_profile=ESTIMATOR_LOAD_PROFILE_CHAT,
        )
        _ESTIMATOR_DATA_CACHE.clear()
        _ESTIMATOR_DATA_CACHE[key] = (now, loaded)
        return loaded


def _bounded_response_scope(scope: dict[str, Any]) -> dict[str, Any]:
    bounded = dict(scope)
    raw_notes = str(bounded.get("raw_input_notes") or "")
    if len(raw_notes) > 2_000:
        bounded["raw_input_notes"] = raw_notes[:2_000].rstrip() + "…"
        bounded["raw_input_notes_truncated"] = True
    return bounded


def _compact_decision_evidence(
    values: list[Any],
    *,
    semantic_concept_ids: set[str],
) -> list[Any]:
    output: list[Any] = []
    for value in values:
        if not isinstance(value, dict):
            output.append(value)
            continue
        row = dict(value)
        if str(row.get("concept_id") or "") in semantic_concept_ids:
            row.pop("sample_inputs", None)
            row.pop("sample_outputs", None)
        output.append(row)
    return output


def _compact_observation_sources(values: list[Any]) -> list[Any]:
    output: list[Any] = []
    for value in values:
        if not isinstance(value, dict):
            output.append(value)
            continue
        row = dict(value)
        row["sources"] = [
            {
                key: source.get(key)
                for key in (
                    "job_id",
                    "example_id",
                    "file_name",
                    "similarity_score",
                    "reference_area_sqft",
                )
                if source.get(key) not in (None, "")
            }
            for source in row.get("sources") or []
            if isinstance(source, dict)
        ][:2]
        output.append(row)
    return output


def _balanced_limit(
    values: list[Any],
    *,
    group_fields: tuple[str, ...],
    per_group: int,
    total: int,
) -> list[Any]:
    output: list[Any] = []
    group_counts: dict[str, int] = {}
    for value in values:
        if not isinstance(value, dict):
            group = "__ungrouped__"
        else:
            group = next(
                (
                    str(value.get(field) or "").strip().lower()
                    for field in group_fields
                    if str(value.get(field) or "").strip()
                ),
                "__ungrouped__",
            )
        if group_counts.get(group, 0) >= per_group:
            continue
        group_counts[group] = group_counts.get(group, 0) + 1
        output.append(value)
        if len(output) >= total:
            break
    return output


def _trim_to_serialized_budget(response: dict[str, Any]) -> None:
    trim_rules = (
        ("decision_evidence", 6),
        ("approved_memories", 3),
        ("pricing_candidates", 12),
        ("product_guidance", 4),
        ("foam_yield_history", 3),
        ("purchasing_guidance", 6),
        ("labor_plan_guidance", 8),
        ("validated_relationships", 3),
        ("historical_assemblies", 1),
        ("historical_labor_performance", 4),
        ("historical_material_usage", 4),
    )
    while _serialized_bytes(response) > AGENT_CONTEXT_TARGET_BYTES:
        trimmed = False
        for field, minimum in trim_rules:
            values = response.get(field)
            if isinstance(values, list) and len(values) > minimum:
                values.pop()
                trimmed = True
                break
        if not trimmed:
            break


def _refresh_budget_metadata(response: dict[str, Any]) -> None:
    budget = response["response_budget"]
    returned_counts = _list_counts(response)
    budget["returned_counts"] = returned_counts
    budget["truncated_fields"] = [
        field
        for field, original in budget["original_counts"].items()
        if returned_counts.get(field, 0) < original
    ]
    budget["truncated"] = bool(budget["truncated_fields"]) or bool(
        response.get("scope", {}).get("raw_input_notes_truncated")
    )
    budget["estimated_serialized_bytes"] = _serialized_bytes(response)
    budget["within_public_limit"] = (
        _serialized_bytes(response) <= AGENT_CONTEXT_PUBLIC_MAX_BYTES
    )


def _list_counts(response: dict[str, Any]) -> dict[str, int]:
    return {
        field: len(response.get(field) or [])
        for field in _BOUNDED_LIST_FIELDS
    }


def _serialized_bytes(value: Any) -> int:
    return len(
        json.dumps(
            json_safe(value),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _retrieval_summary(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "matched_comparable_count": len(context.get("matched_comparables") or []),
        "decision_evidence_count": len(context.get("decision_evidence") or []),
        "historical_material_usage_count": len(
            context.get("historical_material_usage") or []
        ),
        "historical_labor_performance_count": len(
            context.get("historical_labor_performance") or []
        ),
        "historical_assembly_count": len(
            context.get("historical_assemblies") or []
        ),
        "matched_scope_pattern": bool(context.get("matched_scope_pattern")),
        "validated_relationship_count": len(
            context.get("validated_relationships") or []
        ),
        "approved_memory_count": len(context.get("approved_memories") or []),
        "pricing_bucket_count": len(context.get("pricing_candidates") or []),
        "product_guidance_count": len(context.get("product_guidance") or []),
        "decision_concept_count": len(context.get("decision_concepts") or []),
        "calculation_requirement_count": len(
            context.get("calculation_requirements") or []
        ),
        "purchasing_guidance_count": len(
            context.get("purchasing_guidance") or []
        ),
        "labor_plan_guidance_count": len(
            context.get("labor_plan_guidance") or []
        ),
        "logistics_guidance_count": len(context.get("logistics_guidance") or []),
        "source_link_count": len(context.get("source_links") or []),
    }


def _decision_concepts(full_context: dict[str, Any]) -> list[dict[str, Any]]:
    template_type = str(full_context.get("template_type") or "").strip().lower()
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in full_context.get("decision_menu") or []:
        if not isinstance(raw, dict):
            continue
        bucket = str(raw.get("template_bucket") or "").strip().lower()
        label = str(raw.get("label") or bucket.replace("_", " ")).strip()
        if not bucket:
            continue
        concept_id = f"{template_type}.{bucket}" if template_type else bucket
        if concept_id in seen:
            continue
        seen.add(concept_id)
        output.append(
            {
                "concept_id": concept_id,
                "category": bucket,
                "label": label,
                "decision_type": _decision_type(bucket),
                "editable_inputs": [
                    str(value)
                    for value in raw.get("editable_fields") or []
                    if str(value).strip() and str(value).strip() != "include"
                ],
                "required_calculation_inputs": [
                    str(value)
                    for value in raw.get("formula_requirements") or []
                    if str(value).strip()
                ],
            }
        )
    return output


def _semantic_comparables(values: list[Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for raw in values:
        if not isinstance(raw, dict):
            continue
        comparable = {
            key: value
            for key, value in raw.items()
            if key != "active_decision_keys"
        }
        categories = _unique_strings(
            [
                str(value).split("@row_", 1)[0]
                for value in raw.get("active_decision_keys") or []
            ]
        )
        if categories:
            comparable["historical_decision_categories"] = categories
        output.append(comparable)
    return output


def _semantic_decision_evidence(
    values: list[Any],
    *,
    template_type: str,
) -> list[dict[str, Any]]:
    normalized_template_type = str(template_type or "").strip().lower()
    output: list[dict[str, Any]] = []
    for raw in values:
        if not isinstance(raw, dict):
            continue
        category = str(raw.get("template_bucket") or "").strip().lower()
        concept_id = (
            f"{normalized_template_type}.{category}"
            if normalized_template_type and category
            else category
        )
        evidence = {
            key: value
            for key, value in raw.items()
            if key
            not in {
                "decision_id",
                "examples",
                "section",
                "template_bucket",
                "workbook_row",
            }
        }
        evidence["concept_id"] = concept_id
        evidence["category"] = category
        output.append(evidence)
    return output


def _without_verbose_examples(values: list[Any]) -> list[Any]:
    """Drop nested examples already represented by semantic observations."""

    return [
        {key: value for key, value in row.items() if key != "examples"}
        if isinstance(row, dict)
        else row
        for row in values
    ]


def _public_pricing_candidates(
    full_context: dict[str, Any],
    *,
    scope: dict[str, Any] | None = None,
    planning_guidance: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Expose scope-relevant prices without letting generic catalogs crowd them out."""

    scope = scope or {}
    planning_guidance = planning_guidance or {}
    excluded_files = {
        _normalized_source_file(value)
        for value in scope.get("exclude_source_files") or []
        if _normalized_source_file(value)
    }
    excluded_jobs = {
        str(value or "").strip().casefold()
        for value in scope.get("exclude_job_ids") or []
        if str(value or "").strip()
    }
    current = []
    for raw in full_context.get("pricing_candidates_by_bucket") or []:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        row["decision_bucket"] = _pricing_bucket(row)
        row["price_authority"] = (
            "current_template_lookup"
            if row.get("source") == "template_lookup_materials"
            else "current_pricing_catalog"
            if _number(row.get("unit_price")) > 0
            else "unpriced_template_option"
        )
        current.append(row)

    historical: list[dict[str, Any]] = []
    seen_historical: set[tuple[str, str, float]] = set()
    for original_raw in full_context.get("_deterministic_latest_historical_unit_prices") or []:
        raw = dict(original_raw) if isinstance(original_raw, dict) else {}
        if not isinstance(raw, dict) or _number(raw.get("unit_price")) <= 0:
            continue
        source_excluded = (
            _normalized_source_file(raw.get("source_file")) in excluded_files
            or str(raw.get("source_job_id") or "").strip().casefold()
            in excluded_jobs
        )
        used_fallback_source = False
        if source_excluded:
            fallback_file = _normalized_source_file(raw.get("fallback_source_file"))
            fallback_job = str(
                raw.get("fallback_source_job_id") or ""
            ).strip().casefold()
            if (
                _number(raw.get("fallback_unit_price")) <= 0
                or fallback_file in excluded_files
                or fallback_job in excluded_jobs
            ):
                continue
            raw.update(
                {
                    "unit_price": raw.get("fallback_unit_price"),
                    "source_document_id": raw.get("fallback_source_document_id"),
                    "source_job_id": raw.get("fallback_source_job_id"),
                    "source_file": raw.get("fallback_source_file"),
                    "source_sharepoint_url": raw.get(
                        "fallback_source_sharepoint_url"
                    ),
                    "source_effective_at": raw.get(
                        "fallback_source_effective_at"
                    ),
                }
            )
            used_fallback_source = True
        bucket = _pricing_bucket(raw)
        if not bucket:
            continue
        name = str(raw.get("item_name") or "Historical item").strip()
        key = (bucket, name.casefold(), _number(raw.get("unit_price")))
        if key in seen_historical:
            continue
        seen_historical.add(key)
        historical.append(
            {
                "template_bucket": bucket,
                "decision_bucket": bucket,
                "candidate_name": (
                    "Gutter (historical formula row)"
                    if bucket == "gutter"
                    and "gutter" not in name.lower()
                    and "lin.ft" in name.lower()
                    else name
                ),
                "historical_item_name": name,
                "unit": str(raw.get("unit") or ""),
                "unit_basis": _pricing_unit_basis(raw, bucket=bucket),
                "unit_basis_inferred": not bool(str(raw.get("unit") or "").strip()),
                "unit_price": _number(raw.get("unit_price")),
                "source": "latest_historical_estimate",
                "price_authority": "fallback_if_current_unavailable",
                "fallback_reason": "No applicable current price matched this scope category.",
                "unit_price_historical": True,
                "review_required": True,
                "source_document_id": raw.get("source_document_id"),
                "source_job_id": raw.get("source_job_id"),
                "source_file": raw.get("source_file"),
                "source_sharepoint_url": raw.get("source_sharepoint_url"),
                "source_effective_at": raw.get("source_effective_at"),
                "historical_observation_count": raw.get(
                    "historical_observation_count"
                ),
                "fallback_from_excluded_latest": used_fallback_source,
            }
        )

    targets = _pricing_target_buckets(scope, planning_guidance=planning_guidance)
    if not targets:
        targets = list(
            dict.fromkeys(
                _pricing_bucket(row)
                for row in [*current, *historical]
                if _pricing_bucket(row)
            )
        )

    primary: list[dict[str, Any]] = []
    alternates: list[dict[str, Any]] = []
    for bucket in targets:
        bucket_rows = [
            row
            for row in [*current, *historical]
            if _pricing_bucket(row) == bucket
            and _pricing_candidate_is_semantically_usable(row, bucket=bucket)
        ]
        ranked = sorted(
            bucket_rows,
            key=lambda row: _pricing_candidate_rank(
                row,
                bucket=bucket,
                scope=scope,
            ),
            reverse=True,
        )
        priced = [row for row in ranked if _number(row.get("unit_price")) > 0]
        selected = priced[:2] or ranked[:1]
        if len(selected) < 2:
            unpriced_option = next(
                (row for row in ranked if row not in selected),
                None,
            )
            if unpriced_option:
                selected.append(unpriced_option)
        if bucket == "board_stock" and priced:
            scope_text = json.dumps(scope, ensure_ascii=False, default=str).lower()
            board_selected: list[dict[str, Any]] = []
            if "iso" in scope_text:
                target_thicknesses = _scope_board_thicknesses(scope_text)
                iso_candidate = next(
                    (
                        row
                        for row in priced
                        if "iso" in str(row.get("candidate_name") or "").lower()
                        and (
                            not target_thicknesses
                            or _candidate_thickness(row) in target_thicknesses
                        )
                    ),
                    next(
                        (
                            row
                            for row in priced
                            if "iso" in str(row.get("candidate_name") or "").lower()
                        ),
                        None,
                    ),
                )
                if iso_candidate:
                    board_selected.append(iso_candidate)
            if any(token in scope_text for token in ("decking", "plywood", "wood deck")):
                decking_candidate = next(
                    (
                        row
                        for row in priced
                        if any(
                            token in str(row.get("candidate_name") or "").lower()
                            for token in ("plywood", "wood", "deck")
                        )
                    ),
                    None,
                )
                if decking_candidate and decking_candidate not in board_selected:
                    board_selected.append(decking_candidate)
            selected = board_selected[:2] or (priced[:2] or ranked[:1])
        if not selected:
            continue
        prepared = []
        for row in selected:
            prepared.append(
                {
                    **row,
                    "decision_bucket": bucket,
                    "scope_required": True,
                    "scope_relevance": _pricing_relevance_reason(
                        row,
                        bucket=bucket,
                        scope=scope,
                    ),
                }
            )
        primary.append(prepared[0])
        alternates.extend(prepared[1:])
    return [*primary, *alternates]


def _pricing_bucket(row: dict[str, Any]) -> str:
    value = str(
        row.get("decision_bucket") or row.get("template_bucket") or ""
    ).strip().lower()
    return {
        "dumpsters": "dumpster",
        "delivery_fee": "delivery",
        "caulk_sealant": "caulk_detail",
    }.get(value, value)


def _pricing_unit_basis(row: dict[str, Any], *, bucket: str) -> str:
    explicit = str(row.get("unit") or "").strip()
    if explicit:
        return explicit
    return {
        "foam": "board_foot",
        "coating": "gallon",
        "primer": "gallon",
        "caulk_detail": "unit",
        "board_stock": "roofing_square",
        "fasteners": "thousand",
        "plates": "thousand",
        "edge_metal": "linear_foot",
        "gutter": "linear_foot",
        "downspouts": "linear_foot",
        "dumpster": "each",
        "generator": "day",
    }.get(bucket, "unit")


def _pricing_target_buckets(
    scope: dict[str, Any],
    *,
    planning_guidance: dict[str, Any],
) -> list[str]:
    buckets: list[str] = []
    scope_text = json.dumps(scope, ensure_ascii=False, default=str).lower()

    def add(value: str) -> None:
        if value and value not in buckets:
            buckets.append(value)

    purchasing_aliases = {"roofing_foam": "foam"}
    for row in planning_guidance.get("purchasing_guidance") or []:
        if not isinstance(row, dict):
            continue
        category = str(row.get("category") or "").strip().lower()
        if category == "primer" and "primer" not in scope_text:
            continue
        add(purchasing_aliases.get(category, category))

    if any(token in scope_text for token in ("iso", "decking", "plywood", "board")):
        add("board_stock")
    if "iso" in scope_text or "board" in scope_text:
        add("fasteners")
        add("plates")
    if "foam" in scope_text:
        add("foam")
    if any(token in scope_text for token in ("coated foam", "coating", "top coat")):
        add("coating")
    for row in scope.get("linear_scopes") or []:
        if not isinstance(row, dict):
            continue
        text = " ".join(str(row.get(key) or "") for key in ("item", "size", "action"))
        lowered = text.lower()
        if "downspout" in lowered:
            add("downspouts")
        if "gutter" in lowered:
            add("gutter")
        if any(token in lowered for token in ("edge metal", "foam stop", "foam-stop", "coping")):
            add("edge_metal")
    if any(token in scope_text for token in ("caulk", "sealant", "seal seams")):
        add("caulk_detail")
    for row in planning_guidance.get("logistics_guidance") or []:
        if not isinstance(row, dict) or row.get("include") is False:
            continue
        category = str(row.get("category") or "").strip().lower()
        if category in {"dumpster", "generator"}:
            add(category)
    return buckets


def _pricing_candidate_rank(
    row: dict[str, Any],
    *,
    bucket: str,
    scope: dict[str, Any],
) -> tuple[float, str, float]:
    name = str(row.get("candidate_name") or "").lower()
    source = str(row.get("source") or "")
    score = 0.0
    if _number(row.get("unit_price")) > 0:
        score += 100
    if source == "template_lookup_materials":
        score += 35
    elif source == "pricing_catalog":
        score += 20
    elif source == "latest_historical_estimate":
        score += 15
    scope_text = json.dumps(scope, ensure_ascii=False, default=str).lower()
    for token in set(re.findall(r"[a-z0-9]+", name)):
        if len(token) >= 3 and token in scope_text:
            score += 8
    if bucket == "board_stock":
        thickness = _number(row.get("thickness_inches"))
        if thickness > 0 and re.search(
            rf"\b{re.escape(str(thickness).rstrip('0').rstrip('.'))}\s*(?:in(?:ch(?:es)?)?|[\"”])",
            scope_text,
        ):
            score += 60
        if "iso" in scope_text and "iso" in name:
            score += 40
        if any(token in scope_text for token in ("decking", "plywood")) and "plywood" in name:
            score += 35
    if bucket == "foam" and str(scope.get("template_type") or "").lower() == "roofing":
        if any(token in name for token in ("roof", "gaco", "af roof")):
            score += 45
        elif source == "pricing_catalog":
            score -= 35
    if bucket == "coating" and str(scope.get("template_type") or "").lower() == "roofing":
        if any(token in name for token in ("silicone", "acrylic", "urethane", "polyurea", "gaco", "gaf", "roof")):
            score += 35
        elif source == "pricing_catalog":
            score -= 25
    bucket_signals = {
        "edge_metal": ("edge", "foam stop", "foam-stop", "coping"),
        "gutter": ("gutter",),
        "downspouts": ("downspout",),
        "primer": ("primer",),
        "caulk_detail": ("caulk", "sealant", "seam seal", "mastic"),
        "dumpster": ("dumpster", "yard"),
        "generator": ("generator",),
    }
    if any(signal in name for signal in bucket_signals.get(bucket, ())):
        score += 70
    effective = str(row.get("source_effective_at") or "")
    return (score, effective, _number(row.get("historical_observation_count")))


def _pricing_relevance_reason(
    row: dict[str, Any],
    *,
    bucket: str,
    scope: dict[str, Any],
) -> str:
    if row.get("fallback_from_excluded_latest"):
        return "Next-latest historical estimate price after excluding the target workbook."
    if row.get("source") == "template_lookup_materials":
        return "Current Materials-tab lookup matched to required scope category."
    if row.get("source") == "pricing_catalog":
        return "Current pricing-catalog candidate matched to required scope category."
    return "Newest applicable historical estimate price for a required scope category."


def _pricing_candidate_is_semantically_usable(
    row: dict[str, Any],
    *,
    bucket: str,
) -> bool:
    if row.get("source") != "latest_historical_estimate":
        return True
    name = str(
        row.get("historical_item_name") or row.get("candidate_name") or ""
    ).lower()
    signals = {
        "edge_metal": ("edge", "foam stop", "foam-stop", "coping"),
        "gutter": ("gutter", "lin.ft"),
        "downspouts": ("downspout",),
        "caulk_detail": ("caulk", "sealant", "seam seal", "mastic"),
        "dumpster": ("dumpster", "yard"),
        "generator": ("generator",),
    }.get(bucket)
    return not signals or any(signal in name for signal in signals)


def _scope_board_thicknesses(scope_text: str) -> set[float]:
    values: set[float] = set()
    patterns = (
        r"(\d+(?:\.\d+)?)\s*(?:in(?:ch(?:es)?)?|[\"”])\s*(?:resista\s+)?iso",
        r"iso(?:\s+board)?\s*(\d+(?:\.\d+)?)\s*(?:in(?:ch(?:es)?)?|[\"”])",
    )
    for pattern in patterns:
        values.update(float(value) for value in re.findall(pattern, scope_text, flags=re.I))
    return values


def _candidate_thickness(row: dict[str, Any]) -> float:
    explicit = _number(row.get("thickness_inches"))
    if explicit > 0:
        return explicit
    match = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:in(?:ch(?:es)?)?|[\"”])",
        str(row.get("candidate_name") or ""),
        flags=re.I,
    )
    return float(match.group(1)) if match else 0.0


def _normalized_source_file(value: Any) -> str:
    name = str(value or "").strip().replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"\s*\(\d+\)(?=\.[^.]+$)", "", name)
    return name.casefold()


def _pricing_coverage(
    candidates: list[dict[str, Any]],
    *,
    required_buckets: list[str],
) -> dict[str, Any]:
    priced = {
        _pricing_bucket(row)
        for row in candidates
        if isinstance(row, dict) and _number(row.get("unit_price")) > 0
    }
    missing = [bucket for bucket in required_buckets if bucket not in priced]
    return {
        "required_buckets": required_buckets,
        "priced_buckets": [bucket for bucket in required_buckets if bucket in priced],
        "missing_buckets": missing,
        "status": "complete" if not missing else "partial",
        "historical_fallback_is_usable": True,
    }


def _labor_cost_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    required = [row for row in rows if isinstance(row, dict)]
    current_rate_rows = [row for row in required if _number(row.get("current_people_daily_rate")) > 0]
    cost_rows = [row for row in required if _number(row.get("estimated_labor_cost_candidate")) > 0]
    return {
        "required_activity_count": len(required),
        "current_people_rate_activity_count": len(current_rate_rows),
        "costed_activity_count": len(cost_rows),
        "current_people_rates_available": bool(required) and len(current_rate_rows) == len(required),
        "estimated_production_labor_cost_candidate": round(
            sum(_number(row.get("estimated_labor_cost_candidate")) for row in cost_rows),
            2,
        ),
        "uncosted_categories": [
            str(row.get("category") or "")
            for row in required
            if _number(row.get("estimated_labor_cost_candidate")) <= 0
        ],
        "rate_authority": "current_people_tab",
    }


def _commercial_guidance(
    data: EstimatorData,
    *,
    template_type: str,
) -> dict[str, Any]:
    normalized_type = str(template_type or "roofing").strip().lower()
    defaults = {
        "roofing": {"overhead_pct": 35.0, "profit_pct": 15.0},
        "flooring": {"overhead_pct": 35.0, "profit_pct": 15.0},
        "insulation": {"overhead_pct": 30.0, "profit_pct": 10.0},
    }
    selected = dict(defaults.get(normalized_type, defaults["roofing"]))
    history = getattr(data, "commercial_markup_history", None)
    evidence: dict[str, Any] = {}
    if hasattr(history, "empty") and not history.empty:
        rows = history.copy()
        if "template_type" in rows.columns:
            typed = rows[
                rows["template_type"].astype(str).str.strip().str.lower()
                == normalized_type
            ]
            if not typed.empty:
                rows = typed
        for category, output_field in (
            ("overhead", "overhead_pct"),
            ("profit", "profit_pct"),
        ):
            category_rows = rows[
                rows.get("category", "").astype(str).str.strip().str.lower()
                == category
            ].copy()
            if category_rows.empty:
                continue
            category_rows["document_count"] = category_rows[
                "document_count"
            ].fillna(0)
            category_rows = category_rows.sort_values(
                ["document_count", "percentage"],
                ascending=[False, True],
            )
            top = category_rows.iloc[0]
            percentage = _number(top.get("percentage"))
            if percentage > 0:
                selected[output_field] = percentage
            evidence[category] = {
                "recommended_pct": selected[output_field],
                "supporting_document_count": int(
                    _number(top.get("document_count"))
                ),
                "total_priced_document_count": int(
                    sum(_number(value) for value in category_rows["document_count"])
                ),
                "common_percentages": [
                    {
                        "percentage": _number(row.get("percentage")),
                        "document_count": int(_number(row.get("document_count"))),
                    }
                    for row in category_rows.head(3).to_dict(orient="records")
                ],
            }
    return {
        **selected,
        "source": "historical_mode_with_standard_fallback",
        "template_type": normalized_type,
        "review_required": True,
        "blocks_preliminary_estimate": False,
        "blocks_workbook_generation": False,
        "evidence": evidence,
        "usage": (
            "Apply these percentages when the estimator has not supplied an "
            "override. Keep them visible and editable in the draft review."
        ),
    }


def _apply_context_focus(
    response: dict[str, Any],
    *,
    focus: str,
) -> dict[str, Any]:
    selected = str(focus or "full").strip().lower()
    response = dict(response)
    response["focus"] = selected
    if selected == "full":
        return response
    keep_by_focus = {
        "labor": {
            "matched_comparables",
            "historical_labor_performance",
            "labor_plan_guidance",
            "logistics_guidance",
            "source_links",
        },
        "pricing": {
            "pricing_candidates",
            "purchasing_guidance",
            "logistics_guidance",
            "source_links",
        },
        "commercial": {"matched_comparables", "source_links"},
        "materials": {
            "matched_comparables",
            "historical_material_usage",
            "historical_assemblies",
            "pricing_candidates",
            "product_guidance",
            "foam_yield_history",
            "purchasing_guidance",
            "source_links",
        },
        "evidence": {
            "matched_comparables",
            "decision_evidence",
            "historical_material_usage",
            "historical_labor_performance",
            "historical_assemblies",
            "validated_relationships",
            "approved_memories",
            "source_links",
        },
    }
    keep = keep_by_focus.get(selected, set())
    for field in _BOUNDED_LIST_FIELDS:
        if field not in keep:
            response[field] = []
    if selected not in {"pricing", "materials"}:
        response["pricing_candidates"] = []
        response["pricing_coverage"] = {}
    if selected != "labor":
        response["labor_plan_guidance"] = []
        response["labor_cost_summary"] = {}
    return response


def _calculation_requirements(
    concepts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            "concept_id": concept.get("concept_id"),
            "category": concept.get("category"),
            "required_inputs": concept.get("required_calculation_inputs") or [],
        }
        for concept in concepts
        if concept.get("required_calculation_inputs")
    ]


def _decision_type(bucket: str) -> str:
    if bucket.startswith("labor_"):
        return "labor"
    if bucket in {
        "sales_inspection_trips",
        "truck_expense",
        "meals_lodging",
        "freight",
        "delivery",
    }:
        return "logistics"
    if bucket in {"overhead", "profit"}:
        return "commercial"
    return "material_or_scope"


def _source_links(
    data: EstimatorData,
    historical_packet: dict[str, Any],
) -> list[dict[str, Any]]:
    comparables = [
        row
        for row in historical_packet.get("matched_comparables") or []
        if isinstance(row, dict)
    ]
    if not comparables:
        return []
    link_frame = getattr(data, "template_example_source_links", None)
    link_rows = (
        link_frame.fillna("").to_dict(orient="records")
        if hasattr(link_frame, "empty") and not link_frame.empty
        else []
    )
    jobs_frame = getattr(data, "jobs", None)
    job_rows = (
        jobs_frame.fillna("").to_dict(orient="records")
        if hasattr(jobs_frame, "empty") and not jobs_frame.empty
        else []
    )
    jobs_by_id = {
        str(row.get("job_id") or "").strip(): row
        for row in job_rows
        if str(row.get("job_id") or "").strip()
    }
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for comparable in comparables:
        example_id = str(comparable.get("example_id") or "").strip()
        job_id = str(comparable.get("job_id") or "").strip()
        source_file = str(comparable.get("source_file") or "").strip()
        matches = [
            row
            for row in link_rows
            if (
                example_id
                and example_id == str(row.get("example_id") or "").strip()
            )
            or (
                job_id
                and source_file
                and job_id == str(row.get("job_id") or "").strip()
                and source_file
                == str(row.get("source_file") or row.get("file_name") or "").strip()
            )
        ]
        if not matches:
            matches = [{}]
        job = jobs_by_id.get(job_id, {})
        for match in matches:
            row = {
                "source_type": "historical_estimate",
                "example_id": example_id,
                "job_id": job_id,
                "customer": comparable.get("customer"),
                "job_name": comparable.get("job_name"),
                "document_id": match.get("document_id"),
                "file_name": match.get("file_name") or source_file,
                "file_web_url": _safe_web_url(
                    match.get("source_url")
                    or comparable.get("source_url")
                    or comparable.get("web_url")
                ),
                "job_folder_web_url": _safe_web_url(
                    job.get("folder_url") or job.get("folder_link_or_path")
                ),
                "folder_path": match.get("folder_path") or job.get("folder_path"),
                "relative_path": match.get("relative_path"),
            }
            key = tuple(
                str(row.get(field) or "")
                for field in (
                    "example_id",
                    "job_id",
                    "document_id",
                    "file_name",
                    "file_web_url",
                )
            )
            if key in seen:
                continue
            seen.add(key)
            output.append(row)
    return output[:20]


def _safe_web_url(value: Any) -> str:
    text = str(value or "").strip()
    return text if text.lower().startswith(("https://", "http://")) else ""


def _unique_strings(values: list[Any]) -> list[str]:
    return list(
        dict.fromkeys(
            str(value).strip()
            for value in values
            if str(value or "").strip()
        )
    )


def _number(value: Any) -> float:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (date, datetime, Path)):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    scalar = getattr(value, "item", None)
    if callable(scalar):
        try:
            return json_safe(scalar())
        except (TypeError, ValueError):
            pass
    return str(value)
