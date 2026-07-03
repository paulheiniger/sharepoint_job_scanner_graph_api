from __future__ import annotations

import math
import re
from typing import Any

from .rules import first_nonblank, to_float

DEFAULT_R_VALUE_PER_INCH_BY_FOAM_TYPE = {
    "closed_cell": 5.7,
    "closed-cell": 5.7,
    "closed cell": 5.7,
    "open_cell": 3.7,
    "open-cell": 3.7,
    "open cell": 3.7,
}

SURFACE_LABELS = {
    "walls": "Walls",
    "ceiling": "Ceiling",
    "roof_underside": "Roof Underside",
    "gable": "Gable",
    "crawlspace": "Crawlspace",
    "rim_joist": "Rim Joist",
    "general": "General Insulation Area",
}

SURFACE_ALIASES = {
    "wall": "walls",
    "walls": "walls",
    "sidewall": "walls",
    "sidewalls": "walls",
    "ceiling": "ceiling",
    "ceilings": "ceiling",
    "roof": "roof_underside",
    "roof underside": "roof_underside",
    "underside": "roof_underside",
    "underside of roof": "roof_underside",
    "roof deck": "roof_underside",
    "gable": "gable",
    "gables": "gable",
    "crawlspace": "crawlspace",
    "crawl space": "crawlspace",
    "rim joist": "rim_joist",
    "rim joists": "rim_joist",
}


def safe_number(value: Any, default: float = 0.0) -> float:
    number = to_float(value)
    if number is None or not math.isfinite(number):
        return default
    return float(number)


def optional_number(value: Any) -> float | None:
    number = to_float(value)
    if number is None or not math.isfinite(number):
        return None
    return float(number)


def normalize_surface_type(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", " ").replace("_", " ")
    if not text:
        return "general"
    if text in SURFACE_ALIASES:
        return SURFACE_ALIASES[text]
    for needle, surface in SURFACE_ALIASES.items():
        if needle in text:
            return surface
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_") or "general"


def surface_label(surface_type: Any) -> str:
    surface = normalize_surface_type(surface_type)
    return SURFACE_LABELS.get(surface, surface.replace("_", " ").title())


def parse_r_value_targets(notes: str | None) -> list[dict[str, Any]]:
    """Extract surface-specific target R-values from estimator notes.

    This is intentionally deterministic and conservative. It only links a target
    to a surface when the surface term is close to the R-value phrase.
    """

    text = str(notes or "")
    if not text.strip():
        return []
    patterns = [
        re.compile(
            r"\b(?P<surface>walls?|sidewalls?|ceilings?|roof(?:\s+underside)?|underside(?:\s+of\s+roof)?|roof\s+deck|gables?|crawl\s*space|crawlspace|rim\s+joists?)"
            r"[^.\n;]{0,50}?\b(?:target\s*)?R[-\s]?(?P<value>\d+(?:\.\d+)?)\b",
            re.I,
        ),
        re.compile(
            r"\b(?:target\s*)?R[-\s]?(?P<value>\d+(?:\.\d+)?)\b[^.\n;]{0,25}?\b(?:for|on|at|in)\s+"
            r"(?P<surface>walls?|sidewalls?|ceilings?|roof(?:\s+underside)?|underside(?:\s+of\s+roof)?|roof\s+deck|gables?|crawl\s*space|crawlspace|rim\s+joists?)\b",
            re.I,
        ),
    ]
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, float]] = set()
    def surfaces_for_match(raw_surface: str, source_text: str) -> list[str]:
        source_lower = source_text.lower()
        raw = normalize_surface_type(raw_surface)
        surfaces = [raw]
        if "roof/ceiling" in source_lower or "roof and ceiling" in source_lower:
            surfaces.extend(["roof_underside", "ceiling"])
        return list(dict.fromkeys(surfaces))

    for pattern in patterns:
        for match in pattern.finditer(text):
            value = optional_number(match.group("value"))
            if value is None or value <= 0:
                continue
            source = match.group(0).strip()
            for surface in surfaces_for_match(match.group("surface"), source):
                key = (surface, round(value, 4))
                if key in seen:
                    continue
                seen.add(key)
                rows.append(
                    {
                        "surface_type": surface,
                        "target_r_value": round(value, 4),
                        "source_text": source,
                        "confidence": "high",
                    }
                )
    return rows


def build_insulation_deductions(scope: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for opening in scope.get("openings") or []:
        if not isinstance(opening, dict):
            continue
        quantity = safe_number(opening.get("quantity"), 1.0) or 1.0
        width = optional_number(opening.get("width_ft"))
        height = optional_number(opening.get("height_ft"))
        total = optional_number(first_nonblank(opening.get("total_area_sqft"), opening.get("known_area_sqft")))
        area_each = total / quantity if total is not None and quantity else None
        if area_each is None and width is not None and height is not None:
            area_each = width * height
            total = area_each * quantity
        rows.append(
            {
                "opening_type": opening.get("opening_type") or "opening",
                "quantity": quantity,
                "width_ft": width,
                "height_ft": height,
                "area_each_sqft": round(area_each, 4) if area_each is not None else None,
                "total_area_sqft": round(total, 4) if total is not None else None,
                "source_text": opening.get("source_text") or "",
                "missing_dimensions": list(opening.get("missing_dimensions") or []),
            }
        )
    return rows


def _deductions_for_walls(scope: dict[str, Any], deductions: list[dict[str, Any]]) -> float:
    explicit = optional_number(scope.get("wall_deduction_area_sqft"))
    if explicit is not None:
        return explicit
    from_openings = round(sum(safe_number(row.get("total_area_sqft"), 0.0) for row in deductions), 4)
    summarized = safe_number(first_nonblank(scope.get("opening_area_known_sqft"), scope.get("deduction_area_sqft")), 0.0)
    return max(from_openings, summarized)


def _r_targets_by_surface(scope: dict[str, Any], notes: str | None = None) -> dict[str, dict[str, Any]]:
    targets: list[dict[str, Any]] = []
    for row in scope.get("insulation_r_value_targets") or scope.get("r_value_targets") or []:
        if isinstance(row, dict):
            value = optional_number(first_nonblank(row.get("target_r_value"), row.get("r_value")))
            if value is not None and value > 0:
                targets.append(
                    {
                        "surface_type": normalize_surface_type(row.get("surface_type") or row.get("surface")),
                        "target_r_value": value,
                        "source_text": row.get("source_text") or "",
                        "confidence": row.get("confidence") or "medium",
                    }
                )
    targets.extend(parse_r_value_targets(notes or scope.get("notes") or scope.get("raw_input_notes") or ""))
    out: dict[str, dict[str, Any]] = {}
    for row in targets:
        surface = normalize_surface_type(row.get("surface_type"))
        out[surface] = row
    return out


def _surface_row(
    *,
    scope: dict[str, Any],
    surface_type: str,
    gross_area: Any,
    deduction_area: Any = 0.0,
    formula: str = "",
    source_text: str = "",
    confidence: str = "medium",
    r_targets: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    gross = optional_number(gross_area)
    if gross is None or gross <= 0:
        return None
    deduction = max(0.0, safe_number(deduction_area, 0.0))
    net = max(gross - deduction, 0.0)
    target = r_targets.get(normalize_surface_type(surface_type), {})
    return {
        "include": True,
        "section": "insulation_surfaces",
        "decision_id": f"insulation_surface_{normalize_surface_type(surface_type)}",
        "template_bucket": "insulation_surface_areas",
        "surface_type": normalize_surface_type(surface_type),
        "surface": surface_label(surface_type),
        "gross_area_sqft": round(gross, 4),
        "deduction_area_sqft": round(deduction, 4),
        "net_area_sqft": round(net, 4),
        "area_formula": formula,
        "source_text": source_text,
        "target_r_value": optional_number(target.get("target_r_value")),
        "target_r_source_text": target.get("source_text") or "",
        "confidence": confidence,
        "foam_type": first_nonblank(scope.get("foam_type"), ""),
    }


def build_insulation_surface_area_rows(scope: dict[str, Any], notes: str | None = None) -> list[dict[str, Any]]:
    """Build surface rows from parsed insulation scope and explicit overrides."""

    r_targets = _r_targets_by_surface(scope, notes)
    explicit = scope.get("insulation_surface_areas") or scope.get("surface_areas")
    rows: list[dict[str, Any]] = []
    if isinstance(explicit, list) and explicit:
        for raw in explicit:
            if not isinstance(raw, dict):
                continue
            surface = normalize_surface_type(raw.get("surface_type") or raw.get("surface"))
            row = _surface_row(
                scope=scope,
                surface_type=surface,
                gross_area=first_nonblank(raw.get("gross_area_sqft"), raw.get("area_sqft"), raw.get("net_area_sqft")),
                deduction_area=raw.get("deduction_area_sqft"),
                formula=raw.get("area_formula") or "",
                source_text=raw.get("source_text") or raw.get("image_note") or "",
                confidence=raw.get("confidence") or "medium",
                r_targets=r_targets,
            )
            if row:
                if optional_number(raw.get("net_area_sqft")) is not None:
                    row["net_area_sqft"] = round(max(safe_number(raw.get("net_area_sqft"), 0.0), 0.0), 4)
                rows.append(row)
        if rows:
            return rows

    deductions = build_insulation_deductions(scope)
    wall_row = _surface_row(
        scope=scope,
        surface_type="walls",
        gross_area=scope.get("gross_wall_area_sqft"),
        deduction_area=_deductions_for_walls(scope, deductions),
        formula="2 * (building_length_ft + building_width_ft) * wall_height_ft - opening deductions",
        source_text=first_nonblank(scope.get("dimension_evidence"), scope.get("notes"), ""),
        confidence="high" if scope.get("gross_wall_area_sqft") else "medium",
        r_targets=r_targets,
    )
    if wall_row:
        rows.append(wall_row)

    ceiling_row = _surface_row(
        scope=scope,
        surface_type="ceiling",
        gross_area=scope.get("ceiling_area_sqft"),
        deduction_area=scope.get("ceiling_deduction_area_sqft") or 0.0,
        formula="building_length_ft * building_width_ft",
        source_text=first_nonblank(scope.get("dimension_evidence"), scope.get("notes"), ""),
        confidence="high" if scope.get("ceiling_area_sqft") else "medium",
        r_targets=r_targets,
    )
    if ceiling_row:
        rows.append(ceiling_row)

    roof_underside = first_nonblank(scope.get("roof_underside_area_sqft"), scope.get("pitched_roof_underside_area_sqft"))
    roof_row = _surface_row(
        scope=scope,
        surface_type="roof_underside",
        gross_area=roof_underside,
        deduction_area=scope.get("roof_underside_deduction_area_sqft") or 0.0,
        formula=scope.get("roof_underside_area_formula") or "pitched roof underside geometry",
        source_text=scope.get("roof_underside_source_text") or "",
        confidence="medium",
        r_targets=r_targets,
    )
    if roof_row:
        rows.append(roof_row)

    if not rows:
        area = first_nonblank(scope.get("net_insulation_area_sqft"), scope.get("estimated_sqft"), scope.get("surface_area_sqft"))
        fallback_row = _surface_row(
            scope=scope,
            surface_type="general",
            gross_area=area,
            deduction_area=0.0,
            formula="parsed net insulation area",
            source_text=scope.get("notes") or "",
            confidence="low",
            r_targets=r_targets,
        )
        if fallback_row:
            rows.append(fallback_row)
    return rows


def product_r_value_per_inch(product_context: dict[str, Any] | None, foam_type: Any = None) -> tuple[float, str, str]:
    """Return conservative R/inch from product context or estimator defaults."""

    context = product_context or {}
    candidates: list[tuple[float, str, str]] = []
    for key in ("aged_r_value_per_inch", "r_value_per_inch", "initial_r_value_per_inch"):
        value = optional_number(context.get(key))
        if value is not None and value > 0:
            source = str(context.get(f"{key}_source") or "product_knowledge")
            candidates.append((value, key, source))
    if candidates:
        aged = [row for row in candidates if "aged" in row[1]]
        chosen = aged[0] if aged else candidates[0]
        return round(chosen[0], 4), "product_knowledge", chosen[2]

    foam_key = str(foam_type or "").strip().lower().replace("_", " ")
    fallback = DEFAULT_R_VALUE_PER_INCH_BY_FOAM_TYPE.get(foam_key) or DEFAULT_R_VALUE_PER_INCH_BY_FOAM_TYPE.get(foam_key.replace(" ", "_"))
    if fallback:
        return fallback, "estimator_default_by_foam_type", f"{foam_key or 'foam'} default"
    return 0.0, "missing_product_r_value", ""


def round_thickness_inches(value: float, increment: float = 0.5) -> float:
    if value <= 0:
        return 0.0
    return round(math.ceil(value / increment) * increment, 4)


def apply_thickness_decisions(
    surface_rows: list[dict[str, Any]],
    *,
    product_context: dict[str, Any] | None = None,
    foam_type: Any = None,
    default_thickness_inches: Any = None,
) -> list[dict[str, Any]]:
    r_per_inch, r_source, r_source_text = product_r_value_per_inch(product_context, foam_type)
    default_thickness = optional_number(default_thickness_inches)
    rows: list[dict[str, Any]] = []
    for raw in surface_rows:
        row = dict(raw)
        target = optional_number(row.get("target_r_value"))
        review_flags: list[str] = []
        required = 0.0
        rounded = 0.0
        if target is not None and target > 0 and r_per_inch > 0:
            required = target / r_per_inch
            rounded = round_thickness_inches(required)
        elif default_thickness is not None and default_thickness > 0:
            rounded = default_thickness
            required = default_thickness
            review_flags.append("No target R-value found; using historical/default foam thickness.")
        elif target is None:
            review_flags.append("Target R-value missing for this surface.")
        elif r_per_inch <= 0:
            review_flags.append("Product R-value per inch missing; estimator review required.")
        row["product_r_value_per_inch"] = r_per_inch
        row["r_value_source"] = r_source
        row["r_value_source_text"] = r_source_text
        row["required_thickness_inches"] = round(required, 4) if required else 0.0
        row["rounded_thickness_inches"] = round(rounded, 4) if rounded else 0.0
        row["edited_thickness_inches"] = optional_number(row.get("edited_thickness_inches")) or row["rounded_thickness_inches"]
        row["review_flags"] = list(dict.fromkeys([*(row.get("review_flags") or []), *review_flags]))
        row["notes"] = _surface_note(row)
        row["decision_values"] = {
            "surface_type": row.get("surface_type"),
            "gross_area_sqft": row.get("gross_area_sqft"),
            "deduction_area_sqft": row.get("deduction_area_sqft"),
            "net_area_sqft": row.get("net_area_sqft"),
            "target_r_value": row.get("target_r_value"),
            "foam_type": row.get("foam_type"),
            "product_r_value_per_inch": row.get("product_r_value_per_inch"),
            "required_thickness_inches": row.get("required_thickness_inches"),
            "edited_thickness_inches": row.get("edited_thickness_inches"),
        }
        row["recommended_decision_value"] = row.get("rounded_thickness_inches")
        row["editable_decision_value"] = {
            "target_r_value": row.get("target_r_value"),
            "edited_thickness_inches": row.get("edited_thickness_inches"),
        }
        row["calculated_output"] = row.get("edited_thickness_inches")
        row["calculated_output_summary"] = (
            f"{row.get('surface')}: {row.get('net_area_sqft')} sqft at {row.get('edited_thickness_inches')} in"
        )
        rows.append(row)
    return rows


def _surface_note(row: dict[str, Any]) -> str:
    target = row.get("target_r_value")
    thickness = row.get("edited_thickness_inches")
    r_in = row.get("product_r_value_per_inch")
    if target and thickness and r_in:
        return f"R{target:g} target using {r_in:g} R/in gives {row.get('required_thickness_inches'):g} in; rounded to {thickness:g} in."
    if thickness:
        return f"Using {thickness:g} in foam thickness for estimator review."
    return "Surface included; target R-value or foam product R/in is missing."


def build_insulation_surface_decisions(
    scope: dict[str, Any],
    *,
    notes: str | None = None,
    product_context: dict[str, Any] | None = None,
    default_thickness_inches: Any = None,
) -> list[dict[str, Any]]:
    surfaces = build_insulation_surface_area_rows(scope, notes)
    return apply_thickness_decisions(
        surfaces,
        product_context=product_context,
        foam_type=scope.get("foam_type"),
        default_thickness_inches=default_thickness_inches,
    )


def aggregate_surface_foam_outputs(
    surface_rows: list[dict[str, Any]],
    *,
    yield_or_coverage: Any,
    unit_price: Any = None,
    units_per_sqft_per_inch: Any = None,
    cost_per_sqft_per_inch: Any = None,
    cost_per_sqft: Any = None,
    include: bool = True,
) -> dict[str, Any]:
    from .formula_mirror import calculate_insulation_foam

    outputs: list[dict[str, Any]] = []
    total_area = 0.0
    weighted_thickness = 0.0
    total_units = 0.0
    total_sets = 0.0
    total_cost = 0.0
    cost_sources: set[str] = set()
    for row in surface_rows:
        if row.get("include") is False:
            continue
        area = safe_number(row.get("net_area_sqft"), 0.0)
        thickness = safe_number(first_nonblank(row.get("edited_thickness_inches"), row.get("rounded_thickness_inches")), 0.0)
        result = calculate_insulation_foam(
            area_sqft=area,
            thickness_inches=thickness,
            yield_or_coverage=yield_or_coverage,
            unit_price=unit_price,
            units_per_sqft_per_inch=units_per_sqft_per_inch,
            cost_per_sqft=cost_per_sqft,
            cost_per_sqft_per_inch=cost_per_sqft_per_inch,
            include=include,
        )
        output = {
            **row,
            "formula_output": result,
            "estimated_units": result.get("estimated_units"),
            "estimated_sets": result.get("estimated_sets"),
            "estimated_cost": result.get("estimated_cost"),
        }
        outputs.append(output)
        total_area += area
        weighted_thickness += area * thickness
        total_units += safe_number(result.get("estimated_units"), 0.0)
        total_sets += safe_number(result.get("estimated_sets"), 0.0)
        total_cost += safe_number(result.get("estimated_cost"), 0.0)
        cost_sources.add(str(result.get("cost_source") or ""))
    average_thickness = weighted_thickness / total_area if total_area else 0.0
    return {
        "formula_model": "surface_weighted_foam_sets_from_r_value_thickness",
        "area_sqft": round(total_area, 4),
        "weighted_thickness_inches": round(average_thickness, 6),
        "estimated_units": round(total_units, 6),
        "estimated_sets": round(total_sets, 6),
        "estimated_cost": round(total_cost, 2),
        "cost_source": ", ".join(sorted(source for source in cost_sources if source)) or "not_included",
        "surface_outputs": outputs,
    }
