from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from jobscan.document_extraction import extract_pdf
from jobscan.vsimple_projects import normalize_match_text, text_ratio


CUSTOMER_SOURCE = "legacy_customer_list"
VSIMPLE_SOURCE = "legacy_vsimple_warranty_export"
SHAREPOINT_SOURCE = "sharepoint_warranty_folder"
GACO_SOURCE = "manufacturer_warranty_list"
DATE_RE = re.compile(r"\b(0?[1-9]|1[0-2])[/.\-](0?[1-9]|[12][0-9]|3[01])[/.\-]((?:19|20)?\d{2})\b")


@dataclass
class WarrantySourceRecord:
    source_record_id: str
    source_system: str
    source_file: str
    source_sheet: str | None = None
    source_row: int | None = None
    source_locator: str | None = None
    source_url: str | None = None
    snapshot_date: date | None = None
    vsimple_id: str | None = None
    reported_name: str | None = None
    reported_customer: str | None = None
    reported_address: str | None = None
    reported_city: str | None = None
    reported_state: str | None = None
    division: str | None = None
    source_year: int | None = None
    reported_status: str | None = None
    warranty_category: str | None = None
    warranty_type: str | None = None
    provider: str | None = None
    duration_years: float | None = None
    start_date: date | None = None
    expiration_date: date | None = None
    expiration_date_source: str | None = None
    has_date_conflict: bool = False
    coverage_summary: str | None = None
    coverage_excerpt: str | None = None
    source_modified_at: datetime | None = None
    matched_vsimple_id: str | None = None
    matched_job_id: str | None = None
    match_method: str | None = None
    match_confidence: str | None = None
    match_score: float | None = None
    match_candidates: list[dict[str, Any]] = field(default_factory=list)
    match_review_required: bool = True
    extraction_method: str = "deterministic"
    extraction_confidence: str = "medium"
    raw: dict[str, Any] | None = None


def clean(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value != value:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def parse_date(value: Any, *, default_year: int | None = None) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = clean(value)
    if not raw:
        return None
    raw = re.sub(r"(?<=\d)\s+(?=\d|/)", "", raw)
    raw = re.sub(r"\b(\d{1,2}/\d{1,2})(20\d{2})\b", r"\1/\2", raw)
    match = DATE_RE.search(raw)
    if match:
        month, day, year = (int(part) for part in match.groups())
        if year < 100:
            year += 2000
        try:
            return date(year, month, day)
        except ValueError:
            return None
    for fmt in ("%Y-%m-%d", "%m/%Y", "%m-%Y"):
        try:
            parsed = datetime.strptime(raw, fmt).date()
            return parsed
        except ValueError:
            pass
    if default_year and re.fullmatch(r"\d{1,2}[./-]\d{1,2}", raw):
        month, day = (int(part) for part in re.split(r"[./-]", raw))
        try:
            return date(default_year, month, day)
        except ValueError:
            return None
    return None


def parse_years(value: Any) -> float | None:
    match = re.search(r"(?i)\b(\d{1,2})\s*(?:year|yr)", clean(value))
    if not match:
        return None
    years = float(match.group(1))
    return years if 0 < years <= 30 else None


def years_between(start: date | None, expiration: date | None) -> float | None:
    if not start or not expiration or expiration <= start:
        return None
    years = round((expiration - start).days / 365.2425)
    return float(years) if 0 < years <= 30 else None


def add_years(value: date | None, years: float | None) -> date | None:
    if not value or not years or int(years) != years:
        return None
    try:
        return value.replace(year=value.year + int(years))
    except ValueError:
        return value.replace(month=2, day=28, year=value.year + int(years))


def stable_id(source_system: str, *parts: Any) -> str:
    raw = "|".join(clean(part).lower() for part in (source_system, *parts))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def rows_from_xlsx(path: Path) -> Iterable[tuple[str, int, dict[str, Any]]]:
    import openpyxl

    workbook = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        for sheet in workbook.worksheets:
            values = list(sheet.iter_rows(values_only=True))
            header_index = next(
                (
                    index
                    for index, row in enumerate(values[:6])
                    if any(clean(cell).lower() in {"folder name", "vsimple id"} for cell in row)
                ),
                None,
            )
            if header_index is None:
                continue
            headers = [clean(value) for value in values[header_index]]
            for row_number, row in enumerate(values[header_index + 1 :], start=header_index + 2):
                data = {headers[index]: row[index] for index in range(min(len(headers), len(row))) if headers[index]}
                if any(clean(value) for value in data.values()):
                    yield sheet.title, row_number, data
    finally:
        workbook.close()


def _value(row: dict[str, Any], *aliases: str) -> Any:
    normalized = {re.sub(r"\s+", " ", key.strip().lower()): value for key, value in row.items()}
    for alias in aliases:
        key = re.sub(r"\s+", " ", alias.strip().lower())
        if key in normalized:
            return normalized[key]
    return None


def load_customer_list(path: Path) -> list[WarrantySourceRecord]:
    records: list[WarrantySourceRecord] = []
    for sheet, row_number, row in rows_from_xlsx(path):
        division = clean(_value(row, "Roofing or Insulation"))
        if "roof" not in division.lower():
            continue
        year_raw = _value(row, "Year Completed or Proposed")
        year_match = re.search(r"20\d{2}", clean(year_raw) or sheet)
        source_year = int(year_match.group()) if year_match else None
        name = clean(_value(row, "Folder Name"))
        start = parse_date(
            _value(row, "Warranty- Month", "Warranty-Month", "Warranty-Date"),
            default_year=source_year,
        )
        duration = parse_years(_value(row, "If warranty-month and year expiration"))
        expiration = add_years(start, duration)
        records.append(
            WarrantySourceRecord(
                source_record_id=stable_id(CUSTOMER_SOURCE, path.name, sheet, row_number, name),
                source_system=CUSTOMER_SOURCE,
                source_file=path.name,
                source_sheet=sheet,
                source_row=row_number,
                source_locator=f"{sheet}!A{row_number}",
                reported_name=name or None,
                reported_customer=name or None,
                reported_address=clean(_value(row, "Address of Building")) or None,
                reported_city=clean(_value(row, "Location- just city")) or None,
                division="Roofing",
                source_year=source_year,
                reported_status="reported",
                warranty_category="unspecified",
                warranty_type="Legacy customer-list warranty",
                duration_years=duration,
                start_date=start,
                expiration_date=expiration,
                expiration_date_source="start_plus_reported_duration" if expiration else None,
                coverage_summary="Warranty recorded in the Spray-Tec customer master list",
                extraction_method="customer_list_columns_v1",
                extraction_confidence="medium",
                raw={key: clean(value) or None for key, value in row.items()},
            )
        )
    return records


def load_vsimple_warranties(path: Path) -> list[WarrantySourceRecord]:
    records: list[WarrantySourceRecord] = []
    for sheet, row_number, row in rows_from_xlsx(path):
        vsimple_id = clean(_value(row, "Vsimple Id"))
        name = clean(_value(row, "Name"))
        status = clean(_value(row, "Status"))
        warranty_type = clean(_value(row, "Warranty Type"))
        start = parse_date(_value(row, "Install/Completion Date"))
        expiration = parse_date(_value(row, "Warranty Expiration Date"))
        duration = parse_years(warranty_type) or years_between(start, expiration)
        calculated_expiration = add_years(start, duration)
        date_conflict = bool(
            expiration
            and calculated_expiration
            and abs((expiration - calculated_expiration).days) > 31
        )
        closed = parse_date(_value(row, "Closed Date/Time"))
        source_year = (start or closed or expiration).year if (start or closed or expiration) else None
        system = clean(_value(row, "Spray Tec System"))
        records.append(
            WarrantySourceRecord(
                source_record_id=stable_id(VSIMPLE_SOURCE, vsimple_id or name, row_number),
                source_system=VSIMPLE_SOURCE,
                source_file=path.name,
                source_sheet=sheet,
                source_row=row_number,
                source_locator=f"{sheet}!A{row_number}:Z{row_number}",
                source_url=clean(_value(row, "Vsimple URL")) or None,
                vsimple_id=vsimple_id or None,
                reported_name=name or None,
                reported_customer=name or None,
                reported_address=clean(_value(row, "Street Address")) or None,
                reported_city=clean(_value(row, "City")) or None,
                reported_state=clean(_value(row, "State")) or None,
                division="Roofing" if "roof" in clean(_value(row, "Record Type", "Project Type", "Deal Type")).lower() else None,
                source_year=source_year,
                reported_status="reported" if "warranty" in status.lower() else status.lower() or "reported",
                warranty_category="workmanship" if "workmanship" in warranty_type.lower() else "manufacturer_system" if warranty_type or system else "unspecified",
                warranty_type=warranty_type or system or "VSimple-reported warranty",
                provider=system or None,
                duration_years=duration,
                start_date=start,
                expiration_date=expiration,
                expiration_date_source="vsimple_reported_expiration" if expiration else None,
                has_date_conflict=date_conflict,
                coverage_summary=clean(_value(row, "Scope of Work", "Description")) or "Warranty status reported in VSimple export",
                coverage_excerpt=clean(_value(row, "Scope of Work", "Description"))[:1200] or None,
                extraction_method="vsimple_warranty_columns_v1",
                extraction_confidence="medium",
                raw={key: clean(value) or None for key, value in row.items()},
            )
        )
    return records


def load_gaco_warranty_list(path: Path, *, source_url: str | None = None) -> list[WarrantySourceRecord]:
    records: list[WarrantySourceRecord] = []
    with path.open(encoding="utf-8-sig", errors="replace", newline="") as handle:
        for row_number, row in enumerate(csv.DictReader(handle), start=2):
            warranty_number = clean(row.get("Warranty Number"))
            name = clean(row.get("Building Name"))
            if not warranty_number and not name:
                continue
            expiration = parse_date(row.get("Expiration Date"))
            coverage = clean(row.get(None) or row.get("") or "")
            if isinstance(row.get(None), list):
                coverage = clean(" ".join(row.get(None) or []))
            records.append(
                WarrantySourceRecord(
                    source_record_id=stable_id(GACO_SOURCE, warranty_number, name),
                    source_system=GACO_SOURCE,
                    source_file=path.name,
                    source_row=row_number,
                    source_locator=f"CSV row {row_number}; warranty {warranty_number}",
                    source_url=source_url,
                    reported_name=name or None,
                    reported_customer=clean(row.get("Building Owner")) or name or None,
                    reported_address=clean(row.get("Street")) or None,
                    reported_city=clean(row.get("City")) or None,
                    reported_state=clean(row.get("State")) or None,
                    division="Roofing",
                    reported_status="reported",
                    warranty_category="manufacturer_system",
                    warranty_type="Gaco manufacturer warranty",
                    provider="Gaco",
                    expiration_date=expiration,
                    expiration_date_source="manufacturer_reported_expiration" if expiration else None,
                    coverage_summary=coverage or "Gaco manufacturer warranty",
                    coverage_excerpt=(
                        f"Warranty {warranty_number}; {clean(row.get('Substrate'))}; "
                        f"{clean(row.get('Sq. Foot'))} sq ft; {coverage}"
                    ).strip("; ")[:1200],
                    extraction_method="gaco_warranty_csv_v1",
                    extraction_confidence="high" if warranty_number and expiration else "medium",
                    raw={key or "coverage": value for key, value in row.items()},
                )
            )
    return records


def _field(text_value: str, label: str, next_label: str) -> str | None:
    match = re.search(
        rf"(?is){re.escape(label)}\s*:?\s*_?\s*(.*?)\s*(?={re.escape(next_label)}\s*:?)",
        text_value,
    )
    if not match:
        return None
    value = re.sub(r"(?:^|\s+)\d+\)\s*$", "", clean(match.group(1))).strip("_")
    return value or None


def _manifest_urls(root: Path) -> dict[str, str]:
    manifest_path = root / ".jobscan_manifest.json"
    if not manifest_path.exists():
        return {}
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    return {
        clean(item.get("name")): clean(item.get("web_url") or item.get("webUrl"))
        for item in payload.get("documents", [])
        if clean(item.get("name"))
    }


def load_warranty_pdfs(root: Path) -> list[WarrantySourceRecord]:
    records: list[WarrantySourceRecord] = []
    urls = _manifest_urls(root)
    for path in sorted(root.glob("*.pdf")):
        result = extract_pdf(path)
        source_text = "\n".join(row.text_content for row in result.rows)
        owner = _field(source_text, "Building Owner", "Warranted Section")
        section = _field(source_text, "Warranted Section", "Building location")
        address = _field(source_text, "Building location", "Materials Manufacturer")
        manufacturer = _field(source_text, "Materials Manufacturer", "Warranty begins")
        warranty_dates = re.search(
            r"(?is)warranty\s+begins\s*:?\s*(.{0,180}?)(?=\n?6\)\s*(?:7)?warranty)",
            source_text,
        )
        date_segment = warranty_dates.group(1) if warranty_dates else ""
        normalized_segment = re.sub(r"(?<=\d)\s+(?=\d|/)", "", date_segment)
        normalized_segment = re.sub(r"\b(\d{1,2}/\d{1,2})(20\d{2})\b", r"\1/\2", normalized_segment)
        parsed_dates = [parse_date(match.group(0)) for match in DATE_RE.finditer(normalized_segment)]
        parsed_dates = [value for value in parsed_dates if value]
        start = parsed_dates[0] if parsed_dates else None
        expiration = next((value for value in parsed_dates[1:] if start and value > start), None)
        duration = years_between(start, expiration)
        reported_name = " - ".join(part for part in (owner, section) if part) or path.stem
        modified = datetime.fromtimestamp(path.stat().st_mtime)
        records.append(
            WarrantySourceRecord(
                source_record_id=stable_id(SHAREPOINT_SOURCE, urls.get(path.name), path.name),
                source_system=SHAREPOINT_SOURCE,
                source_file=path.name,
                source_locator="PDF text",
                source_url=urls.get(path.name) or None,
                reported_name=reported_name,
                reported_customer=owner,
                reported_address=address,
                division="Roofing",
                source_year=start.year if start else None,
                reported_status="issued",
                warranty_category="workmanship",
                warranty_type="Spray-Tec contractor workmanship",
                provider="Spray-Tec",
                duration_years=duration,
                start_date=start,
                expiration_date=expiration,
                expiration_date_source="explicit_warranty_expiration" if expiration else None,
                coverage_summary="Contractor workmanship coverage for the warranted roof section",
                coverage_excerpt=clean(source_text)[:1200],
                source_modified_at=modified,
                extraction_method=f"{result.extraction_method}_warranty_fields_v1",
                extraction_confidence="high" if start else "medium",
                raw={"building_owner": owner, "warranted_section": section, "materials_manufacturer": manufacturer},
            )
        )
    return records


def load_match_candidates(engine: Engine) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    with engine.connect() as connection:
        projects = [
            dict(row)
            for row in connection.execute(
                text(
                    """
                    SELECT vsimple_id, name, job_name, customer, site_address, city_state_zip,
                           completion_date, closed_date, sharepoint_url
                    FROM vsimple_projects
                    """
                )
            ).mappings()
        ]
        accepted = {
            clean(row["vsimple_id"]): dict(row)
            for row in connection.execute(
                text("SELECT vsimple_id, job_id, match_score FROM vsimple_sharepoint_job_matches_accepted")
            ).mappings()
        }
    return projects, accepted


def match_records(records: list[WarrantySourceRecord], engine: Engine) -> None:
    projects, accepted = load_match_candidates(engine)
    by_id = {clean(project.get("vsimple_id")): project for project in projects}
    for record in records:
        direct = accepted.get(clean(record.vsimple_id)) if record.vsimple_id else None
        if direct:
            record.matched_vsimple_id = clean(record.vsimple_id)
            record.matched_job_id = clean(direct.get("job_id")) or None
            record.match_method = "vsimple_id_accepted_job_match"
            record.match_confidence = "high"
            record.match_score = float(direct.get("match_score") or 1)
            record.match_candidates = [
                _match_candidate(by_id.get(clean(record.vsimple_id), {}), record.match_score, direct)
            ]
            record.match_review_required = False
            continue
        if record.vsimple_id and clean(record.vsimple_id) in by_id:
            record.matched_vsimple_id = clean(record.vsimple_id)
            record.match_method = "vsimple_id"
            record.match_confidence = "high"
            record.match_score = 1.0
            record.match_candidates = [_match_candidate(by_id[clean(record.vsimple_id)], 1.0, None)]
            record.match_review_required = False
            continue

        scored: list[tuple[float, dict[str, Any]]] = []
        for project in projects:
            name_score = max(
                text_ratio(record.reported_name, project.get("name")),
                text_ratio(record.reported_name, project.get("job_name")),
                text_ratio(record.reported_customer, project.get("customer")),
            )
            address_score = text_ratio(record.reported_address, project.get("site_address"))
            score = max(name_score, min(1.0, 0.65 * name_score + 0.35 * address_score))
            if record.source_year:
                project_year = re.search(r"20\d{2}", clean(project.get("completion_date") or project.get("closed_date") or project.get("name")))
                if project_year and int(project_year.group()) == record.source_year:
                    score = min(1.0, score + 0.04)
            if score >= 0.55:
                scored.append((score, project))
        scored.sort(key=lambda item: item[0], reverse=True)
        if not scored:
            continue
        record.match_candidates = [
            _match_candidate(project, score, accepted.get(clean(project.get("vsimple_id"))))
            for score, project in scored[:3]
        ]
        best_score, best = scored[0]
        margin = best_score - (scored[1][0] if len(scored) > 1 else 0)
        if best_score >= 0.88 and margin >= 0.06:
            vsimple_id = clean(best.get("vsimple_id"))
            record.matched_vsimple_id = vsimple_id or None
            accepted_match = accepted.get(vsimple_id)
            record.match_method = "name_address_vsimple_match"
            record.match_confidence = "high" if best_score >= 0.95 else "medium"
            record.match_score = round(best_score, 4)
            record.match_review_required = record.match_confidence != "high"
            record.matched_job_id = (
                clean(accepted_match.get("job_id"))
                if accepted_match and not record.match_review_required
                else None
            )
        else:
            record.match_method = "ambiguous_candidate"
            record.match_confidence = "low"
            record.match_score = round(best_score, 4)
            record.match_review_required = True


def _match_candidate(
    project: dict[str, Any],
    score: float,
    accepted_match: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        key: value
        for key, value in {
            "vsimple_id": clean(project.get("vsimple_id")) or None,
            "job_id": clean((accepted_match or {}).get("job_id")) or None,
            "name": clean(project.get("name") or project.get("job_name")) or None,
            "customer": clean(project.get("customer")) or None,
            "site_address": clean(project.get("site_address")) or None,
            "score": round(float(score), 4),
        }.items()
        if value not in (None, "")
    }


def ensure_table(engine: Engine) -> None:
    schema = Path(__file__).resolve().parents[1] / "db" / "warranty_source_records.sql"
    statements = [statement.strip() for statement in schema.read_text(encoding="utf-8").split(";") if statement.strip()]
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def write_records(records: list[WarrantySourceRecord], engine: Engine) -> int:
    ensure_table(engine)
    statement = text(
        """
        INSERT INTO warranty_source_records (
            source_record_id, source_system, source_file, source_sheet, source_row,
            source_locator, source_url, snapshot_date, vsimple_id, reported_name,
            reported_customer, reported_address, reported_city, reported_state, division,
            source_year, reported_status, warranty_category, warranty_type, provider,
            duration_years, start_date, expiration_date, expiration_date_source,
            has_date_conflict, coverage_summary, coverage_excerpt,
            source_modified_at, matched_vsimple_id, matched_job_id, match_method,
            match_confidence, match_score, match_candidates, match_review_required, extraction_method,
            extraction_confidence, raw, imported_at, updated_at
        ) VALUES (
            :source_record_id, :source_system, :source_file, :source_sheet, :source_row,
            :source_locator, :source_url, :snapshot_date, :vsimple_id, :reported_name,
            :reported_customer, :reported_address, :reported_city, :reported_state, :division,
            :source_year, :reported_status, :warranty_category, :warranty_type, :provider,
            :duration_years, :start_date, :expiration_date, :expiration_date_source,
            :has_date_conflict, :coverage_summary, :coverage_excerpt,
            :source_modified_at, :matched_vsimple_id, :matched_job_id, :match_method,
            :match_confidence, :match_score, CAST(:match_candidates AS JSONB), :match_review_required, :extraction_method,
            :extraction_confidence, CAST(:raw AS JSONB), NOW(), NOW()
        )
        ON CONFLICT (source_record_id) DO UPDATE SET
            source_url = EXCLUDED.source_url,
            reported_status = EXCLUDED.reported_status,
            warranty_type = EXCLUDED.warranty_type,
            provider = EXCLUDED.provider,
            duration_years = EXCLUDED.duration_years,
            start_date = EXCLUDED.start_date,
            expiration_date = EXCLUDED.expiration_date,
            expiration_date_source = EXCLUDED.expiration_date_source,
            has_date_conflict = EXCLUDED.has_date_conflict,
            coverage_summary = EXCLUDED.coverage_summary,
            coverage_excerpt = EXCLUDED.coverage_excerpt,
            matched_vsimple_id = EXCLUDED.matched_vsimple_id,
            matched_job_id = EXCLUDED.matched_job_id,
            match_method = EXCLUDED.match_method,
            match_confidence = EXCLUDED.match_confidence,
            match_score = EXCLUDED.match_score,
            match_candidates = EXCLUDED.match_candidates,
            match_review_required = EXCLUDED.match_review_required,
            extraction_method = EXCLUDED.extraction_method,
            extraction_confidence = EXCLUDED.extraction_confidence,
            raw = EXCLUDED.raw,
            updated_at = NOW()
        """
    )
    payloads = []
    for record in records:
        payload = asdict(record)
        payload["raw"] = json.dumps(payload["raw"] or {}, default=str)
        payload["match_candidates"] = json.dumps(payload["match_candidates"], default=str)
        payloads.append(payload)
    with engine.begin() as connection:
        connection.execute(statement, payloads)
    return len(payloads)


def rematch_existing_records(engine: Engine, *, only_unresolved: bool = False) -> dict[str, int]:
    ensure_table(engine)
    condition = "WHERE matched_job_id IS NULL OR match_review_required" if only_unresolved else ""
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                f"""
                SELECT source_record_id, vsimple_id, reported_name, reported_customer,
                       reported_address, source_year
                FROM warranty_source_records
                {condition}
                ORDER BY source_record_id
                """
            )
        ).mappings()
        records = [WarrantySourceRecord(**dict(row), source_system="existing", source_file="existing") for row in rows]
    match_records(records, engine)
    update = text(
        """
        UPDATE warranty_source_records
        SET matched_vsimple_id = :matched_vsimple_id,
            matched_job_id = :matched_job_id,
            match_method = :match_method,
            match_confidence = :match_confidence,
            match_score = :match_score,
            match_candidates = CAST(:match_candidates AS JSONB),
            match_review_required = :match_review_required,
            updated_at = NOW()
        WHERE source_record_id = :source_record_id
        """
    )
    payloads = [
        {
            "source_record_id": record.source_record_id,
            "matched_vsimple_id": record.matched_vsimple_id,
            "matched_job_id": record.matched_job_id,
            "match_method": record.match_method,
            "match_confidence": record.match_confidence,
            "match_score": record.match_score,
            "match_candidates": json.dumps(record.match_candidates, default=str),
            "match_review_required": record.match_review_required,
        }
        for record in records
    ]
    if payloads:
        with engine.begin() as connection:
            connection.execute(update, payloads)
    return {
        "records_considered": len(records),
        "matched_to_vsimple": sum(bool(record.matched_vsimple_id) for record in records),
        "matched_to_job": sum(bool(record.matched_job_id) for record in records),
        "review_required": sum(record.match_review_required for record in records),
        "with_candidates": sum(bool(record.match_candidates) for record in records),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Stage legacy and standalone warranty sources with provenance and conservative job matching.")
    parser.add_argument("--customer-list", type=Path)
    parser.add_argument("--vsimple-export", type=Path)
    parser.add_argument("--warranty-folder", type=Path)
    parser.add_argument("--gaco-csv", type=Path)
    parser.add_argument("--gaco-source-url", default="")
    parser.add_argument("--database-url", default=os.getenv("NEON_DATABASE_URL") or os.getenv("DATABASE_URL"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--rematch-existing", action="store_true")
    parser.add_argument("--only-unresolved", action="store_true")
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    records: list[WarrantySourceRecord] = []
    if args.customer_list:
        records.extend(load_customer_list(args.customer_list))
    if args.vsimple_export:
        records.extend(load_vsimple_warranties(args.vsimple_export))
    if args.warranty_folder:
        records.extend(load_warranty_pdfs(args.warranty_folder))
    if args.gaco_csv:
        records.extend(load_gaco_warranty_list(args.gaco_csv, source_url=args.gaco_source_url or None))
    if not records and not args.rematch_existing:
        raise SystemExit("Provide at least one warranty source.")
    if not args.database_url:
        raise SystemExit("Set --database-url, NEON_DATABASE_URL, or DATABASE_URL.")
    engine = create_engine(args.database_url, future=True)
    if args.rematch_existing:
        summary = rematch_existing_records(engine, only_unresolved=args.only_unresolved)
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0
    match_records(records, engine)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps([asdict(record) for record in records], indent=2, default=str), encoding="utf-8")
    written = 0 if args.dry_run else write_records(records, engine)
    summary = {
        "records_considered": len(records),
        "records_written": written,
        "dry_run": args.dry_run,
        "by_source": {source: sum(record.source_system == source for record in records) for source in sorted({record.source_system for record in records})},
        "matched_to_vsimple": sum(bool(record.matched_vsimple_id) for record in records),
        "matched_to_job": sum(bool(record.matched_job_id) for record in records),
        "review_required": sum(record.match_review_required for record in records),
        "with_start_date": sum(bool(record.start_date) for record in records),
        "with_duration": sum(bool(record.duration_years) for record in records),
        "with_expiration": sum(bool(record.expiration_date) for record in records),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
