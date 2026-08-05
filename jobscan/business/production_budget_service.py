from __future__ import annotations

import re
from collections import defaultdict
from statistics import median
from typing import Any, Iterable, Mapping

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Engine

from jobscan.business.job_service import (
    MAX_JOB_SEARCH_RESULTS,
    JobIntelligenceUnavailableError,
    _available_fields,
    _columns,
    _job_relation,
    _query_rows,
    _resolve_engine,
    _utc_now,
)


TRACKING_RELATION = "job_tracking_summary"
BUDGET_RELATION = "job_tracking_estimate_budget_snapshot"
MAX_TRACKING_SOURCES_PER_JOB = 10
MAX_QUANTITY_USAGE_RATIO = 10.0
MIN_PLAUSIBLE_LABOR_RATE = 10.0
MAX_PLAUSIBLE_LABOR_RATE = 500.0
FOAM_LBS_PER_STROKE = 0.625

ACTUAL_FIELDS = (
    "actual_labor_hours",
    "actual_foam_sqft",
    "actual_foam_strokes",
    "actual_foam_lbs",
    "actual_base_coat_1",
    "actual_base_coat_2",
    "actual_primer",
    "actual_sf",
    "actual_caulk",
    "actual_af_buttergrade",
    "actual_granules",
)
ESTIMATED_FIELDS = tuple(field.replace("actual_", "estimated_") for field in ACTUAL_FIELDS)
TRACKING_FIELDS = (
    "job_id",
    "tracking_id",
    "tracking_status",
    "tracking_file",
    "source_file",
    "source_path",
    "actual_first_work_date",
    "actual_last_work_date",
    "updated_at",
    *ACTUAL_FIELDS,
    *ESTIMATED_FIELDS,
)
JOB_FIELDS = (
    "job_id",
    "source_year",
    "customer",
    "job_name",
    "division",
    "pipeline_status",
    "status",
    "estimated_value",
    "final_price",
    "folder_url",
    "folder_path",
)

BUDGET_BUCKETS = (
    {
        "bucket": "Labor",
        "kind": "labor",
        "actual_fields": ("actual_labor_hours",),
        "estimated_fields": ("estimated_labor_hours",),
        "quantity_unit": "hours",
        "comparison": "sum",
    },
    {
        "bucket": "Foam / SPF",
        "kind": "material",
        "actual_fields": (
            "actual_foam_sqft",
            "actual_foam_lbs",
            "actual_foam_strokes",
        ),
        "estimated_fields": (
            "estimated_foam_sqft",
            "estimated_foam_lbs",
            "estimated_foam_strokes",
        ),
        "quantity_unit": "paired source unit",
        "comparison": "paired",
    },
    {
        "bucket": "Coating",
        "kind": "material",
        "actual_fields": ("actual_base_coat_1", "actual_base_coat_2"),
        "estimated_fields": ("estimated_base_coat_1", "estimated_base_coat_2"),
        "quantity_unit": "coating units",
        "comparison": "sum",
    },
    {
        "bucket": "Primer / Sealants",
        "kind": "material",
        "actual_fields": (
            "actual_primer",
            "actual_sf",
            "actual_caulk",
            "actual_af_buttergrade",
        ),
        "estimated_fields": (
            "estimated_primer",
            "estimated_sf",
            "estimated_caulk",
            "estimated_af_buttergrade",
        ),
        "quantity_unit": "mixed source units",
        "comparison": "incomparable",
    },
    {
        "bucket": "Granules",
        "kind": "material",
        "actual_fields": ("actual_granules",),
        "estimated_fields": ("estimated_granules",),
        "quantity_unit": "units",
        "comparison": "sum",
    },
)


def get_production_budget_health(
    *,
    database_url: str | None = None,
    engine: Engine | None = None,
    job_ids: Iterable[str] = (),
    division: str = "",
    job_year: int | None = None,
    over_plan_only: bool = False,
    include_no_actuals: bool = False,
    include_completed: bool = False,
    limit: int = 10,
) -> dict[str, Any]:
    """Compare tracked usage with estimate-derived production budgets.

    The returned dollar values are estimate-rate usage proxies. They are not
    accounting actual costs and cannot establish realized profitability.
    """

    resolved_engine, owns_engine = _resolve_engine(database_url, engine)
    try:
        tracking_columns = _columns(resolved_engine, TRACKING_RELATION)
        budget_columns = _columns(resolved_engine, BUDGET_RELATION)
        if "job_id" not in tracking_columns:
            raise JobIntelligenceUnavailableError(
                "The job tracking summary source is unavailable."
            )
        if not {"job_id", "budget_bucket", "estimated_bucket_cost"}.issubset(
            budget_columns
        ):
            raise JobIntelligenceUnavailableError(
                "The estimate-derived production budget snapshot is unavailable."
            )

        normalized_job_ids = tuple(
            dict.fromkeys(
                str(job_id).strip() for job_id in job_ids if str(job_id).strip()
            )
        )
        tracking_rows = _relation_rows(
            resolved_engine,
            TRACKING_RELATION,
            TRACKING_FIELDS,
            (),
        )
        budget_rows = _relation_rows(
            resolved_engine,
            BUDGET_RELATION,
            (
                "job_id",
                "budget_bucket",
                "estimated_bucket_cost",
                "estimate_budget_rows_used",
                "refreshed_at",
            ),
            (),
        )
        job_rows = _job_rows(resolved_engine, ())
        if job_year is not None and not any(
            str(row.get("source_year") or "").strip() for row in job_rows
        ):
            raise JobIntelligenceUnavailableError(
                "The job source does not expose source_year for job-year filtering."
            )
        job_lookup = _variant_lookup(job_rows)
        budget_lookup = _budget_lookup(budget_rows)

        tracking_groups = _tracking_groups(tracking_rows)
        ambiguous_job_ids = sorted(
            job_id
            for job_id, rows in tracking_groups.items()
            if _source_count(rows) > MAX_TRACKING_SOURCES_PER_JOB
        )
        valid_groups = {
            job_id: rows
            for job_id, rows in tracking_groups.items()
            if job_id not in set(ambiguous_job_ids)
        }
        if normalized_job_ids:
            requested_variants = {
                variant
                for requested in normalized_job_ids
                for variant in _job_id_variants(requested)
            }
            valid_groups = {
                job_id: rows
                for job_id, rows in valid_groups.items()
                if requested_variants & set(_job_id_variants(job_id))
            }
        rolled_tracking = {
            job_id: _with_derived_foam_quantities(_rollup_tracking_rows(rows))
            for job_id, rows in valid_groups.items()
        }
        labor_rates = _estimate_derived_labor_rates(
            rolled_tracking,
            budget_lookup,
        )
        fallback_labor_rate = median(labor_rates) if labor_rates else None

        all_records: list[dict[str, Any]] = []
        all_bucket_rows: list[dict[str, Any]] = []
        jobs_without_budget = 0
        jobs_without_actuals = 0
        division_key = division.strip().lower()
        for job_id, tracking in rolled_tracking.items():
            metadata = _lookup_by_variants(job_lookup, job_id)
            if division_key and str(metadata.get("division") or "").lower() != division_key:
                continue
            if job_year is not None and str(metadata.get("source_year") or "").strip() != str(job_year):
                continue
            if not include_completed and _is_completed(metadata):
                continue
            record, buckets = _build_job_record(
                job_id,
                tracking,
                metadata,
                budget_lookup,
                fallback_labor_rate=fallback_labor_rate,
            )
            if record["estimated_production_budget"] <= 0:
                jobs_without_budget += 1
            if record["comparable_actual_bucket_count"] == 0:
                jobs_without_actuals += 1
                if not include_no_actuals:
                    continue
            if over_plan_only and record["budget_status"] != "Usage Over Plan":
                continue
            all_records.append(record)
            all_bucket_rows.extend(buckets)

        all_records.sort(key=_record_sort_key)
        portfolio_rankings = _portfolio_rankings(all_records)
        applied_limit = max(1, min(int(limit), MAX_JOB_SEARCH_RESULTS))
        selected_records = all_records[:applied_limit]
        selected_job_ids = {str(row["job_id"]) for row in selected_records}
        selected_buckets = [
            row for row in all_bucket_rows if str(row["job_id"]) in selected_job_ids
        ]
        as_of = _latest_text(
            [
                *(row.get("updated_at") for row in tracking_rows),
                *(row.get("refreshed_at") for row in budget_rows),
            ]
        )
        warnings = [
            (
                "Dollar values are production-plan proxies: tracked quantities and "
                "hours are valued with estimate-derived rates. They are not accounting "
                "actual costs or realized profitability."
            ),
            (
                "Budget-used percentages do not establish percent complete or a "
                "forecast-at-completion margin."
            ),
            (
                "When only foam strokes or pounds are available, the response may "
                "derive the missing unit at 0.625 pounds per stroke. The conversion "
                "is an operational proxy based on the tracking workbook calculation."
            ),
            (
                "Primer / Sealants contains mixed source units, so it is shown for "
                "quantity review but excluded from dollar usage calculations."
            ),
            (
                "Implausible quantity ratios or estimate-derived labor rates are "
                "quarantined for review and excluded from dollar usage calculations."
            ),
        ]
        if ambiguous_job_ids:
            warnings.append(
                f"{len(ambiguous_job_ids)} tracking job IDs were excluded because "
                "they aggregate more than "
                f"{MAX_TRACKING_SOURCES_PER_JOB} source files and may represent "
                "mislinked nested jobs."
            )
        if fallback_labor_rate is not None:
            warnings.append(
                "Jobs without a direct labor cost baseline may use the median "
                "estimate-derived labor rate from comparable tracked jobs."
            )

        return {
            "schema_version": "spraytec.production_budget_health.v1",
            "as_of": as_of or _utc_now(),
            "truth_class": "proxy",
            "methodology": {
                "estimated_production_budget": (
                    "Sum of classified estimate-template budget buckets."
                ),
                "estimated_cost_used_proxy": (
                    "Tracked comparable quantity multiplied by its estimate-derived "
                    "bucket unit rate."
                ),
                "budget_used_pct": (
                    "Estimated cost used proxy divided by the comparable "
                    "estimate-derived budget. It is not percent complete."
                ),
                "usage_over_plan_threshold": 1.05,
                "foam_stroke_conversion": (
                    "0.625 pounds per stroke, used only to derive a missing "
                    "foam pounds or strokes comparison. Derived values do not "
                    "replace source-entered tracking values."
                ),
            },
            "filters_applied": {
                "job_ids": list(normalized_job_ids),
                "division": division.strip() or None,
                "job_year": job_year,
                "over_plan_only": over_plan_only,
                "include_no_actuals": include_no_actuals,
                "include_completed": include_completed,
                "limit": applied_limit,
            },
            "headline_metrics": _headline_metrics(all_records),
            "bucket_rollup": _bucket_rollup(all_bucket_rows),
            "portfolio_rankings": portfolio_rankings,
            "records": selected_records,
            "bucket_details": selected_buckets,
            "attention_items": _attention_items(all_records)[:25],
            "source_links": _source_links(selected_records),
            "source_tables": [
                TRACKING_RELATION,
                BUDGET_RELATION,
                _job_relation(resolved_engine)[0],
            ],
            "data_freshness": {
                "tracking_as_of": _latest_text(
                    row.get("updated_at") for row in tracking_rows
                ),
                "estimate_budget_as_of": _latest_text(
                    row.get("refreshed_at") for row in budget_rows
                ),
            },
            "coverage": {
                "source_tracking_rows": len(tracking_rows),
                "source_tracking_job_ids": len(tracking_groups),
                "eligible_tracking_job_ids": len(valid_groups),
                "ambiguous_tracking_job_ids_excluded": len(ambiguous_job_ids),
                "ambiguous_tracking_job_id_sample": ambiguous_job_ids[:10],
                "source_budget_rows": len(budget_rows),
                "source_budget_job_ids": len(
                    {str(row.get("job_id") or "") for row in budget_rows}
                ),
                "matching_jobs_before_result_limit": len(all_records),
                "jobs_without_estimate_cost_baseline": jobs_without_budget,
                "jobs_without_comparable_actuals": jobs_without_actuals,
                "results_truncated": len(all_records) > applied_limit,
                "estimate_derived_labor_rate_samples": len(labor_rates),
                "mixed_unit_buckets_excluded_from_cost_proxy": [
                    "Primer / Sealants"
                ],
                "quarantined_implausible_bucket_count": sum(
                    row.get("budget_status")
                    in {
                        "Implausible Quantity Ratio / Review",
                        "Implausible Labor Rate / Review",
                    }
                    for row in all_bucket_rows
                ),
            },
            "warnings": warnings,
            "response_budget": {
                "max_records": MAX_JOB_SEARCH_RESULTS,
                "max_bucket_details_per_job": len(BUDGET_BUCKETS),
                "returned_records": len(selected_records),
                "returned_bucket_details": len(selected_buckets),
            },
        }
    finally:
        if owns_engine:
            resolved_engine.dispose()


def _relation_rows(
    engine: Engine,
    relation: str,
    fields: Iterable[str],
    job_ids: tuple[str, ...],
) -> list[dict[str, Any]]:
    columns = _columns(engine, relation)
    selected = _available_fields(columns, fields)
    if not selected:
        return []
    sql = f"SELECT {', '.join(selected)} FROM {relation}"
    params: dict[str, Any] = {}
    statement = text(sql)
    if job_ids and "job_id" in columns:
        statement = text(sql + " WHERE job_id IN :job_ids").bindparams(
            bindparam("job_ids", expanding=True)
        )
        params["job_ids"] = list(job_ids)
    return _query_rows(engine, statement, params)


def _job_rows(engine: Engine, job_ids: tuple[str, ...]) -> list[dict[str, Any]]:
    relation, columns = _job_relation(engine)
    selected = _available_fields(columns, JOB_FIELDS)
    sql = f"SELECT {', '.join(selected)} FROM {relation}"
    params: dict[str, Any] = {}
    statement = text(sql)
    if job_ids:
        statement = text(sql + " WHERE job_id IN :job_ids").bindparams(
            bindparam("job_ids", expanding=True)
        )
        params["job_ids"] = list(job_ids)
    return _query_rows(engine, statement, params)


def _tracking_groups(
    rows: Iterable[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen: set[tuple[str, str, str, str]] = set()
    candidates: dict[tuple[str, str], dict[str, Any]] = {}
    for source in rows:
        row = dict(source)
        raw_job_id = str(row.get("job_id") or "").strip()
        if not raw_job_id:
            continue
        job_id = _canonical_job_id(raw_job_id)
        dedupe_key = (
            job_id,
            str(row.get("source_file") or ""),
            str(row.get("actual_first_work_date") or ""),
            str(row.get("actual_last_work_date") or ""),
        )
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        row["source_job_id"] = raw_job_id
        family = _tracking_source_family(row)
        family_key = (job_id, family or str(row.get("tracking_id") or dedupe_key))
        existing = candidates.get(family_key)
        if existing is None or _tracking_source_preference(row) > _tracking_source_preference(
            existing
        ):
            candidates[family_key] = row
    for (job_id, _family), row in candidates.items():
        grouped[job_id].append(row)
    return dict(grouped)


def _source_count(rows: Iterable[Mapping[str, Any]]) -> int:
    return len(
        {
            str(row.get("tracking_file") or row.get("source_file") or "").strip()
            for row in rows
            if str(row.get("tracking_file") or row.get("source_file") or "").strip()
        }
    )


def _rollup_tracking_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(
        rows,
        key=_tracking_source_preference,
        reverse=True,
    )
    rolled: dict[str, Any] = {
        "job_id": str(ordered[0].get("job_id") or ""),
        "tracking_status": _first_text(ordered, "tracking_status"),
        "tracking_file": _joined_text(ordered, "tracking_file"),
        "source_file": _joined_text(ordered, "source_file"),
        "source_path": _joined_text(ordered, "source_path"),
        "actual_first_work_date": min(
            (
                str(row.get("actual_first_work_date"))
                for row in ordered
                if row.get("actual_first_work_date")
            ),
            default=None,
        ),
        "actual_last_work_date": max(
            (
                str(row.get("actual_last_work_date"))
                for row in ordered
                if row.get("actual_last_work_date")
            ),
            default=None,
        ),
        "updated_at": max(
            (str(row.get("updated_at")) for row in ordered if row.get("updated_at")),
            default=None,
        ),
        "tracking_source_count": _source_count(ordered),
    }
    for field in ACTUAL_FIELDS:
        values = [_positive_number(row.get(field)) for row in ordered]
        positive = [value for value in values if value is not None]
        rolled[field] = sum(positive) if positive else None
    for field in ESTIMATED_FIELDS:
        actual_field = field.replace("estimated_", "actual_")
        rolled[field] = next(
            (
                value
                for row in ordered
                if _positive_number(row.get(actual_field)) is not None
                if (value := _positive_number(row.get(field))) is not None
            ),
            next(
                (
                    value
                    for row in ordered
                    if (value := _positive_number(row.get(field))) is not None
                ),
                None,
            ),
        )
    return rolled


def _with_derived_foam_quantities(row: Mapping[str, Any]) -> dict[str, Any]:
    """Fill only missing foam units for like-for-like business comparisons."""

    result = dict(row)
    derivations: list[dict[str, Any]] = []
    pairs = (
        ("actual_foam_strokes", "actual_foam_lbs"),
        ("estimated_foam_strokes", "estimated_foam_lbs"),
    )
    for strokes_field, pounds_field in pairs:
        strokes = _positive_number(result.get(strokes_field))
        pounds = _positive_number(result.get(pounds_field))
        if pounds is None and strokes is not None:
            result[pounds_field] = strokes * FOAM_LBS_PER_STROKE
            derivations.append(
                {
                    "field": pounds_field,
                    "source_field": strokes_field,
                    "formula": "strokes * 0.625 lb/stroke",
                }
            )
        elif strokes is None and pounds is not None:
            result[strokes_field] = pounds / FOAM_LBS_PER_STROKE
            derivations.append(
                {
                    "field": strokes_field,
                    "source_field": pounds_field,
                    "formula": "pounds / 0.625 lb/stroke",
                }
            )
    result["foam_quantity_derivations"] = derivations
    return result


def _budget_lookup(
    rows: Iterable[Mapping[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for source in rows:
        row = dict(source)
        job_id = str(row.get("job_id") or "").strip()
        bucket = str(row.get("budget_bucket") or "").strip()
        if not job_id or not bucket:
            continue
        for variant in _job_id_variants(job_id):
            key = (variant, bucket)
            existing = result.get(key)
            if (
                existing is None
                or _number(row.get("estimated_bucket_cost"))
                > _number(existing.get("estimated_bucket_cost"))
            ):
                result[key] = row
    return result


def _variant_lookup(
    rows: Iterable[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for source in rows:
        row = dict(source)
        job_id = str(row.get("job_id") or "").strip()
        for variant in _job_id_variants(job_id):
            result.setdefault(variant, row)
    return result


def _lookup_by_variants(
    lookup: Mapping[str, dict[str, Any]],
    job_id: str,
) -> dict[str, Any]:
    return next(
        (lookup[variant] for variant in _job_id_variants(job_id) if variant in lookup),
        {},
    )


def _job_id_variants(value: object) -> tuple[str, ...]:
    raw = str(value or "").strip()
    if not raw:
        return ()
    variants = [raw]
    tokens = raw.split("-")
    if (
        len(tokens) >= 3
        and all(token.isdigit() for token in tokens[-3:])
        and len(tokens[-1]) in {2, 4}
    ):
        variants.append("-".join(tokens[:-3]))
    if "-E-TOWN" in raw:
        variants.append(raw.replace("-E-TOWN", "-ETOWN"))
    if "-ETOWN" in raw:
        variants.append(raw.replace("-ETOWN", "-E-TOWN"))
    return tuple(dict.fromkeys(variant for variant in variants if variant))


def _estimate_derived_labor_rates(
    tracking: Mapping[str, Mapping[str, Any]],
    budget_lookup: Mapping[tuple[str, str], Mapping[str, Any]],
) -> list[float]:
    rates: list[float] = []
    for job_id, row in tracking.items():
        hours = _positive_number(row.get("estimated_labor_hours"))
        budget = _budget_for_job(budget_lookup, job_id, "Labor")
        cost = _positive_number(budget.get("estimated_bucket_cost"))
        if hours is not None and cost is not None:
            rates.append(cost / hours)
    return rates


def _budget_for_job(
    lookup: Mapping[tuple[str, str], Mapping[str, Any]],
    job_id: str,
    bucket: str,
) -> dict[str, Any]:
    return next(
        (
            dict(lookup[(variant, bucket)])
            for variant in _job_id_variants(job_id)
            if (variant, bucket) in lookup
        ),
        {},
    )


def _build_job_record(
    job_id: str,
    tracking: Mapping[str, Any],
    metadata: Mapping[str, Any],
    budget_lookup: Mapping[tuple[str, str], Mapping[str, Any]],
    *,
    fallback_labor_rate: float | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    bucket_rows: list[dict[str, Any]] = []
    for spec in BUDGET_BUCKETS:
        bucket = str(spec["bucket"])
        budget = _budget_for_job(budget_lookup, job_id, bucket)
        estimated_cost = _positive_number(budget.get("estimated_bucket_cost"))
        actual_quantity: float | None
        estimated_quantity: float | None
        quantity_unit = str(spec["quantity_unit"])
        if spec["comparison"] == "paired":
            actual_quantity, estimated_quantity, paired_field = _paired_quantities(
                tracking,
                tuple(spec["actual_fields"]),
                tuple(spec["estimated_fields"]),
            )
            if paired_field:
                quantity_unit = paired_field.removeprefix("actual_").replace("_", " ")
        else:
            actual_quantity = _sum_positive(
                tracking.get(field) for field in spec["actual_fields"]
            )
            estimated_quantity = _sum_positive(
                tracking.get(field) for field in spec["estimated_fields"]
            )

        cost_basis = "estimate_budget_snapshot" if estimated_cost is not None else ""
        if (
            bucket == "Labor"
            and estimated_cost is None
            and estimated_quantity is not None
            and fallback_labor_rate is not None
        ):
            estimated_cost = estimated_quantity * fallback_labor_rate
            cost_basis = "median_estimate_derived_labor_rate"

        comparable = (
            spec["comparison"] != "incomparable"
            and estimated_cost is not None
            and estimated_quantity is not None
            and actual_quantity is not None
        )
        unit_cost = (
            estimated_cost / estimated_quantity
            if comparable and estimated_quantity and estimated_quantity > 0
            else None
        )
        quantity_ratio = (
            actual_quantity / estimated_quantity
            if actual_quantity is not None
            and estimated_quantity is not None
            and estimated_quantity > 0
            else None
        )
        review_reason = ""
        if comparable and quantity_ratio is not None and quantity_ratio > MAX_QUANTITY_USAGE_RATIO:
            comparable = False
            unit_cost = None
            review_reason = "quantity usage ratio exceeds the plausibility threshold"
        if (
            comparable
            and bucket == "Labor"
            and unit_cost is not None
            and not (MIN_PLAUSIBLE_LABOR_RATE <= unit_cost <= MAX_PLAUSIBLE_LABOR_RATE)
        ):
            comparable = False
            unit_cost = None
            review_reason = "estimate-derived labor rate is outside the plausibility range"
        estimated_cost_used = (
            actual_quantity * unit_cost
            if comparable and unit_cost is not None and actual_quantity is not None
            else None
        )
        budget_variance = (
            estimated_cost_used - estimated_cost
            if estimated_cost_used is not None and estimated_cost is not None
            else None
        )
        budget_used_pct = (
            estimated_cost_used / estimated_cost
            if estimated_cost_used is not None and estimated_cost
            else None
        )
        if spec["comparison"] == "incomparable" and (
            actual_quantity is not None or estimated_quantity is not None
        ):
            status = "Mixed Units / Review"
        elif review_reason.startswith("quantity"):
            status = "Implausible Quantity Ratio / Review"
        elif review_reason.startswith("estimate-derived labor"):
            status = "Implausible Labor Rate / Review"
        elif estimated_cost is None:
            status = "No Cost Baseline"
        elif actual_quantity is None:
            status = "No Actuals Yet"
        elif estimated_quantity is None:
            status = "Incomplete Quantity Baseline"
        elif budget_used_pct is not None and budget_used_pct > 1.05:
            status = "Usage Over Plan"
        elif budget_used_pct is not None and budget_used_pct >= 0.9:
            status = "Near Plan"
        else:
            status = "On Track"
        bucket_rows.append(
            {
                "job_id": job_id,
                "bucket": bucket,
                "bucket_kind": spec["kind"],
                "truth_class": "proxy",
                "budget_status": status,
                "actual_quantity": actual_quantity,
                "estimated_quantity": estimated_quantity,
                "quantity_unit": quantity_unit,
                "quantity_derivations": (
                    list(tracking.get("foam_quantity_derivations") or [])
                    if bucket == "Foam / SPF"
                    else []
                ),
                "quantity_pct_used": quantity_ratio,
                "estimated_cost": estimated_cost,
                "estimated_cost_used_proxy": estimated_cost_used,
                "estimated_cost_variance_proxy": budget_variance,
                "budget_used_pct": budget_used_pct,
                "estimate_budget_rows_used": budget.get(
                    "estimate_budget_rows_used"
                ),
                "cost_basis": cost_basis,
                "comparable_for_cost_proxy": comparable,
                "review_reason": review_reason,
            }
        )

    estimated_budget = sum(
        _number(row.get("estimated_cost")) for row in bucket_rows
    )
    estimated_cost_used = sum(
        _number(row.get("estimated_cost_used_proxy")) for row in bucket_rows
    )
    comparable_budget = sum(
        _number(row.get("estimated_cost"))
        for row in bucket_rows
        if row["comparable_for_cost_proxy"]
    )
    comparable_actual_count = sum(
        bool(row["comparable_for_cost_proxy"]) for row in bucket_rows
    )
    over_plan_buckets = [
        str(row["bucket"])
        for row in bucket_rows
        if row["budget_status"] == "Usage Over Plan"
    ]
    if over_plan_buckets:
        status = "Usage Over Plan"
    elif any(row["budget_status"] == "Near Plan" for row in bucket_rows):
        status = "Near Plan"
    elif estimated_budget <= 0:
        status = "No Cost Baseline"
    elif comparable_actual_count == 0:
        status = "No Actuals Yet"
    elif any(
        row["budget_status"]
        in {
            "No Cost Baseline",
            "Incomplete Quantity Baseline",
            "Mixed Units / Review",
            "Implausible Quantity Ratio / Review",
            "Implausible Labor Rate / Review",
        }
        for row in bucket_rows
    ):
        status = "Incomplete Baseline"
    else:
        status = "On Track"
    folder_link = str(
        metadata.get("folder_url") or metadata.get("folder_path") or ""
    ).strip()
    record = {
        "job_id": job_id,
        "source_year": metadata.get("source_year"),
        "customer": metadata.get("customer"),
        "job_name": metadata.get("job_name"),
        "division": metadata.get("division"),
        "pipeline_status": metadata.get("pipeline_status"),
        "status": metadata.get("status"),
        "tracking_status": tracking.get("tracking_status"),
        "estimated_value": _optional_number(
            metadata.get("final_price") or metadata.get("estimated_value")
        ),
        "actual_last_work_date": tracking.get("actual_last_work_date"),
        "budget_status": status,
        "truth_class": "proxy",
        "estimated_production_budget": estimated_budget,
        "comparable_estimated_budget": comparable_budget,
        "estimated_cost_used_proxy": estimated_cost_used,
        "estimated_cost_variance_proxy": (
            estimated_cost_used - comparable_budget
            if comparable_budget > 0
            else None
        ),
        "budget_used_pct": (
            estimated_cost_used / comparable_budget
            if comparable_budget > 0
            else None
        ),
        "comparable_budget_coverage_pct": (
            comparable_budget / estimated_budget if estimated_budget > 0 else None
        ),
        "usage_over_plan_buckets": over_plan_buckets,
        "no_baseline_bucket_count": sum(
            row["budget_status"] == "No Cost Baseline" for row in bucket_rows
        ),
        "no_actual_bucket_count": sum(
            row["budget_status"] == "No Actuals Yet" for row in bucket_rows
        ),
        "comparable_actual_bucket_count": comparable_actual_count,
        "tracking_source_count": tracking.get("tracking_source_count"),
        "source_file": tracking.get("source_file"),
        "tracking_file": tracking.get("tracking_file"),
        "folder_link_or_path": folder_link,
    }
    return record, bucket_rows


def _paired_quantities(
    row: Mapping[str, Any],
    actual_fields: tuple[str, ...],
    estimated_fields: tuple[str, ...],
) -> tuple[float | None, float | None, str | None]:
    first_actual: tuple[float, str] | None = None
    first_estimated: float | None = None
    for actual_field, estimated_field in zip(actual_fields, estimated_fields):
        actual = _positive_number(row.get(actual_field))
        estimated = _positive_number(row.get(estimated_field))
        if actual is not None and estimated is not None:
            return actual, estimated, actual_field
        if actual is not None and first_actual is None:
            first_actual = (actual, actual_field)
        if estimated is not None and first_estimated is None:
            first_estimated = estimated
    if first_actual:
        return first_actual[0], None, first_actual[1]
    return None, first_estimated, None


def _canonical_job_id(value: object) -> str:
    variants = _job_id_variants(value)
    return min(variants, key=len) if variants else ""


def _tracking_source_family(row: Mapping[str, Any]) -> str:
    source = str(row.get("source_file") or row.get("tracking_file") or "").strip()
    source = source.replace("\\", "/").rsplit("/", 1)[-1].lower()
    source = re.sub(r"\.(xlsx|xlsm|xls)$", "", source)
    source = re.sub(r"^(updated|revised|final)\s+", "", source)
    source = re.sub(
        r"\s*\(\s*\+?\s*co\s*#?\s*\d+(?:\s*[-–]\s*\d+)?\s*\)",
        "",
        source,
    )
    source = re.sub(
        r"\s+\+?\s*co\s*#?\s*\d+(?:\s*[-–]\s*\d+)?\b",
        "",
        source,
    )
    return re.sub(r"[^a-z0-9]+", "-", source).strip("-")


def _tracking_source_preference(row: Mapping[str, Any]) -> tuple[Any, ...]:
    source = " ".join(
        str(row.get(field) or "") for field in ("source_file", "tracking_file")
    )
    revision = int(
        bool(
            re.search(
                r"\b(updated|revised|final)\b|(?:\+|\b)co\s*#?\s*\d|change\s*order",
                source,
                flags=re.IGNORECASE,
            )
        )
    )
    actual_signal = sum(
        _positive_number(row.get(field)) is not None for field in ACTUAL_FIELDS
    )
    return (
        revision,
        str(row.get("actual_last_work_date") or ""),
        str(row.get("updated_at") or ""),
        actual_signal,
    )


def _headline_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    over_plan = [
        record for record in records if record["budget_status"] == "Usage Over Plan"
    ]
    return {
        "jobs_with_budget_signal": len(records),
        "jobs_usage_over_plan": len(over_plan),
        "jobs_near_plan": sum(
            record["budget_status"] == "Near Plan" for record in records
        ),
        "estimated_production_budget": sum(
            _number(record.get("estimated_production_budget")) for record in records
        ),
        "estimated_cost_used_proxy": sum(
            _number(record.get("estimated_cost_used_proxy")) for record in records
        ),
        "estimated_over_plan_exposure_proxy": sum(
            max(0.0, _number(record.get("estimated_cost_variance_proxy")))
            for record in over_plan
        ),
    }


def _portfolio_rankings(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Return complete-portfolio cost-position extremes independent of record limit."""
    rankable = [
        record
        for record in records
        if isinstance(record.get("budget_used_pct"), (int, float))
    ]

    def summary(record: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "job_id": record.get("job_id"),
            "customer": record.get("customer"),
            "job_name": record.get("job_name"),
            "budget_status": record.get("budget_status"),
            "budget_used_pct": record.get("budget_used_pct"),
            "estimated_cost_variance_proxy": record.get(
                "estimated_cost_variance_proxy"
            ),
            "comparable_budget_coverage_pct": record.get(
                "comparable_budget_coverage_pct"
            ),
            "truth_class": "proxy",
        }

    strongest = sorted(
        rankable,
        key=lambda row: (_number(row.get("budget_used_pct")), str(row.get("job_id"))),
    )[:5]
    weakest = sorted(
        rankable,
        key=lambda row: (-_number(row.get("budget_used_pct")), str(row.get("job_id"))),
    )[:5]
    return {
        "strongest_cost_position": [summary(row) for row in strongest],
        "weakest_cost_position": [summary(row) for row in weakest],
    }


def _bucket_rollup(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["bucket"])].append(row)
    result = []
    for bucket, items in grouped.items():
        result.append(
            {
                "bucket": bucket,
                "jobs": len(items),
                "jobs_usage_over_plan": sum(
                    item["budget_status"] == "Usage Over Plan" for item in items
                ),
                "estimated_cost": sum(
                    _number(item.get("estimated_cost")) for item in items
                ),
                "estimated_cost_used_proxy": sum(
                    _number(item.get("estimated_cost_used_proxy")) for item in items
                ),
                "comparable_jobs": sum(
                    bool(item.get("comparable_for_cost_proxy")) for item in items
                ),
            }
        )
    return sorted(
        result,
        key=lambda row: (-_number(row.get("estimated_cost")), str(row["bucket"])),
    )


def _attention_items(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items = []
    for record in records:
        if record["budget_status"] != "Usage Over Plan":
            continue
        items.append(
            {
                "type": "production_usage_over_plan",
                "severity": "warning",
                "job_id": record["job_id"],
                "message": (
                    "Tracked production usage exceeds the estimate-derived plan "
                    f"for: {', '.join(record['usage_over_plan_buckets'])}."
                ),
                "estimated_cost_variance_proxy": record[
                    "estimated_cost_variance_proxy"
                ],
                "truth_class": "proxy",
            }
        )
    return sorted(
        items,
        key=lambda item: -_number(item.get("estimated_cost_variance_proxy")),
    )


def _source_links(records: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    links = []
    seen: set[str] = set()
    for row in records:
        url = str(row.get("folder_link_or_path") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        links.append(
            {
                "source_type": "job_folder",
                "label": str(
                    row.get("job_name") or row.get("customer") or row.get("job_id")
                ),
                "url": url,
                "job_id": str(row.get("job_id") or ""),
            }
        )
    return links


def _record_sort_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    rank = {
        "Usage Over Plan": 0,
        "Near Plan": 1,
        "Incomplete Baseline": 2,
        "On Track": 3,
        "No Actuals Yet": 4,
        "No Cost Baseline": 5,
    }
    return (
        rank.get(str(row.get("budget_status")), 9),
        -_number(row.get("estimated_cost_variance_proxy")),
        str(row.get("job_id") or ""),
    )


def _first_text(rows: Iterable[Mapping[str, Any]], field: str) -> str | None:
    return next(
        (
            str(row.get(field)).strip()
            for row in rows
            if str(row.get(field) or "").strip()
        ),
        None,
    )


def _joined_text(
    rows: Iterable[Mapping[str, Any]],
    field: str,
    *,
    limit: int = 5,
) -> str | None:
    values = list(
        dict.fromkeys(
            str(row.get(field)).strip()
            for row in rows
            if str(row.get(field) or "").strip()
        )
    )
    return "; ".join(values[:limit]) or None


def _positive_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _optional_number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _number(value: Any) -> float:
    return _optional_number(value) or 0.0


def _sum_positive(values: Iterable[Any]) -> float | None:
    positive = [
        number for value in values if (number := _positive_number(value)) is not None
    ]
    return sum(positive) if positive else None


def _latest_text(values: Iterable[Any]) -> str | None:
    texts = [str(value) for value in values if value]
    return max(texts) if texts else None


def _is_completed(row: Mapping[str, Any]) -> bool:
    values = {
        str(row.get(field) or "").strip().lower()
        for field in ("pipeline_status", "status")
    }
    return bool(
        values
        & {
            "complete",
            "completed",
            "closed",
            "invoiced",
            "cancelled",
            "canceled",
            "lost",
        }
    )
