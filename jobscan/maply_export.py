from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import bindparam, text
from sqlalchemy.engine import Engine

from jobscan.db_connections import create_resilient_engine

MAPLY_COLUMNS = [
    "Name",
    "Address",
    "Zip/Postal Code",
    "Country",
    "Lat",
    "Lng",
    "Weight",
    "Remarks",
    "Division",
    "Status",
    "Job Type",
    "Job ID",
    "Customer",
    "Job Name",
    "City",
    "State",
    "Phone",
    "Email",
    "Estimate Date",
    "Value",
    "Folder URL",
]

TEMPLATE_BUCKETS = (
    "estimate_date",
    "job_name",
    "site_address",
    "city_state_zip",
    "contact",
    "email",
    "phone",
)


@dataclass(frozen=True)
class ExportSpec:
    division: str | None
    status: str | None
    year: int | None
    output_path: Path


def text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    text_value = str(value).strip()
    if not text_value or text_value.lower() in {"nan", "none", "null"}:
        return None
    return text_value


def first_nonblank(*values: Any) -> str | None:
    for value in values:
        text_value = text_or_none(value)
        if text_value:
            return text_value
    return None


def normalize_key(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def split_city_state_zip(value: Any) -> tuple[str | None, str | None, str | None]:
    text_value = text_or_none(value)
    if not text_value:
        return None, None, None
    match = re.match(r"^\s*(?P<city>.*?)[,\s]+(?P<state>[A-Z]{2})\s+(?P<zip>\d{5}(?:-\d{4})?)\s*$", text_value)
    if match:
        return match.group("city").strip(" ,") or None, match.group("state"), match.group("zip")
    zip_match = re.search(r"\b(\d{5}(?:-\d{4})?)\b", text_value)
    state_match = re.search(r"\b([A-Z]{2})\b", text_value)
    city = text_value
    if zip_match:
        city = city.replace(zip_match.group(1), "")
    if state_match:
        city = city.replace(state_match.group(1), "")
    city = city.strip(" ,")
    return city or None, state_match.group(1) if state_match else None, zip_match.group(1) if zip_match else None


def parse_cell_values(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    text_value = text_or_none(value)
    if not text_value:
        return {}
    try:
        parsed = json.loads(text_value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def template_value(row: pd.Series, cell_ref: str) -> str | None:
    cell_values = parse_cell_values(row.get("cell_values"))
    return first_nonblank(cell_values.get(cell_ref), row.get("row_label"), row.get("selected_item_name"))


def read_sql_df(engine: Engine, query: str, params: dict[str, Any] | None = None) -> pd.DataFrame:
    with engine.connect() as connection:
        return pd.read_sql_query(text(query), connection, params=params or {})


def load_jobs_df(engine: Engine) -> pd.DataFrame:
    try:
        return read_sql_df(engine, "SELECT * FROM dashboard_jobs")
    except Exception:
        return read_sql_df(engine, "SELECT * FROM jobs")


def load_template_fallbacks_df(engine: Engine) -> pd.DataFrame:
    statement = text(
        """
        SELECT job_id, template_bucket, row_label, selected_item_name, cell_values
        FROM estimate_template_rows
        WHERE job_id IS NOT NULL
          AND template_bucket IN :buckets
        """
    ).bindparams(bindparam("buckets", expanding=True))
    try:
        with engine.connect() as connection:
            rows = pd.read_sql_query(statement, connection, params={"buckets": list(TEMPLATE_BUCKETS)})
    except Exception as first_exc:
        rows = read_sql_df(
            engine,
            """
            SELECT job_id, template_bucket, row_label, selected_item_name, cell_values
            FROM estimate_template_rows
            WHERE job_id IS NOT NULL
            """,
        ) if "syntax error" in str(first_exc).lower() else pd.DataFrame()
        if not rows.empty and "template_bucket" in rows.columns:
            rows = rows[rows["template_bucket"].isin(TEMPLATE_BUCKETS)].copy()
    if rows.empty:
        return pd.DataFrame(columns=["job_id"])

    cell_by_bucket = {
        "estimate_date": "C1",
        "job_name": "C2",
        "site_address": "C4",
        "city_state_zip": "C5",
        "contact": "C6",
        "email": "C8",
        "phone": "C9",
    }
    fallback_rows: dict[str, dict[str, Any]] = {}
    for _, row in rows.iterrows():
        job_id = text_or_none(row.get("job_id"))
        bucket = text_or_none(row.get("template_bucket"))
        if not job_id or not bucket:
            continue
        out = fallback_rows.setdefault(job_id, {"job_id": job_id})
        value = template_value(row, cell_by_bucket.get(bucket, ""))
        if value and not out.get(f"template_{bucket}"):
            out[f"template_{bucket}"] = value
    return pd.DataFrame(list(fallback_rows.values())) if fallback_rows else pd.DataFrame(columns=["job_id"])


def apply_template_fallbacks(jobs: pd.DataFrame, template_fallbacks: pd.DataFrame) -> pd.DataFrame:
    if jobs.empty:
        return jobs.copy()
    df = jobs.copy()
    if "job_id" not in df.columns:
        df["job_id"] = ""
    df["job_id"] = df["job_id"].fillna("").astype(str)
    if not template_fallbacks.empty:
        template = template_fallbacks.copy()
        template["job_id"] = template["job_id"].fillna("").astype(str)
        df = df.merge(template, on="job_id", how="left")
    for column in (
        "customer",
        "job_name",
        "site_address",
        "city",
        "state",
        "zip_code",
        "contact_name",
        "contact_email",
        "contact_phone",
        "estimate_date",
    ):
        if column not in df.columns:
            df[column] = None

    df["job_name"] = df.apply(lambda row: first_nonblank(row.get("job_name"), row.get("template_job_name")), axis=1)
    df["site_address"] = df.apply(
        lambda row: first_nonblank(row.get("site_address"), row.get("template_site_address")),
        axis=1,
    )
    df["contact_name"] = df.apply(lambda row: first_nonblank(row.get("contact_name"), row.get("template_contact")), axis=1)
    df["contact_email"] = df.apply(lambda row: first_nonblank(row.get("contact_email"), row.get("template_email")), axis=1)
    df["contact_phone"] = df.apply(lambda row: first_nonblank(row.get("contact_phone"), row.get("template_phone")), axis=1)
    df["estimate_date"] = df.apply(lambda row: first_nonblank(row.get("estimate_date"), row.get("template_estimate_date")), axis=1)

    city_state_zip = df.get("template_city_state_zip")
    if city_state_zip is not None:
        split_values = city_state_zip.apply(split_city_state_zip)
        df["_template_city"] = split_values.apply(lambda value: value[0])
        df["_template_state"] = split_values.apply(lambda value: value[1])
        df["_template_zip"] = split_values.apply(lambda value: value[2])
        df["city"] = df.apply(lambda row: first_nonblank(row.get("city"), row.get("_template_city")), axis=1)
        df["state"] = df.apply(lambda row: first_nonblank(row.get("state"), row.get("_template_state")), axis=1)
        df["zip_code"] = df.apply(lambda row: first_nonblank(row.get("zip_code"), row.get("_template_zip")), axis=1)
    return df


def filter_jobs(df: pd.DataFrame, *, year: int | None, divisions: list[str], statuses: list[str]) -> pd.DataFrame:
    out = df.copy()
    if year is not None:
        year_text = str(year)
        masks = []
        if "source_year" in out.columns:
            masks.append(out["source_year"].astype(str).str.replace(r"\.0$", "", regex=True) == year_text)
        if "estimate_date" in out.columns:
            masks.append(out["estimate_date"].astype(str).str[:4] == year_text)
        if "scan_root" in out.columns:
            masks.append(out["scan_root"].astype(str).str.contains(year_text, na=False))
        if masks:
            mask = masks[0]
            for extra in masks[1:]:
                mask = mask | extra
            out = out[mask].copy()
    if divisions:
        division_keys = {normalize_key(value) for value in divisions}
        out = out[out.get("division", pd.Series(dtype=str)).apply(normalize_key).isin(division_keys)].copy()
    if statuses:
        status_keys = {normalize_key(value) for value in statuses}
        pipeline = out.get("pipeline_status", pd.Series(index=out.index, dtype=object)).apply(normalize_key)
        status = out.get("status", pd.Series(index=out.index, dtype=object)).apply(normalize_key)
        out = out[pipeline.isin(status_keys) | status.isin(status_keys)].copy()
    return out


def dedupe_sites(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    out["_site_key"] = out.apply(
        lambda row: "||".join(
            [
                normalize_key(row.get("site_address")),
                normalize_key(row.get("city")),
                normalize_key(row.get("state")),
                normalize_key(row.get("zip_code")),
            ]
        ),
        axis=1,
    )
    out = out.sort_values(["estimated_value"], ascending=False, na_position="last") if "estimated_value" in out.columns else out
    out = out.drop_duplicates("_site_key", keep="first").drop(columns=["_site_key"])
    return out


def maply_name(row: pd.Series) -> str:
    return first_nonblank(row.get("job_name"), row.get("customer"), row.get("folder_name"), row.get("job_id")) or "Unnamed Job"


def maply_address(row: pd.Series) -> str:
    return first_nonblank(row.get("site_address"), row.get("folder_path")) or ""


def row_remarks(row: pd.Series) -> str:
    remarks: list[str] = []
    if not text_or_none(row.get("site_address")):
        remarks.append("Missing address")
    if not text_or_none(row.get("job_name")):
        remarks.append("Missing job name")
    if not text_or_none(row.get("zip_code")):
        remarks.append("Missing zip")
    warnings = text_or_none(row.get("warnings"))
    if warnings:
        remarks.append(warnings)
    return "; ".join(remarks)


def to_maply_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=MAPLY_COLUMNS)
    out = pd.DataFrame()
    out["Name"] = df.apply(maply_name, axis=1)
    out["Address"] = df.apply(maply_address, axis=1)
    out["Zip/Postal Code"] = df.get("zip_code", pd.Series(index=df.index, dtype=object)).fillna("")
    out["Country"] = "USA"
    out["Lat"] = df.get("lat", df.get("latitude", pd.Series(index=df.index, dtype=object))).fillna("")
    out["Lng"] = df.get("lng", df.get("longitude", pd.Series(index=df.index, dtype=object))).fillna("")
    out["Weight"] = pd.to_numeric(df.get("estimated_value", pd.Series(index=df.index)), errors="coerce").fillna("")
    out["Remarks"] = df.apply(row_remarks, axis=1)
    out["Division"] = df.get("division", pd.Series(index=df.index, dtype=object)).fillna("")
    out["Status"] = df.apply(lambda row: first_nonblank(row.get("pipeline_status"), row.get("status")) or "", axis=1)
    out["Job Type"] = df.get("job_type", pd.Series(index=df.index, dtype=object)).fillna("")
    out["Job ID"] = df.get("job_id", pd.Series(index=df.index, dtype=object)).fillna("")
    out["Customer"] = df.get("customer", pd.Series(index=df.index, dtype=object)).fillna("")
    out["Job Name"] = df.get("job_name", pd.Series(index=df.index, dtype=object)).fillna("")
    out["City"] = df.get("city", pd.Series(index=df.index, dtype=object)).fillna("")
    out["State"] = df.get("state", pd.Series(index=df.index, dtype=object)).fillna("")
    out["Phone"] = df.get("contact_phone", pd.Series(index=df.index, dtype=object)).fillna("")
    out["Email"] = df.get("contact_email", pd.Series(index=df.index, dtype=object)).fillna("")
    out["Estimate Date"] = df.get("estimate_date", pd.Series(index=df.index, dtype=object)).fillna("")
    out["Value"] = pd.to_numeric(df.get("estimated_value", pd.Series(index=df.index)), errors="coerce").fillna("")
    out["Folder URL"] = df.get("folder_url", pd.Series(index=df.index, dtype=object)).fillna("")
    return out[MAPLY_COLUMNS]


def export_summary(df: pd.DataFrame, path: Path) -> dict[str, int | str]:
    missing_name = int((df["Name"].fillna("").astype(str).str.strip() == "").sum()) if "Name" in df.columns else 0
    missing_address = int((df["Address"].fillna("").astype(str).str.strip() == "").sum()) if "Address" in df.columns else 0
    missing_zip = int((df["Zip/Postal Code"].fillna("").astype(str).str.strip() == "").sum()) if "Zip/Postal Code" in df.columns else 0
    return {
        "path": str(path),
        "rows": len(df),
        "missing_name": missing_name,
        "missing_address": missing_address,
        "missing_zip": missing_zip,
    }


def write_maply_csv(df: pd.DataFrame, path: Path) -> dict[str, int | str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    summary = export_summary(df, path)
    print(
        f"{path}: rows={summary['rows']}, missing_name={summary['missing_name']}, "
        f"missing_address={summary['missing_address']}, missing_zip={summary['missing_zip']}"
    )
    return summary


def build_export_dataframe(
    engine: Engine,
    *,
    year: int | None = None,
    divisions: list[str] | None = None,
    statuses: list[str] | None = None,
    dedupe: bool = False,
) -> pd.DataFrame:
    jobs = load_jobs_df(engine)
    fallbacks = load_template_fallbacks_df(engine)
    merged = apply_template_fallbacks(jobs, fallbacks)
    filtered = filter_jobs(merged, year=year, divisions=divisions or [], statuses=statuses or [])
    if dedupe:
        filtered = dedupe_sites(filtered)
    return to_maply_dataframe(filtered)


def standard_export_specs(year: int, out_dir: Path) -> list[ExportSpec]:
    return [
        ExportSpec("Roofing", "Contracted", year, out_dir / f"roofing_contracted_{year}.csv"),
        ExportSpec("Roofing", "Completed", year, out_dir / f"roofing_completed_{year}.csv"),
        ExportSpec("Insulation", "Contracted", year, out_dir / f"insulation_contracted_{year}.csv"),
        ExportSpec("Insulation", "Completed", year, out_dir / f"insulation_completed_{year}.csv"),
    ]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Export Spray-Tec jobs to Maply-compatible CSV files.")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL") or os.getenv("NEON_DATABASE_URL"))
    parser.add_argument("--out", type=Path, default=Path("output/maply/maply_jobs.csv"))
    parser.add_argument("--out-dir", type=Path, default=Path("output/maply"))
    parser.add_argument("--year", type=int)
    parser.add_argument("--division", action="append", default=[])
    parser.add_argument("--status", action="append", default=[])
    parser.add_argument("--standard-exports", action="store_true")
    parser.add_argument("--dedupe-sites", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.database_url:
        raise SystemExit("Set --database-url, DATABASE_URL, or NEON_DATABASE_URL.")
    engine = create_resilient_engine(args.database_url)
    if args.standard_exports:
        year = args.year or 2025
        for spec in standard_export_specs(year, args.out_dir):
            df = build_export_dataframe(
                engine,
                year=spec.year,
                divisions=[spec.division] if spec.division else [],
                statuses=[spec.status] if spec.status else [],
                dedupe=args.dedupe_sites,
            )
            write_maply_csv(df, spec.output_path)
        return 0

    df = build_export_dataframe(
        engine,
        year=args.year,
        divisions=args.division,
        statuses=args.status,
        dedupe=args.dedupe_sites,
    )
    write_maply_csv(df, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
