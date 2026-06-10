from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class JobRecord:
    job_id: str
    folder_name: str
    folder_path: str
    folder_url: str | None = None

    division: str | None = None
    pipeline_status: str | None = None
    scan_root: str | None = None
    source_year: int | None = None

    customer: str | None = None
    job_name: str | None = None
    job_type: str | None = None
    site_address: str | None = None
    city: str | None = None
    state: str | None = None
    zip_code: str | None = None
    contact_name: str | None = None
    contact_title: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None

    estimate_file: str | None = None
    estimate_date: str | None = None
    estimated_sqft: float | None = None
    material_subtotal: float | None = None
    labor_subtotal: float | None = None
    warranty_bonding_insurance_subtotal: float | None = None
    total_job_cost: float | None = None
    overhead_pct: float | None = None
    overhead_amount: float | None = None
    profit_pct: float | None = None
    profit_amount: float | None = None
    worksheet_price: float | None = None
    final_price: float | None = None
    price_per_sqft: float | None = None

    invoice_file: str | None = None
    invoice_number: str | None = None
    invoice_amount: float | None = None
    invoice_date: str | None = None

    has_signed_contract: bool = False
    has_invoice: bool = False
    has_warranty: bool = False
    has_proposal: bool = False
    has_job_spec: bool = False
    has_aerial: bool = False
    has_notes: bool = False
    photo_count: int = 0
    duplicate_photo_count: int | None = 0
    image_files_cached: bool = True
    skipped_image_count: int = 0

    status: str = "Unknown"
    crew_leader: str | None = None
    crew_type: str | None = None
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
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def money(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    text = str(value).replace(",", "").replace("$", "").strip()
    if not text:
        return None
    try:
        return round(float(text), 2)
    except ValueError:
        return None


def rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)
