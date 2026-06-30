from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Connection, Engine


UNKNOWN_BUCKET_VALUES = {"", "unknown", "none", "nan"}
TEMPLATE_ROW_TABLE = "estimate_template_rows"
UNKNOWN_EXPORT_COLUMNS = [
    "cluster_id",
    "row_count",
    "distinct_file_count",
    "template_type",
    "sheet_name",
    "row_number",
    "row_label",
    "selected_item_name",
    "line_item_kind",
    "source_file_pattern",
    "sample_source_files",
    "sample_job_ids",
    "nearby_known_buckets_above",
    "nearby_known_buckets_below",
    "suggested_bucket",
    "suggested_line_item_kind",
    "confidence",
    "review_status",
]
MAPPING_COLUMNS = [
    "cluster_id",
    "match_template_type",
    "match_sheet_name",
    "match_row_number",
    "match_row_label_pattern",
    "match_selected_item_pattern",
    "target_template_bucket",
    "target_line_item_kind",
    "notes",
    "approved",
]


def norm(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().replace("_", " ").replace("-", " ").split())


def is_unknown_bucket(value: Any) -> bool:
    return norm(value) in UNKNOWN_BUCKET_VALUES


def truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return norm(value) in {"1", "true", "yes", "y", "approved"}


def stable_cluster_id(parts: list[Any]) -> str:
    payload = "|".join(str(part or "") for part in parts)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def source_file_pattern(source_file: Any) -> str:
    name = Path(str(source_file or "")).name.lower()
    name = re.sub(r"\d{4,}", "#", name)
    name = re.sub(r"\b\d+\b", "#", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def table_columns(conn: Connection) -> set[str]:
    try:
        return {column["name"] for column in inspect(conn).get_columns(TEMPLATE_ROW_TABLE)}
    except Exception:
        return set()


def select_existing_columns(conn: Connection, desired: list[str]) -> list[str]:
    existing = table_columns(conn)
    return [column for column in desired if column in existing]


def read_template_rows(conn: Connection, *, unknown_only: bool = False) -> pd.DataFrame:
    desired = [
        "template_row_id",
        "document_id",
        "job_id",
        "source_file",
        "template_type",
        "sheet_name",
        "row_number",
        "template_bucket",
        "line_item_kind",
        "row_label",
        "selected_item_name",
        "raw_text",
        "estimated_cost",
    ]
    columns = select_existing_columns(conn, desired)
    if not columns:
        return pd.DataFrame(columns=desired)
    sql = f"SELECT {', '.join(columns)} FROM {TEMPLATE_ROW_TABLE}"
    if unknown_only and "template_bucket" in columns:
        sql += " WHERE LOWER(COALESCE(template_bucket, '')) IN ('', 'unknown', 'none', 'nan')"
    frame = pd.read_sql_query(text(sql), conn)
    for column in desired:
        if column not in frame.columns:
            frame[column] = None
    return frame[desired]


def summarize_unknown_rows(rows: pd.DataFrame) -> dict[str, Any]:
    unknown = rows[rows["template_bucket"].map(is_unknown_bucket)].copy() if not rows.empty else rows.copy()

    def counts(column: str, limit: int | None = None) -> list[dict[str, Any]]:
        if unknown.empty or column not in unknown.columns:
            return []
        series = unknown[column].fillna("").astype(str).replace("", "(blank)")
        counted = series.value_counts(dropna=False)
        if limit:
            counted = counted.head(limit)
        return [{"value": str(index), "count": int(value)} for index, value in counted.items()]

    return {
        "total_unknown_rows": int(len(unknown)),
        "unknown_by_template_type": counts("template_type"),
        "unknown_by_sheet_name": counts("sheet_name"),
        "unknown_by_line_item_kind": counts("line_item_kind"),
        "top_unknown_row_labels": counts("row_label", 50),
        "top_unknown_selected_item_names": counts("selected_item_name", 50),
    }


def print_summary(summary: dict[str, Any]) -> None:
    print(f"total unknown rows: {summary.get('total_unknown_rows', 0)}")
    for title, key in [
        ("unknown rows by template_type", "unknown_by_template_type"),
        ("unknown rows by sheet_name", "unknown_by_sheet_name"),
        ("unknown rows by line_item_kind", "unknown_by_line_item_kind"),
        ("top 50 unknown row labels", "top_unknown_row_labels"),
        ("top 50 unknown selected item names", "top_unknown_selected_item_names"),
    ]:
        print(title + ":")
        for row in summary.get(key, []):
            print(f"  {row['value']}: {row['count']}")


def suggest_mapping(row_label: Any, selected_item_name: Any, row_number: Any = None) -> tuple[str, str, str]:
    text_value = norm(f"{row_label} {selected_item_name}")
    if not text_value:
        return "", "unknown", "low"
    if any(token in text_value for token in ["subtotal", "total", "worksheet price", "sales tax", "overhead", "profit", "price per sqft"]):
        return "review_total_or_header", "total", "medium"
    if any(token in text_value for token in ["setup", "set up", "safety", "prep", "power wash", "p wash", "pwash"]):
        return "labor_prep", "labor", "medium"
    if "prime" in text_value and "primer" not in text_value:
        return "labor_prime", "labor", "medium"
    if "base" in text_value and "coat" in text_value:
        return "labor_base", "labor", "medium"
    if "top" in text_value and "coat" in text_value:
        return "labor_top_coat", "labor", "medium"
    if any(token in text_value for token in ["clean", "touch up", "cleanup"]):
        return "labor_cleanup", "labor", "medium"
    if "loading" in text_value:
        return "labor_loading", "labor", "medium"
    if any(token in text_value for token in ["travel", "lodging", "meal"]):
        return "labor_traveling", "labor", "medium"
    if "primer" in text_value:
        return "primer", "material", "medium"
    if any(token in text_value for token in ["silicone", "coating", "topcoat", "top coat"]):
        return "coating", "material", "medium"
    if any(token in text_value for token in ["foam", "spray foam"]):
        return "foam", "material", "medium"
    if any(token in text_value for token in ["seam", "fabric"]):
        return "seam_treatment", "material", "medium"
    if any(token in text_value for token in ["fastener", "screw", "plate"]):
        return "fastener_treatment", "material", "medium"
    if any(token in text_value for token in ["caulk", "sealant", "pitch pocket", "penetration"]):
        return "caulk_detail", "material", "medium"
    if any(token in text_value for token in ["lift", "boom", "scissor"]):
        return "lift", "equipment", "medium"
    if "generator" in text_value:
        return "generator", "equipment", "medium"
    if any(token in text_value for token in ["dumpster", "disposal", "freight", "delivery"]):
        return "misc_equipment", "equipment", "low"
    return "", "unknown", "low"


def _sample_values(series: pd.Series, limit: int = 5) -> str:
    values = [str(value) for value in series.dropna().astype(str).unique() if str(value).strip()]
    return " | ".join(values[:limit])


def _known_context_index(all_rows: pd.DataFrame) -> dict[tuple[str, str], pd.DataFrame]:
    if all_rows.empty:
        return {}
    known = all_rows[~all_rows["template_bucket"].map(is_unknown_bucket)].copy()
    known = known[pd.to_numeric(known["row_number"], errors="coerce").notna()].copy()
    index: dict[tuple[str, str], pd.DataFrame] = {}
    for key, group in known.groupby(["document_id", "sheet_name"], dropna=False):
        index[(str(key[0]), str(key[1]))] = group.sort_values("row_number").copy()
    return index


def _nearby_buckets(cluster_rows: pd.DataFrame, context_index: dict[tuple[str, str], pd.DataFrame], *, above: bool) -> str:
    buckets: dict[str, int] = {}
    for _, row in cluster_rows.head(20).iterrows():
        row_number = pd.to_numeric(pd.Series([row.get("row_number")]), errors="coerce").iloc[0]
        if pd.isna(row_number):
            continue
        context = context_index.get((str(row.get("document_id")), str(row.get("sheet_name"))))
        if context is None or context.empty:
            continue
        context_row_numbers = pd.to_numeric(context["row_number"], errors="coerce")
        if above:
            nearby = context[(context_row_numbers < row_number) & (context_row_numbers >= row_number - 5)]
        else:
            nearby = context[(context_row_numbers > row_number) & (context_row_numbers <= row_number + 5)]
        for _, nearby_row in nearby.iterrows():
            key = f"{nearby_row.get('template_bucket')}/{nearby_row.get('line_item_kind')}"
            buckets[key] = buckets.get(key, 0) + 1
    ranked = sorted(buckets.items(), key=lambda item: item[1], reverse=True)
    return " | ".join(f"{bucket} ({count})" for bucket, count in ranked[:5])


def build_unknown_clusters(unknown_rows: pd.DataFrame, all_rows: pd.DataFrame | None = None, *, limit: int = 500) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if unknown_rows.empty:
        empty_clusters = pd.DataFrame(columns=UNKNOWN_EXPORT_COLUMNS)
        return empty_clusters, pd.DataFrame(), pd.DataFrame(columns=MAPPING_COLUMNS)
    rows = unknown_rows.copy()
    rows["source_file_pattern"] = rows["source_file"].map(source_file_pattern)
    group_cols = ["template_type", "sheet_name", "row_number", "row_label", "selected_item_name", "line_item_kind"]
    context_index = _known_context_index(all_rows if all_rows is not None else pd.DataFrame())
    clusters: list[dict[str, Any]] = []
    sample_rows: list[dict[str, Any]] = []
    mapping_rows: list[dict[str, Any]] = []
    grouped = rows.groupby(group_cols, dropna=False, sort=False)
    for key_values, group in grouped:
        key_values = key_values if isinstance(key_values, tuple) else (key_values,)
        key = dict(zip(group_cols, key_values, strict=False))
        cluster_id = stable_cluster_id([key.get(column) for column in group_cols])
        suggested_bucket, suggested_kind, confidence = suggest_mapping(key.get("row_label"), key.get("selected_item_name"), key.get("row_number"))
        cluster = {
            "cluster_id": cluster_id,
            "row_count": int(len(group)),
            "distinct_file_count": int(group["source_file"].dropna().astype(str).nunique()),
            **key,
            "source_file_pattern": _sample_values(group["source_file_pattern"], 3),
            "sample_source_files": _sample_values(group["source_file"], 5),
            "sample_job_ids": _sample_values(group["job_id"], 5),
            "nearby_known_buckets_above": _nearby_buckets(group, context_index, above=True),
            "nearby_known_buckets_below": _nearby_buckets(group, context_index, above=False),
            "suggested_bucket": suggested_bucket,
            "suggested_line_item_kind": suggested_kind,
            "confidence": confidence,
            "review_status": "needs_review",
        }
        clusters.append(cluster)
        for _, sample in group.head(5).iterrows():
            sample_payload = sample.to_dict()
            sample_payload["cluster_id"] = cluster_id
            sample_rows.append(sample_payload)
        mapping_rows.append(
            {
                "cluster_id": cluster_id,
                "match_template_type": key.get("template_type"),
                "match_sheet_name": key.get("sheet_name"),
                "match_row_number": key.get("row_number"),
                "match_row_label_pattern": key.get("row_label"),
                "match_selected_item_pattern": key.get("selected_item_name"),
                "target_template_bucket": suggested_bucket,
                "target_line_item_kind": suggested_kind if suggested_kind != "unknown" else "",
                "notes": "",
                "approved": False,
            }
        )
    clusters_df = pd.DataFrame(clusters).sort_values(["row_count", "distinct_file_count"], ascending=[False, False]).head(limit)
    keep_cluster_ids = set(clusters_df["cluster_id"])
    samples_df = pd.DataFrame([row for row in sample_rows if row.get("cluster_id") in keep_cluster_ids])
    mapping_df = pd.DataFrame([row for row in mapping_rows if row.get("cluster_id") in keep_cluster_ids], columns=MAPPING_COLUMNS)
    return clusters_df[UNKNOWN_EXPORT_COLUMNS], samples_df, mapping_df


def export_unknown_review(conn: Connection, output_dir: Path | str, *, limit: int = 500) -> dict[str, Path]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    unknown_rows = read_template_rows(conn, unknown_only=True)
    all_rows = read_template_rows(conn, unknown_only=False)
    clusters, samples, mapping = build_unknown_clusters(unknown_rows, all_rows, limit=limit)
    paths = {
        "clusters": output / "unknown_row_clusters.csv",
        "samples": output / "unknown_row_samples.csv",
        "mapping": output / "unknown_mapping_template.csv",
    }
    clusters.to_csv(paths["clusters"], index=False)
    samples.to_csv(paths["samples"], index=False)
    mapping.to_csv(paths["mapping"], index=False)
    return paths


def ensure_original_template_bucket(conn: Connection) -> None:
    if "original_template_bucket" not in table_columns(conn):
        conn.execute(text(f"ALTER TABLE {TEMPLATE_ROW_TABLE} ADD COLUMN original_template_bucket TEXT"))


def _like_pattern(value: Any) -> str | None:
    text_value = str(value or "").strip()
    if not text_value:
        return None
    return text_value.lower() if "%" in text_value else f"%{text_value.lower()}%"


def _conditions_for_mapping(row: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    conditions = ["LOWER(COALESCE(template_bucket, '')) IN ('', 'unknown', 'none', 'nan')"]
    params: dict[str, Any] = {}
    if str(row.get("match_template_type") or "").strip():
        conditions.append("LOWER(COALESCE(template_type, '')) = :template_type")
        params["template_type"] = str(row["match_template_type"]).strip().lower()
    if str(row.get("match_sheet_name") or "").strip():
        conditions.append("LOWER(COALESCE(sheet_name, '')) = :sheet_name")
        params["sheet_name"] = str(row["match_sheet_name"]).strip().lower()
    row_number = pd.to_numeric(pd.Series([row.get("match_row_number")]), errors="coerce").iloc[0]
    if not pd.isna(row_number):
        conditions.append("row_number = :row_number")
        params["row_number"] = int(row_number)
    label_pattern = _like_pattern(row.get("match_row_label_pattern"))
    if label_pattern:
        conditions.append("LOWER(COALESCE(row_label, '')) LIKE :row_label_pattern")
        params["row_label_pattern"] = label_pattern
    selected_pattern = _like_pattern(row.get("match_selected_item_pattern"))
    if selected_pattern:
        conditions.append("LOWER(COALESCE(selected_item_name, '')) LIKE :selected_item_pattern")
        params["selected_item_pattern"] = selected_pattern
    return " AND ".join(conditions), params


def approved_mapping_rows(mapping_path: Path | str) -> list[dict[str, Any]]:
    frame = pd.read_csv(mapping_path).fillna("")
    approved = frame[frame["approved"].map(truthy)].copy() if "approved" in frame.columns else frame.iloc[0:0].copy()
    valid = []
    for row in approved.to_dict(orient="records"):
        target_bucket = str(row.get("target_template_bucket") or "").strip()
        target_kind = str(row.get("target_line_item_kind") or "").strip()
        if not target_bucket or target_bucket == "unknown" or not target_kind or target_kind == "unknown":
            continue
        valid.append(row)
    return valid


def write_parser_rule_output(rows: list[dict[str, Any]], output_dir: Path | str) -> dict[str, Path]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    serializable = [
        {
            "cluster_id": row.get("cluster_id"),
            "template_type": row.get("match_template_type"),
            "sheet_name": row.get("match_sheet_name"),
            "row_number": row.get("match_row_number"),
            "row_label_pattern": row.get("match_row_label_pattern"),
            "selected_item_pattern": row.get("match_selected_item_pattern"),
            "template_bucket": row.get("target_template_bucket"),
            "line_item_kind": row.get("target_line_item_kind"),
            "notes": row.get("notes"),
        }
        for row in rows
    ]
    json_path = output / "approved_unknown_row_mappings.json"
    py_path = output / "approved_unknown_row_mappings.py"
    json_path.write_text(json.dumps(serializable, indent=2, sort_keys=True, default=str), encoding="utf-8")
    py_path.write_text(
        "# Generated by jobscan.estimator.unknown_rows. Review before committing as parser rules.\n"
        "APPROVED_UNKNOWN_ROW_MAPPINGS = "
        + repr(serializable)
        + "\n",
        encoding="utf-8",
    )
    return {"json": json_path, "python": py_path}


def apply_mapping(conn: Connection, mapping_path: Path | str, *, dry_run: bool = False, output_dir: Path | str | None = None) -> pd.DataFrame:
    mappings = approved_mapping_rows(mapping_path)
    if not dry_run:
        ensure_original_template_bucket(conn)
    results: list[dict[str, Any]] = []
    existing_columns = table_columns(conn)
    for row in mappings:
        where_sql, params = _conditions_for_mapping(row)
        count = conn.execute(text(f"SELECT COUNT(*) FROM {TEMPLATE_ROW_TABLE} WHERE {where_sql}"), params).scalar_one()
        results.append(
            {
                "cluster_id": row.get("cluster_id"),
                "target_template_bucket": row.get("target_template_bucket"),
                "target_line_item_kind": row.get("target_line_item_kind"),
                "matched_rows": int(count or 0),
                "dry_run": dry_run,
            }
        )
        if dry_run or not count:
            continue
        set_clauses = [
            "original_template_bucket = COALESCE(original_template_bucket, template_bucket)",
            "template_bucket = :target_template_bucket",
            "line_item_kind = :target_line_item_kind",
        ]
        if "updated_at" in existing_columns:
            set_clauses.append("updated_at = CURRENT_TIMESTAMP")
        update_params = {
            **params,
            "target_template_bucket": row.get("target_template_bucket"),
            "target_line_item_kind": row.get("target_line_item_kind"),
        }
        conn.execute(text(f"UPDATE {TEMPLATE_ROW_TABLE} SET {', '.join(set_clauses)} WHERE {where_sql}"), update_params)
    if output_dir:
        write_parser_rule_output(mappings, output_dir)
    return pd.DataFrame(results)


def print_apply_summary(results: pd.DataFrame, *, dry_run: bool) -> None:
    prefix = "would remap" if dry_run else "remapped"
    if results.empty:
        print("No approved mappings found.")
        return
    grouped = (
        results.groupby(["target_template_bucket", "target_line_item_kind"], dropna=False, as_index=False)["matched_rows"]
        .sum()
        .sort_values("matched_rows", ascending=False)
    )
    total = int(grouped["matched_rows"].sum())
    print(f"{prefix} {total} rows")
    for _, row in grouped.iterrows():
        print(f"  {row['target_template_bucket']} / {row['target_line_item_kind']}: {int(row['matched_rows'])}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Review and map unknown estimate_template_rows.")
    parser.add_argument("--db-url", required=True)
    parser.add_argument("--output-dir", default="output/unknown_rows_review")
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--summary", action="store_true")
    parser.add_argument("--apply-mapping")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    engine: Engine = create_engine(args.db_url)
    output_dir = Path(args.output_dir)
    with engine.begin() as conn:
        if args.summary:
            rows = read_template_rows(conn, unknown_only=False)
            print_summary(summarize_unknown_rows(rows))
            return 0
        if args.apply_mapping:
            results = apply_mapping(conn, args.apply_mapping, dry_run=args.dry_run, output_dir=output_dir)
            print_apply_summary(results, dry_run=args.dry_run)
            return 0
        paths = export_unknown_review(conn, output_dir, limit=args.limit)
    print("Unknown row review exports written:")
    for label, path in paths.items():
        print(f"  {label}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
