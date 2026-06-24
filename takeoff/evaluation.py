from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


STACK_TAKEOFF_COLUMNS = [
    "Takeoff Name",
    "Takeoff Description",
    "Sq Ft",
    "Ln Ft",
    "Cu Yd",
    "EA",
    "Drop Count",
    "Takeoff Quantity",
    "Takeoff Unit",
    "Scale",
    "Plan Name",
]


@dataclass(frozen=True)
class TakeoffMeasurementLabel:
    plan_name: str
    project_id: str = ""
    trade_type: str = "foam_insulation"
    canonical_sheet_id: str = ""
    original_page_number: int | None = None
    measurement_type: str = "unknown"
    takeoff_name: str = ""
    takeoff_description: str = ""
    quantity: float | None = None
    unit: str = ""
    row_count: int = 1
    source_rows: list[dict[str, Any]] = field(default_factory=list)

    @property
    def match_key(self) -> str:
        if self.canonical_sheet_id:
            return f"sheet:{self.canonical_sheet_id}"
        if self.original_page_number is not None:
            return f"page:{self.original_page_number}"
        return f"plan:{self.plan_name.strip().lower()}"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["match_key"] = self.match_key
        return data


def canonical_sheet_id_from_plan_name(plan_name: str) -> str:
    text = Path(str(plan_name or "").strip()).name
    if text.lower().endswith(".pdf"):
        text = text[:-4]
    text = re.sub(r"\s+Page\s+\d+\s*$", "", text, flags=re.I)
    match = re.search(r"\b(?P<prefix>FP|FA|[ASMEPCLG]\d?)[._ -](?P<number>\d{2,4})\b", text, flags=re.I)
    if match:
        return f"{match.group('prefix').upper()}-{match.group('number')}"
    match = re.search(r"\b(?P<prefix>FP|FA|[ASMEPCLG])(?P<number>\d{3,4})\b", text, flags=re.I)
    if match:
        return f"{match.group('prefix').upper()}-{match.group('number')}"
    return ""


def original_page_number_from_plan_name(plan_name: str) -> int | None:
    match = re.search(r"\bPage\s+(?P<page>\d{1,5})\b", str(plan_name or ""), flags=re.I)
    return int(match.group("page")) if match else None


def infer_measurement_type(row: dict[str, Any]) -> str:
    name = str(row.get("Takeoff Name") or "")
    description = str(row.get("Takeoff Description") or "")
    haystack = f"{name} {description}".lower()
    unit = str(row.get("Takeoff Unit") or "").strip().lower()
    if "perimeter" in haystack or unit in {"ln ft", "lf", "linear feet"} or _number(row.get("Ln Ft")) is not None:
        return "perimeter"
    if "elevation" in haystack:
        return "elevation_area"
    if "attic" in haystack:
        return "attic_area"
    if unit in {"sq ft", "sf", "sqft", "square feet"} or _number(row.get("Sq Ft")) is not None:
        return "area"
    if unit in {"ea", "each"} or _number(row.get("EA")) is not None:
        return "count"
    return "unknown"


def parse_stack_takeoff_csv(
    payload: str | bytes | Path,
    *,
    project_id: str = "",
    trade_type: str = "foam_insulation",
) -> list[TakeoffMeasurementLabel]:
    text = _read_text(payload)
    reader = csv.DictReader(io.StringIO(text))
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in reader:
        plan_name = str(row.get("Plan Name") or "").strip()
        if not plan_name:
            continue
        canonical_sheet_id = canonical_sheet_id_from_plan_name(plan_name)
        original_page_number = original_page_number_from_plan_name(plan_name)
        key = f"sheet:{canonical_sheet_id}" if canonical_sheet_id else f"page:{original_page_number}" if original_page_number else f"plan:{plan_name.lower()}"
        grouped.setdefault(key, []).append(row)

    labels: list[TakeoffMeasurementLabel] = []
    for rows in grouped.values():
        first = rows[0]
        plan_name = str(first.get("Plan Name") or "").strip()
        canonical_sheet_id = canonical_sheet_id_from_plan_name(plan_name)
        original_page_number = original_page_number_from_plan_name(plan_name)
        measurement_types = [infer_measurement_type(row) for row in rows]
        measurement_type = _preferred_measurement_type(measurement_types)
        quantity = _first_number(
            first.get("Takeoff Quantity"),
            first.get("Sq Ft"),
            first.get("Ln Ft"),
            first.get("Cu Yd"),
            first.get("EA"),
            first.get("Drop Count"),
        )
        labels.append(
            TakeoffMeasurementLabel(
                plan_name=plan_name,
                project_id=project_id,
                trade_type=trade_type,
                canonical_sheet_id=canonical_sheet_id,
                original_page_number=original_page_number,
                measurement_type=measurement_type,
                takeoff_name=str(first.get("Takeoff Name") or ""),
                takeoff_description=str(first.get("Takeoff Description") or ""),
                quantity=quantity,
                unit=str(first.get("Takeoff Unit") or ""),
                row_count=len(rows),
                source_rows=rows,
            )
        )
    return sorted(labels, key=lambda label: (label.canonical_sheet_id or "", label.original_page_number or 0, label.plan_name))


def compare_foamscope_output_to_takeoff_export(foamscope_json: dict[str, Any] | str | bytes | Path, takeoff_csv: str | bytes | Path) -> dict[str, Any]:
    foamscope = _load_foamscope_json(foamscope_json)
    expected_labels = parse_stack_takeoff_csv(takeoff_csv)
    selected_pages = _selected_measurement_pages(foamscope)

    expected_by_key = {label.match_key: label for label in expected_labels}
    selected_by_key = {page["match_key"]: page for page in selected_pages if page.get("match_key")}
    expected_keys = set(expected_by_key)
    selected_keys = set(selected_by_key)
    matched_keys = expected_keys & selected_keys
    missed_keys = expected_keys - selected_keys
    extra_keys = selected_keys - expected_keys
    recall = len(matched_keys) / len(expected_keys) if expected_keys else 0.0
    precision = len(matched_keys) / len(selected_keys) if selected_keys else 0.0
    return {
        "expected_measurement_pages": [expected_by_key[key].to_dict() for key in sorted(expected_keys)],
        "selected_measurement_pages": [selected_by_key[key] for key in sorted(selected_keys)],
        "matched_pages": [_merge_match(expected_by_key[key], selected_by_key[key]) for key in sorted(matched_keys)],
        "missed_pages": [expected_by_key[key].to_dict() for key in sorted(missed_keys)],
        "extra_selected_pages": [selected_by_key[key] for key in sorted(extra_keys)],
        "recall": recall,
        "precision": precision,
        "counts": {
            "expected": len(expected_keys),
            "selected": len(selected_keys),
            "matched": len(matched_keys),
            "missed": len(missed_keys),
            "extra": len(extra_keys),
        },
    }


def _selected_measurement_pages(foamscope: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = (foamscope.get("measurement_tree") or {}).get("nodes") or []
    selected: list[dict[str, Any]] = []
    for node in nodes:
        if node.get("role") != "measurement_page":
            continue
        canonical_sheet_id = str(node.get("canonical_sheet_id") or node.get("sheet_id") or "").strip()
        original_page_number = _int_or_none(node.get("original_page_number"))
        match_key = f"sheet:{canonical_sheet_id}" if canonical_sheet_id else f"page:{original_page_number}" if original_page_number is not None else ""
        selected.append(
            {
                "match_key": match_key,
                "canonical_sheet_id": canonical_sheet_id,
                "original_page_number": original_page_number,
                "document_name": node.get("document_name"),
                "original_document_name": node.get("original_document_name"),
                "page_num": node.get("page_num"),
                "sheet_title": node.get("sheet_title"),
                "role": node.get("role"),
                "inclusion_path": node.get("inclusion_path") or [],
                "measurement_guidance": node.get("measurement_guidance"),
            }
        )
    return selected


def _merge_match(expected: TakeoffMeasurementLabel, selected: dict[str, Any]) -> dict[str, Any]:
    out = expected.to_dict()
    out.update(
        {
            "selected_document_name": selected.get("document_name"),
            "selected_page_num": selected.get("page_num"),
            "selected_sheet_title": selected.get("sheet_title"),
            "selected_inclusion_path": selected.get("inclusion_path"),
        }
    )
    return out


def _read_text(payload: str | bytes | Path) -> str:
    if isinstance(payload, bytes):
        return payload.decode("utf-8-sig")
    if isinstance(payload, Path):
        return payload.read_text(encoding="utf-8-sig")
    text = str(payload)
    if text.lstrip().startswith(("{", "[")):
        return text
    if "\n" not in text:
        try:
            path = Path(text)
            if path.exists():
                return path.read_text(encoding="utf-8-sig")
        except OSError:
            return text
    return text


def _load_foamscope_json(payload: dict[str, Any] | str | bytes | Path) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    return json.loads(_read_text(payload))


def _number(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).replace(",", "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _first_number(*values: Any) -> float | None:
    for value in values:
        number = _number(value)
        if number is not None:
            return number
    return None


def _int_or_none(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _preferred_measurement_type(types: list[str]) -> str:
    for option in ("perimeter", "elevation_area", "attic_area", "area", "linear", "count"):
        if option in types:
            return option
    return types[0] if types else "unknown"
