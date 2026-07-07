from __future__ import annotations

import math
import re
from typing import Any

import pandas as pd


YIELD_MIN = 500.0
YIELD_MAX = 50000.0


def _safe_number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return float(number)


def _positive_number(*values: Any, default: float = 0.0) -> float:
    for value in values:
        number = _safe_number(value, 0.0)
        if number > 0:
            return number
    return default


def _normalized(value: Any) -> str:
    return " ".join(str(value or "").lower().replace("_", " ").replace("-", " ").split())


def _frame(data: Any, attr: str) -> pd.DataFrame:
    value = getattr(data, attr, pd.DataFrame()) if data is not None else pd.DataFrame()
    return value.copy() if isinstance(value, pd.DataFrame) else pd.DataFrame(value)


def infer_foam_type(*values: Any) -> str:
    text = _normalized(" ".join(str(value or "") for value in values))
    if not text:
        return ""
    if "open cell" in text or "open-cell" in text or re.search(r"\b0\.5\s*lb\b", text):
        return "open_cell"
    if (
        "closed cell" in text
        or "closed-cell" in text
        or re.search(r"\b2(?:\.0)?\s*lb\b", text)
        or re.search(r"\b3(?:\.0)?\s*lb\b", text)
    ):
        return "closed_cell"
    return ""


def requested_foam_type(scope: dict[str, Any] | None) -> str:
    scope = scope or {}
    direct = str(scope.get("foam_type") or "").strip().lower().replace("-", "_").replace(" ", "_")
    if direct in {"open_cell", "closed_cell"}:
        return direct
    return infer_foam_type(
        scope.get("notes"),
        scope.get("raw_input_notes"),
        scope.get("project_type"),
        scope.get("scope_of_work"),
        scope.get("building_type"),
    )


def thickness_band(value: Any) -> str:
    thickness = _safe_number(value, 0.0)
    if thickness <= 0:
        return "unknown"
    lower = int(math.floor(thickness / 2.0) * 2)
    upper = lower + 2
    if lower >= 8:
        return "8+"
    return f"{lower}-{upper}"


def _percentile(values: list[float], q: float) -> float:
    series = pd.Series(values, dtype="float64")
    series = series[series.notna() & (series > 0)]
    if series.empty:
        return 0.0
    return round(float(series.quantile(q)), 4)


def _job_count(frame: pd.DataFrame) -> int:
    for column in ("job_id", "document_id", "source_file", "template_file"):
        if column in frame.columns:
            count = int(frame[column].dropna().astype(str).str.strip().replace("", pd.NA).dropna().nunique())
            if count:
                return count
    return int(len(frame))


def _source_rows(data: Any, template_type: str) -> pd.DataFrame:
    rows = _frame(data, "template_rows")
    if rows.empty:
        return rows
    for column in ("template_type", "template_bucket", "line_item_kind"):
        if column not in rows.columns:
            rows[column] = ""
    if template_type:
        scoped = rows[rows["template_type"].map(_normalized).eq(_normalized(template_type))].copy()
        if not scoped.empty:
            rows = scoped
    bucket = rows["template_bucket"].map(_normalized)
    kind = rows["line_item_kind"].map(_normalized)
    foam_rows = rows[bucket.eq("foam") | (bucket.str.contains("foam", na=False) & kind.isin(["material", ""]))].copy()
    if foam_rows.empty and "row_number" in rows.columns:
        row_number = pd.to_numeric(rows["row_number"], errors="coerce")
        foam_rows = rows[row_number.isin([19, 20, 21])].copy()
    return foam_rows


def _record_from_row(row: dict[str, Any]) -> dict[str, Any] | None:
    product_name = next(
        (
            str(row.get(field) or "").strip()
            for field in ("resolved_item_name", "selected_item_name", "product_name", "item_name", "current_item", "row_label")
            if str(row.get(field) or "").strip()
        ),
        "",
    )
    foam_type = str(row.get("foam_type") or "").strip().lower().replace("-", "_").replace(" ", "_")
    if foam_type not in {"open_cell", "closed_cell"}:
        foam_type = infer_foam_type(
            product_name,
            row.get("resolved_item_name"),
            row.get("selected_item_name"),
            row.get("product_name"),
            row.get("row_label"),
            row.get("notes"),
        )
    area = _positive_number(
        row.get("area_sqft"),
        row.get("basis_sqft"),
        row.get("estimated_sqft"),
        row.get("net_sqft"),
        row.get("quantity"),
    )
    thickness = _positive_number(row.get("thickness_inches"), row.get("foam_thickness_inches"))
    units = _positive_number(row.get("estimated_units"), row.get("calculated_quantity"))
    sets = _positive_number(row.get("estimated_sets"))
    if units <= 0 and sets > 0:
        units = sets if sets > 100 else sets * 1000.0
    direct_yield = _positive_number(row.get("yield_or_coverage"), row.get("yield_factor"), row.get("median_foam_yield"))
    realized_yield = (area * thickness * 1000.0 / units) if area > 0 and thickness > 0 and units > 0 else 0.0
    yields = [value for value in (direct_yield, realized_yield) if YIELD_MIN <= value <= YIELD_MAX]
    if not yields:
        return None
    selected_yield = direct_yield if YIELD_MIN <= direct_yield <= YIELD_MAX else realized_yield
    return {
        "foam_type": foam_type or "unknown",
        "template_option": product_name or "Unknown foam",
        "template_option_normalized": _normalized(product_name),
        "thickness_inches": round(thickness, 4) if thickness else 0.0,
        "thickness_band": thickness_band(thickness),
        "yield_or_coverage": round(selected_yield, 4),
        "direct_yield_or_coverage": round(direct_yield, 4) if direct_yield else 0.0,
        "realized_yield_or_coverage": round(realized_yield, 4) if realized_yield else 0.0,
        "area_sqft": round(area, 2) if area else 0.0,
        "estimated_units": round(units, 4) if units else 0.0,
        "unit_price": _positive_number(row.get("unit_price"), row.get("current_unit_price"), row.get("current_price")),
        "job_id": str(row.get("job_id") or row.get("document_id") or row.get("source_file") or "").strip(),
    }


def build_foam_yield_history_digest(
    data: Any,
    *,
    scope: dict[str, Any] | None = None,
    template_type: str = "insulation",
    limit: int = 8,
) -> list[dict[str, Any]]:
    source = _source_rows(data, template_type)
    if source.empty:
        return []
    records = [
        record
        for record in (_record_from_row(row) for row in source.fillna("").to_dict(orient="records"))
        if record is not None
    ]
    if not records:
        return []
    frame = pd.DataFrame(records)
    requested_type = requested_foam_type(scope)
    requested_band = thickness_band((scope or {}).get("foam_thickness_inches") or (scope or {}).get("thickness_inches"))
    groups: list[dict[str, Any]] = []
    group_cols = ["foam_type", "template_option_normalized", "thickness_band"]
    for keys, group in frame.groupby(group_cols, dropna=False):
        foam_type, _, band = keys
        yields = group["yield_or_coverage"].dropna().astype(float).tolist()
        if not yields:
            continue
        label = str(group["template_option"].mode().iloc[0] if not group["template_option"].mode().empty else group["template_option"].iloc[0])
        groups.append(
            {
                "foam_type": str(foam_type or "unknown"),
                "template_option": label,
                "thickness_band": str(band or "unknown"),
                "median_yield_or_coverage": _percentile(yields, 0.5),
                "p25_yield_or_coverage": _percentile(yields, 0.25),
                "p75_yield_or_coverage": _percentile(yields, 0.75),
                "median_thickness_inches": _percentile(group["thickness_inches"].dropna().astype(float).tolist(), 0.5),
                "median_unit_price": _percentile(group["unit_price"].dropna().astype(float).tolist(), 0.5),
                "evidence_count": int(len(group)),
                "source_jobs_count": _job_count(group),
                "examples": group[["template_option", "thickness_inches", "yield_or_coverage", "job_id"]].head(3).to_dict(orient="records"),
                "match_score": _yield_group_match_score(
                    foam_type=str(foam_type or ""),
                    template_option=label,
                    thickness_band_value=str(band or ""),
                    requested_type=requested_type,
                    requested_band=requested_band,
                    scope=scope or {},
                    evidence_count=int(len(group)),
                    source_jobs_count=_job_count(group),
                ),
            }
        )
    groups.sort(key=lambda item: (item["match_score"], item["source_jobs_count"], item["evidence_count"]), reverse=True)
    return groups[:limit]


def _yield_group_match_score(
    *,
    foam_type: str,
    template_option: str,
    thickness_band_value: str,
    requested_type: str,
    requested_band: str,
    scope: dict[str, Any],
    evidence_count: int,
    source_jobs_count: int,
) -> float:
    score = min(evidence_count, 20) + min(source_jobs_count, 20)
    if requested_type and foam_type == requested_type:
        score += 60
    elif requested_type and foam_type not in {"", "unknown", requested_type}:
        score -= 100
    if requested_band != "unknown" and thickness_band_value == requested_band:
        score += 35
    elif requested_band != "unknown" and thickness_band_value != "unknown":
        score -= 10
    scope_text = _normalized(
        " ".join(
            str(scope.get(key) or "")
            for key in ("resolved_template_option", "selected_pricing_candidate", "foam_product", "notes", "raw_input_notes")
        )
    )
    option_text = _normalized(template_option)
    if option_text and scope_text and (option_text in scope_text or scope_text in option_text):
        score += 35
    return score


def best_foam_yield_history(
    data: Any,
    *,
    scope: dict[str, Any] | None = None,
    template_type: str = "insulation",
) -> dict[str, Any]:
    digest = build_foam_yield_history_digest(data, scope=scope, template_type=template_type, limit=1)
    return digest[0] if digest else {}
