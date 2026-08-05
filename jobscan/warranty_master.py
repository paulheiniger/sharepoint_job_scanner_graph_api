from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

from psycopg2.extras import execute_values
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from jobscan.env import load_project_env
from jobscan.warranty_sources import (
    WarrantySourceRecord,
    add_years,
    clean,
    parse_date,
    stable_id,
    write_records,
    years_between,
)


CUSTOMER_SOURCE = "vsimple_customer_export"
PROJECT_SOURCE = "vsimple_project_warranty_export"
RECENT_SOURCE = "recent_completed_warranty_list"
MONTHS = {
    name.lower(): number
    for number, name in enumerate(
        (
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ),
        start=1,
    )
}


def workbook_rows(path: Path) -> Iterable[tuple[str, int, dict[str, Any]]]:
    import openpyxl

    workbook = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        for sheet in workbook.worksheets:
            iterator = sheet.iter_rows(values_only=True)
            headers: list[str] | None = None
            header_row = 0
            for row_number, row in enumerate(iterator, start=1):
                values = [clean(value) for value in row]
                if not any(values):
                    continue
                if headers is None:
                    headers = values
                    header_row = row_number
                    continue
                data = {
                    headers[index]: row[index]
                    for index in range(min(len(headers), len(row)))
                    if headers[index]
                }
                if any(clean(value) for value in data.values()):
                    yield sheet.title, row_number, data
            if headers is None:
                continue
    finally:
        workbook.close()


def value(row: dict[str, Any], *aliases: str) -> Any:
    normalized = {re.sub(r"\s+", " ", key.strip().lower()): item for key, item in row.items()}
    for alias in aliases:
        key = re.sub(r"\s+", " ", alias.strip().lower())
        if key in normalized:
            return normalized[key]
    return None


def nonblank(value_: Any) -> str | None:
    cleaned = clean(value_)
    return cleaned if cleaned and cleaned != "[]" else None


def parse_project_id(row: dict[str, Any]) -> str:
    explicit = nonblank(value(row, "Vsimple Id", "id"))
    if explicit:
        return explicit
    match = re.search(r"/([0-9]+)(?:[/?#]|$)", clean(value(row, "URL", "Vsimple URL")))
    return match.group(1) if match else ""


def parse_contact_ids(row: dict[str, Any]) -> list[str]:
    values = " ".join(
        filter(
            None,
            [
                nonblank(value(row, "contact_info")),
                nonblank(value(row, "associated_contact_id")),
            ],
        )
    )
    return list(dict.fromkeys(re.findall(r"[0-9]+", values)))


def parse_component_date(row: dict[str, Any], prefix: str) -> date | None:
    year_raw = nonblank(value(row, f"{prefix} - Year"))
    month_raw = nonblank(value(row, f"{prefix} - Month"))
    day_raw = nonblank(value(row, f"{prefix} - Day"))
    if not year_raw or not month_raw or not day_raw:
        return None
    try:
        month = MONTHS.get(month_raw.lower(), int(float(month_raw)) if month_raw.replace(".", "", 1).isdigit() else 0)
        return date(int(float(year_raw)), month, int(float(day_raw)))
    except (TypeError, ValueError):
        return None


def parse_duration(value_: Any) -> float | None:
    if isinstance(value_, (int, float)) and not isinstance(value_, bool):
        numeric = float(value_)
        return numeric if 0 < numeric <= 30 else None
    raw = clean(value_)
    if not raw or raw.lower() in {"tbd", "??", "n/a", "none"}:
        return None
    match = re.search(r"(?i)\b(\d{1,2})(?:\.0+)?\s*(?:year|yr)?\b", raw)
    if not match:
        return None
    numeric = float(match.group(1))
    return numeric if 0 < numeric <= 30 else None


def warranty_category(term: str, warranty_type: str) -> str:
    identity = f"{term} {warranty_type}".lower()
    if "workmanship" in identity or "spray-tec" in identity or "spray tec" in identity:
        return "workmanship"
    if any(token in identity for token in ("manufacturer", "system", "gaco", "gaf")):
        return "manufacturer_system"
    return "unspecified"


def load_customers(path: Path) -> list[dict[str, Any]]:
    customers: list[dict[str, Any]] = []
    for sheet, row_number, row in workbook_rows(path):
        customer_id = nonblank(value(row, "id"))
        if not customer_id:
            continue
        customers.append(
            {
                "customer_id": customer_id,
                "record_id": nonblank(value(row, "record_id")),
                "display_name": nonblank(value(row, "Name")),
                "first_name": nonblank(value(row, "first_name")),
                "last_name": nonblank(value(row, "last_name")),
                "company_name": nonblank(value(row, "company_name")),
                "job_title": nonblank(value(row, "job_title")),
                "email": nonblank(value(row, "email")),
                "mobile_phone": nonblank(value(row, "mobile_phone_number")),
                "phone": nonblank(value(row, "phone_number")),
                "address": nonblank(value(row, "address", "office_address")),
                "city": nonblank(value(row, "city", "office_address_city")),
                "state": nonblank(value(row, "state", "office_address_state")),
                "postal_code": nonblank(value(row, "billing_zip", "office_address_zip")),
                "vsimple_url": nonblank(value(row, "URL")),
                "source_file": path.name,
                "source_sheet": sheet,
                "source_row": row_number,
                "raw": {key: nonblank(item) for key, item in row.items()},
            }
        )
    return customers


def _project_payload(
    path: Path,
    sheet: str,
    row_number: int,
    row: dict[str, Any],
    *,
    recent: bool,
) -> tuple[dict[str, Any], WarrantySourceRecord] | None:
    vsimple_id = parse_project_id(row)
    project_name = nonblank(value(row, "Name"))
    term = nonblank(value(row, "Warranty", "warranty"))
    if recent and not term:
        term = "Reported warranty; term not captured"
    if not vsimple_id or not term or not project_name:
        return None
    completion = (
        parse_date(value(row, "Date of Completion", "Install/Completion Date"))
        or parse_component_date(row, "completion_date")
    )
    explicit_start = (
        parse_date(value(row, "Warranty Start Date"))
        or parse_component_date(row, "warranty_signed_date")
    )
    start = explicit_start or completion
    expiration = (
        parse_date(value(row, "Warranty Expiration Date"))
        or parse_component_date(row, "warranty_expiration_date")
    )
    duration = parse_duration(term) or years_between(start, expiration)
    calculated_expiration = add_years(start, duration)
    if expiration is None:
        expiration = calculated_expiration
    type_ = nonblank(value(row, "warranty_type", "Warranty Type"))
    provider = nonblank(value(row, "Manufacturer", "spray_tec_system", "System To Be Installed"))
    source_system = RECENT_SOURCE if recent else PROJECT_SOURCE
    vsimple_url = nonblank(value(row, "Vsimple URL", "URL"))
    sharepoint_url = nonblank(value(row, "Sharepoint URL", "sharepoint_url"))
    reported_contact_name = nonblank(value(row, "Contact Name", "bill_to_contact"))
    if not reported_contact_name:
        reported_contact_name = nonblank(
            " ".join(
                filter(
                    None,
                    [
                        nonblank(value(row, "contact_first_name")),
                        nonblank(value(row, "contact_last_name")),
                    ],
                )
            )
        )
    reported_contact_email = nonblank(value(row, "Contact Email", "bill_to_email_address", "contact_email"))
    reported_contact_phone = nonblank(value(row, "Contact Phone", "bill_to_phone", "contact_phone"))
    project = {
        "vsimple_id": vsimple_id,
        "project_name": project_name,
        "customer_name": nonblank(value(row, "name_of_building_or_customer", "bill_to_contact")),
        "project_status": nonblank(value(row, "Status")),
        "division": "Roofing" if "roof" in project_name.lower() else None,
        "site_address": nonblank(value(row, "Street Address", "street_address", "street_address_warranty")),
        "city": nonblank(value(row, "City", "city_state_zip", "city_warranty")),
        "state": nonblank(value(row, "State", "state", "state_warranty")),
        "postal_code": nonblank(value(row, "Zip", "zip")),
        "warranty_term_raw": term,
        "warranty_type": type_,
        "provider": provider,
        "duration_years": duration,
        "completion_date": completion,
        "start_date": start,
        "expiration_date": expiration,
        "expiration_date_source": (
            "vsimple_reported_expiration"
            if expiration and expiration != calculated_expiration
            else "start_plus_reported_duration"
            if expiration
            else None
        ),
        "warranty_number": nonblank(value(row, "warranty_number", "Warranty Number")),
        "reported_contact_name": reported_contact_name,
        "reported_contact_email": reported_contact_email,
        "reported_contact_phone": reported_contact_phone,
        "vsimple_url": vsimple_url,
        "sharepoint_url": sharepoint_url,
        "source_file": path.name,
        "source_sheet": sheet,
        "source_row": row_number,
        "raw": {key: nonblank(item) for key, item in row.items()},
    }
    source_record = WarrantySourceRecord(
        source_record_id=stable_id(source_system, vsimple_id, path.name, row_number),
        source_system=source_system,
        source_file=path.name,
        source_sheet=sheet,
        source_row=row_number,
        source_locator=f"{sheet}!A{row_number}",
        source_url=vsimple_url,
        vsimple_id=vsimple_id,
        reported_name=project_name,
        reported_customer=project["customer_name"] or project_name,
        reported_address=project["site_address"],
        reported_city=project["city"],
        reported_state=project["state"],
        division=project["division"],
        source_year=(completion or start or expiration).year if (completion or start or expiration) else None,
        reported_status="reported",
        warranty_category=warranty_category(term, type_ or ""),
        warranty_type=type_ or term,
        provider=provider,
        duration_years=duration,
        start_date=start,
        expiration_date=expiration,
        expiration_date_source=project["expiration_date_source"],
        has_date_conflict=bool(
            expiration
            and calculated_expiration
            and abs((expiration - calculated_expiration).days) > 31
        ),
        coverage_summary=nonblank(value(row, "Scope of Work", "scope_of_work", "Description", "project_description"))
        or "Warranty term reported in VSimple project export",
        extraction_method="recent_warranty_project_columns_v1" if recent else "vsimple_project_warranty_columns_v1",
        extraction_confidence="high" if vsimple_id and duration else "medium",
        raw=project["raw"],
    )
    return project, source_record


def load_projects(
    path: Path,
    *,
    recent: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[WarrantySourceRecord]]:
    projects: list[dict[str, Any]] = []
    relationships: list[dict[str, Any]] = []
    sources: list[WarrantySourceRecord] = []
    for sheet, row_number, row in workbook_rows(path):
        payload = _project_payload(path, sheet, row_number, row, recent=recent)
        if payload is None:
            continue
        project, source = payload
        projects.append(project)
        sources.append(source)
        for customer_id in parse_contact_ids(row):
            relationships.append(
                {
                    "vsimple_id": project["vsimple_id"],
                    "customer_id": customer_id,
                    "relationship_source": "contact_info" if nonblank(value(row, "contact_info")) else "associated_contact_id",
                    "source_file": path.name,
                    "source_sheet": sheet,
                    "source_row": row_number,
                }
            )
    return projects, relationships, sources


def ensure_schema(engine: Engine) -> None:
    schema = Path(__file__).resolve().parents[1] / "db" / "warranty_master_clean.sql"
    statements = [statement.strip() for statement in schema.read_text(encoding="utf-8").split(";") if statement.strip()]
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def match_project_sources(records: list[WarrantySourceRecord], engine: Engine) -> None:
    """Resolve exact VSimple identities and only attach high-confidence job matches."""
    if not records:
        return
    vsimple_ids = sorted({record.vsimple_id for record in records if record.vsimple_id})
    with engine.connect() as connection:
        known_projects = {
            clean(row["vsimple_id"])
            for row in connection.execute(
                text("SELECT vsimple_id FROM vsimple_projects WHERE vsimple_id = ANY(:vsimple_ids)"),
                {"vsimple_ids": vsimple_ids},
            ).mappings()
        }
        strict_matches = {
            clean(row["vsimple_id"]): dict(row)
            for row in connection.execute(
                text(
                    """
                    SELECT vsimple_id, job_id, match_score
                    FROM vsimple_sharepoint_job_matches
                    WHERE vsimple_id = ANY(:vsimple_ids)
                      AND match_status = 'matched'
                    """
                ),
                {"vsimple_ids": vsimple_ids},
            ).mappings()
        }
    for record in records:
        vsimple_id = clean(record.vsimple_id)
        if not vsimple_id:
            continue
        if vsimple_id in known_projects:
            record.matched_vsimple_id = vsimple_id
            record.match_method = "vsimple_id"
            record.match_confidence = "high"
            record.match_score = 1.0
            record.match_review_required = False
        strict = strict_matches.get(vsimple_id)
        if strict and clean(strict.get("job_id")):
            record.matched_job_id = clean(strict["job_id"])
            record.match_method = "vsimple_id_strict_job_match"
            record.match_score = float(strict.get("match_score") or 0)


def _json_payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = dict(row)
    payload["raw"] = json.dumps(payload.get("raw") or {}, default=str)
    return payload


def write_customers(engine: Engine, customers: list[dict[str, Any]]) -> int:
    if not customers:
        return 0
    statement = """
        INSERT INTO vsimple_customers_clean (
            customer_id, record_id, display_name, first_name, last_name, company_name,
            job_title, email, mobile_phone, phone, address, city, state, postal_code,
            vsimple_url, source_file, source_sheet, source_row, raw, imported_at, updated_at
        ) VALUES %s
        ON CONFLICT (customer_id) DO UPDATE SET
            record_id = EXCLUDED.record_id,
            display_name = EXCLUDED.display_name,
            first_name = EXCLUDED.first_name,
            last_name = EXCLUDED.last_name,
            company_name = EXCLUDED.company_name,
            job_title = EXCLUDED.job_title,
            email = EXCLUDED.email,
            mobile_phone = EXCLUDED.mobile_phone,
            phone = EXCLUDED.phone,
            address = EXCLUDED.address,
            city = EXCLUDED.city,
            state = EXCLUDED.state,
            postal_code = EXCLUDED.postal_code,
            vsimple_url = EXCLUDED.vsimple_url,
            source_file = EXCLUDED.source_file,
            source_sheet = EXCLUDED.source_sheet,
            source_row = EXCLUDED.source_row,
            raw = EXCLUDED.raw,
            updated_at = NOW()
        """
    payloads = [_json_payload(row) for row in customers]
    columns = (
        "customer_id", "record_id", "display_name", "first_name", "last_name",
        "company_name", "job_title", "email", "mobile_phone", "phone", "address",
        "city", "state", "postal_code", "vsimple_url", "source_file", "source_sheet",
        "source_row", "raw",
    )
    template = "(" + ", ".join(["%s"] * 18 + ["%s::jsonb", "NOW()", "NOW()"]) + ")"
    raw_connection = engine.raw_connection()
    cursor = None
    try:
        cursor = raw_connection.cursor()
        execute_values(
            cursor,
            statement,
            [tuple(row[column] for column in columns) for row in payloads],
            template=template,
            page_size=500,
        )
        raw_connection.commit()
    except Exception:
        raw_connection.rollback()
        raise
    finally:
        if cursor is not None:
            cursor.close()
        raw_connection.close()
    return len(customers)


def write_projects(engine: Engine, projects: list[dict[str, Any]]) -> int:
    if not projects:
        return 0
    merged: dict[str, dict[str, Any]] = {}
    for row in projects:
        project_id = row["vsimple_id"]
        if project_id not in merged:
            merged[project_id] = dict(row)
            continue
        current = merged[project_id]
        for key, item in row.items():
            if key == "raw":
                current[key] = {**(current.get(key) or {}), **(item or {})}
            elif item not in (None, ""):
                current[key] = item
    payload_rows = list(merged.values())
    columns = list(payload_rows[0])
    insert_columns = ", ".join(columns + ["imported_at", "updated_at"])
    values = ", ".join(("CAST(:raw AS JSONB)" if column == "raw" else f":{column}") for column in columns)
    updates = ",\n".join(
        f"{column} = COALESCE(EXCLUDED.{column}, vsimple_warranty_projects_clean.{column})"
        for column in columns
        if column not in {"vsimple_id", "raw"}
    )
    statement = text(
        f"""
        INSERT INTO vsimple_warranty_projects_clean ({insert_columns})
        VALUES ({values}, NOW(), NOW())
        ON CONFLICT (vsimple_id) DO UPDATE SET
            {updates},
            raw = vsimple_warranty_projects_clean.raw || EXCLUDED.raw,
            updated_at = NOW()
        """
    )
    with engine.begin() as connection:
        connection.execute(statement, [_json_payload(row) for row in payload_rows])
    return len(payload_rows)


def write_relationships(engine: Engine, relationships: list[dict[str, Any]]) -> int:
    if not relationships:
        return 0
    statement = text(
        """
        INSERT INTO vsimple_project_contacts_clean (
            vsimple_id, customer_id, relationship_source, source_file, source_sheet,
            source_row, imported_at, updated_at
        ) VALUES (
            :vsimple_id, :customer_id, :relationship_source, :source_file, :source_sheet,
            :source_row, NOW(), NOW()
        )
        ON CONFLICT (vsimple_id, customer_id) DO UPDATE SET
            relationship_source = EXCLUDED.relationship_source,
            source_file = EXCLUDED.source_file,
            source_sheet = EXCLUDED.source_sheet,
            source_row = EXCLUDED.source_row,
            updated_at = NOW()
        """
    )
    unique = {(row["vsimple_id"], row["customer_id"]): row for row in relationships}
    with engine.begin() as connection:
        connection.execute(statement, list(unique.values()))
    return len(unique)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build cleaned VSimple warranty contacts and the master warranty registry.")
    parser.add_argument("--customer-export", type=Path)
    parser.add_argument("--project-export", type=Path)
    parser.add_argument("--recent-warranty-list", type=Path)
    parser.add_argument("--database-url", default=os.getenv("NEON_DATABASE_URL") or os.getenv("DATABASE_URL"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output", type=Path, help="Optional JSON audit output for parsed source records.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    load_project_env(Path(".env"))
    args = parse_args(argv)
    if not any((args.customer_export, args.project_export, args.recent_warranty_list)):
        raise SystemExit("Provide at least one customer or project export.")
    if not args.database_url:
        raise SystemExit("Set --database-url, NEON_DATABASE_URL, or DATABASE_URL.")
    customers = load_customers(args.customer_export) if args.customer_export else []
    projects: list[dict[str, Any]] = []
    relationships: list[dict[str, Any]] = []
    sources: list[WarrantySourceRecord] = []
    if args.project_export:
        parsed_projects, parsed_relationships, parsed_sources = load_projects(args.project_export, recent=False)
        projects.extend(parsed_projects)
        relationships.extend(parsed_relationships)
        sources.extend(parsed_sources)
    if args.recent_warranty_list:
        parsed_projects, parsed_relationships, parsed_sources = load_projects(args.recent_warranty_list, recent=True)
        projects.extend(parsed_projects)
        relationships.extend(parsed_relationships)
        sources.extend(parsed_sources)
    engine = create_engine(args.database_url, future=True)
    match_project_sources(sources, engine)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(
                {
                    "customers": customers,
                    "projects": projects,
                    "relationships": relationships,
                    "warranty_sources": [asdict(record) for record in sources],
                },
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
    if args.dry_run:
        written = {"customers": 0, "projects": 0, "relationships": 0, "warranty_sources": 0}
    else:
        ensure_schema(engine)
        written = {
            "customers": write_customers(engine, customers),
            "projects": write_projects(engine, projects),
            "relationships": write_relationships(engine, relationships),
            "warranty_sources": write_records(sources, engine),
        }
    summary = {
        "dry_run": args.dry_run,
        "records_considered": {
            "customers": len(customers),
            "projects": len({row["vsimple_id"] for row in projects}),
            "relationships": len({(row['vsimple_id'], row['customer_id']) for row in relationships}),
            "warranty_sources": len(sources),
        },
        "records_written": written,
        "matched_to_vsimple": sum(bool(record.matched_vsimple_id) for record in sources),
        "matched_to_job": sum(bool(record.matched_job_id) for record in sources),
        "with_duration": sum(record.duration_years is not None for record in sources),
        "with_start_date": sum(record.start_date is not None for record in sources),
        "with_expiration": sum(record.expiration_date is not None for record in sources),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
