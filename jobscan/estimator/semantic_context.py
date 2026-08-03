from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable


_QUANTITY_FIELDS: tuple[tuple[str, str], ...] = (
    ("estimated_sets", "set"),
    ("estimated_gallons", "gal"),
    ("estimated_units", ""),
    ("quantity", ""),
    ("units", ""),
    ("linear_ft", "linear_ft"),
    ("amount", ""),
)

_BASIS_FIELDS: tuple[tuple[str, str], ...] = (
    ("basis_sqft", "sqft"),
    ("area_sqft", "sqft"),
    ("board_area_sqft", "sqft"),
    ("reference_area_sqft", "sqft"),
    ("linear_ft", "linear_ft"),
    ("length_ft", "ft"),
    ("width_ft", "ft"),
    ("count", "count"),
)

_APPLICATION_PARAMETER_FIELDS: tuple[tuple[str, str], ...] = (
    ("thickness_inches", "in"),
    ("yield_or_coverage", ""),
    ("coverage_sqft_per_unit", "sqft_per_unit"),
    ("gal_per_100_sqft", "gal_per_100_sqft"),
    ("wet_mils", "mil"),
    ("coats", "count"),
    ("waste_pct", "percent"),
    ("waste_percent", "percent"),
)


def build_semantic_observations(
    *,
    decision_evidence: Iterable[Any],
    matched_comparables: Iterable[Any],
    source_links: Iterable[Any],
    template_type: str,
) -> dict[str, list[dict[str, Any]]]:
    """Translate row-derived evidence without changing the row-oriented source.

    Workbook coordinates are intentionally not copied. The source evidence
    remains untouched and available to the existing estimator path.
    """

    normalized_type = _slug(template_type)
    links_by_job = _links_by_job(source_links)
    materials: list[dict[str, Any]] = []
    labor: list[dict[str, Any]] = []
    for raw in decision_evidence:
        if not isinstance(raw, dict):
            continue
        category = _slug(raw.get("template_bucket") or raw.get("category"))
        if not category:
            continue
        concept_id = (
            f"{normalized_type}.{category}" if normalized_type else category
        )
        sources = _evidence_sources(raw, links_by_job)
        if category.startswith("labor_"):
            labor.append(
                _labor_observation(
                    raw,
                    concept_id=concept_id,
                    category=category,
                    sources=sources,
                )
            )
            continue
        material = _material_observation(
            raw,
            concept_id=concept_id,
            category=category,
            sources=sources,
        )
        if material is not None:
            materials.append(material)

    assemblies = [
        observation
        for raw in matched_comparables
        if isinstance(raw, dict)
        for observation in [
            _assembly_observation(
                raw,
                template_type=normalized_type,
                links_by_job=links_by_job,
            )
        ]
        if observation is not None
    ]
    return {
        "historical_material_usage": materials[:30],
        "historical_labor_performance": labor[:30],
        "historical_assemblies": assemblies[:10],
    }


def _material_observation(
    raw: dict[str, Any],
    *,
    concept_id: str,
    category: str,
    sources: list[dict[str, Any]],
) -> dict[str, Any] | None:
    inputs = _mapping(raw.get("sample_inputs"))
    outputs = _mapping(raw.get("sample_outputs"))
    explicit_unit = _text(
        inputs.get("unit")
        or inputs.get("uom")
        or raw.get("unit")
        or raw.get("uom")
    )
    quantities = _measurements(
        inputs,
        _QUANTITY_FIELDS,
        explicit_unit=explicit_unit,
    )
    bases = _measurements(inputs, _BASIS_FIELDS)
    parameters = _measurements(inputs, _APPLICATION_PARAMETER_FIELDS)
    unit_price = _first_number(
        inputs.get("unit_price"),
        inputs.get("current_unit_price"),
        raw.get("unit_price"),
    )
    estimated_cost = _first_number(
        outputs.get("estimated_cost"),
        outputs.get("calculated_cost"),
        outputs.get("line_total"),
    )
    if not quantities and not bases and not parameters and unit_price is None:
        return None
    item_name = _text(
        raw.get("line_item")
        or raw.get("material_name")
        or raw.get("template_option")
    )
    return {
        "observation_id": _observation_id(
            "material",
            concept_id,
            item_name,
            sources,
        ),
        "concept_id": concept_id,
        "category": category,
        "material_name": item_name,
        "quantity_measurements": quantities,
        "basis_measurements": bases,
        "application_parameters": parameters,
        "unit_price": unit_price,
        "estimated_cost": estimated_cost,
        "support_count": _integer(raw.get("support_count")),
        "confidence": _number(raw.get("confidence")),
        "formula_ready": _optional_bool(raw.get("formula_ready")),
        "missing_inputs": _strings(raw.get("missing_inputs")),
        "sources": sources,
    }


def _labor_observation(
    raw: dict[str, Any],
    *,
    concept_id: str,
    category: str,
    sources: list[dict[str, Any]],
) -> dict[str, Any]:
    inputs = _mapping(raw.get("sample_inputs"))
    outputs = _mapping(raw.get("sample_outputs"))
    driver = {
        **{
            key: value
            for key, value in inputs.items()
            if key
            in {
                "labor_driver_type",
                "labor_driver_unit",
                "labor_driver_rate_unit",
                "historical_driver_rate",
                "historical_driver_evidence_count",
                "labor_driver_quantity",
            }
        },
        **_mapping(raw.get("labor_driver")),
    }
    activity = _text(raw.get("line_item") or raw.get("activity"))
    return {
        "observation_id": _observation_id(
            "labor",
            concept_id,
            activity,
            sources,
        ),
        "concept_id": concept_id,
        "category": category,
        "activity": activity,
        "total_hours": _first_number(
            inputs.get("total_hours"),
            inputs.get("editable_total_hours"),
            outputs.get("total_hours"),
        ),
        "crew_size": _first_number(
            inputs.get("crew_size"),
            inputs.get("crew_people_selection"),
            inputs.get("people_count"),
        ),
        "days": _first_number(inputs.get("days"), inputs.get("editable_days")),
        "hourly_rate": _first_number(inputs.get("hourly_rate")),
        "daily_rate": _first_number(inputs.get("daily_rate")),
        "estimated_cost": _first_number(
            outputs.get("estimated_cost"),
            outputs.get("calculated_cost"),
            outputs.get("line_total"),
        ),
        "productivity": {
            "driver_type": _text(driver.get("labor_driver_type")),
            "driver_quantity": _number(driver.get("labor_driver_quantity")),
            "driver_unit": _text(driver.get("labor_driver_unit")),
            "rate": _number(driver.get("historical_driver_rate")),
            "rate_unit": _text(driver.get("labor_driver_rate_unit")),
            "evidence_count": _integer(
                driver.get("historical_driver_evidence_count")
            ),
        },
        "support_count": _integer(raw.get("support_count")),
        "confidence": _number(raw.get("confidence")),
        "formula_ready": _optional_bool(raw.get("formula_ready")),
        "missing_inputs": _strings(raw.get("missing_inputs")),
        "sources": sources,
    }


def _assembly_observation(
    raw: dict[str, Any],
    *,
    template_type: str,
    links_by_job: dict[str, list[dict[str, Any]]],
) -> dict[str, Any] | None:
    job_id = _text(raw.get("job_id"))
    example_id = _text(raw.get("example_id"))
    categories = _strings(
        raw.get("historical_decision_categories")
        or [
            str(value).split("@row_", 1)[0]
            for value in raw.get("active_decision_keys") or []
        ]
    )
    if not job_id and not example_id and not categories:
        return None
    sources = _comparable_sources(raw, links_by_job)
    label = _text(
        raw.get("job_name")
        or raw.get("customer")
        or raw.get("source_file")
        or job_id
    )
    return {
        "observation_id": _observation_id(
            "assembly",
            template_type or _slug(raw.get("template_type")),
            label,
            sources,
        ),
        "template_type": template_type or _slug(raw.get("template_type")),
        "job_id": job_id,
        "example_id": example_id,
        "label": label,
        "project_class": _text(raw.get("project_class")),
        "market_segment": _text(raw.get("market_segment")),
        "building_type": _text(raw.get("building_type")),
        "substrate": _text(raw.get("substrate")),
        "material_system": _text(raw.get("material_system")),
        "warranty_years": _number(raw.get("warranty_years")),
        "area_sqft": _number(raw.get("area_sqft")),
        "scope_summary": _text(raw.get("scope_summary")),
        "decision_categories": categories,
        "similarity_score": _number(raw.get("similarity_score")),
        "match_reasons": _strings(raw.get("match_reasons")),
        "sources": sources,
    }


def _evidence_sources(
    raw: dict[str, Any],
    links_by_job: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for example in raw.get("examples") or []:
        if not isinstance(example, dict):
            continue
        job_id = _text(example.get("job_id"))
        links = links_by_job.get(job_id) or [{}]
        for link in links:
            output.append(
                _source_reference(
                    job_id=job_id,
                    label=_text(example.get("label")),
                    similarity_score=_number(example.get("similarity_score")),
                    match_reasons=_strings(example.get("match_reasons")),
                    reference_area_sqft=_number(
                        example.get("reference_area_sqft")
                    ),
                    link=link,
                )
            )
    return _dedupe_sources(output)


def _comparable_sources(
    raw: dict[str, Any],
    links_by_job: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    job_id = _text(raw.get("job_id"))
    links = links_by_job.get(job_id) or [
        {
            "example_id": raw.get("example_id"),
            "file_name": raw.get("source_file"),
        }
    ]
    return _dedupe_sources(
        [
            _source_reference(
                job_id=job_id,
                label=_text(
                    raw.get("job_name")
                    or raw.get("customer")
                    or raw.get("source_file")
                ),
                similarity_score=_number(raw.get("similarity_score")),
                match_reasons=_strings(raw.get("match_reasons")),
                reference_area_sqft=_number(raw.get("area_sqft")),
                link=link,
            )
            for link in links
        ]
    )


def _source_reference(
    *,
    job_id: str,
    label: str,
    similarity_score: float | None,
    match_reasons: list[str],
    reference_area_sqft: float | None,
    link: dict[str, Any],
) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "example_id": _text(link.get("example_id")),
        "document_id": _text(link.get("document_id")),
        "label": label,
        "file_name": _text(link.get("file_name")),
        "file_web_url": _text(link.get("file_web_url")),
        "folder_path": _text(link.get("folder_path")),
        "relative_path": _text(link.get("relative_path")),
        "similarity_score": similarity_score,
        "match_reasons": match_reasons,
        "reference_area_sqft": reference_area_sqft,
    }


def _links_by_job(
    source_links: Iterable[Any],
) -> dict[str, list[dict[str, Any]]]:
    output: dict[str, list[dict[str, Any]]] = {}
    for raw in source_links:
        if not isinstance(raw, dict):
            continue
        job_id = _text(raw.get("job_id"))
        if job_id:
            output.setdefault(job_id, []).append(raw)
    return output


def _measurements(
    values: dict[str, Any],
    fields: tuple[tuple[str, str], ...],
    *,
    explicit_unit: str = "",
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for field, default_unit in fields:
        number = _number(values.get(field))
        if number is None:
            continue
        output.append(
            {
                "name": field,
                "value": number,
                "unit": explicit_unit or default_unit or "source_unit_unspecified",
            }
        )
    return output


def _observation_id(
    kind: str,
    concept_id: str,
    label: str,
    sources: list[dict[str, Any]],
) -> str:
    source_keys = [
        {
            "job_id": source.get("job_id"),
            "example_id": source.get("example_id"),
            "document_id": source.get("document_id"),
        }
        for source in sources
    ]
    digest = hashlib.sha256(
        json.dumps(
            {
                "kind": kind,
                "concept_id": concept_id,
                "label": label,
                "sources": source_keys,
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:12]
    return f"{kind}.{concept_id or 'uncategorized'}.{digest}"


def _dedupe_sources(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for value in values:
        key = tuple(
            _text(value.get(field))
            for field in ("job_id", "example_id", "document_id", "file_name")
        )
        if key in seen:
            continue
        seen.add(key)
        output.append(value)
    return output[:10]


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _strings(value: Any) -> list[str]:
    values = value if isinstance(value, (list, tuple, set)) else [value]
    return list(
        dict.fromkeys(_text(item) for item in values if _text(item))
    )


def _text(value: Any) -> str:
    return str(value or "").strip()


def _slug(value: Any) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", _text(value).lower())).strip(
        "_"
    )


def _number(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _first_number(*values: Any) -> float | None:
    for value in values:
        number = _number(value)
        if number is not None:
            return number
    return None


def _integer(value: Any) -> int:
    number = _number(value)
    return max(0, int(number)) if number is not None else 0


def _optional_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None
