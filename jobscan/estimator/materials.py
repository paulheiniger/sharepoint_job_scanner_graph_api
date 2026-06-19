from __future__ import annotations

from typing import Any

import pandas as pd

from .line_items import classify_template_line_item
from .rules import first_nonblank, to_float
from .schemas import EstimatorAssumptions


def classify_line_item(row: dict[str, Any] | pd.Series) -> str:
    section = classify_template_line_item(row).template_section
    return "travel/lodging" if section == "travel" else section


def aggregate_line_items(line_items: pd.DataFrame, similar_job_ids: list[str]) -> dict[str, Any]:
    if line_items.empty or "job_id" not in line_items.columns or not similar_job_ids:
        return {"category_totals": {}, "common_items": []}
    filtered = line_items[line_items["job_id"].astype(str).isin([str(job_id) for job_id in similar_job_ids])].copy()
    if filtered.empty:
        return {"category_totals": {}, "common_items": []}
    filtered["estimate_category"] = filtered.apply(classify_line_item, axis=1)
    filtered["template_bucket"] = filtered.apply(lambda row: classify_template_line_item(row).template_bucket, axis=1)
    filtered["extended_cost"] = pd.to_numeric(filtered.get("extended_cost"), errors="coerce").fillna(0)
    category_totals = filtered.groupby("estimate_category")["extended_cost"].sum().to_dict()
    bucket_totals = filtered.groupby("template_bucket")["extended_cost"].sum().to_dict()
    group_cols = [column for column in ["estimate_category", "template_bucket", "line_item_category", "line_item_name", "unit"] if column in filtered.columns]
    common = (
        filtered.groupby(group_cols, dropna=False, as_index=False)
        .agg(count=("extended_cost", "size"), median_cost=("extended_cost", "median"))
        .sort_values(["count", "median_cost"], ascending=[False, False])
        .head(20)
    )
    return {"category_totals": category_totals, "bucket_totals": bucket_totals, "common_items": common.to_dict(orient="records")}


def coating_wet_mils(coating_type: str, target: Any = None) -> float:
    target_value = to_float(target)
    if target_value:
        return target_value
    key = coating_type.lower()
    if "silicone" in key:
        return 24.0
    if "acrylic" in key:
        return 30.0
    if "urethane" in key:
        return 22.0
    return 24.0


def coating_gallons(surface_area_sqft: float, wet_mils: float, waste_factor: float = 0.12) -> float:
    return surface_area_sqft * wet_mils / 1604.0 * (1 + waste_factor)


def _current_pricing(pricing: pd.DataFrame) -> pd.DataFrame:
    if pricing.empty:
        return pricing
    out = pricing.copy()
    if "is_current" in out.columns:
        out = out[out["is_current"].apply(_truthy)]
    if "status" in out.columns:
        out = out[out["status"].fillna("").astype(str).str.lower().eq("active")]
    if "needs_review" in out.columns:
        out = out[~out["needs_review"].apply(_truthy)]
    return out


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "y"}


def find_current_price(pricing: pd.DataFrame, keywords: list[str], preferred_price_column: str = "price_per_gallon") -> dict[str, Any] | None:
    current = _current_pricing(pricing)
    if current.empty:
        return None
    haystack = current.apply(
        lambda row: " ".join(str(row.get(column) or "") for column in ("product_name", "description", "category", "price_basis")).lower(),
        axis=1,
    )
    mask = pd.Series(True, index=current.index)
    for keyword in keywords:
        if keyword:
            mask &= haystack.str.contains(keyword.lower(), regex=False, na=False)
    candidates = current[mask].copy()
    if candidates.empty:
        return None
    price_column = preferred_price_column if preferred_price_column in candidates.columns else "unit_price"
    candidates[price_column] = pd.to_numeric(candidates[price_column], errors="coerce")
    candidates = candidates[candidates[price_column].notna()]
    if candidates.empty:
        return None
    row = candidates.sort_values(price_column).iloc[0].to_dict()
    row["matched_price"] = float(row.get(price_column))
    row["matched_price_column"] = price_column
    return row


def historical_unit_cost(line_items: pd.DataFrame, keywords: list[str], area_sqft: float | None) -> float | None:
    if line_items.empty:
        return None
    haystack = line_items.apply(
        lambda row: " ".join(str(row.get(column) or "") for column in ("line_item_name", "line_item_category", "description", "section")).lower(),
        axis=1,
    )
    mask = pd.Series(True, index=line_items.index)
    for keyword in keywords:
        mask &= haystack.str.contains(keyword.lower(), regex=False, na=False)
    rows = line_items[mask].copy()
    if rows.empty:
        return None
    for column in ("unit_cost", "unit_price", "extended_cost"):
        if column in rows.columns:
            values = pd.to_numeric(rows[column], errors="coerce").dropna()
            values = values[values > 0]
            if not values.empty:
                if column == "extended_cost" and area_sqft:
                    return float(values.median() / area_sqft)
                return float(values.median())
    return None


def estimate_materials(
    scope: dict[str, Any],
    pricing: pd.DataFrame,
    line_items: pd.DataFrame,
    assumptions: EstimatorAssumptions | None = None,
    decision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    assumptions = assumptions or EstimatorAssumptions()
    area = to_float(scope.get("surface_area_sqft")) or to_float(scope.get("wall_area_sqft")) or 0.0
    coating_type = first_nonblank(scope.get("coating_type"))
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    material_low = 0.0
    material_high = 0.0

    if scope.get("coating_required") and area > 0:
        material_assumptions = (decision or {}).get("material_assumptions") or {}
        wet_mils = coating_wet_mils(coating_type, scope.get("target_wet_mils") or material_assumptions.get("coating_wet_mils"))
        gallons = coating_gallons(area, wet_mils, assumptions.coating_waste_factor)
        price = find_current_price(pricing, [coating_type] if coating_type else ["coating"], "price_per_gallon")
        needs_review = False
        source_type = "current_pricing"
        unit_price = None
        if price:
            unit_price = to_float(price.get("matched_price"))
            product_name = first_nonblank(price.get("product_name"), coating_type or "Roof coating")
        else:
            unit_price = historical_unit_cost(line_items, [coating_type or "coating"], area)
            product_name = coating_type or "Roof coating"
            source_type = "historical_fallback"
            needs_review = True
            warnings.append("No current coating price found; using historical fallback where available.")
        if unit_price:
            low = gallons * unit_price * 0.9
            high = gallons * unit_price * 1.15
            material_low += low
            material_high += high
        else:
            low = high = 0.0
            needs_review = True
            warnings.append("Coating material price is missing.")
        rows.append(
            {
                "item": product_name,
                "quantity": round(gallons, 1),
                "unit": "gal",
                "unit_price": unit_price,
                "cost_low": round(low, 2),
                "cost_high": round(high, 2),
                "price_source_type": source_type,
                "needs_pricing_review": needs_review,
                "notes": f"{wet_mils:g} wet mils, {assumptions.coating_waste_factor:.0%} waste",
            }
        )

    if scope.get("foam_required") and area > 0:
        material_assumptions = (decision or {}).get("material_assumptions") or {}
        thickness = to_float(scope.get("foam_thickness_inches")) or to_float(material_assumptions.get("foam_thickness_inches")) or 1.0
        board_feet = area * thickness
        price = find_current_price(pricing, ["foam"], "price_per_sqft")
        source_type = "current_pricing" if price else "historical_fallback"
        unit_price = to_float(price.get("matched_price")) if price else historical_unit_cost(line_items, ["foam"], area)
        needs_review = not bool(price)
        if needs_review:
            warnings.append("No current foam price found; using historical fallback where available.")
        low = board_feet * unit_price * 0.9 if unit_price else 0.0
        high = board_feet * unit_price * 1.2 if unit_price else 0.0
        material_low += low
        material_high += high
        rows.append(
            {
                "item": first_nonblank(price.get("product_name") if price else "", "Spray foam"),
                "quantity": round(board_feet, 1),
                "unit": "board ft",
                "unit_price": unit_price,
                "cost_low": round(low, 2),
                "cost_high": round(high, 2),
                "price_source_type": source_type,
                "needs_pricing_review": needs_review,
                "notes": f"{thickness:g} inch foam thickness",
            }
        )

    return {
        "material_items": rows,
        "material_cost_low": round(material_low, 2),
        "material_cost_high": round(material_high, 2),
        "pricing_warnings": warnings,
        "needs_pricing_review": any(row.get("needs_pricing_review") for row in rows),
    }
