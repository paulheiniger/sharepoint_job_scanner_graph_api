from __future__ import annotations

import os
import re
from copy import deepcopy
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from .workbook_profile import (
    EstimateWorkbookProfile,
    discover_flooring_workbook_profile,
    discover_insulation_workbook_profile,
    discover_roofing_workbook_profile,
)
from .scope_integrity import evaluate_roofing_scope_integrity
from .workbook_writer import (
    DEFAULT_FLOORING_ESTIMATE_TEMPLATE_PATH,
    DEFAULT_INSULATION_ESTIMATE_TEMPLATE_PATH,
    DEFAULT_ROOFING_ESTIMATE_TEMPLATE_PATH,
    generate_estimate_workbook,
    safe_filename,
)


DEFAULT_API_OUTPUT_DIR = Path("output/estimates/api")


class EstimateWorkbookUnavailableError(RuntimeError):
    pass


class EstimateWorkbookInputError(ValueError):
    def __init__(self, issues: list[str]):
        self.issues = issues
        super().__init__("; ".join(issues))


class EstimateWorkbookOutputError(RuntimeError):
    def __init__(self, issues: list[str]):
        self.issues = issues
        super().__init__("; ".join(issues))


@dataclass(frozen=True)
class EstimateWorkbookArtifact:
    artifact_id: str
    file_name: str
    path: Path
    calculated_outputs: dict[str, float]
    template_profile: dict[str, Any]


def roofing_template_path(*, base_dir: Path) -> Path:
    configured = str(os.getenv("ESTIMATOR_ROOFING_TEMPLATE_PATH") or "").strip()
    candidate = (
        Path(configured)
        if configured
        else DEFAULT_ROOFING_ESTIMATE_TEMPLATE_PATH
    )
    if not candidate.is_absolute():
        candidate = base_dir / candidate
    resolved = candidate.resolve()
    if not resolved.is_file():
        raise EstimateWorkbookUnavailableError(
            "The configured roofing estimate template is unavailable."
        )
    return resolved


def insulation_template_path(*, base_dir: Path) -> Path:
    configured = str(os.getenv("ESTIMATOR_INSULATION_TEMPLATE_PATH") or "").strip()
    candidate = (
        Path(configured)
        if configured
        else DEFAULT_INSULATION_ESTIMATE_TEMPLATE_PATH
    )
    if not candidate.is_absolute():
        candidate = base_dir / candidate
    resolved = candidate.resolve()
    if not resolved.is_file():
        raise EstimateWorkbookUnavailableError(
            "The configured insulation estimate template is unavailable."
        )
    return resolved


def flooring_template_path(*, base_dir: Path) -> Path:
    configured = str(os.getenv("ESTIMATOR_FLOORING_TEMPLATE_PATH") or "").strip()
    candidate = (
        Path(configured)
        if configured
        else DEFAULT_FLOORING_ESTIMATE_TEMPLATE_PATH
    )
    if not candidate.is_absolute():
        candidate = base_dir / candidate
    resolved = candidate.resolve()
    if not resolved.is_file():
        raise EstimateWorkbookUnavailableError(
            "The configured flooring estimate template is unavailable."
        )
    return resolved


def estimate_template_path(template_type: str, *, base_dir: Path) -> Path:
    if template_type == "roofing":
        return roofing_template_path(base_dir=base_dir)
    if template_type == "insulation":
        return insulation_template_path(base_dir=base_dir)
    if template_type == "flooring":
        return flooring_template_path(base_dir=base_dir)
    issue = f"Unsupported estimate template_type: {template_type or 'blank'}."
    raise EstimateWorkbookInputError([issue])


def discover_estimate_workbook_profile(
    template_type: str,
    template_path: Path,
) -> EstimateWorkbookProfile:
    if template_type == "insulation":
        return discover_insulation_workbook_profile(template_path)
    if template_type == "flooring":
        return discover_flooring_workbook_profile(template_path)
    return discover_roofing_workbook_profile(template_path)


def estimate_artifact_dir(*, base_dir: Path) -> Path:
    configured = str(os.getenv("ESTIMATOR_API_ARTIFACT_DIR") or "").strip()
    candidate = Path(configured) if configured else DEFAULT_API_OUTPUT_DIR
    if not candidate.is_absolute():
        candidate = base_dir / candidate
    return candidate.resolve()


def semantic_workbook_inputs(payload: dict[str, Any]) -> dict[str, Any]:
    header_payload = dict(payload.get("header") or {})
    header = {
        "C2_job_name": header_payload.get("job_name"),
        "C3_job_type": header_payload.get("job_type"),
        "C4_site_address": header_payload.get("site_address"),
        "C5_city_state_zip": header_payload.get("city_state_zip"),
        "C6_contact": header_payload.get("contact"),
        "C7_title": header_payload.get("title"),
        "C8_email": header_payload.get("email"),
        "C9_phone": header_payload.get("phone"),
        "G9_estimator": header_payload.get("estimator"),
        "C12_estimated_sqft": header_payload.get("estimated_sqft"),
        "mobilizations": header_payload.get("mobilizations"),
        "estimated_days": header_payload.get("estimated_days"),
        "estimated_hours": header_payload.get("estimated_hours"),
        "estimated_crew_size": header_payload.get("estimated_crew_size"),
        "repair_area_description": header_payload.get("repair_area_description"),
        "warranty_description": header_payload.get("warranty_description"),
        "sqft_calculation_rows": list(
            header_payload.get("sqft_calculation_rows") or []
        ),
    }
    decisions: list[dict[str, Any]] = []
    for group_name in ("materials", "logistics"):
        for item in payload.get(group_name) or []:
            if not item.get("include", True):
                continue
            row = {key: value for key, value in item.items() if key != "include"}
            row["row_type"] = "material"
            row["decision_id"] = row.pop("concept_id", "")
            decisions.append(row)
    for item in payload.get("labor") or []:
        if not item.get("include", True):
            continue
        row = {key: value for key, value in item.items() if key != "include"}
        row["row_type"] = "labor"
        row["decision_id"] = row.pop("concept_id", "")
        row["adjusted_days"] = row.pop("days", None)
        if row.get("label") and not row.get("notes"):
            row["notes"] = row["label"]
        decisions.append(row)
    for item in payload.get("adders") or []:
        if not item.get("include", True):
            continue
        row = {key: value for key, value in item.items() if key != "include"}
        row["row_type"] = "adder"
        row["decision_id"] = row.pop("concept_id", "")
        row["item"] = row.pop("label")
        row["estimated_cost"] = row.pop("amount", None)
        decisions.append(row)
    return {
        "template_type": str(payload.get("template_type") or "roofing").lower(),
        "header": header,
        "pricing": dict(payload.get("pricing") or {}),
        "warranty": dict(payload.get("warranty") or {}),
        "scope_of_work": list(payload.get("scope_of_work") or []),
        "spec_notes": list(payload.get("spec_notes") or []),
        "workbook_decisions": decisions,
    }


def validate_semantic_workbook_payload(
    payload: dict[str, Any],
    profile: EstimateWorkbookProfile,
) -> None:
    issues: list[str] = []
    header = payload.get("header") or {}
    if not _positive_number(header.get("estimated_sqft")):
        issues.append("header.estimated_sqft must be greater than zero.")
    dimension_rows = header.get("sqft_calculation_rows") or []
    if profile.template_type == "insulation" and dimension_rows:
        dimension_total = sum(
            float(item.get("area_sqft") or 0)
            for item in dimension_rows
            if isinstance(item, dict)
        )
        estimated_sqft = float(header.get("estimated_sqft") or 0)
        if abs(dimension_total - estimated_sqft) > 0.5:
            issues.append(
                "header.sqft_calculation_rows must reconcile to "
                "header.estimated_sqft within 0.5 sq. ft."
            )

    active_materials = [
        item for item in payload.get("materials") or [] if item.get("include", True)
    ]
    if not active_materials:
        issues.append(
            f"At least one included {profile.template_type} material is required."
        )
    scope_integrity: dict[str, Any] = {}
    structured_scope = payload.get("structured_scope") or {}
    if profile.template_type == "roofing" and structured_scope:
        scope_integrity = evaluate_roofing_scope_integrity(structured_scope)
        issues.extend(
            f"Structured scope: {issue}"
            for issue in scope_integrity.get("blocking_issues") or []
        )
        canonical_area = float(
            scope_integrity.get("canonical_area_total_sqft") or 0
        )
        estimated_area = float(header.get("estimated_sqft") or 0)
        if canonical_area > 0 and abs(canonical_area - estimated_area) > 1.0:
            issues.append(
                "header.estimated_sqft must match the structured scope's "
                f"canonical {canonical_area:g} sq. ft. roof area."
            )
        for category, basis_field, label in (
            ("roofing_foam", "foam_basis_sqft", "Roofing foam"),
            ("coating", "coating_basis_sqft", "Roof coating"),
            ("board_stock", "board_basis_sqft", "Board stock"),
        ):
            expected_area = float(scope_integrity.get(basis_field) or 0)
            if expected_area <= 0:
                continue
            matching = [
                item
                for item in active_materials
                if str(item.get("category") or "") == category
            ]
            if not matching:
                issues.append(
                    f"{label} is required by structured scope over {expected_area:g} sq. ft."
                )
                continue
            for item in matching:
                measured_area = item.get("area_sqft")
                if not _positive_number(measured_area):
                    issues.append(
                        f"{label} requires area_sqft as the measured scope basis."
                    )
                    continue
                measured_area = float(measured_area)
                if abs(measured_area - expected_area) > 1.0:
                    issues.append(
                        f"{label} area_sqft must match the structured scope's "
                        f"{expected_area:g} sq. ft. basis."
                    )
                purchase_basis = item.get("basis_sqft")
                if _positive_number(purchase_basis):
                    purchase_basis = float(purchase_basis)
                    if purchase_basis < measured_area - 1.0:
                        issues.append(
                            f"{label} basis_sqft cannot be smaller than its measured area_sqft."
                        )
                    if (
                        abs(purchase_basis - measured_area) > 0.5
                        and not str(item.get("quantity_adjustment_reason") or "").strip()
                    ):
                        issues.append(
                            f"{label} requires quantity_adjustment_reason when "
                            "basis_sqft differs from area_sqft."
                        )

    warranty = payload.get("warranty") or {}
    if warranty.get("include", False):
        if profile.warranty is None:
            issues.append(
                f"The selected {profile.template_type} template does not support warranty inputs."
            )
        if not _positive_number(warranty.get("years")):
            issues.append("Included warranty requires years greater than zero.")
        if not isinstance(warranty.get("unit_cost"), (int, float)) or isinstance(
            warranty.get("unit_cost"),
            bool,
        ):
            issues.append("Included warranty requires a numeric unit_cost.")
        elif float(warranty.get("unit_cost")) > 25:
            issues.append(
                "warranty.unit_cost is dollars per square foot and cannot exceed "
                "$25/sq. ft.; put a flat warranty allowance in adders.amount."
            )
        if str(warranty.get("pricing_basis") or "per_sqft") != "per_sqft":
            issues.append(
                "Warranty pricing_basis must be per_sqft; use adders.amount for "
                "a flat warranty allowance."
            )
        warranty_area = warranty.get("area_sqft")
        estimated_area = header.get("estimated_sqft")
        if _positive_number(warranty_area) and _positive_number(estimated_area):
            if abs(float(warranty_area) - float(estimated_area)) > 0.5:
                issues.append(
                    "warranty.area_sqft must match header.estimated_sqft because "
                    "the current template prices warranty over the estimate area; "
                    "use adders.amount for a localized flat allowance."
                )
        description = str(header.get("warranty_description") or "")
        described_years = re.search(r"\b(\d{1,3})\s*(?:year|yr)\b", description, re.I)
        if described_years and _positive_number(warranty.get("years")):
            if int(described_years.group(1)) != int(float(warranty["years"])):
                issues.append(
                    "header.warranty_description conflicts with warranty.years."
                )

    logistics = payload.get("logistics") or []
    truck_included = False
    for category, label in (
        ("sales_inspection_trips", "Sales/inspection travel"),
        ("truck_expense", "Truck travel"),
    ):
        decision = _decision_by_category(logistics, category)
        if decision is None:
            issues.append(
                f"{label} must be explicitly included or excluded in logistics."
            )
            continue
        if not decision.get("include", True):
            continue
        if category == "truck_expense":
            truck_included = True
        if not _positive_number(decision.get("trip_count")):
            issues.append(f"{label} requires trip_count greater than zero.")
        if not _positive_number(decision.get("round_trip_miles")):
            issues.append(f"{label} requires round_trip_miles greater than zero.")

    labor = payload.get("labor") or []
    active_labor_tasks = {
        str(item.get("task") or "").strip()
        for item in labor
        if item.get("include", True)
    }
    if {"labor_setup_safety", "labor_full_repair"} <= active_labor_tasks:
        issues.append(
            "labor_setup_safety and labor_full_repair use the same template "
            "activity row and cannot both be included."
        )
    supported_crew_sizes = set(profile.crew_daily_rate_cells)
    active_production_labor = 0
    for item in labor:
        if not item.get("include", True):
            continue
        task = str(item.get("task") or "").strip()
        if task in profile.labor_rows:
            active_production_labor += 1
            if not _positive_number(item.get("days")):
                issues.append(f"{task} requires days greater than zero.")
            if item.get("crew_size") not in supported_crew_sizes:
                issues.append(
                    f"{task} crew_size must be one of {sorted(supported_crew_sizes)}."
                )
        elif task not in profile.per_trip_labor_rows:
            issues.append(
                f"Unsupported included {profile.template_type} labor task: "
                f"{task or 'blank'}."
            )
    if not active_production_labor:
        issues.append("At least one included production labor task is required.")
    if scope_integrity.get("requires_tearoff") and "labor_tearoff" not in active_labor_tasks:
        issues.append(
            "Structured full-removal scope requires included labor_tearoff."
        )

    for task, label in (
        ("labor_loading", "Loading labor"),
        ("labor_traveling", "Traveling labor"),
    ):
        decision = _decision_by_task(labor, task)
        if decision is None:
            issues.append(f"{label} must be explicitly included or excluded in labor.")
            continue
        if not decision.get("include", True):
            continue
        if not truck_included:
            issues.append(
                f"{label} requires included Truck travel because this template "
                "uses the truck trip count in its labor formula."
            )
        if not _positive_number(decision.get("hours_per_trip")):
            issues.append(f"{label} requires hours_per_trip greater than zero.")
        if decision.get("crew_size") not in supported_crew_sizes:
            issues.append(
                f"{label} crew_size must be one of {sorted(supported_crew_sizes)}."
            )

    if issues:
        raise EstimateWorkbookInputError(issues)


def recalculate_estimate_workbook(path: Path) -> None:
    executable = str(os.getenv("ESTIMATOR_WORKBOOK_RECALCULATOR") or "").strip()
    if executable:
        executable_path = shutil.which(executable) or executable
    else:
        executable_path = shutil.which("soffice") or shutil.which("libreoffice")
    if not executable_path:
        raise EstimateWorkbookUnavailableError(
            "A spreadsheet recalculation engine is required before workbook delivery."
        )

    path = Path(path).resolve()
    with tempfile.TemporaryDirectory(
        prefix=".estimate-recalculate-",
        dir=path.parent,
    ) as temporary_directory:
        temporary_root = Path(temporary_directory)
        profile_dir = temporary_root / "profile"
        output_dir = temporary_root / "output"
        cache_dir = temporary_root / "cache"
        profile_dir.mkdir()
        output_dir.mkdir()
        cache_dir.mkdir()
        environment = os.environ.copy()
        environment["XDG_CACHE_HOME"] = str(cache_dir)
        command = [
            executable_path,
            f"-env:UserInstallation={profile_dir.as_uri()}",
            "--headless",
            "--convert-to",
            "xlsx",
            "--outdir",
            str(output_dir),
            str(path),
        ]
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=90,
                env=environment,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise EstimateWorkbookUnavailableError(
                f"Workbook recalculation failed: {type(exc).__name__}."
            ) from exc
        recalculated_path = output_dir / path.name
        if completed.returncode != 0 or not recalculated_path.is_file():
            raise EstimateWorkbookUnavailableError(
                "Workbook recalculation engine did not produce an output file."
            )
        os.replace(recalculated_path, path)


def validate_recalculated_workbook(
    path: Path,
    payload: dict[str, Any],
    profile: EstimateWorkbookProfile,
) -> dict[str, float]:
    try:
        import openpyxl
    except ImportError as exc:
        raise EstimateWorkbookUnavailableError(
            "Install openpyxl to validate estimate workbooks."
        ) from exc

    values_workbook = openpyxl.load_workbook(path, data_only=True, read_only=False)
    formula_workbook = openpyxl.load_workbook(path, data_only=False, read_only=False)
    estimate_values = values_workbook[profile.estimate_sheet]
    estimate_formulas = formula_workbook[profile.estimate_sheet]
    people_values = values_workbook[profile.people_sheet]
    issues: list[str] = []

    calculated_outputs = {
        "material_subtotal": _required_numeric_output(
            estimate_values,
            profile.material_subtotal_cell,
            "material subtotal",
            issues,
        ),
        "labor_subtotal": _required_numeric_output(
            estimate_values,
            profile.labor_subtotal_cell,
            "labor subtotal",
            issues,
        ),
        "total_job_cost": _required_numeric_output(
            estimate_values,
            profile.total_job_cost_cell,
            "total job cost",
            issues,
        ),
        "worksheet_price": _required_numeric_output(
            estimate_values,
            profile.final_price_cell,
            "worksheet price",
            issues,
        ),
    }

    warranty = payload.get("warranty") or {}
    if warranty.get("include", False) and profile.warranty is not None:
        calculated_outputs["warranty_cost"] = _required_nonnegative_numeric_output(
            estimate_values,
            profile.warranty.cost_cell,
            profile.warranty.label,
            issues,
        )

    logistics = payload.get("logistics") or []
    for key, category, cost_row in (
        ("sales_inspection_cost", "sales_inspection_trips", profile.sales_inspection),
        ("truck_expense_cost", "truck_expense", profile.truck_expense),
    ):
        decision = _decision_by_category(logistics, category)
        if decision and decision.get("include", True):
            calculated_outputs[key] = _required_numeric_output(
                estimate_values,
                cost_row.cost_cell,
                cost_row.label,
                issues,
            )

    for item in payload.get("labor") or []:
        if not item.get("include", True):
            continue
        task = str(item.get("task") or "")
        if task in profile.labor_rows:
            cost_row = profile.labor_rows[task]
            crew_size = int(item["crew_size"])
            daily_rate = _required_numeric_output(
                people_values,
                profile.crew_daily_rate_cells[crew_size],
                f"People daily rate for crew size {crew_size}",
                issues,
            )
            calculated_outputs[f"{task}_daily_rate"] = daily_rate
            calculated_outputs[f"{task}_cost"] = _required_numeric_output(
                estimate_values,
                cost_row.cost_cell,
                cost_row.label,
                issues,
            )
        elif task in profile.per_trip_labor_rows:
            cost_row = profile.per_trip_labor_rows[task]
            calculated_outputs[f"{task}_cost"] = _required_numeric_output(
                estimate_values,
                cost_row.cost_cell,
                cost_row.label,
                issues,
            )

    formula_cells = {
        profile.material_subtotal_cell,
        profile.labor_subtotal_cell,
        profile.total_job_cost_cell,
        profile.final_price_cell,
        profile.sales_inspection.cost_cell,
        profile.truck_expense.cost_cell,
        *(row.cost_cell for row in profile.labor_rows.values()),
        *(row.cost_cell for row in profile.per_trip_labor_rows.values()),
    }
    if profile.warranty is not None:
        formula_cells.add(profile.warranty.cost_cell)
    for cell in formula_cells:
        value = estimate_formulas[cell].value
        if not isinstance(value, str) or not value.startswith("="):
            issues.append(f"Required template formula is missing from Estimate!{cell}.")

    if issues:
        raise EstimateWorkbookOutputError(issues)
    return calculated_outputs


def create_estimate_workbook(
    payload: dict[str, Any],
    *,
    base_dir: Path,
    file_label: str = "",
) -> EstimateWorkbookArtifact:
    artifact_id = uuid4().hex
    job_name = str((payload.get("header") or {}).get("job_name") or "estimate")
    label_suffix = f" - {safe_filename(file_label)}" if file_label.strip() else ""
    friendly_file_name = f"Estimate - {safe_filename(job_name)}{label_suffix}.xlsx"
    stored_file_name = f"{artifact_id}__{friendly_file_name}"
    path: Path | None = None
    try:
        template_type = str(payload.get("template_type") or "roofing").strip().lower()
        template_path = estimate_template_path(template_type, base_dir=base_dir)
        profile = discover_estimate_workbook_profile(template_type, template_path)
        validate_semantic_workbook_payload(payload, profile)
        path = generate_estimate_workbook(
            semantic_workbook_inputs(payload),
            template_path,
            estimate_artifact_dir(base_dir=base_dir),
            stored_file_name,
        )
        recalculate_estimate_workbook(path)
        calculated_outputs = validate_recalculated_workbook(
            path,
            payload,
            profile,
        )
    except (EstimateWorkbookUnavailableError, EstimateWorkbookInputError, EstimateWorkbookOutputError):
        if path is not None:
            path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        raise EstimateWorkbookUnavailableError(
            f"Estimate workbook generation failed: {type(exc).__name__}."
        ) from exc
    return EstimateWorkbookArtifact(
        artifact_id=artifact_id,
        file_name=friendly_file_name,
        path=path,
        calculated_outputs=calculated_outputs,
        template_profile=profile.action_summary(),
    )


def create_estimate_workbook_options(
    options: list[tuple[str, dict[str, Any]]],
    *,
    base_dir: Path,
) -> list[tuple[str, EstimateWorkbookArtifact]]:
    """Create a complete workbook for each approved option or leave none behind."""
    created: list[tuple[str, EstimateWorkbookArtifact]] = []
    current_label = ""
    try:
        for current_label, option_payload in options:
            payload = deepcopy(option_payload)
            payload["confirmed"] = True
            artifact = create_estimate_workbook(
                payload,
                base_dir=base_dir,
                file_label=current_label,
            )
            created.append((current_label, artifact))
    except EstimateWorkbookInputError as exc:
        _remove_estimate_artifacts(created)
        raise EstimateWorkbookInputError(
            [f"{current_label}: {issue}" for issue in exc.issues]
        ) from exc
    except EstimateWorkbookOutputError as exc:
        _remove_estimate_artifacts(created)
        raise EstimateWorkbookOutputError(
            [f"{current_label}: {issue}" for issue in exc.issues]
        ) from exc
    except EstimateWorkbookUnavailableError as exc:
        _remove_estimate_artifacts(created)
        raise EstimateWorkbookUnavailableError(
            f"Option {current_label!r} could not be generated: {exc}"
        ) from exc
    except Exception:
        _remove_estimate_artifacts(created)
        raise
    return created


def _remove_estimate_artifacts(
    created: list[tuple[str, EstimateWorkbookArtifact]],
) -> None:
    for _label, artifact in created:
        artifact.path.unlink(missing_ok=True)


def resolve_estimate_artifact(artifact_id: str, *, base_dir: Path) -> tuple[Path, str]:
    if len(artifact_id) != 32 or any(character not in "0123456789abcdef" for character in artifact_id):
        raise FileNotFoundError("Estimate artifact was not found.")
    output_dir = estimate_artifact_dir(base_dir=base_dir)
    matches = list(output_dir.glob(f"{artifact_id}__*.xlsx"))
    if len(matches) != 1:
        raise FileNotFoundError("Estimate artifact was not found.")
    path = matches[0].resolve()
    if path.parent != output_dir:
        raise FileNotFoundError("Estimate artifact was not found.")
    return path, path.name.split("__", 1)[1]


def _decision_by_category(
    decisions: list[dict[str, Any]],
    category: str,
) -> dict[str, Any] | None:
    aliases = {
        "sales_inspection_trips": {"sales_inspection_trips", "sales_trips"},
        "truck_expense": {"truck_expense"},
    }.get(category, {category})
    return next(
        (
            item
            for item in decisions
            if str(item.get("category") or "").strip().lower() in aliases
        ),
        None,
    )


def _decision_by_task(
    decisions: list[dict[str, Any]],
    task: str,
) -> dict[str, Any] | None:
    return next(
        (
            item
            for item in decisions
            if str(item.get("task") or "").strip().lower() == task
        ),
        None,
    )


def _positive_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def _required_numeric_output(
    sheet: Any,
    cell: str,
    label: str,
    issues: list[str],
) -> float:
    value = sheet[cell].value
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        issues.append(f"Calculated {label} is blank, invalid, or zero at {sheet.title}!{cell}.")
        return 0.0
    return float(value)


def _required_nonnegative_numeric_output(
    sheet: Any,
    cell: str,
    label: str,
    issues: list[str],
) -> float:
    value = sheet[cell].value
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        issues.append(f"Calculated {label} is blank or invalid at {sheet.title}!{cell}.")
        return 0.0
    return float(value)
