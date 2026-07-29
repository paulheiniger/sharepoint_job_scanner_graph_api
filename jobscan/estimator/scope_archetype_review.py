from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from .model_routing import DEFAULT_ESTIMATOR_MODEL, model_call_metadata
from .scope_archetypes import (
    ArchetypeAnalysisConfig,
    build_analysis_diagnostics,
    build_candidate_archetypes,
    build_decision_matrix,
    build_estimate_observations,
    mine_association_rules,
    select_rule_candidates,
)


REVIEW_SCHEMA_VERSION = "scope_archetype_review.v1"
AI_PROMPT_VERSION = "scope_archetype_label.v1"
ALLOWED_ROW_STATES = {"included", "excluded", "unknown", "not_applicable"}
AI_RESPONSE_FORMAT = {
    "type": "json_schema",
    "name": "scope_archetype_label",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "proposed_name": {"type": "string"},
            "base_system": {"type": "string"},
            "required_signals": {
                "type": "array",
                "items": {"type": "string"},
            },
            "core_decisions": {
                "type": "array",
                "items": {"type": "string"},
            },
            "conditional_modifiers": {
                "type": "array",
                "items": {"type": "string"},
            },
            "likely_exclusions": {
                "type": "array",
                "items": {"type": "string"},
            },
            "ambiguities": {
                "type": "array",
                "items": {"type": "string"},
            },
            "review_confidence": {"type": "number"},
        },
        "required": [
            "proposed_name",
            "base_system",
            "required_signals",
            "core_decisions",
            "conditional_modifiers",
            "likely_exclusions",
            "ambiguities",
            "review_confidence",
        ],
        "additionalProperties": False,
    },
}


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
    if normalized in {"true", "yes", "y", "1", "include", "included"}:
        return True
    if normalized in {"false", "no", "n", "0", "exclude", "excluded"}:
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


def _stable_rank(*values: Any) -> str:
    payload = "|".join(_text(value) for value in values)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _archetype_membership(result: dict[str, Any]) -> dict[str, str]:
    membership: dict[str, str] = {}
    archetypes = result.get("candidate_archetypes", pd.DataFrame())
    if not isinstance(archetypes, pd.DataFrame) or archetypes.empty:
        return membership
    for row in archetypes.to_dict(orient="records"):
        for observation_id in _json_list(row.get("member_observation_ids_json")):
            membership[str(observation_id)] = str(row.get("archetype_id") or "")
    return membership


def _select_row_review_sample(
    rows: pd.DataFrame,
    *,
    excluded_per_estimate: int = 10,
) -> pd.DataFrame:
    selected_groups: list[pd.DataFrame] = []
    for observation_id, group in rows.groupby("observation_id", dropna=False):
        required = group[group["row_state"].isin({"included", "unknown"})]
        excluded = group[group["row_state"].eq("excluded")].copy()
        if not excluded.empty:
            excluded["_review_stratum"] = (
                excluded.get("decision_role", "").fillna("").astype(str)
                + "|"
                + excluded.get("template_bucket", "").fillna("").astype(str)
            )
            excluded["_review_rank"] = excluded.apply(
                lambda row: _stable_rank(
                    "excluded-row-review",
                    observation_id,
                    row.get("template_row_id"),
                    row.get("source_index"),
                ),
                axis=1,
            )
            sampled: list[pd.DataFrame] = []
            strata = [
                stratum.sort_values("_review_rank")
                for _, stratum in excluded.groupby("_review_stratum", dropna=False)
            ]
            while sum(len(frame) for frame in sampled) < excluded_per_estimate and strata:
                remaining = []
                for stratum in strata:
                    if sum(len(frame) for frame in sampled) >= excluded_per_estimate:
                        break
                    sampled.append(stratum.iloc[[0]])
                    if len(stratum) > 1:
                        remaining.append(stratum.iloc[1:])
                strata = remaining
            sampled_excluded = (
                pd.concat(sampled, ignore_index=False)
                if sampled
                else excluded.iloc[0:0]
            )
            sampled_excluded = sampled_excluded.drop(
                columns=["_review_stratum", "_review_rank"],
                errors="ignore",
            )
        else:
            sampled_excluded = excluded
        selected_groups.append(pd.concat([required, sampled_excluded]))
    if not selected_groups:
        return rows.iloc[0:0].copy()
    return pd.concat(selected_groups, ignore_index=False)


def build_stratified_review_queue(
    result: dict[str, Any],
    *,
    target_estimates: int = 90,
    seed: str = "spraytec-scope-review-v1",
) -> dict[str, pd.DataFrame]:
    observations = result.get("observations", pd.DataFrame())
    classified = result.get("classified_rows", pd.DataFrame())
    if not isinstance(observations, pd.DataFrame) or observations.empty:
        return {
            "estimate_review": pd.DataFrame(),
            "row_review": pd.DataFrame(),
            "archetype_review": pd.DataFrame(),
            "rule_review": pd.DataFrame(),
        }

    target = max(1, min(int(target_estimates), len(observations)))
    candidates = observations.copy()
    membership = _archetype_membership(result)
    candidates["archetype_id"] = candidates["observation_id"].astype(str).map(
        membership
    ).fillna("")
    candidates["_priority"] = (
        candidates.get("revision_review_required", False).fillna(False).astype(int) * 8
        + (pd.to_numeric(candidates.get("unknown_decision_count", 0), errors="coerce").fillna(0) > 0).astype(int) * 4
        + candidates["archetype_id"].ne("").astype(int) * 2
        + (~candidates.get("training_eligible", False).fillna(False).astype(bool)).astype(int)
    )
    candidates["_stratum"] = (
        candidates.get("template_type", "").fillna("").astype(str)
        + "|"
        + candidates.get("source_year", "").fillna("").astype(str)
        + "|"
        + candidates["archetype_id"].where(candidates["archetype_id"].ne(""), "none")
    )
    candidates["_rank"] = candidates.apply(
        lambda row: _stable_rank(seed, row.get("observation_id")),
        axis=1,
    )

    selected_indices: list[Any] = []
    strata = [
        group.sort_values(["_priority", "_rank"], ascending=[False, True])
        for _, group in candidates.groupby("_stratum", dropna=False)
    ]
    strata.sort(
        key=lambda group: (
            -int(group["_priority"].max()),
            str(group["_stratum"].iloc[0]),
        )
    )
    while len(selected_indices) < target and strata:
        remaining: list[pd.DataFrame] = []
        for group in strata:
            if len(selected_indices) >= target:
                break
            selected_indices.append(group.index[0])
            if len(group) > 1:
                remaining.append(group.iloc[1:])
        strata = remaining

    selected = candidates.loc[selected_indices].copy()
    selected = selected.sort_values(
        ["_priority", "template_type", "_rank"],
        ascending=[False, True, True],
    )
    selected["review_training_selected"] = ""
    selected["review_complete"] = False
    selected["reviewer_notes"] = ""
    estimate_columns = [
        "observation_id",
        "job_id",
        "document_id",
        "source_file",
        "template_type",
        "source_year",
        "revision_timestamp",
        "estimator",
        "estimator_identity_source",
        "estimate_status",
        "area_sqft",
        "archetype_id",
        "training_eligible",
        "training_selected",
        "revision_selection_reason",
        "revision_review_required",
        "included_decision_count",
        "unknown_decision_count",
        "included_decisions_json",
        "scope_excerpt",
        "review_training_selected",
        "review_complete",
        "reviewer_notes",
    ]
    estimate_review = selected.reindex(columns=estimate_columns).reset_index(drop=True)

    selected_ids = set(estimate_review["observation_id"].astype(str))
    if isinstance(classified, pd.DataFrame) and not classified.empty:
        row_review = classified[
            classified["observation_id"].astype(str).isin(selected_ids)
            & ~classified["row_state"].eq("not_applicable")
        ].copy()
        row_review = _select_row_review_sample(row_review)
        row_review["row_review_key"] = row_review.apply(
            lambda row: _stable_rank(
                row.get("observation_id"),
                row.get("template_row_id"),
                row.get("source_index"),
            )[:20],
            axis=1,
        )
        row_review["review_row_state"] = ""
        row_review["review_complete"] = False
        row_review["reviewer_notes"] = ""
        row_columns = [
            "row_review_key",
            "observation_id",
            "job_id",
            "document_id",
            "source_file",
            "template_type",
            "template_row_id",
            "sheet_name",
            "row_number",
            "template_bucket",
            "line_item_kind",
            "decision_key",
            "decision_role",
            "resolved_item_name",
            "selected_item_name",
            "row_state",
            "state_reason",
            "state_confidence",
            "positive_evidence_fields_json",
            "review_row_state",
            "review_complete",
            "reviewer_notes",
        ]
        row_review = row_review.reindex(columns=row_columns).sort_values(
            ["observation_id", "sheet_name", "row_number", "decision_key"],
            na_position="last",
        )
    else:
        row_review = pd.DataFrame()

    archetypes = result.get("candidate_archetypes", pd.DataFrame())
    archetype_review = (
        archetypes.copy()
        if isinstance(archetypes, pd.DataFrame)
        else pd.DataFrame(archetypes)
    )
    if not archetype_review.empty:
        archetype_review["review_proposed_name"] = ""
        archetype_review["review_approved"] = ""
        archetype_review["review_complete"] = False
        archetype_review["reviewer_notes"] = ""
    return {
        "estimate_review": estimate_review,
        "row_review": row_review.reset_index(drop=True),
        "archetype_review": archetype_review.reset_index(drop=True),
        "rule_review": pd.DataFrame(),
    }


def write_review_workbook(
    review_queue: dict[str, pd.DataFrame],
    path: str | Path,
) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    instructions = pd.DataFrame(
        {
            "instruction": [
                "Review only the editable columns whose names begin with review_.",
                "For revision groups, set review_training_selected to TRUE on exactly one eligible estimate.",
                "For row corrections, use included, excluded, unknown, or not_applicable.",
                "Set review_complete to TRUE only after checking the source estimate.",
                "AI archetype names remain offline until human approval and holdout validation.",
                "Rule review contains only statistically stable candidates; approval still requires business review.",
            ]
        }
    )
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        instructions.to_excel(writer, sheet_name="instructions", index=False)
        for sheet_name in (
            "estimate_review",
            "row_review",
            "archetype_review",
            "rule_review",
        ):
            review_queue.get(sheet_name, pd.DataFrame()).to_excel(
                writer,
                sheet_name=sheet_name,
                index=False,
            )
        workbook = writer.book
        for worksheet in workbook.worksheets:
            worksheet.freeze_panes = "A2"
            worksheet.auto_filter.ref = worksheet.dimensions
            for column_cells in worksheet.columns:
                values = [_text(cell.value) for cell in column_cells[:100]]
                width = min(max(max((len(value) for value in values), default=0) + 2, 12), 60)
                worksheet.column_dimensions[column_cells[0].column_letter].width = width
    return output


def load_review_workbook(path: str | Path) -> dict[str, pd.DataFrame]:
    source = Path(path)
    if source.suffix.lower() in {".xlsx", ".xlsm"}:
        workbook = pd.ExcelFile(source)
        names = (
            "estimate_review",
            "row_review",
            "archetype_review",
            "rule_review",
        )
        return {
            name: (
                pd.read_excel(workbook, sheet_name=name).fillna("")
                if name in workbook.sheet_names
                else pd.DataFrame()
            )
            for name in names
        }
    frame = pd.read_csv(source).fillna("")
    return {
        "estimate_review": frame if "review_training_selected" in frame.columns else pd.DataFrame(),
        "row_review": frame if "review_row_state" in frame.columns else pd.DataFrame(),
        "archetype_review": frame if "review_approved" in frame.columns else pd.DataFrame(),
        "rule_review": (
            frame
            if {"rule_key", "review_approved"}.issubset(frame.columns)
            else pd.DataFrame()
        ),
    }


def merge_review_queue_edits(
    generated: dict[str, pd.DataFrame],
    reviewed: dict[str, pd.DataFrame] | None,
) -> dict[str, pd.DataFrame]:
    if not reviewed:
        return generated
    merged = {name: frame.copy() for name, frame in generated.items()}
    key_by_sheet = {
        "estimate_review": "observation_id",
        "row_review": "row_review_key",
        "archetype_review": "archetype_id",
        "rule_review": "rule_key",
    }
    for sheet_name, key in key_by_sheet.items():
        current = merged.get(sheet_name, pd.DataFrame())
        prior = reviewed.get(sheet_name, pd.DataFrame())
        if current.empty:
            if not prior.empty and sheet_name in {"archetype_review", "rule_review"}:
                merged[sheet_name] = prior.copy()
            continue
        if prior.empty or key not in current.columns or key not in prior.columns:
            continue
        prior_lookup = {
            _text(row.get(key)): row
            for row in prior.to_dict(orient="records")
            if _text(row.get(key))
        }
        for index, row in current.iterrows():
            previous = prior_lookup.get(_text(row.get(key)))
            if not previous:
                continue
            for field, value in previous.items():
                if field.startswith("review_") and field in current.columns:
                    current.at[index, field] = value
        merged[sheet_name] = current
    return merged


def _reviewed_rows(frame: pd.DataFrame, field: str) -> pd.DataFrame:
    if frame.empty or field not in frame.columns:
        return frame.iloc[0:0].copy()
    complete = (
        frame.get("review_complete", pd.Series(False, index=frame.index))
        .map(_as_bool)
        .fillna(False)
    )
    has_value = frame[field].map(_text).ne("")
    return frame[complete & has_value].copy()


def apply_review_overrides(
    data: Any,
    result: dict[str, Any],
    review: dict[str, pd.DataFrame],
    *,
    config: ArchetypeAnalysisConfig | None = None,
) -> tuple[dict[str, Any], pd.DataFrame]:
    config = config or ArchetypeAnalysisConfig(**result.get("config", {}))
    classified = result["classified_rows"].copy()
    correction_rows: list[dict[str, Any]] = []

    row_review = _reviewed_rows(
        review.get("row_review", pd.DataFrame()),
        "review_row_state",
    )
    for row in row_review.to_dict(orient="records"):
        state = _text(row.get("review_row_state")).lower()
        if state not in ALLOWED_ROW_STATES:
            raise ValueError(f"Unsupported reviewed row state: {state}")
        mask = classified["observation_id"].astype(str).eq(
            _text(row.get("observation_id"))
        )
        template_row_id = _text(row.get("template_row_id"))
        if template_row_id and "template_row_id" in classified.columns:
            mask &= classified["template_row_id"].astype(str).eq(template_row_id)
        else:
            mask &= classified["source_index"].astype(str).eq(
                _text(row.get("source_index"))
            )
        matches = classified.index[mask]
        if len(matches) != 1:
            raise ValueError(
                "Reviewed row must resolve to exactly one classified row: "
                f"{row.get('observation_id')} / {template_row_id or row.get('source_index')}"
            )
        index = matches[0]
        previous = classified.at[index, "row_state"]
        classified.at[index, "row_state"] = state
        classified.at[index, "state_reason"] = "human_review_override"
        classified.at[index, "state_confidence"] = 1.0
        correction_rows.append(
            {
                "correction_type": "row_state",
                "observation_id": row.get("observation_id"),
                "target": template_row_id or row.get("source_index"),
                "previous_value": previous,
                "reviewed_value": state,
                "reviewer_notes": row.get("reviewer_notes"),
            }
        )

    observations, classified = build_estimate_observations(
        data,
        config=config,
        classified_rows=classified,
    )
    estimate_review = _reviewed_rows(
        review.get("estimate_review", pd.DataFrame()),
        "review_training_selected",
    )
    if not estimate_review.empty:
        selected_review = estimate_review[
            estimate_review["review_training_selected"].map(_as_bool).eq(True)
        ]
        observation_lookup = observations.set_index("observation_id", drop=False)
        group_choices: dict[str, list[str]] = {}
        for row in selected_review.to_dict(orient="records"):
            observation_id = _text(row.get("observation_id"))
            if observation_id not in observation_lookup.index:
                raise ValueError(f"Unknown reviewed observation: {observation_id}")
            observation = observation_lookup.loc[observation_id]
            if not bool(observation["training_eligible"]):
                raise ValueError(
                    f"Reviewed training selection is not eligible: {observation_id}"
                )
            group_key = _text(observation.get("job_id")) or observation_id
            group_choices.setdefault(group_key, []).append(observation_id)
        duplicates = {
            group: choices
            for group, choices in group_choices.items()
            if len(choices) != 1
        }
        if duplicates:
            raise ValueError(
                "Review must select exactly one training revision per reviewed group: "
                + json.dumps(duplicates, sort_keys=True)
            )
        for group_key, choices in group_choices.items():
            observation_id = choices[0]
            group_mask = observations["job_id"].fillna("").astype(str).eq(group_key)
            if not group_mask.any():
                group_mask = observations["observation_id"].astype(str).eq(group_key)
            previous_ids = observations.loc[
                group_mask & observations["training_selected"].astype(bool),
                "observation_id",
            ].astype(str).tolist()
            observations.loc[group_mask, "training_selected"] = False
            observations.loc[group_mask, "revision_selection_reason"] = (
                "superseded_by_human_review"
            )
            selected_mask = observations["observation_id"].astype(str).eq(observation_id)
            observations.loc[selected_mask, "training_selected"] = True
            observations.loc[selected_mask, "revision_selection_reason"] = (
                "human_review_selection"
            )
            observations.loc[selected_mask, "revision_review_required"] = False
            correction_rows.append(
                {
                    "correction_type": "revision_selection",
                    "observation_id": observation_id,
                    "target": group_key,
                    "previous_value": json.dumps(previous_ids),
                    "reviewed_value": observation_id,
                    "reviewer_notes": _text(
                        selected_review.loc[
                            selected_review["observation_id"].astype(str).eq(observation_id),
                            "reviewer_notes",
                        ].iloc[0]
                    ),
                }
            )

    classified = classified.drop(
        columns=[
            "training_selected",
            "revision_selection_reason",
            "revision_review_required",
        ],
        errors="ignore",
    ).merge(
        observations[
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
    rebuilt = _analysis_from_reviewed_frames(observations, classified, config)
    return rebuilt, pd.DataFrame(correction_rows)


def _analysis_from_reviewed_frames(
    observations: pd.DataFrame,
    classified: pd.DataFrame,
    config: ArchetypeAnalysisConfig,
) -> dict[str, Any]:
    matrix = build_decision_matrix(observations)
    associations, negative = mine_association_rules(observations, config=config)
    candidates = select_rule_candidates(
        associations,
        min_support_count=max(config.min_support_count, 5),
    )
    archetypes, packets = build_candidate_archetypes(observations, config=config)
    diagnostics = build_analysis_diagnostics(
        observations,
        classified,
        associations,
        negative,
        candidates,
        archetypes,
    )
    return {
        "config": asdict(config),
        "classified_rows": classified,
        "observations": observations,
        "decision_matrix": matrix,
        "association_rules": associations,
        "negative_associations": negative,
        "rule_candidates": candidates,
        "candidate_archetypes": archetypes,
        "ai_review_packets": packets,
        "diagnostics": diagnostics,
    }


def _decision_sets(frame: pd.DataFrame) -> list[set[str]]:
    return [
        {str(item) for item in _json_list(value) if _text(item)}
        for value in frame.get("included_decisions_json", pd.Series(dtype=object))
    ]


def _rule_metrics(
    frame: pd.DataFrame,
    *,
    antecedent: str,
    consequent: str,
    false_positive_weight: float,
) -> dict[str, Any]:
    sets = _decision_sets(frame)
    antecedent_count = sum(antecedent in decisions for decisions in sets)
    true_positives = sum(
        antecedent in decisions and consequent in decisions for decisions in sets
    )
    false_positives = antecedent_count - true_positives
    consequent_count = sum(consequent in decisions for decisions in sets)
    confidence = true_positives / antecedent_count if antecedent_count else 0.0
    baseline = consequent_count / len(sets) if sets else 0.0
    lift = confidence / baseline if baseline else 0.0
    denominator = true_positives + false_positive_weight * false_positives
    weighted_precision = true_positives / denominator if denominator else 0.0
    return {
        "observation_count": len(sets),
        "antecedent_count": antecedent_count,
        "true_positives": true_positives,
        "false_positives": false_positives,
        "precision": round(confidence, 6),
        "false_positive_weighted_precision": round(weighted_precision, 6),
        "lift": round(lift, 6),
    }


def _wilson_lower_bound(successes: int, trials: int, z: float = 1.96) -> float:
    if trials <= 0:
        return 0.0
    proportion = successes / trials
    denominator = 1 + z**2 / trials
    centre = proportion + z**2 / (2 * trials)
    adjustment = z * (
        (proportion * (1 - proportion) + z**2 / (4 * trials)) / trials
    ) ** 0.5
    return max(0.0, (centre - adjustment) / denominator)


def _temporal_split(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame] | None:
    timestamps = pd.to_datetime(
        frame.get("revision_timestamp", pd.Series(index=frame.index, dtype=object)),
        errors="coerce",
        utc=True,
    )
    years = pd.to_numeric(
        frame.get("source_year", pd.Series(index=frame.index, dtype=object)),
        errors="coerce",
    )
    fallback = pd.Series(pd.NaT, index=frame.index, dtype="datetime64[ns, UTC]")
    valid_years = years.notna()
    if valid_years.any():
        fallback.loc[valid_years] = pd.to_datetime(
            years.loc[valid_years].astype(int).astype(str) + "-12-31",
            format="%Y-%m-%d",
            errors="coerce",
            utc=True,
        )
    timestamps = timestamps.fillna(fallback)
    dated = frame[timestamps.notna()].copy()
    if len(dated) < 10:
        return None
    dated["_holdout_date"] = timestamps[timestamps.notna()]
    dated = dated.sort_values(["_holdout_date", "observation_id"])
    holdout_count = max(2, int(round(len(dated) * 0.2)))
    return (
        dated.iloc[:-holdout_count].drop(columns="_holdout_date"),
        dated.iloc[-holdout_count:].drop(columns="_holdout_date"),
    )


def evaluate_rule_holdouts(
    observations: pd.DataFrame,
    rule_candidates: pd.DataFrame,
    *,
    false_positive_weight: float = 3.0,
    min_holdout_antecedents: int = 3,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    selected = observations[
        observations.get("training_selected", False).fillna(False).astype(bool)
    ].copy()
    if selected.empty or rule_candidates.empty:
        return pd.DataFrame(), pd.DataFrame()
    evaluation_rows: list[dict[str, Any]] = []

    for rule in rule_candidates.to_dict(orient="records"):
        segment = selected[
            selected["template_type"].fillna("").astype(str).eq(
                _text(rule.get("segment_value"))
            )
        ]
        splits: list[tuple[str, str, pd.DataFrame, pd.DataFrame]] = []
        temporal = _temporal_split(segment)
        if temporal is not None:
            splits.append(("temporal", "latest_20_percent", temporal[0], temporal[1]))
        if "estimator" in segment.columns:
            estimators = segment["estimator"].fillna("").astype(str).str.strip()
            for estimator in sorted(value for value in estimators.unique() if value):
                holdout = segment[estimators.eq(estimator)]
                train = segment[~estimators.eq(estimator)]
                if len(holdout) >= 2 and len(train) >= 5:
                    identity_sources = (
                        holdout.get(
                            "estimator_identity_source",
                            pd.Series(dtype=object),
                        )
                        .fillna("")
                        .astype(str)
                        .str.strip()
                    )
                    identity_source = (
                        identity_sources.mode().iloc[0]
                        if not identity_sources.mode().empty
                        else "unknown"
                    )
                    split_type = (
                        "leave_estimator_out"
                        if identity_source == "declared_estimator"
                        else "leave_file_modifier_out"
                    )
                    splits.append((split_type, estimator, train, holdout))

        for split_type, split_value, train, holdout in splits:
            train_metrics = _rule_metrics(
                train,
                antecedent=_text(rule.get("antecedent")),
                consequent=_text(rule.get("consequent")),
                false_positive_weight=false_positive_weight,
            )
            holdout_metrics = _rule_metrics(
                holdout,
                antecedent=_text(rule.get("antecedent")),
                consequent=_text(rule.get("consequent")),
                false_positive_weight=false_positive_weight,
            )
            train_eligible = (
                train_metrics["antecedent_count"] >= 5
                and _wilson_lower_bound(
                    train_metrics["true_positives"],
                    train_metrics["antecedent_count"],
                )
                >= 0.7
                and train_metrics["lift"] >= 1.2
            )
            sufficient = holdout_metrics["antecedent_count"] >= min_holdout_antecedents
            stable = (
                train_eligible
                and sufficient
                and holdout_metrics["false_positive_weighted_precision"] >= 0.7
                and holdout_metrics["precision"] >= 0.8
            )
            evaluation_rows.append(
                {
                    "rule_key": _stable_rank(
                        rule.get("segment_value"),
                        rule.get("antecedent"),
                        rule.get("consequent"),
                    )[:20],
                    "segment_value": rule.get("segment_value"),
                    "antecedent": rule.get("antecedent"),
                    "consequent": rule.get("consequent"),
                    "split_type": split_type,
                    "split_value": split_value,
                    **{f"train_{key}": value for key, value in train_metrics.items()},
                    **{
                        f"holdout_{key}": value
                        for key, value in holdout_metrics.items()
                    },
                    "sufficient_holdout": sufficient,
                    "eligible_from_train": train_eligible,
                    "stable_on_split": stable,
                    "false_positive_weight": false_positive_weight,
                }
            )

    evaluations = pd.DataFrame(evaluation_rows)
    summary_rows: list[dict[str, Any]] = []
    for rule in rule_candidates.to_dict(orient="records"):
        rule_key = _stable_rank(
            rule.get("segment_value"),
            rule.get("antecedent"),
            rule.get("consequent"),
        )[:20]
        matching = (
            evaluations[evaluations["rule_key"].eq(rule_key)]
            if not evaluations.empty
            else pd.DataFrame()
        )
        sufficient = (
            matching[matching["sufficient_holdout"].astype(bool)]
            if not matching.empty
            else matching
        )
        if sufficient.empty:
            status = "insufficient_holdout_data"
        elif bool(sufficient["stable_on_split"].all()):
            status = "stable_candidate"
        else:
            status = "unstable_candidate"
        summary_rows.append(
            {
                "rule_key": rule_key,
                "segment_value": rule.get("segment_value"),
                "antecedent": rule.get("antecedent"),
                "consequent": rule.get("consequent"),
                "full_support_count": rule.get("support_count"),
                "full_confidence": rule.get("confidence"),
                "full_lift": rule.get("lift"),
                "evaluated_split_count": len(matching),
                "sufficient_split_count": len(sufficient),
                "stable_split_count": int(
                    sufficient.get("stable_on_split", pd.Series(dtype=bool)).sum()
                ),
                "validation_status": status,
                "runtime_activation": False,
            }
        )
    return evaluations, pd.DataFrame(summary_rows)


def _bounded_packet(packet: dict[str, Any], max_characters: int) -> dict[str, Any]:
    bounded = json.loads(json.dumps(packet, default=str))
    representatives = bounded.get("representative_estimates") or []
    for representative in representatives:
        representative["scope_excerpt"] = _text(
            representative.get("scope_excerpt")
        )[:800]
    while len(json.dumps(bounded, sort_keys=True)) > max_characters and representatives:
        representatives.pop()
    if len(json.dumps(bounded, sort_keys=True)) > max_characters:
        summary = bounded.get("statistical_summary") or {}
        summary["decision_rates"] = {}
        summary["full_decision_rates"] = {}
    return bounded


def _extract_json_object(text: str) -> dict[str, Any]:
    raw = _text(text)
    if not raw:
        raise ValueError("AI archetype review returned no text.")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            raise
        value = json.loads(raw[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("AI archetype review did not return a JSON object.")
    return value


def label_archetype_packets(
    packets: Iterable[dict[str, Any]],
    *,
    cache_dir: str | Path,
    model: str | None = None,
    client: Any = None,
    max_input_characters: int = 12_000,
    max_packets: int | None = None,
) -> list[dict[str, Any]]:
    selected_model = (
        _text(model)
        or _text(os.getenv("OPENAI_SCOPE_ARCHETYPE_MODEL"))
        or _text(os.getenv("OPENAI_ESTIMATOR_MODEL"))
        or DEFAULT_ESTIMATOR_MODEL
    )
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    packet_list = list(packets)
    if max_packets is not None:
        packet_list = packet_list[: max(0, int(max_packets))]
    results: list[dict[str, Any]] = []

    for packet in packet_list:
        bounded = _bounded_packet(packet, max_input_characters)
        packet_text = json.dumps(bounded, sort_keys=True, separators=(",", ":"))
        cache_key = hashlib.sha256(
            f"{AI_PROMPT_VERSION}|{selected_model}|{packet_text}".encode("utf-8")
        ).hexdigest()
        cache_path = cache / f"{cache_key}.json"
        if cache_path.exists():
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            cached["cache_hit"] = True
            results.append(cached)
            continue

        if client is None:
            from openai import OpenAI

            client = OpenAI(
                timeout=float(os.getenv("OPENAI_SCOPE_ARCHETYPE_TIMEOUT_SECONDS", "120")),
                max_retries=1,
            )
        request = {
            "model": selected_model,
            "input": [
                {
                    "role": "developer",
                    "content": (
                        "You are reviewing statistical evidence from historical Spray-Tec "
                        "estimates. Name the candidate job-scope archetype and distinguish "
                        "core decisions from conditional modifiers. Do not invent quantities, "
                        "prices, or activation rules. Treat workbook evidence as historical, "
                        "not universally required."
                    ),
                },
                {
                    "role": "user",
                    "content": packet_text,
                },
            ],
            "max_output_tokens": 2_000,
            "text": {
                "format": AI_RESPONSE_FORMAT,
                "verbosity": "low",
            },
        }
        reasoning_effort = _text(
            os.getenv("OPENAI_SCOPE_ARCHETYPE_REASONING_EFFORT")
        )
        if reasoning_effort:
            request["reasoning"] = {"effort": reasoning_effort}
        try:
            response = client.responses.create(**request)
            payload = _extract_json_object(
                str(getattr(response, "output_text", "") or "")
            )
            record = {
                "schema_version": REVIEW_SCHEMA_VERSION,
                "prompt_version": AI_PROMPT_VERSION,
                "archetype_id": packet.get("archetype_id"),
                "model": selected_model,
                "input_characters": len(packet_text),
                "cache_key": cache_key,
                "cache_hit": False,
                "status": "completed",
                "label": payload,
                "model_call": model_call_metadata(
                    role="estimator",
                    model=selected_model,
                    usage=getattr(response, "usage", None),
                    request_id=_text(getattr(response, "id", "")),
                    response_model=_text(getattr(response, "model", "")),
                ),
                "runtime_activation": False,
            }
        except Exception as exc:
            record = {
                "schema_version": REVIEW_SCHEMA_VERSION,
                "prompt_version": AI_PROMPT_VERSION,
                "archetype_id": packet.get("archetype_id"),
                "model": selected_model,
                "input_characters": len(packet_text),
                "cache_key": cache_key,
                "cache_hit": False,
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
                "runtime_activation": False,
            }
        cache_path.write_text(
            json.dumps(record, indent=2, default=str, sort_keys=True),
            encoding="utf-8",
        )
        results.append(record)
    return results


def build_archetype_review_catalog(
    archetypes: pd.DataFrame,
    labels: Iterable[dict[str, Any]],
    human_review: pd.DataFrame | None = None,
) -> pd.DataFrame:
    if archetypes.empty:
        return archetypes.copy()
    label_lookup = {
        _text(record.get("archetype_id")): record
        for record in labels
        if record.get("status") == "completed"
    }
    human_lookup = {}
    if isinstance(human_review, pd.DataFrame) and not human_review.empty:
        human_lookup = {
            _text(row.get("archetype_id")): row
            for row in human_review.to_dict(orient="records")
        }
    rows: list[dict[str, Any]] = []
    for row in archetypes.to_dict(orient="records"):
        archetype_id = _text(row.get("archetype_id"))
        label_record = label_lookup.get(archetype_id, {})
        label = label_record.get("label") or {}
        human = human_lookup.get(archetype_id, {})
        human_complete = _as_bool(human.get("review_complete")) is True
        human_approved = (
            _as_bool(human.get("review_approved")) is True if human_complete else False
        )
        reviewed_name = _text(human.get("review_proposed_name"))
        rows.append(
            {
                **row,
                "ai_proposed_name": _text(label.get("proposed_name")),
                "ai_base_system": _text(label.get("base_system")),
                "ai_required_signals_json": json.dumps(
                    label.get("required_signals") or [],
                    sort_keys=True,
                ),
                "ai_conditional_modifiers_json": json.dumps(
                    label.get("conditional_modifiers") or [],
                    sort_keys=True,
                ),
                "ai_likely_exclusions_json": json.dumps(
                    label.get("likely_exclusions") or [],
                    sort_keys=True,
                ),
                "ai_ambiguities_json": json.dumps(
                    label.get("ambiguities") or [],
                    sort_keys=True,
                ),
                "ai_review_confidence": label.get("review_confidence"),
                "reviewed_name": reviewed_name,
                "human_review_complete": human_complete,
                "human_approved": human_approved,
                "catalog_status": (
                    "human_approved_offline"
                    if human_approved
                    else "pending_human_review"
                ),
                "runtime_activation": False,
            }
        )
    return pd.DataFrame(rows)


def write_review_validation_artifacts(
    result: dict[str, Any],
    output_dir: str | Path,
    *,
    review_queue: dict[str, pd.DataFrame],
    ai_labels: Iterable[dict[str, Any]] = (),
    review_corrections: pd.DataFrame | None = None,
    false_positive_weight: float = 3.0,
) -> dict[str, Path]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    evaluations, validation = evaluate_rule_holdouts(
        result["observations"],
        result["rule_candidates"],
        false_positive_weight=false_positive_weight,
    )
    label_records = list(ai_labels)
    original_archetype_review = review_queue.get(
        "archetype_review",
        pd.DataFrame(),
    )
    catalog = build_archetype_review_catalog(
        result["candidate_archetypes"],
        label_records,
        original_archetype_review,
    )
    archetype_review_sheet = catalog.copy()
    editable_columns = [
        "archetype_id",
        "review_proposed_name",
        "review_approved",
        "review_complete",
        "reviewer_notes",
    ]
    if (
        isinstance(original_archetype_review, pd.DataFrame)
        and not original_archetype_review.empty
    ):
        editable = original_archetype_review.reindex(columns=editable_columns)
        archetype_review_sheet = archetype_review_sheet.merge(
            editable,
            on="archetype_id",
            how="left",
        )
    else:
        for column in editable_columns[1:]:
            archetype_review_sheet[column] = ""
    rule_review_sheet = _build_rule_review_sheet(
        result["rule_candidates"],
        validation,
        review_queue.get("rule_review", pd.DataFrame()),
    )
    workbook_queue = {
        **review_queue,
        "archetype_review": archetype_review_sheet,
        "rule_review": rule_review_sheet,
    }
    workbook_path = write_review_workbook(
        workbook_queue,
        output / "scope_archetype_review.xlsx",
    )
    paths[workbook_path.name] = workbook_path

    for filename, frame in {
        "rule_holdout_evaluation.csv": evaluations,
        "rule_validation_summary.csv": validation,
        "review_corrections.csv": (
            review_corrections
            if isinstance(review_corrections, pd.DataFrame)
            else pd.DataFrame()
        ),
    }.items():
        path = output / filename
        frame.to_csv(path, index=False)
        paths[filename] = path

    labels_path = output / "ai_archetype_labels.jsonl"
    with labels_path.open("w", encoding="utf-8") as handle:
        for record in label_records:
            handle.write(json.dumps(record, default=str, sort_keys=True) + "\n")
    paths[labels_path.name] = labels_path

    catalog_path = output / "archetype_review_catalog.csv"
    catalog.to_csv(catalog_path, index=False)
    paths[catalog_path.name] = catalog_path

    summary = {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "review_estimate_count": len(review_queue.get("estimate_review", pd.DataFrame())),
        "review_row_count": len(review_queue.get("row_review", pd.DataFrame())),
        "candidate_rule_count": len(result.get("rule_candidates", pd.DataFrame())),
        "holdout_evaluation_count": len(evaluations),
        "stable_candidate_rule_count": int(
            validation.get("validation_status", pd.Series(dtype=str))
            .eq("stable_candidate")
            .sum()
        ),
        "ai_label_count": sum(
            record.get("status") == "completed" for record in label_records
        ),
        "false_positive_weight": false_positive_weight,
        "runtime_activation": False,
    }
    summary_path = output / "review_validation_summary.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    paths[summary_path.name] = summary_path
    return paths


def _build_rule_review_sheet(
    rule_candidates: pd.DataFrame,
    validation: pd.DataFrame,
    existing_review: pd.DataFrame | None = None,
) -> pd.DataFrame:
    if rule_candidates.empty or validation.empty:
        return pd.DataFrame(
            columns=[
                "rule_key",
                "segment_value",
                "antecedent",
                "consequent",
                "validation_status",
                "review_approved",
                "review_complete",
                "reviewer_notes",
            ]
        )
    stable = validation[
        validation["validation_status"].eq("stable_candidate")
    ].copy()
    candidate_columns = [
        "segment_value",
        "antecedent",
        "consequent",
        "support_count",
        "confidence",
        "confidence_wilson_lower",
        "lift",
        "leverage",
        "antecedent_role",
        "consequent_role",
    ]
    candidates = rule_candidates.reindex(columns=candidate_columns)
    stable = stable.merge(
        candidates,
        on=["segment_value", "antecedent", "consequent"],
        how="left",
    )
    editable_columns = [
        "rule_key",
        "review_approved",
        "review_complete",
        "reviewer_notes",
    ]
    if isinstance(existing_review, pd.DataFrame) and not existing_review.empty:
        existing = existing_review.reindex(columns=editable_columns)
        stable = stable.merge(existing, on="rule_key", how="left")
    else:
        stable["review_approved"] = ""
        stable["review_complete"] = False
        stable["reviewer_notes"] = ""
    return stable.sort_values(
        ["segment_value", "full_support_count", "antecedent", "consequent"],
        ascending=[True, False, True, True],
    ).reset_index(drop=True)
