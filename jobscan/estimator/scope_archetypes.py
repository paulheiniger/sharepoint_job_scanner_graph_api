from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

import pandas as pd


NON_DECISION_BUCKETS = {
    "",
    "unknown",
    "job_name",
    "job_type",
    "site_address",
    "city_state_zip",
    "contact",
    "estimate_date",
    "email",
    "phone",
    "estimated_square_feet",
    "total_job_cost",
    "subtotal_materials",
    "labor_subtotal",
    "overhead",
    "profit",
    "sales_tax",
    "worksheet_price",
    "worksheet_price_adjusted",
    "price_per_sqft_estimated_sets",
    "warranty",
}

NON_DECISION_KINDS = {
    "header",
    "metadata",
    "pricing",
    "summary",
    "subtotal",
    "total",
}

EXPLICIT_STATE_FIELDS = (
    "included",
    "include",
    "is_included",
    "included_in_total",
    "selected",
    "is_selected",
    "applies",
)

MATERIAL_USAGE_FIELDS = (
    "estimated_units",
    "estimated_sets",
    "estimated_gallons",
    "estimated_cost",
    "calculated_cost",
    "linear_ft",
)

LABOR_USAGE_FIELDS = (
    "total_hours",
    "days",
    "calculated_cost",
    "estimated_cost",
)

EQUIPMENT_USAGE_FIELDS = (
    "trips",
    "days",
    "estimated_units",
    "estimated_cost",
    "calculated_cost",
)

PHYSICAL_QUANTITY_UNITS = {
    "bag",
    "bags",
    "board",
    "boards",
    "case",
    "cases",
    "drum",
    "drums",
    "ea",
    "each",
    "gal",
    "gallon",
    "gallons",
    "lf",
    "linear feet",
    "linear ft",
    "pail",
    "pails",
    "roll",
    "rolls",
    "set",
    "sets",
    "unit",
    "units",
}

FINAL_STATUS_TERMS = (
    "accepted",
    "approved",
    "completed",
    "contract",
    "final",
    "sent",
    "sold",
    "won",
)

BASE_SYSTEM_PACKAGES = {
    "coating",
    "floor_base_coat",
    "floor_coating",
    "floor_flake",
    "floor_primer",
    "floor_topcoat",
    "floor_top_coat",
    "foam",
    "membrane",
    "thermal_barrier",
    "thermal_barrier_coating",
}

SCOPE_MODIFIER_PACKAGES = {
    "board_stock",
    "caulk_detail",
    "caulk_sealant",
    "downspouts",
    "edge_metal",
    "fabric",
    "fastener_treatment",
    "fasteners",
    "granules",
    "gutter",
    "plates",
    "primer",
    "seam_treatment",
    "seams_misc",
    "thinner",
}

LOGISTICS_PACKAGES = {
    "abaa_fee",
    "delivery_fee",
    "drum_disposal",
    "dumpster",
    "dumpsters",
    "estimate_adder",
    "freight",
    "generator",
    "infrared_scan",
    "lift",
    "meals_lodging",
    "misc_insurance",
    "sales_inspection_trips",
    "space_heater",
    "truck_expense",
}


@dataclass(frozen=True)
class ArchetypeAnalysisConfig:
    min_support_count: int = 3
    min_archetype_jobs: int = 3
    jaccard_threshold: float = 0.58
    core_decision_rate: float = 0.8
    typical_decision_rate: float = 0.5
    negative_lift_threshold: float = 0.5
    max_unknown_decision_rate: float = 0.25
    max_review_examples: int = 5


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _norm(value: Any) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", _text(value).lower())).strip("_")


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _positive(value: Any) -> bool:
    number = _number(value)
    return number is not None and number > 0


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return bool(value)
    normalized = _norm(value)
    if normalized in {"true", "yes", "y", "1", "include", "included", "selected", "applies"}:
        return True
    if normalized in {"false", "no", "n", "0", "exclude", "excluded", "not_selected", "not_applicable"}:
        return False
    return None


def _stable_id(prefix: str, *parts: Any) -> str:
    payload = "||".join(_text(part) for part in parts)
    return f"{prefix}-{hashlib.sha1(payload.encode('utf-8')).hexdigest()[:20]}"


def _mode(values: Iterable[Any]) -> str:
    cleaned = [_text(value) for value in values if _text(value)]
    if not cleaned:
        return ""
    counts = pd.Series(cleaned).value_counts()
    return str(counts.index[0])


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if not _text(value):
        return []
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def observation_identity(row: dict[str, Any]) -> tuple[str, str]:
    for field in ("document_id", "source_file", "estimate_id", "job_id"):
        value = _text(row.get(field))
        if value:
            return value, field
    fallback = _stable_id(
        "unresolved-observation",
        row.get("template_type"),
        row.get("sheet_name"),
        row.get("row_number"),
        row.get("template_row_id"),
    )
    return fallback, "generated"


def canonical_package(row: dict[str, Any]) -> str:
    bucket = _norm(row.get("template_bucket"))
    if bucket:
        return bucket
    kind = _norm(row.get("line_item_kind"))
    return kind if kind not in NON_DECISION_KINDS else ""


def decision_key(row: dict[str, Any]) -> str:
    package = canonical_package(row)
    row_number = _number(row.get("row_number") or row.get("workbook_row"))
    if row_number is None:
        return package
    return f"{package}@row_{int(row_number)}"


def decision_role(package: Any) -> str:
    normalized = _norm(package)
    if normalized in BASE_SYSTEM_PACKAGES:
        return "base_system"
    if normalized in SCOPE_MODIFIER_PACKAGES:
        return "scope_modifier"
    if normalized.startswith("labor_"):
        return "execution"
    if normalized in LOGISTICS_PACKAGES:
        return "logistics"
    return "other"


def _explicit_row_state(row: dict[str, Any]) -> tuple[bool | None, str]:
    for field in EXPLICIT_STATE_FIELDS:
        if field not in row:
            continue
        state = _as_bool(row.get(field))
        if state is not None:
            return state, f"explicit_{field}"
    return None, ""


def _usage_fields_for_row(row: dict[str, Any]) -> tuple[str, ...]:
    kind = _norm(row.get("line_item_kind"))
    package = canonical_package(row)
    if kind == "labor" or package.startswith("labor_"):
        return LABOR_USAGE_FIELDS
    if kind in {"adder", "equipment", "travel"} or package in {
        "delivery_fee",
        "drum_disposal",
        "dumpster",
        "dumpsters",
        "freight",
        "generator",
        "lift",
        "meals_lodging",
        "sales_inspection_trips",
        "space_heater",
        "truck_expense",
    }:
        return EQUIPMENT_USAGE_FIELDS
    return MATERIAL_USAGE_FIELDS


def classify_template_row(row: dict[str, Any]) -> dict[str, Any]:
    package = canonical_package(row)
    kind = _norm(row.get("line_item_kind"))
    result = {
        "package": package,
        "decision_key": decision_key(row),
        "decision_role": decision_role(package),
        "row_state": "unknown",
        "state_confidence": 0.35,
        "state_reason": "unclassified",
        "positive_evidence_fields": [],
    }

    if package in NON_DECISION_BUCKETS or kind in NON_DECISION_KINDS:
        result.update(
            row_state="not_applicable",
            state_confidence=0.99,
            state_reason="workbook_metadata_or_summary",
        )
        return result

    if not package:
        result.update(
            row_state="unknown",
            state_confidence=0.2,
            state_reason="missing_decision_package",
        )
        return result

    explicit_state, explicit_reason = _explicit_row_state(row)
    if explicit_state is False:
        result.update(
            row_state="excluded",
            state_confidence=0.99,
            state_reason=explicit_reason,
        )
        return result

    usage_fields = [field for field in _usage_fields_for_row(row) if _positive(row.get(field))]

    unit = _norm(row.get("unit")).replace("_", " ")
    if _positive(row.get("quantity")) and unit in PHYSICAL_QUANTITY_UNITS:
        usage_fields.append("quantity")
    configured_area = _positive(row.get("area_sqft")) or (
        _positive(row.get("quantity"))
        and unit in {"sq ft", "sqft", "sf", "square feet"}
    )
    has_application_input = any(
        _positive(row.get(field))
        for field in (
            "thickness_inches",
            "yield_or_coverage",
            "yield_factor",
            "gal_per_100_sqft",
            "gal_per_sqft",
            "ft_per_unit",
        )
    )
    if (
        configured_area
        and has_application_input
        and (_positive(row.get("selector_code")) or _text(row.get("resolved_item_name")))
    ):
        usage_fields.append("configured_scope_basis")

    usage_fields = list(dict.fromkeys(usage_fields))
    result["positive_evidence_fields"] = usage_fields
    if explicit_state is True:
        result.update(
            row_state="included",
            state_confidence=0.99,
            state_reason=explicit_reason,
        )
        return result
    if usage_fields:
        result.update(
            row_state="included",
            state_confidence=0.95,
            state_reason="positive_workbook_usage:" + ",".join(usage_fields),
        )
        return result

    selected_name = _text(row.get("resolved_item_name") or row.get("selected_item_name"))
    has_price_only = _positive(row.get("unit_price")) and not any(
        _positive(row.get(field))
        for field in (
            "estimated_cost",
            "calculated_cost",
            "estimated_units",
            "estimated_sets",
            "estimated_gallons",
            "total_hours",
            "days",
            "trips",
            "linear_ft",
        )
    )
    if has_price_only or (
        _positive(row.get("selector_code"))
        and not usage_fields
        and not configured_area
    ):
        result.update(
            row_state="excluded",
            state_confidence=0.9,
            state_reason=(
                "price_or_selector_only_template_default"
                if _positive(row.get("selector_code"))
                else "price_only_template_default"
            ),
        )
        return result
    if selected_name and kind not in {"material", "labor", "equipment", "travel", "adder"}:
        result.update(
            row_state="unknown",
            state_confidence=0.4,
            state_reason="named_row_without_usage_or_decision_kind",
        )
        return result

    result.update(
        row_state="excluded",
        state_confidence=0.85,
        state_reason="recognized_decision_without_positive_usage",
    )
    return result


def classify_template_rows(template_rows: pd.DataFrame) -> pd.DataFrame:
    if template_rows is None or template_rows.empty:
        return pd.DataFrame()
    records: list[dict[str, Any]] = []
    for source_index, row in template_rows.iterrows():
        source = row.to_dict()
        identity, identity_source = observation_identity(source)
        classification = classify_template_row(source)
        records.append(
            {
                **source,
                "source_index": source_index,
                "observation_key": identity,
                "observation_identity_source": identity_source,
                **classification,
                "positive_evidence_fields_json": json.dumps(
                    classification["positive_evidence_fields"],
                    sort_keys=True,
                ),
            }
        )
    classified = pd.DataFrame(records)
    classified["observation_id"] = classified.apply(
        lambda row: _stable_id(
            "estimate-observation",
            row.get("observation_identity_source"),
            row.get("observation_key"),
        ),
        axis=1,
    )
    return classified


def _frame(data: Any, attr: str) -> pd.DataFrame:
    value = getattr(data, attr, pd.DataFrame()) if data is not None else pd.DataFrame()
    return value.copy() if isinstance(value, pd.DataFrame) else pd.DataFrame(value)


def _lookup_by_job(frame: pd.DataFrame) -> dict[str, dict[str, Any]]:
    if frame.empty or "job_id" not in frame.columns:
        return {}
    rows: dict[str, dict[str, Any]] = {}
    for job_id, group in frame.groupby(frame["job_id"].fillna("").astype(str), dropna=False):
        key = _text(job_id)
        if not key:
            continue
        rows[key] = group.fillna("").iloc[0].to_dict()
    return rows


def _scope_lookup(frame: pd.DataFrame) -> dict[str, str]:
    if frame.empty or "job_id" not in frame.columns:
        return {}
    lookup: dict[str, str] = {}
    for job_id, group in frame.groupby(frame["job_id"].fillna("").astype(str), dropna=False):
        texts = [
            _text(value)
            for value in group.get("scope_text", pd.Series(dtype=object)).tolist()
            if _text(value)
        ]
        if texts:
            lookup[_text(job_id)] = " ".join(" ".join(text.split()) for text in texts)[:2000]
    return lookup


def _estimate_matches(estimates: pd.DataFrame, job_id: str, source_file: str, document_id: str) -> pd.DataFrame:
    if estimates.empty:
        return estimates
    matched = estimates.copy()
    masks: list[pd.Series] = []
    if document_id:
        for column in ("document_id", "source_document_id"):
            if column in matched.columns:
                masks.append(matched[column].fillna("").astype(str).str.strip().eq(document_id))
    if source_file:
        source_name = source_file.lower()
        for column in ("source_file", "estimate_file", "source_path", "file_name"):
            if column in matched.columns:
                values = matched[column].fillna("").astype(str).str.strip().str.lower()
                masks.append(values.eq(source_name) | values.str.endswith(f"/{source_name}"))
    if masks:
        mask = masks[0]
        for extra in masks[1:]:
            mask = mask | extra
        matched = matched[mask]
    elif job_id and "job_id" in matched.columns:
        matched = matched[matched["job_id"].fillna("").astype(str).str.strip().eq(job_id)]
    else:
        return matched.iloc[0:0].copy()
    return matched


def _estimate_context(estimates: pd.DataFrame, job_id: str, source_file: str, document_id: str) -> dict[str, Any]:
    matched = _estimate_matches(estimates, job_id, source_file, document_id)
    if matched.empty:
        return {}
    context: dict[str, Any] = {}
    for target, candidates in {
        "estimate_status": ("status", "pipeline_status", "estimate_status"),
        "project_type": ("project_type", "job_type"),
        "substrate": ("substrate", "roof_type"),
        "area_sqft": ("estimated_sqft", "area_sqft", "surface_area_sqft"),
        "source_year": ("source_year",),
        "estimator": (
            "estimator",
            "estimator_name",
            "created_by",
            "prepared_by",
            "sales_person",
            "salesperson",
        ),
        "estimate_file_modified_by": ("estimate_file_modified_by",),
    }.items():
        for column in candidates:
            if column not in matched.columns:
                continue
            values = [_text(value) for value in matched[column].tolist() if _text(value)]
            if values:
                context[target] = _mode(values)
                break
    for column in (
        "updated_at",
        "modified_at",
        "source_modified_at",
        "estimate_date",
        "created_at",
    ):
        if column not in matched.columns:
            continue
        dates = pd.to_datetime(matched[column], errors="coerce", utc=True).dropna()
        if not dates.empty:
            context["revision_timestamp"] = dates.max().isoformat()
            break
    return context


def build_estimate_observations(
    data: Any,
    *,
    config: ArchetypeAnalysisConfig | None = None,
    classified_rows: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    config = config or ArchetypeAnalysisConfig()
    classified = (
        classified_rows.copy()
        if isinstance(classified_rows, pd.DataFrame)
        else classify_template_rows(_frame(data, "template_rows"))
    )
    if classified.empty:
        return pd.DataFrame(), classified
    classified = classified.drop(
        columns=[
            "training_selected",
            "revision_selection_reason",
            "revision_review_required",
        ],
        errors="ignore",
    )

    estimates = _frame(data, "estimates")
    profiles = _lookup_by_job(_frame(data, "job_context_profiles"))
    jobs = _lookup_by_job(_frame(data, "jobs"))
    scope_by_job = _scope_lookup(_frame(data, "historical_scope_texts"))
    observations: list[dict[str, Any]] = []

    for observation_id, group in classified.groupby("observation_id", dropna=False):
        first = group.iloc[0].to_dict()
        job_id = _text(first.get("job_id"))
        source_file = _text(first.get("source_file"))
        document_id = _text(first.get("document_id"))
        estimate_context = _estimate_context(estimates, job_id, source_file, document_id)
        profile = profiles.get(job_id, {})
        job = jobs.get(job_id, {})
        state_counts = group["row_state"].value_counts().to_dict()
        included = group[group["row_state"].eq("included")]
        decisions = sorted(set(included["decision_key"].dropna().astype(str)) - {""})
        clustering_decisions = sorted(
            set(
                included[
                    included["decision_role"].isin({"base_system", "scope_modifier"})
                ]["decision_key"]
                .dropna()
                .astype(str)
            )
            - {""}
        )
        unknown_count = int(state_counts.get("unknown", 0))
        decision_count = len(decisions)
        unknown_rate = unknown_count / max(decision_count + unknown_count, 1)
        template_type = _mode(group.get("template_type", pd.Series(dtype=object)).tolist())
        area_values = pd.to_numeric(group.get("area_sqft", pd.Series(dtype=float)), errors="coerce")
        area_values = area_values[area_values > 0]
        area = _number(estimate_context.get("area_sqft"))
        if area is None and not area_values.empty:
            area = float(area_values.max())
        observations.append(
            {
                "observation_id": observation_id,
                "observation_key": first.get("observation_key"),
                "observation_identity_source": first.get("observation_identity_source"),
                "job_id": job_id,
                "document_id": document_id,
                "source_file": source_file,
                "template_type": template_type,
                "project_type": _text(
                    estimate_context.get("project_type")
                    or profile.get("project_class")
                    or job.get("project_type")
                    or job.get("job_type")
                ),
                "substrate": _text(
                    estimate_context.get("substrate")
                    or profile.get("substrate")
                    or job.get("substrate")
                ),
                "building_type": _text(profile.get("building_type") or job.get("building_type")),
                "market_segment": _text(profile.get("market_segment")),
                "area_sqft": area,
                "estimate_status": _text(
                    estimate_context.get("estimate_status")
                    or job.get("status")
                    or job.get("pipeline_status")
                ),
                "source_year": _text(estimate_context.get("source_year") or job.get("source_year")),
                "revision_timestamp": _text(estimate_context.get("revision_timestamp")),
                "estimator": _text(
                    estimate_context.get("estimator")
                    or estimate_context.get("estimate_file_modified_by")
                    or job.get("estimator")
                    or job.get("estimator_name")
                    or job.get("sales_person")
                    or job.get("salesperson")
                    or job.get("deal_owner")
                ),
                "estimator_identity_source": (
                    "declared_estimator"
                    if _text(estimate_context.get("estimator"))
                    else "estimate_file_modified_by"
                    if _text(estimate_context.get("estimate_file_modified_by"))
                    else ""
                ),
                "scope_excerpt": _text(scope_by_job.get(job_id) or profile.get("scope_summary"))[:2000],
                "included_decision_count": decision_count,
                "excluded_decision_count": int(state_counts.get("excluded", 0)),
                "unknown_decision_count": unknown_count,
                "not_applicable_row_count": int(state_counts.get("not_applicable", 0)),
                "unknown_decision_rate": round(unknown_rate, 6),
                "included_decisions_json": json.dumps(decisions, sort_keys=True),
                "clustering_decisions_json": json.dumps(
                    clustering_decisions,
                    sort_keys=True,
                ),
                "training_eligible": bool(
                    decision_count > 0
                    and clustering_decisions
                    and unknown_rate <= config.max_unknown_decision_rate
                    and template_type in {"roofing", "insulation", "flooring"}
                ),
            }
        )

    observation_frame = pd.DataFrame(observations)
    observation_frame = select_training_revisions(observation_frame)
    classified = classified.merge(
        observation_frame[
            [
                "observation_id",
                "training_selected",
                "revision_selection_reason",
                "revision_review_required",
            ]
        ],
        on="observation_id",
        how="left",
    )
    return observation_frame, classified


def _final_status(value: Any) -> bool:
    normalized = _norm(value)
    return any(term in normalized for term in FINAL_STATUS_TERMS)


def select_training_revisions(observations: pd.DataFrame) -> pd.DataFrame:
    if observations.empty:
        return observations.copy()
    out = observations.copy()
    out["training_selected"] = False
    out["revision_selection_reason"] = "not_eligible"
    out["revision_review_required"] = False
    group_key = out["job_id"].fillna("").astype(str)
    empty_job = group_key.str.strip().eq("")
    group_key = group_key.where(~empty_job, out["observation_id"].astype(str))
    out["_revision_group"] = group_key

    for _, group in out.groupby("_revision_group", dropna=False):
        eligible = group[group["training_eligible"].astype(bool)].copy()
        if eligible.empty:
            continue
        if len(eligible) == 1:
            selected_index = eligible.index[0]
            out.loc[selected_index, "training_selected"] = True
            out.loc[selected_index, "revision_selection_reason"] = "only_eligible_revision"
            continue

        final = eligible[eligible["estimate_status"].map(_final_status)].copy()
        candidates = final if not final.empty else eligible
        timestamps = pd.to_datetime(candidates["revision_timestamp"], errors="coerce", utc=True)
        if timestamps.notna().any():
            selected_index = timestamps.idxmax()
            reason = "latest_final_revision" if not final.empty else "latest_available_revision"
            review_required = final.empty
        else:
            candidates = candidates.sort_values(
                ["included_decision_count", "source_file", "observation_id"],
                ascending=[False, True, True],
            )
            selected_index = candidates.index[0]
            reason = "ambiguous_revision_fallback"
            review_required = True
        out.loc[selected_index, "training_selected"] = True
        out.loc[selected_index, "revision_selection_reason"] = reason
        out.loc[selected_index, "revision_review_required"] = review_required
        unselected = eligible.index.difference([selected_index])
        out.loc[unselected, "revision_selection_reason"] = "superseded_or_alternate_revision"

    return out.drop(columns=["_revision_group"])


def build_decision_matrix(observations: pd.DataFrame) -> pd.DataFrame:
    selected = observations[observations.get("training_selected", False).astype(bool)].copy()
    if selected.empty:
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    all_decisions: set[str] = set()
    decisions_by_observation: dict[str, set[str]] = {}
    for row in selected.to_dict(orient="records"):
        decisions = {str(value) for value in _json_list(row.get("included_decisions_json")) if _text(value)}
        decisions_by_observation[str(row["observation_id"])] = decisions
        all_decisions.update(decisions)
    for row in selected.to_dict(orient="records"):
        observation_id = str(row["observation_id"])
        record = {
            "observation_id": observation_id,
            "job_id": row.get("job_id"),
            "document_id": row.get("document_id"),
            "source_file": row.get("source_file"),
            "template_type": row.get("template_type"),
        }
        record.update(
            {
                decision: int(decision in decisions_by_observation[observation_id])
                for decision in sorted(all_decisions)
            }
        )
        rows.append(record)
    return pd.DataFrame(rows)


def _wilson_lower_bound(successes: int, trials: int, z: float = 1.96) -> float:
    if trials <= 0:
        return 0.0
    proportion = successes / trials
    denominator = 1 + z**2 / trials
    centre = proportion + z**2 / (2 * trials)
    adjustment = z * math.sqrt((proportion * (1 - proportion) + z**2 / (4 * trials)) / trials)
    return max(0.0, (centre - adjustment) / denominator)


def mine_association_rules(
    observations: pd.DataFrame,
    *,
    config: ArchetypeAnalysisConfig | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    config = config or ArchetypeAnalysisConfig()
    selected = observations[observations.get("training_selected", False).astype(bool)].copy()
    if selected.empty:
        return pd.DataFrame(), pd.DataFrame()
    positive_rows: list[dict[str, Any]] = []
    negative_rows: list[dict[str, Any]] = []

    segments: list[tuple[str, str, pd.DataFrame]] = [("all", "all", selected)]
    for template_type, group in selected.groupby("template_type", dropna=False):
        segments.append(("template_type", _text(template_type) or "unknown", group))

    for segment_type, segment_value, segment in segments:
        decision_sets = [
            {str(value) for value in _json_list(value) if _text(value)}
            for value in segment["included_decisions_json"].tolist()
        ]
        decision_universe = sorted(set().union(*decision_sets) if decision_sets else set())
        total = len(decision_sets)
        counts = {
            decision: sum(decision in decisions for decisions in decision_sets)
            for decision in decision_universe
        }
        for antecedent in decision_universe:
            antecedent_count = counts[antecedent]
            if antecedent_count < config.min_support_count:
                continue
            for consequent in decision_universe:
                if antecedent == consequent:
                    continue
                consequent_count = counts[consequent]
                if consequent_count < config.min_support_count:
                    continue
                joint = sum(
                    antecedent in decisions and consequent in decisions
                    for decisions in decision_sets
                )
                confidence = joint / antecedent_count
                baseline = consequent_count / total if total else 0.0
                lift = confidence / baseline if baseline else 0.0
                record = {
                    "segment_type": segment_type,
                    "segment_value": segment_value,
                    "antecedent": antecedent,
                    "consequent": consequent,
                    "antecedent_role": decision_role(antecedent.split("@", 1)[0]),
                    "consequent_role": decision_role(consequent.split("@", 1)[0]),
                    "observation_count": total,
                    "antecedent_count": antecedent_count,
                    "consequent_count": consequent_count,
                    "support_count": joint,
                    "support": round(joint / total, 6) if total else 0.0,
                    "confidence": round(confidence, 6),
                    "confidence_wilson_lower": round(
                        _wilson_lower_bound(joint, antecedent_count),
                        6,
                    ),
                    "consequent_baseline_rate": round(baseline, 6),
                    "lift": round(lift, 6),
                    "leverage": round((joint / total) - (antecedent_count / total) * baseline, 6)
                    if total
                    else 0.0,
                }
                if joint >= config.min_support_count and lift >= 1.0:
                    positive_rows.append({**record, "association_direction": "positive"})
                elif lift <= config.negative_lift_threshold:
                    negative_rows.append({**record, "association_direction": "negative"})

    sort_columns = ["confidence_wilson_lower", "lift", "support_count"]
    positives = pd.DataFrame(positive_rows)
    negatives = pd.DataFrame(negative_rows)
    if not positives.empty:
        positives = positives.sort_values(sort_columns, ascending=False).reset_index(drop=True)
    if not negatives.empty:
        negatives = negatives.sort_values(
            ["lift", "antecedent_count", "consequent_count"],
            ascending=[True, False, False],
        ).reset_index(drop=True)
    return positives, negatives


def select_rule_candidates(
    rules: pd.DataFrame,
    *,
    min_support_count: int = 5,
    min_wilson_confidence: float = 0.7,
    min_lift: float = 1.2,
) -> pd.DataFrame:
    if rules.empty:
        return rules.copy()
    candidates = rules[
        rules["segment_type"].eq("template_type")
        & (pd.to_numeric(rules["support_count"], errors="coerce") >= min_support_count)
        & (
            pd.to_numeric(rules["confidence_wilson_lower"], errors="coerce")
            >= min_wilson_confidence
        )
        & (pd.to_numeric(rules["lift"], errors="coerce") >= min_lift)
        & ~rules["consequent_role"].eq("other")
    ].copy()
    candidates["review_status"] = "candidate_unreviewed"
    candidates["activation_status"] = "offline_only"
    return candidates.reset_index(drop=True)


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    if not union:
        return 1.0
    return len(left & right) / len(union)


def _cluster_medoid(members: list[tuple[str, set[str]]]) -> tuple[str, set[str]]:
    if len(members) == 1:
        return members[0]
    scored = []
    for observation_id, decisions in members:
        mean_similarity = sum(
            _jaccard(decisions, other_decisions)
            for _, other_decisions in members
        ) / len(members)
        scored.append((mean_similarity, observation_id, decisions))
    scored.sort(key=lambda value: (-value[0], value[1]))
    return scored[0][1], scored[0][2]


def build_candidate_archetypes(
    observations: pd.DataFrame,
    *,
    config: ArchetypeAnalysisConfig | None = None,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    config = config or ArchetypeAnalysisConfig()
    selected = observations[observations.get("training_selected", False).astype(bool)].copy()
    if selected.empty:
        return pd.DataFrame(), []

    archetypes: list[dict[str, Any]] = []
    review_packets: list[dict[str, Any]] = []
    observation_lookup = {
        str(row["observation_id"]): row
        for row in selected.to_dict(orient="records")
    }

    for template_type, group in selected.groupby("template_type", dropna=False):
        members = [
            (
                str(row["observation_id"]),
                {
                    str(value)
                    for value in _json_list(row["clustering_decisions_json"])
                    if _text(value)
                },
            )
            for row in group.to_dict(orient="records")
        ]
        members.sort(key=lambda value: (-len(value[1]), value[0]))
        clusters: list[list[tuple[str, set[str]]]] = []
        medoids: list[tuple[str, set[str]]] = []
        for member in members:
            similarities = [
                _jaccard(member[1], medoid[1])
                for medoid in medoids
            ]
            if similarities and max(similarities) >= config.jaccard_threshold:
                cluster_index = max(
                    range(len(similarities)),
                    key=lambda index: (similarities[index], -index),
                )
                clusters[cluster_index].append(member)
                medoids[cluster_index] = _cluster_medoid(clusters[cluster_index])
            else:
                clusters.append([member])
                medoids.append(member)

        for cluster_members in clusters:
            if len(cluster_members) < config.min_archetype_jobs:
                continue
            medoid_id, _ = _cluster_medoid(cluster_members)
            all_decisions = sorted(
                set().union(*(decisions for _, decisions in cluster_members))
            )
            decision_rates = {
                decision: sum(decision in decisions for _, decisions in cluster_members)
                / len(cluster_members)
                for decision in all_decisions
            }
            decisions_by_role: dict[str, list[str]] = {
                "base_system": [],
                "scope_modifier": [],
                "execution": [],
                "logistics": [],
                "other": [],
            }
            full_decision_sets = [
                {
                    str(value)
                    for value in _json_list(
                        observation_lookup[observation_id]["included_decisions_json"]
                    )
                    if _text(value)
                }
                for observation_id, _ in cluster_members
            ]
            full_decision_universe = sorted(
                set().union(*full_decision_sets) if full_decision_sets else set()
            )
            full_decision_rates = {
                decision: sum(decision in decisions for decisions in full_decision_sets)
                / len(full_decision_sets)
                for decision in full_decision_universe
            }
            for decision in full_decision_universe:
                role = decision_role(decision.split("@", 1)[0])
                decisions_by_role.setdefault(role, []).append(decision)
            core = sorted(
                decision
                for decision, rate in decision_rates.items()
                if rate >= config.core_decision_rate
            )
            typical = sorted(
                decision
                for decision, rate in decision_rates.items()
                if config.typical_decision_rate <= rate < config.core_decision_rate
            )
            occasional = sorted(
                decision
                for decision, rate in decision_rates.items()
                if rate < config.typical_decision_rate
            )
            member_rows = [
                observation_lookup[observation_id]
                for observation_id, _ in cluster_members
            ]
            project_type = _mode(row.get("project_type") for row in member_rows)
            substrate = _mode(row.get("substrate") for row in member_rows)
            archetype_id = _stable_id(
                "scope-archetype",
                template_type,
                *core,
                *typical,
            )
            label_source = [
                decision
                for decision in (core or typical)
                if decision_role(decision.split("@", 1)[0]) == "base_system"
            ] or core or typical
            label_parts = list(
                dict.fromkeys(
                    decision.split("@", 1)[0].replace("_", " ")
                    for decision in label_source
                )
            )[:3]
            provisional_label = (
                f"{_text(template_type) or 'unknown'}: " + " + ".join(label_parts)
                if label_parts
                else f"{_text(template_type) or 'unknown'}: unlabeled"
            )
            mean_similarity = sum(
                _jaccard(decisions, dict(cluster_members)[medoid_id])
                for _, decisions in cluster_members
            ) / len(cluster_members)
            archetypes.append(
                {
                    "archetype_id": archetype_id,
                    "status": "candidate_unreviewed",
                    "template_type": _text(template_type),
                    "provisional_label": provisional_label,
                    "observation_count": len(cluster_members),
                    "representative_observation_id": medoid_id,
                    "mean_jaccard_to_representative": round(mean_similarity, 6),
                    "project_type_mode": project_type,
                    "substrate_mode": substrate,
                    "core_decisions_json": json.dumps(core, sort_keys=True),
                    "typical_decisions_json": json.dumps(typical, sort_keys=True),
                    "occasional_decisions_json": json.dumps(occasional, sort_keys=True),
                    "decision_rates_json": json.dumps(decision_rates, sort_keys=True),
                    "full_decision_rates_json": json.dumps(
                        full_decision_rates,
                        sort_keys=True,
                    ),
                    "base_system_decisions_json": json.dumps(
                        decisions_by_role["base_system"],
                        sort_keys=True,
                    ),
                    "scope_modifier_decisions_json": json.dumps(
                        decisions_by_role["scope_modifier"],
                        sort_keys=True,
                    ),
                    "execution_decisions_json": json.dumps(
                        decisions_by_role["execution"],
                        sort_keys=True,
                    ),
                    "logistics_decisions_json": json.dumps(
                        decisions_by_role["logistics"],
                        sort_keys=True,
                    ),
                    "member_observation_ids_json": json.dumps(
                        sorted(observation_id for observation_id, _ in cluster_members),
                        sort_keys=True,
                    ),
                }
            )
            ranked_examples = sorted(
                member_rows,
                key=lambda row: (
                    row["observation_id"] != medoid_id,
                    -int(row.get("included_decision_count") or 0),
                    str(row.get("observation_id")),
                ),
            )[: config.max_review_examples]
            review_packets.append(
                {
                    "schema_version": "scope_archetype_review_packet.v1",
                    "archetype_id": archetype_id,
                    "statistical_summary": {
                        "template_type": _text(template_type),
                        "observation_count": len(cluster_members),
                        "project_type_mode": project_type,
                        "substrate_mode": substrate,
                        "core_decisions": core,
                        "typical_decisions": typical,
                        "occasional_decisions": occasional,
                        "decision_rates": decision_rates,
                        "full_decision_rates": full_decision_rates,
                        "decisions_by_role": decisions_by_role,
                    },
                    "representative_estimates": [
                        {
                            "observation_id": row.get("observation_id"),
                            "job_id": row.get("job_id"),
                            "document_id": row.get("document_id"),
                            "source_file": row.get("source_file"),
                            "project_type": row.get("project_type"),
                            "substrate": row.get("substrate"),
                            "area_sqft": row.get("area_sqft"),
                            "scope_excerpt": row.get("scope_excerpt"),
                            "included_decisions": _json_list(
                                row.get("included_decisions_json")
                            ),
                        }
                        for row in ranked_examples
                    ],
                    "requested_ai_review": {
                        "task": "Propose a human-readable scope archetype and conditional modifiers from the statistical evidence.",
                        "return_fields": [
                            "proposed_name",
                            "base_system",
                            "required_signals",
                            "core_decisions",
                            "conditional_modifiers",
                            "likely_exclusions",
                            "ambiguities",
                            "review_confidence",
                        ],
                        "constraints": [
                            "Do not activate estimator rules.",
                            "Do not infer a decision solely because a blank template row exists.",
                            "Explain disagreements between proposal scope text and workbook decisions.",
                        ],
                    },
                }
            )

    archetype_frame = pd.DataFrame(archetypes)
    if not archetype_frame.empty:
        archetype_frame = archetype_frame.sort_values(
            ["template_type", "observation_count", "provisional_label"],
            ascending=[True, False, True],
        ).reset_index(drop=True)
    return archetype_frame, review_packets


def build_analysis_diagnostics(
    observations: pd.DataFrame,
    classified_rows: pd.DataFrame,
    association_rules: pd.DataFrame,
    negative_associations: pd.DataFrame,
    rule_candidates: pd.DataFrame,
    archetypes: pd.DataFrame,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []

    def add(metric: str, value: Any, severity: str = "info") -> None:
        rows.append({"metric": metric, "value": value, "severity": severity})

    add("template_rows", len(classified_rows))
    add("estimate_observations", len(observations))
    add("training_selected_observations", int(observations.get("training_selected", pd.Series(dtype=bool)).sum()))
    add("candidate_archetypes", len(archetypes))
    add("positive_association_rules", len(association_rules))
    add("negative_association_rules", len(negative_associations))
    add("review_candidate_rules", len(rule_candidates))
    if not classified_rows.empty:
        for state, count in classified_rows["row_state"].value_counts().items():
            add(f"row_state:{state}", int(count))
        unknown_rows = classified_rows[classified_rows["row_state"].eq("unknown")]
        add(
            "unknown_row_rate",
            round(len(unknown_rows) / len(classified_rows), 6),
            "warning" if len(unknown_rows) / len(classified_rows) > 0.1 else "info",
        )
        metadata = classified_rows[
            classified_rows["state_reason"].eq("workbook_metadata_or_summary")
        ]
        add("metadata_rows_quarantined", len(metadata))
    if not observations.empty:
        revision_reviews = observations[
            observations.get("revision_review_required", False).astype(bool)
        ]
        add(
            "selected_revisions_requiring_review",
            len(revision_reviews),
            "warning" if not revision_reviews.empty else "info",
        )
        ineligible = observations[~observations["training_eligible"].astype(bool)]
        add(
            "ineligible_observations",
            len(ineligible),
            "warning" if not ineligible.empty else "info",
        )
    return pd.DataFrame(rows)


def analyze_scope_archetypes(
    data: Any,
    *,
    config: ArchetypeAnalysisConfig | None = None,
) -> dict[str, Any]:
    config = config or ArchetypeAnalysisConfig()
    observations, classified_rows = build_estimate_observations(data, config=config)
    matrix = build_decision_matrix(observations)
    association_rules, negative_associations = mine_association_rules(
        observations,
        config=config,
    )
    rule_candidates = select_rule_candidates(
        association_rules,
        min_support_count=max(config.min_support_count, 5),
    )
    archetypes, review_packets = build_candidate_archetypes(
        observations,
        config=config,
    )
    diagnostics = build_analysis_diagnostics(
        observations,
        classified_rows,
        association_rules,
        negative_associations,
        rule_candidates,
        archetypes,
    )
    return {
        "config": asdict(config),
        "classified_rows": classified_rows,
        "observations": observations,
        "decision_matrix": matrix,
        "association_rules": association_rules,
        "negative_associations": negative_associations,
        "rule_candidates": rule_candidates,
        "candidate_archetypes": archetypes,
        "ai_review_packets": review_packets,
        "diagnostics": diagnostics,
    }


def _report_markdown(result: dict[str, Any]) -> str:
    diagnostics = result["diagnostics"]
    archetypes = result["candidate_archetypes"]
    rules = result["rule_candidates"]
    metric_lookup = {
        str(row["metric"]): row["value"]
        for row in diagnostics.to_dict(orient="records")
    }
    lines = [
        "# Estimate Scope Archetype Analysis",
        "",
        "This report is an offline discovery artifact. It does not activate estimator defaults.",
        "",
        "## Corpus",
        "",
        f"- Template rows classified: {metric_lookup.get('template_rows', 0)}",
        f"- Estimate observations: {metric_lookup.get('estimate_observations', 0)}",
        f"- Training revisions selected: {metric_lookup.get('training_selected_observations', 0)}",
        f"- Metadata rows quarantined: {metric_lookup.get('metadata_rows_quarantined', 0)}",
        f"- Selected revisions requiring review: {metric_lookup.get('selected_revisions_requiring_review', 0)}",
        "",
        "## Candidate Archetypes",
        "",
    ]
    if archetypes.empty:
        lines.append("No candidate archetype met the configured support threshold.")
    else:
        for row in archetypes.head(20).to_dict(orient="records"):
            lines.append(
                f"- **{row['provisional_label']}**: {row['observation_count']} observations; "
                f"representative `{row['representative_observation_id']}`."
            )
    lines.extend(["", "## Strongest Directed Associations", ""])
    if rules.empty:
        lines.append("No directed association met the configured support threshold.")
    else:
        for row in rules.head(20).to_dict(orient="records"):
            lines.append(
                f"- `{row['antecedent']}` -> `{row['consequent']}`: "
                f"confidence {row['confidence']:.1%}, lift {row['lift']:.2f}, "
                f"support {row['support_count']}."
            )
    lines.extend(
        [
            "",
            "## Review Boundary",
            "",
            "- Validate ambiguous revision selection before treating the corpus as authoritative.",
            "- Review `ai_review_packets.jsonl`; AI labels are proposals, not active rules.",
            "- Calibrate activation thresholds on temporal and estimator holdouts before runtime integration.",
            "",
        ]
    )
    return "\n".join(lines)


def write_scope_archetype_analysis(
    result: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, Path]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    frames = {
        "row_classifications.csv": result["classified_rows"],
        "estimate_observations.csv": result["observations"],
        "decision_matrix.csv": result["decision_matrix"],
        "association_rules.csv": result["association_rules"],
        "negative_associations.csv": result["negative_associations"],
        "rule_candidates.csv": result["rule_candidates"],
        "candidate_archetypes.csv": result["candidate_archetypes"],
        "diagnostics.csv": result["diagnostics"],
    }
    paths: dict[str, Path] = {}
    for filename, frame in frames.items():
        path = output / filename
        frame.to_csv(path, index=False)
        paths[filename] = path

    review_path = output / "ai_review_packets.jsonl"
    with review_path.open("w", encoding="utf-8") as handle:
        for packet in result["ai_review_packets"]:
            handle.write(json.dumps(packet, default=str, sort_keys=True) + "\n")
    paths[review_path.name] = review_path

    summary = {
        "schema_version": "scope_archetype_analysis.v1",
        "config": result["config"],
        "metrics": {
            str(row["metric"]): row["value"]
            for row in result["diagnostics"].to_dict(orient="records")
        },
        "artifacts": sorted(
            [*frames, review_path.name, "analysis_summary.json", "report.md"]
        ),
        "runtime_activation": False,
    }
    summary_path = output / "analysis_summary.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, default=str, sort_keys=True),
        encoding="utf-8",
    )
    paths[summary_path.name] = summary_path

    report_path = output / "report.md"
    report_path.write_text(_report_markdown(result), encoding="utf-8")
    paths[report_path.name] = report_path
    return paths
