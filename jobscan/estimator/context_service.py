from __future__ import annotations

import json
import math
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
    "decision_concepts",
    "calculation_requirements",
    "source_links",
)


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

    estimator_data = data or load_estimator_data(
        base_dir=base_dir,
        database_url=database_url,
        prefer_database=bool(database_url),
        load_profile=ESTIMATOR_LOAD_PROFILE_CHAT,
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
    )
    response: dict[str, Any] = {
        "schema_version": "spraytec.copilot_estimator_context.v1",
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
        "pricing_candidates": full_context.get("pricing_candidates_by_bucket") or [],
        "product_guidance": full_context.get("product_guidance_digest") or [],
        "foam_yield_history": _without_verbose_examples(
            full_context.get("foam_yield_history_digest") or []
        ),
        **planning_guidance,
        "decision_concepts": decision_concepts,
        "calculation_requirements": _calculation_requirements(decision_concepts),
        "source_links": source_links,
        "warnings": _unique_strings(estimator_data.warnings),
    }
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
        "target_bytes": AGENT_CONTEXT_TARGET_BYTES,
        "public_max_bytes": AGENT_CONTEXT_PUBLIC_MAX_BYTES,
        "original_counts": original_counts,
    }
    _trim_to_serialized_budget(response)
    _refresh_budget_metadata(response)
    return response


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
        ("decision_evidence", 10),
        ("approved_memories", 4),
        ("pricing_candidates", 12),
        ("product_guidance", 6),
        ("foam_yield_history", 4),
        ("purchasing_guidance", 6),
        ("labor_plan_guidance", 8),
        ("validated_relationships", 4),
        ("historical_assemblies", 1),
        ("historical_labor_performance", 6),
        ("historical_material_usage", 6),
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
