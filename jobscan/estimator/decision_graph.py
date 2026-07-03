from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]


GRAPH_SHEETS = {
    "decision_nodes": "decision_nodes",
    "selector_options": "selector_options",
    "formula_models": "formula_models",
    "row_traceability": "row_traceability",
    "crew_rate_options": "crew_rate_options",
    "area_calculation_models": "area_calculation_models",
}


INPUT_FIELD_HINTS = {
    "selector_code",
    "resolved_item_name",
    "foam_product_selector",
    "coating_product_selector",
    "manufacturer",
    "density",
    "area_sqft",
    "thickness_inches",
    "yield_or_coverage",
    "unit_price",
    "gal_per_100_sqft",
    "gal_per_sqft",
    "wet_mils_estimate",
    "waste_factor_pct",
    "linear_ft",
    "ft_per_unit",
    "margin_pct",
    "labor_task",
    "days",
    "crew_size",
    "crew_person_selector_code",
    "selected_daily_rate_cell",
    "hourly_rate",
    "blended_rate",
    "prevailing_wage",
    "fringe",
    "daily_rate",
}

COMPUTED_FIELD_HINTS = {
    "estimated_units",
    "estimated_sets",
    "estimated_gallons",
    "estimated_cost",
    "calculated_cost",
    "total_hours",
    "daily_rate",
    "cost_per_sqft",
}


def _json_loads(value: Any, fallback: Any = None) -> Any:
    if value in (None, ""):
        return fallback
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        return fallback
    try:
        return json.loads(value)
    except Exception:
        return fallback


def _json_dumps(value: Any) -> str:
    return json.dumps(value if value is not None else [], default=str, sort_keys=True)


def _safe_text(value: Any) -> str:
    return "" if value is None else str(value)


def _slug(value: Any) -> str:
    text = _safe_text(value).strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def _unique(values: list[Any]) -> list[Any]:
    seen: set[str] = set()
    result: list[Any] = []
    for value in values:
        key = json.dumps(value, default=str, sort_keys=True) if isinstance(value, (dict, list)) else _safe_text(value)
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _first_nonblank(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _rows_for_intelligence(intelligence: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    material_rows = intelligence.get("material_rows") or intelligence.get("materials_rows") or []
    labor_rows = intelligence.get("labor_rows") or []
    return list(material_rows), list(labor_rows)


def resolve_intelligence_path(path: str | Path | None, template_type: str) -> Path:
    """Resolve common template intelligence paths used by local CLIs and tests."""
    candidates: list[Path] = []
    if path:
        supplied = Path(path).expanduser()
        candidates.extend(
            [
                supplied,
                Path.cwd() / supplied,
                PROJECT_ROOT / supplied,
                PROJECT_ROOT / "output" / supplied.name,
            ]
        )
    names = [
        f"{template_type}_template_intelligence.json",
        f"template_intelligence_{template_type}.json",
    ]
    for name in names:
        candidates.extend([Path.cwd() / name, PROJECT_ROOT / name, Path.cwd() / "output" / name, PROJECT_ROOT / "output" / name])
    for candidate in candidates:
        if candidate.exists():
            return candidate
    requested = path or names[0]
    raise FileNotFoundError(f"Could not find {template_type} template intelligence JSON for {requested!s}.")


def load_template_intelligence(path: str | Path | None, template_type: str) -> dict[str, Any]:
    resolved = resolve_intelligence_path(path, template_type)
    with resolved.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    data.setdefault("source_intelligence_path", str(resolved))
    return data


def _selector_options_for_rows(intelligence: dict[str, Any], row_numbers: set[int], decision_id: str) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    for row in intelligence.get("selector_maps") or []:
        try:
            row_number = int(row.get("row_number"))
        except Exception:
            continue
        if row_number not in row_numbers:
            continue
        options.append(
            {
                "decision_id": decision_id,
                "template_type": intelligence.get("template_type"),
                "sheet_name": row.get("sheet_name"),
                "row_number": row_number,
                "selector_cell": row.get("selector_cell"),
                "resolved_cell": row.get("resolved_cell"),
                "selector_code": row.get("selector_code"),
                "resolved_item_name": row.get("resolved_item_name"),
                "source_type": "row_selector_map",
                "formula": row.get("formula"),
            }
        )
    return _unique(options)


def _crew_rate_options(intelligence: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in intelligence.get("people_rate_table") or []:
        rows.append(
            {
                "decision_id": f"{intelligence.get('template_type')}_crew_rate_selection",
                "template_type": intelligence.get("template_type"),
                "selector_code": row.get("selector_code"),
                "crew_size": row.get("crew_size"),
                "people_sheet_column": row.get("people_sheet_column"),
                "daily_rate_cell": row.get("daily_rate_cell"),
                "daily_rate_formula": row.get("daily_rate_formula"),
                "hours_per_day_cell": row.get("hours_per_day_cell"),
                "hours_per_day": row.get("hours_per_day"),
                "formula_dependencies": row.get("formula_dependencies"),
                "crew_components": row.get("crew_components"),
                "source_type": "people_daily_rate_selector",
            }
        )
    return rows


def _area_calculation_models(intelligence: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in intelligence.get("sq_ft_calculation") or []:
        rows.append(
            {
                "decision_id": f"{intelligence.get('template_type')}_area_takeoff",
                "template_type": intelligence.get("template_type"),
                "sheet_name": row.get("sheet_name"),
                "row_number": row.get("row_number"),
                "area_type": row.get("area_type"),
                "description_cell": row.get("description_cell"),
                "description": row.get("description"),
                "height_cell": row.get("height_cell"),
                "width_cell": row.get("width_cell"),
                "total_cell": row.get("total_cell"),
                "total_formula": row.get("total_formula"),
                "model": row.get("model"),
                "final_total_cell": row.get("final_total_cell"),
                "final_total_formula": row.get("final_total_formula"),
            }
        )
    return rows


def _material_decision_id(template_type: str, row: dict[str, Any]) -> str:
    bucket = _safe_text(row.get("template_bucket"))
    formula_model = _safe_text(row.get("formula_model"))
    if template_type == "insulation" and bucket == "foam" and formula_model == "foam_sets_from_area_thickness_yield":
        return "insulation_foam_system"
    if template_type == "insulation" and bucket == "thermal_barrier_coating":
        return "insulation_thermal_barrier"
    if template_type == "roofing" and bucket == "coating":
        return "roofing_coating_system"
    if bucket == "caulk_sealant":
        return f"{template_type}_caulk_sealant"
    if bucket == "lift":
        return f"{template_type}_lift_equipment"
    return f"{template_type}_{_slug(bucket or row.get('line_item_kind') or 'material')}"


def _labor_decision_id(template_type: str, row: dict[str, Any]) -> str:
    bucket = _safe_text(row.get("template_bucket")) or _slug(row.get("labor_task")) or "labor"
    return f"{template_type}_{_slug(bucket)}"


def _title_for_node(template_type: str, rows: list[dict[str, Any]], decision_id: str) -> str:
    bucket = _safe_text(rows[0].get("template_bucket")) if rows else decision_id
    if decision_id == "insulation_foam_system":
        return "Insulation Foam System"
    if decision_id == "insulation_thermal_barrier":
        return "Insulation Thermal Barrier / DC315"
    if decision_id == "roofing_coating_system":
        return "Roofing Coating System"
    if rows and rows[0].get("line_item_kind") == "labor":
        return _first_nonblank(rows[0].get("labor_task"), bucket.replace("_", " ").title())
    return bucket.replace("_", " ").title()


def _category_for_rows(template_type: str, rows: list[dict[str, Any]], decision_id: str) -> str:
    if decision_id.endswith("_area_takeoff"):
        return "scope_decision"
    if not rows:
        return "calculated_output"
    row = rows[0]
    bucket = _safe_text(row.get("template_bucket"))
    kind = _safe_text(row.get("line_item_kind"))
    formula_model = _safe_text(row.get("formula_model"))
    if kind == "labor" or bucket.startswith("labor_"):
        return "labor_planning"
    if bucket in {"lift", "generator", "space_heater", "dumpsters"} or kind == "equipment":
        return "equipment_selection"
    if bucket in {"sales_inspection_trips", "truck_expense", "labor_traveling", "travel"}:
        return "travel_logistics"
    if bucket in {"delivery_fee", "freight", "drum_disposal", "abaa_audit", "abaa_fee", "misc", "meals_lodging"}:
        return "cost_adder"
    if formula_model in {"foam_sets_from_area_thickness_yield", "coating_gallons_from_area_rate_waste"}:
        return "product_selection"
    if bucket in {"primer", "caulk_sealant", "fabric", "thinner", "granules", "fasteners", "plates"}:
        return "product_selection"
    return "quantity_driver"


def _formula_models_for_rows(rows: list[dict[str, Any]]) -> list[str]:
    return _unique([row.get("formula_model") for row in rows if row.get("formula_model")])


def _dependencies_for_rows(rows: list[dict[str, Any]]) -> list[str]:
    dependencies: list[str] = []
    for row in rows:
        for source in (row.get("formula_dependencies"), row.get("formula_cells")):
            parsed = _json_loads(source, source)
            if isinstance(parsed, list):
                dependencies.extend([_safe_text(item) for item in parsed if item not in (None, "")])
            elif isinstance(parsed, dict):
                for value in parsed.values():
                    dependencies.extend([_safe_text(item) for item in _json_loads(row.get("formula_dependencies"), []) or []])
                    if isinstance(value, str):
                        dependencies.extend(re.findall(r"(?:[A-Za-z ]+!)?\$?[A-Z]{1,3}\$?\d+(?::\$?[A-Z]{1,3}\$?\d+)?", value))
            elif isinstance(parsed, str):
                dependencies.extend(re.findall(r"(?:[A-Za-z ]+!)?\$?[A-Z]{1,3}\$?\d+(?::\$?[A-Z]{1,3}\$?\d+)?", parsed))
    return sorted(set(dep for dep in dependencies if dep))


def _fields_from_rows(rows: list[dict[str, Any]], hints: set[str]) -> list[str]:
    fields: list[str] = []
    for row in rows:
        role_map = _json_loads(row.get("cell_roles"), {})
        if isinstance(role_map, dict):
            for role in role_map.values():
                if role in hints:
                    fields.append(role)
        for key, value in row.items():
            if key in hints and value not in (None, ""):
                fields.append(key)
    return _unique(fields)


def _special_input_fields(decision_id: str) -> list[str]:
    if decision_id == "insulation_foam_system":
        return [
            "foam_product_selector",
            "manufacturer",
            "density",
            "area_sqft",
            "thickness_inches",
            "yield_or_coverage",
            "unit_price",
        ]
    if decision_id in {"roofing_coating_system", "insulation_thermal_barrier"}:
        return [
            "coating_product_selector",
            "coating_type",
            "area_sqft",
            "gal_per_100_sqft",
            "gal_per_sqft",
            "wet_mils_estimate",
            "waste_factor_pct",
            "unit_price",
        ]
    return []


def _special_computed_fields(decision_id: str, rows: list[dict[str, Any]]) -> list[str]:
    if decision_id == "insulation_foam_system":
        return ["estimated_units", "estimated_sets", "estimated_cost"]
    if decision_id in {"roofing_coating_system", "insulation_thermal_barrier"}:
        return ["estimated_gallons", "estimated_cost"]
    if rows and rows[0].get("line_item_kind") == "labor":
        return ["total_hours", "daily_rate", "calculated_cost"]
    return []


def _downstream_outputs(computed_fields: list[str]) -> list[str]:
    outputs = []
    if "estimated_cost" in computed_fields or "calculated_cost" in computed_fields:
        outputs.append("estimate_total")
    if "estimated_units" in computed_fields or "estimated_gallons" in computed_fields:
        outputs.append("material_quantity")
    if "total_hours" in computed_fields:
        outputs.append("labor_hours")
    return outputs


def _node_notes(decision_id: str, rows: list[dict[str, Any]]) -> str:
    formula_models = ", ".join(_formula_models_for_rows(rows))
    if decision_id == "insulation_foam_system":
        return "Groups foam rows into one estimator decision for product, area, thickness, yield, sets, and cost."
    if decision_id == "insulation_thermal_barrier":
        return "Groups DC315/thermal barrier rows into one coating decision with area, gallons, waste, and cost."
    if decision_id == "roofing_coating_system":
        return "Groups roof coating coat rows into one coating system decision for product, mils, waste, gallons, and cost."
    if rows and rows[0].get("line_item_kind") == "labor":
        return "Labor task decision using days, crew selection, hourly/daily rate logic, total hours, and cost."
    return f"Decision group built from workbook formula model(s): {formula_models}."


def _build_node(intelligence: dict[str, Any], decision_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    template_type = _safe_text(intelligence.get("template_type"))
    row_numbers = [int(row.get("row_number")) for row in rows if row.get("row_number") is not None]
    selector_options = _selector_options_for_rows(intelligence, set(row_numbers), decision_id)
    input_fields = _unique(_special_input_fields(decision_id) + _fields_from_rows(rows, INPUT_FIELD_HINTS))
    computed_fields = _unique(_special_computed_fields(decision_id, rows) + _fields_from_rows(rows, COMPUTED_FIELD_HINTS))
    dependencies = _dependencies_for_rows(rows)
    return {
        "decision_id": decision_id,
        "template_type": template_type,
        "title": _title_for_node(template_type, rows, decision_id),
        "category": _category_for_rows(template_type, rows, decision_id),
        "rows_controlled": row_numbers,
        "input_fields": input_fields,
        "computed_fields": computed_fields,
        "selector_options": selector_options,
        "formula_models": _formula_models_for_rows(rows),
        "dependencies": dependencies,
        "downstream_outputs": _downstream_outputs(computed_fields),
        "requires_estimator_review": bool(
            not rows
            or any(row.get("template_bucket") in {"misc", "unknown"} for row in rows)
            or any(row.get("formula_model") in {"fixed_cost", "material_cost", "manual_sealant_units_cost"} for row in rows)
        ),
        "notes": _node_notes(decision_id, rows),
    }


def _area_node(intelligence: dict[str, Any]) -> dict[str, Any] | None:
    area_rows = intelligence.get("sq_ft_calculation") or []
    if not area_rows:
        return None
    decision_id = f"{intelligence.get('template_type')}_area_takeoff"
    row_numbers = [int(row.get("row_number")) for row in area_rows if row.get("row_number") is not None]
    dependencies = sorted({dep for row in area_rows for dep in re.findall(r"\$?[A-Z]{1,3}\$?\d+", _safe_text(row.get("total_formula")))})
    return {
        "decision_id": decision_id,
        "template_type": intelligence.get("template_type"),
        "title": "Insulation Area Takeoff",
        "category": "scope_decision",
        "rows_controlled": row_numbers,
        "input_fields": ["description", "height", "width", "wall_rows", "ceiling_rows", "gable_rows"],
        "computed_fields": ["row_area_sqft", "final_total_sqft"],
        "selector_options": [],
        "formula_models": sorted({row.get("model") for row in area_rows if row.get("model")}),
        "dependencies": dependencies,
        "downstream_outputs": ["area_sqft", "basis_sqft", "estimate_total"],
        "requires_estimator_review": False,
        "notes": "Sq Ft Calculation sheet drives wall, ceiling, gable, and final area totals.",
    }


def _group_rows(intelligence: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    template_type = _safe_text(intelligence.get("template_type"))
    material_rows, labor_rows = _rows_for_intelligence(intelligence)
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in material_rows:
        decision_id = _material_decision_id(template_type, row)
        groups.setdefault(decision_id, []).append(row)
    for row in labor_rows:
        decision_id = _labor_decision_id(template_type, row)
        groups.setdefault(decision_id, []).append(row)
    return groups


def _insulation_surface_decision_nodes(template_type: str) -> list[dict[str, Any]]:
    if template_type != "insulation":
        return []
    return [
        {
            "decision_id": "insulation_surface_areas",
            "template_type": "insulation",
            "category": "scope_geometry",
            "label": "Insulation Surface Areas",
            "description": "Surface-specific gross, deduction, and net insulation areas that feed foam quantity rows.",
            "rows_controlled": [],
            "input_fields": [
                "surface_type",
                "gross_area_sqft",
                "deduction_area_sqft",
                "net_area_sqft",
                "area_formula",
                "source_text",
            ],
            "computed_fields": ["net_area_sqft"],
            "dependencies": ["field_notes", "Sq Ft Calculation"],
            "selector_options": [],
        },
        {
            "decision_id": "insulation_deductions",
            "template_type": "insulation",
            "category": "scope_geometry",
            "label": "Insulation Deductions",
            "description": "Opening deductions by type, quantity, width, height, and source evidence.",
            "rows_controlled": [],
            "input_fields": ["opening_type", "quantity", "width_ft", "height_ft"],
            "computed_fields": ["area_each_sqft", "total_area_sqft"],
            "dependencies": ["field_notes"],
            "selector_options": [],
        },
        {
            "decision_id": "insulation_r_value_targets",
            "template_type": "insulation",
            "category": "scope_requirement",
            "label": "Insulation R-Value Targets",
            "description": "Surface-specific target R-values parsed from notes or edited by the estimator.",
            "rows_controlled": [],
            "input_fields": ["surface_type", "target_r_value", "source_text"],
            "computed_fields": [],
            "dependencies": ["field_notes"],
            "selector_options": [],
        },
        {
            "decision_id": "insulation_foam_type",
            "template_type": "insulation",
            "category": "product_selection",
            "label": "Insulation Foam Type",
            "description": "Open-cell vs closed-cell foam selection used for product and R/in defaults.",
            "rows_controlled": [19, 20, 21],
            "input_fields": ["foam_type"],
            "computed_fields": [],
            "dependencies": ["insulation_surface_areas", "insulation_r_value_targets"],
            "selector_options": [],
        },
        {
            "decision_id": "insulation_product_selection",
            "template_type": "insulation",
            "category": "product_selection",
            "label": "Insulation Product Selection",
            "description": "Selected foam product, manufacturer, density, yield, and product-sheet R-value per inch.",
            "rows_controlled": [19, 20, 21],
            "input_fields": ["product_id", "product_name", "manufacturer", "r_value_per_inch", "density", "approved_use"],
            "computed_fields": ["yield_or_coverage"],
            "dependencies": ["insulation_foam_system", "product_knowledge"],
            "selector_options": [],
        },
        {
            "decision_id": "insulation_thickness_calculation",
            "template_type": "insulation",
            "category": "formula_model",
            "label": "Insulation Thickness Calculation",
            "description": "Required thickness by surface: target R-value divided by selected product R-value per inch.",
            "rows_controlled": [19, 20, 21],
            "input_fields": ["surface_type", "target_r_value", "product_r_value_per_inch"],
            "computed_fields": ["required_thickness_inches", "rounded_thickness_inches"],
            "dependencies": ["insulation_surface_areas", "insulation_r_value_targets", "insulation_product_selection"],
            "selector_options": [],
        },
    ]


def build_decision_graph(intelligence: dict[str, Any]) -> dict[str, Any]:
    """Build a normalized estimator decision graph from template intelligence JSON."""
    template_type = _safe_text(intelligence.get("template_type"))
    groups = _group_rows(intelligence)
    nodes = [_build_node(intelligence, decision_id, rows) for decision_id, rows in sorted(groups.items())]
    area_node = _area_node(intelligence)
    if area_node:
        nodes.insert(0, area_node)
    nodes.extend(_insulation_surface_decision_nodes(template_type))
    selector_options = [option for node in nodes for option in node.get("selector_options", [])]
    crew_options = _crew_rate_options(intelligence)
    selector_options.extend(
        {
            "decision_id": row["decision_id"],
            "template_type": row["template_type"],
            "sheet_name": "People",
            "row_number": 12,
            "selector_cell": "crew_size",
            "resolved_cell": row.get("daily_rate_cell"),
            "selector_code": row.get("selector_code"),
            "resolved_item_name": f"{row.get('crew_size')} person crew daily rate",
            "source_type": "people_daily_rate_selector",
            "formula": row.get("daily_rate_formula"),
        }
        for row in crew_options
    )
    return {
        "template_type": template_type,
        "template_name": intelligence.get("template_name"),
        "template_path": intelligence.get("template_path"),
        "source_intelligence_path": intelligence.get("source_intelligence_path"),
        "decision_nodes": nodes,
        "selector_options": _unique(selector_options),
        "formula_models": _formula_summary_rows(intelligence, nodes),
        "row_traceability": _row_traceability_rows(intelligence, nodes),
        "crew_rate_options": crew_options,
        "area_calculation_models": _area_calculation_models(intelligence),
    }


def _formula_summary_rows(intelligence: dict[str, Any], nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    node_by_id = {node["decision_id"]: node for node in nodes}
    rows_by_number: dict[int, list[dict[str, Any]]] = {}
    for row in (intelligence.get("formula_models") or []):
        try:
            rows_by_number.setdefault(int(row.get("row_number")), []).append(row)
        except Exception:
            continue
    for node_id, node in node_by_id.items():
        for row_number in node.get("rows_controlled", []):
            matching = rows_by_number.get(int(row_number), [])
            if matching:
                for formula in matching:
                    rows.append(
                        {
                            "decision_id": node_id,
                            "template_type": intelligence.get("template_type"),
                            "row_number": row_number,
                            "template_bucket": formula.get("template_bucket"),
                            "formula_model": formula.get("formula_model"),
                            "formula_mode": formula.get("formula_mode"),
                            "formula_basis": formula.get("formula_basis") or formula.get("formula"),
                            "formula_cells": formula.get("formula_cells"),
                            "dependencies": formula.get("dependencies") or formula.get("formula_dependencies"),
                        }
                    )
            else:
                for formula_model in node.get("formula_models") or []:
                    rows.append(
                        {
                            "decision_id": node_id,
                            "template_type": intelligence.get("template_type"),
                            "row_number": row_number,
                            "template_bucket": "",
                            "formula_model": formula_model,
                            "formula_mode": "",
                            "formula_basis": "",
                            "formula_cells": "",
                            "dependencies": _json_dumps(node.get("dependencies")),
                        }
                    )
    return rows


def _row_traceability_rows(intelligence: dict[str, Any], nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    source_rows = []
    material_rows, labor_rows = _rows_for_intelligence(intelligence)
    source_rows.extend(material_rows)
    source_rows.extend(labor_rows)
    by_row_number = {int(row.get("row_number")): row for row in source_rows if row.get("row_number") is not None}
    node_for_row: dict[int, str] = {}
    for node in nodes:
        for row_number in node.get("rows_controlled", []):
            node_for_row.setdefault(int(row_number), node["decision_id"])
    rows: list[dict[str, Any]] = []
    for row_number, source in sorted(by_row_number.items()):
        rows.append(
            {
                "decision_id": node_for_row.get(row_number, ""),
                "template_type": intelligence.get("template_type"),
                "sheet_name": source.get("sheet_name"),
                "row_number": row_number,
                "template_bucket": source.get("template_bucket"),
                "line_item_kind": source.get("line_item_kind"),
                "formula_model": source.get("formula_model"),
                "formula_mode": source.get("formula_mode"),
                "cell_roles": source.get("cell_roles"),
                "selector_map": source.get("selector_map"),
                "formula_cells": source.get("formula_cells"),
                "formula_dependencies": source.get("formula_dependencies"),
            }
        )
    for row in intelligence.get("sq_ft_calculation") or []:
        rows.append(
            {
                "decision_id": f"{intelligence.get('template_type')}_area_takeoff",
                "template_type": intelligence.get("template_type"),
                "sheet_name": row.get("sheet_name"),
                "row_number": row.get("row_number"),
                "template_bucket": row.get("area_type"),
                "line_item_kind": "scope",
                "formula_model": row.get("model"),
                "formula_mode": "",
                "cell_roles": _json_dumps(
                    {
                        "description": row.get("description_cell"),
                        "height": row.get("height_cell"),
                        "width": row.get("width_cell"),
                        "total": row.get("total_cell"),
                    }
                ),
                "selector_map": "",
                "formula_cells": row.get("total_formula"),
                "formula_dependencies": "",
            }
        )
    return rows


def _excel_safe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    safe: list[dict[str, Any]] = []
    for row in rows:
        clean: dict[str, Any] = {}
        for key, value in row.items():
            if isinstance(value, (dict, list, tuple, set)):
                clean[key] = _json_dumps(list(value) if isinstance(value, set) else value)
            else:
                clean[key] = value
        safe.append(clean)
    return safe


def decision_graph_frames(graphs: list[dict[str, Any]]) -> dict[str, pd.DataFrame]:
    combined: dict[str, list[dict[str, Any]]] = {sheet: [] for sheet in GRAPH_SHEETS.values()}
    for graph in graphs:
        for key in combined:
            combined[key].extend(_excel_safe_rows(graph.get(key) or []))
    return {sheet_name: pd.DataFrame(rows) for sheet_name, rows in combined.items()}


def write_decision_graph_outputs(
    insulation_graph: dict[str, Any] | None,
    roofing_graph: dict[str, Any] | None,
    out_dir: str | Path = "output",
) -> tuple[list[Path], Path]:
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    json_paths: list[Path] = []
    graphs = []
    for graph in [insulation_graph, roofing_graph]:
        if not graph:
            continue
        graphs.append(graph)
        template_type = graph.get("template_type")
        path = out_path / f"template_decision_graph_{template_type}.json"
        path.write_text(json.dumps(graph, indent=2, default=str, sort_keys=True), encoding="utf-8")
        json_paths.append(path)
    xlsx_path = out_path / "template_decision_graph_summary.xlsx"
    with pd.ExcelWriter(xlsx_path) as writer:
        for sheet_name, frame in decision_graph_frames(graphs).items():
            frame.to_excel(writer, sheet_name=sheet_name[:31], index=False)
    return json_paths, xlsx_path


def build_graphs_from_files(insulation: str | Path | None, roofing: str | Path | None) -> tuple[dict[str, Any], dict[str, Any]]:
    insulation_graph = build_decision_graph(load_template_intelligence(insulation, "insulation"))
    roofing_graph = build_decision_graph(load_template_intelligence(roofing, "roofing"))
    return insulation_graph, roofing_graph


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build estimator decision graphs from template intelligence JSON.")
    parser.add_argument("--insulation", default=None, help="Path to insulation template intelligence JSON.")
    parser.add_argument("--roofing", default=None, help="Path to roofing template intelligence JSON.")
    parser.add_argument("--out-dir", default="output", help="Output directory for graph JSON/XLSX files.")
    args = parser.parse_args(argv)

    insulation_graph, roofing_graph = build_graphs_from_files(args.insulation, args.roofing)
    json_paths, xlsx_path = write_decision_graph_outputs(insulation_graph, roofing_graph, args.out_dir)
    for path in json_paths:
        print(f"Wrote decision graph JSON: {path}")
    print(f"Wrote decision graph summary workbook: {xlsx_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
