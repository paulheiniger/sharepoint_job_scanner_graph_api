from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from .models import JobRecord, rel

KNOWN_CREW_LEADERS = [
    "Mariano",
    "Gustavo",
    "Santos",
    "Carlos",
    "Eli",
    "Ethan",
    "Colby",
    "Matt Ash",
    "Mike Roberts",
]

SCHEDULE_KEYWORDS = [
    "crew",
    "crew leader",
    "foreman",
    "lead",
    "sub",
    "subcontractor",
    "start date",
    "scheduled start",
    "begin",
    "duration",
    "days",
    "working days",
    "estimated days",
    "job duration",
    "production days",
    "install days",
    "mobilize",
    "mobilization",
]

CONTRACTED_STATUSES = {"contracted", "contracted repairs", "folder created"}


@dataclass
class ScheduleExtraction:
    crew_leader: str | None = None
    assigned_crew_leader: str | None = None
    crew_type: str | None = None
    suggested_crew_type: str | None = None
    suggested_crew_reason: str | None = None
    scheduled_sequence: int | None = None
    estimated_start_date: str | None = None
    estimated_duration_days: int | None = None
    estimated_end_date: str | None = None
    schedule_status: str | None = None
    ready_to_schedule: bool = False
    blocking_issue: str | None = None
    schedule_notes: str | None = None
    schedule_source_file: str | None = None
    schedule_confidence: str | None = None


@dataclass(frozen=True)
class TextSource:
    name: str
    text: str
    kind: str


def apply_schedule_extraction(record: JobRecord, folder: Path, root: Path, classified: dict[str, Any]) -> None:
    extraction = extract_schedule(record, folder, root, classified)
    for key, value in extraction.__dict__.items():
        setattr(record, key, value)


def finalize_schedule_record(record: JobRecord) -> None:
    extraction = ScheduleExtraction(
        crew_leader=record.crew_leader,
        assigned_crew_leader=record.assigned_crew_leader or record.crew_leader,
        crew_type=record.crew_type,
        suggested_crew_type=record.suggested_crew_type,
        suggested_crew_reason=record.suggested_crew_reason,
        scheduled_sequence=record.scheduled_sequence,
        estimated_start_date=record.estimated_start_date,
        estimated_duration_days=record.estimated_duration_days,
        estimated_end_date=record.estimated_end_date,
        schedule_notes=record.schedule_notes,
        schedule_source_file=record.schedule_source_file,
    )
    extraction.suggested_crew_type = extraction.suggested_crew_type or None
    extraction.suggested_crew_reason = extraction.suggested_crew_reason or "manual_needed"
    extraction.schedule_status = _schedule_status(record, record.schedule_notes or "", extraction)
    extraction.blocking_issue = _blocking_issue(record, extraction)
    extraction.ready_to_schedule = _ready_to_schedule(record, extraction)
    extraction.schedule_confidence = _schedule_confidence(extraction)
    record.assigned_crew_leader = extraction.assigned_crew_leader
    if not record.crew_leader and extraction.assigned_crew_leader:
        record.crew_leader = extraction.assigned_crew_leader
    record.suggested_crew_type = extraction.suggested_crew_type
    record.suggested_crew_reason = extraction.suggested_crew_reason
    record.schedule_status = extraction.schedule_status
    record.blocking_issue = extraction.blocking_issue
    record.ready_to_schedule = extraction.ready_to_schedule
    record.schedule_confidence = extraction.schedule_confidence


def extract_schedule(record: JobRecord, folder: Path, root: Path, classified: dict[str, Any]) -> ScheduleExtraction:
    assigned_crew_leader = record.assigned_crew_leader or record.crew_leader
    extraction = ScheduleExtraction(
        crew_leader=assigned_crew_leader,
        assigned_crew_leader=assigned_crew_leader,
        crew_type=record.crew_type,
        suggested_crew_type=record.suggested_crew_type,
        suggested_crew_reason=record.suggested_crew_reason,
        scheduled_sequence=record.scheduled_sequence,
        estimated_start_date=record.estimated_start_date,
        estimated_duration_days=record.estimated_duration_days,
        estimated_end_date=record.estimated_end_date,
        schedule_source_file=record.labor_duration_source,
    )

    duration_source = None
    if record.labor_duration_source and record.estimate_file:
        duration_source = TextSource(record.estimate_file, record.labor_duration_source, "estimate")

    if extraction.estimated_start_date and extraction.estimated_duration_days:
        extraction.estimated_end_date = add_business_days(extraction.estimated_start_date, extraction.estimated_duration_days)

    extraction.schedule_source_file = _best_source_name(duration_source) or extraction.schedule_source_file
    extraction.suggested_crew_type = extraction.suggested_crew_type or None
    extraction.suggested_crew_reason = extraction.suggested_crew_reason or "manual_needed"
    extraction.schedule_notes = _schedule_notes(record, extraction, "")
    extraction.schedule_status = _schedule_status(record, "", extraction)
    extraction.blocking_issue = _blocking_issue(record, extraction)
    extraction.ready_to_schedule = _ready_to_schedule(record, extraction)
    extraction.schedule_confidence = _schedule_confidence(extraction)
    return extraction


def collect_schedule_text_sources(folder: Path, root: Path, classified: dict[str, Any]) -> list[TextSource]:
    sources = [TextSource("folder_name", f"{folder.name}\n{rel(folder, root)}", "folder")]
    files: list[Path] = []
    for key in ("job_specs", "estimate_files", "proposals", "notes"):
        files.extend(classified.get(key) or [])

    seen: set[Path] = set()
    for path in files:
        if path in seen:
            continue
        seen.add(path)
        text = readable_text(path)
        if text:
            sources.append(TextSource(rel(path, root), text, _source_kind(path)))
        else:
            sources.append(TextSource(rel(path, root), path.name, _source_kind(path)))
    return sources


def readable_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".txt", ".csv"}:
        return _read_text_file(path)
    if suffix == ".docx":
        return _read_docx(path)
    if suffix in {".xlsx", ".xlsm"}:
        return _read_xlsx(path)
    if suffix == ".pdf":
        return _read_pdf(path)
    return path.name


def parse_crew_leader(text: str) -> str | None:
    explicit = re.search(r"\b(?:crew\s*leader|foreman)\s*[:\-]\s*([A-Za-z][A-Za-z .'-]{1,40})", text, flags=re.I)
    if explicit:
        return _normalize_known_name(explicit.group(1))

    lowered = text.lower()
    if not any(keyword in lowered for keyword in SCHEDULE_KEYWORDS):
        return None
    for leader in KNOWN_CREW_LEADERS:
        if re.search(rf"\b{re.escape(leader)}\b", text, flags=re.I):
            return leader
    return None


def parse_duration_days(text: str) -> int | None:
    match = re.search(r"\b(?:duration|estimated duration|job duration)\s*[:\-]?\s*(\d{1,2})\s*weeks?\b", text, flags=re.I)
    if match:
        return int(match.group(1)) * 5
    patterns = [
        r"\b(?:duration|estimated duration|job duration|install days|production days|estimated days)\s*[:\-]?\s*(\d{1,2})(?:\s*(?:working\s*)?days?)?\b",
        r"\b(\d{1,2})\s*(?:working\s*)?days?\b",
        r"\b(\d{1,2})\s*weeks?\b",
    ]
    for pattern in patterns[:2]:
        match = re.search(pattern, text, flags=re.I)
        if match:
            return int(match.group(1))
    match = re.search(patterns[2], text, flags=re.I)
    if match:
        return int(match.group(1)) * 5
    return None


def parse_start_date(text: str) -> str | None:
    pattern = (
        r"\b(?:start date|scheduled start|begin|mobilize|mobilization)\s*[:\-]?\s*"
        r"(\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?)"
    )
    match = re.search(pattern, text, flags=re.I)
    if not match:
        return None
    return _parse_date(match.group(1), text)


def parse_scheduled_sequence(text: str) -> int | None:
    match = re.search(r"\b(?:sequence|scheduled sequence|schedule order)\s*[:#\-]?\s*(\d{1,3})\b", text, flags=re.I)
    return int(match.group(1)) if match else None


def add_business_days(start_date: str, duration_days: int) -> str:
    current = date.fromisoformat(start_date)
    remaining = max(duration_days - 1, 0)
    while remaining:
        current += timedelta(days=1)
        if current.weekday() < 5:
            remaining -= 1
    return current.isoformat()


def _read_text_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _read_docx(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as docx:
            xml = docx.read("word/document.xml")
    except (OSError, KeyError, zipfile.BadZipFile):
        return ""
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return ""
    return " ".join(node.text or "" for node in root.iter() if node.tag.endswith("}t") and node.text)


def _read_xlsx(path: Path) -> str:
    try:
        import openpyxl
    except ImportError:
        return ""
    try:
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    except Exception:
        return ""
    values: list[str] = []
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if cell.value is not None:
                    values.append(str(cell.value))
    return "\n".join(values)


def _read_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader  # type: ignore[import-not-found]
    except ImportError:
        try:
            from PyPDF2 import PdfReader  # type: ignore[import-not-found,no-redef]
        except ImportError:
            return ""
    try:
        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception:
        return ""


def _parse_date(value: str, context: str) -> str | None:
    parts = re.split(r"[./-]", value)
    if len(parts) not in {2, 3}:
        return None
    month, day = int(parts[0]), int(parts[1])
    if len(parts) == 3:
        year = int(parts[2])
        if year < 100:
            year += 2000
    else:
        year_match = re.search(r"\b(20\d{2})\b", context)
        year = int(year_match.group(1)) if year_match else datetime.now().year
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def _normalize_known_name(value: str) -> str:
    cleaned = re.split(r"\s{2,}|[,;\n\r]", value.strip())[0].strip(" .:-")
    for leader in KNOWN_CREW_LEADERS:
        if re.search(rf"\b{re.escape(leader)}\b", cleaned, flags=re.I):
            return leader
    return cleaned


def _is_subcontractor(crew_leader: str, text: str) -> bool:
    if crew_leader in {"Eli", "Ethan", "Colby", "Matt Ash", "Mike Roberts"}:
        return True
    leader_match = re.search(rf".{{0,40}}\b{re.escape(crew_leader)}\b.{{0,40}}", text, flags=re.I | re.S)
    return bool(leader_match and re.search(r"\b(sub|subcontractor)\b", leader_match.group(0), flags=re.I))


def _source_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xlsm", ".xls"}:
        return "estimate"
    if suffix in {".doc", ".docx", ".pdf"}:
        return "document"
    return "notes"


def _best_source_name(*sources: TextSource | None) -> str | None:
    for source in sources:
        if source:
            return source.name
    return None


def _schedule_notes(record: JobRecord, extraction: ScheduleExtraction, text: str) -> str | None:
    notes: list[str] = []
    if extraction.assigned_crew_leader:
        notes.append(f"Assigned crew leader: {extraction.assigned_crew_leader}")
    if extraction.estimated_start_date:
        notes.append(f"Estimated start date: {extraction.estimated_start_date}")
    if extraction.estimated_duration_days:
        notes.append(f"Estimated duration found: {extraction.estimated_duration_days} days")
    if record.labor_duration_source:
        notes.append(record.labor_duration_source)
    return "; ".join(notes) if notes else None


def _schedule_status(record: JobRecord, text: str, extraction: ScheduleExtraction) -> str:
    if _is_completed(record):
        return "Complete"
    if not _is_contractable(record) or extraction.estimated_duration_days is None:
        return "Not Ready"
    assigned_crew = extraction.assigned_crew_leader or extraction.crew_leader
    if assigned_crew and extraction.estimated_start_date:
        return "Scheduled"
    if assigned_crew:
        return "Needs Start Date"
    return "Needs Assignment"


def _blocking_issue(record: JobRecord, extraction: ScheduleExtraction) -> str | None:
    issues: list[str] = []
    if _is_completed(record):
        issues.append("Completed job")
    if not _is_contractable(record) and not _is_completed(record):
        issues.append("Not contracted")
    if not extraction.estimated_duration_days:
        issues.append("Missing estimated duration")
    base_ready = _ready_to_schedule(record, extraction)
    assigned_crew = extraction.assigned_crew_leader or extraction.crew_leader
    if base_ready and not assigned_crew:
        issues.append("Needs crew assignment")
    if base_ready and assigned_crew and not extraction.estimated_start_date:
        issues.append("Needs start date")
    return "; ".join(dict.fromkeys(issues)) if issues else None


def _ready_to_schedule(record: JobRecord, extraction: ScheduleExtraction) -> bool:
    return (
        _is_contractable(record)
        and extraction.estimated_duration_days is not None
        and not _is_completed(record)
    )


def _schedule_confidence(extraction: ScheduleExtraction) -> str:
    return "medium" if extraction.estimated_duration_days is not None else "manual_needed"


def _is_completed(record: JobRecord) -> bool:
    status_text = f"{record.status or ''} {record.pipeline_status or ''}".strip().lower()
    return "completed" in status_text or "complete" in status_text


def _is_contractable(record: JobRecord) -> bool:
    pipeline_status = (record.pipeline_status or "").strip().lower()
    status = (record.status or "").strip().lower()
    return pipeline_status in CONTRACTED_STATUSES or (not pipeline_status and status in CONTRACTED_STATUSES)
