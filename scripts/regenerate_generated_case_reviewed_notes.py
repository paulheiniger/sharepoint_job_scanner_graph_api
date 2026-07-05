from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from jobscan.estimator import generated_cases


DEFAULT_ROOT = Path("output/estimator_generated_cases")
DEFAULT_JSONL = DEFAULT_ROOT / "generated_live_cases_chat_reviewed.jsonl"


def fmt(value: Any) -> str:
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.2f}".rstrip("0").rstrip(".")


def expected_warranty(case: dict[str, Any]) -> int | None:
    value = (case.get("expected_scope_fields") or {}).get("warranty_years")
    if value:
        return int(float(value))
    return None


def insulation_dimensions(case: dict[str, Any]) -> str:
    trace = case["area_trace"]
    length = fmt(trace["building_length_ft"])
    width = fmt(trace["building_width_ft"])
    height = fmt(trace["wall_height_ft"])
    deduction = float(trace.get("deduction_area_sqft") or 0)
    if not deduction and float(trace.get("net_area_sqft") or 0) < 500:
        return "Localized patch area is about {area} sq ft; verify exact patch edges before final.".format(
            area=fmt(trace["net_area_sqft"])
        )
    text = f"Building is {length} ft by {width} ft with {height} ft walls."
    if deduction:
        text += " Spray outside walls and flat ceiling. Deduct two 10 ft by 10 ft overhead door openings."
    else:
        text += " Localized patch area is about {area} sq ft; verify exact patch edges before final.".format(
            area=fmt(trace["net_area_sqft"])
        )
    return text


def roofing_dimensions(case: dict[str, Any]) -> str:
    trace = case["area_trace"]
    deduction = float(trace.get("deduction_area_sqft") or 0)
    text = f"Main roof measures {fmt(trace['length_ft'])} ft by {fmt(trace['width_ft'])} ft."
    if deduction:
        text += f" Deduct {fmt(deduction)} square feet for curbs/equipment from the measured roof area."
    return text


def insulation_note(case: dict[str, Any]) -> str:
    case_id = str(case.get("case_id") or "")
    dims = insulation_dimensions(case)
    barrier = "DC315 or other ignition/thermal barrier"
    sealant_hint = "Review sealant at seams/transitions."

    if "graves_county" in case_id:
        return (
            "Email / field note draft for Graves County Athletic at 2290 KY-121: multipurpose facility at Graves County High School. "
            f"{dims} Owner wants durable closed-cell foam on the metal building shell. Walls target R-21. Ceiling target R-30. "
            f"Estimator should infer foam system, thickness, and yield from the surface R-values and condensation risk. Code/owner may require {barrier}. "
            "Tall walls and open ceiling likely need lift/scissor access, masking around athletic finishes, setup, cleanup, loading, travel, and temporary power/heat review. "
            f"{sealant_hint}"
        )
    if "austin_in_travis_gardner" in case_id:
        return (
            "Dictated note: Austin, IN / Travis Gardner pole-barn style insulation request. "
            f"{dims} Customer is talking walls and ceiling, not just a patch. Walls target R-21. Ceiling target R-30 if budget allows. "
            "Use the R-value targets to decide thickness and whether closed-cell or open-cell makes sense by surface. "
            f"Review {barrier}, masking, setup, cleanup, loading, travel, and small equipment/access."
        )
    if "massey_eric_pole_barn" in case_id:
        return (
            "Field notes for Eric Massey pole barn: spray foam quote for a small metal/pole-barn building. "
            f"{dims} Owner wants the barn tightened up and usable through temperature swings. Walls target R-21. Ceiling target R-30. "
            "Estimator should decide closed-cell versus open-cell by surface and condensation risk. "
            f"Review {barrier}, final opening deductions, corner/door sealant, masking, setup, cleanup, loading, and travel."
        )
    if "eastern_elementary" in case_id:
        return (
            "Eastern Elementary, 6928 Bethlehem Rd. Small school insulation/foam repair scope, not a full-building shell. "
            f"{dims} Existing condition appears to need dense closed-cell or roof-foam style patching, not production wall foam. "
            "Review primer/detail prep, sealant at transitions, masking, setup, cleanup, and school-hours access coordination. "
            f"Verify substrate, exact location, target thickness/R-value, and whether {barrier} is required."
        )
    if "ku_ghent" in case_id:
        return (
            "KU Ghent 4G coal conveyor belt ramp: industrial insulation/foam scope on the conveyor/ramp enclosure. "
            f"{dims} Environment is dirty/industrial with access and safety constraints. Use closed-cell/roof-foam thinking because condensation and abuse are concerns. "
            "Walls target about R-14. Ceiling target about R-21 unless the plant specifies otherwise. "
            "Include orientation/ISN review, lift or equipment access, generator/temp power, masking, setup, cleanup, loading, travel, drum disposal/freight if minimums apply. "
            f"{sealant_hint} Review thermal or ignition barrier requirements before final."
        )
    return (
        f"{case.get('customer') or 'Customer'} insulation review. {dims} Walls target R-21 and ceiling target R-30. "
        "Review foam type, thickness, thermal/ignition barrier, masking, setup, cleanup, loading, travel, and opening deductions."
    )


def roofing_note(case: dict[str, Any]) -> str:
    case_id = str(case.get("case_id") or "")
    dims = roofing_dimensions(case)
    warranty = expected_warranty(case)
    warranty_text = f"{warranty}-year" if warranty else "reviewed"
    coating = "silicone-style"

    if "pegasus" in case_id:
        return (
            "Pegasus, 39 Pearce Industrial Rd. Various roof repairs/restoration review. "
            f"{dims} Repair list from walkdown: seal open panel laps and ridge seams, replace or tighten rusted screws/fasteners, reinforce leaking penetrations and curb corners, repair edge flashing gaps, and evaluate ponding/wet areas before coating. "
            f"Customer wants a practical {warranty_text} {coating} restoration option if the metal roof qualifies, not just isolated patches. "
            "Review rust-inhibitive primer need, seam treatment, detail sealant, reinforcement fabric at repairs, wet-area allowance, access/equipment, generator if power is unreliable, truck/travel, loading, and detail/top-coat labor."
        )
    if "uk_wt_young" in case_id:
        return (
            "UK WT Young Library Phase 2 roof restoration notes. "
            f"{dims} Existing roof has open seams, curb/drain/penetration details, edge flashing gaps, and isolated board/fastener/plate repairs before coating. "
            f"Owner is asking for a {warranty_text} {coating} roof coating restoration option if the roof qualifies. "
            "Review primer, detail sealant, reinforcement fabric at problem details, granules or walk areas, lift/forklift/generator access, loading/travel, library work constraints, and whether wet areas need IR scan or repair allowance."
        )
    if "galt_house" in case_id:
        return (
            "Galt House WT main pool deck / Aqua-Seal scope, dated around May 2025. "
            f"{dims} This is a pool deck/waterproofing coating project rather than a standard metal roof. "
            f"Owner needs a {warranty_text} waterproofing/wear system if the deck substrate qualifies. "
            "Repair list is surface prep, cracks and control joints, drain transitions, coating tie-ins, traffic/walk areas, and drainage/ponding review. Include pedestrian protection, masking, hotel/pool work windows, generator/equipment if needed, cleanup, truck/travel, and detail labor."
        )
    if "2025_roofing_did_not_get" in case_id:
        return (
            "Roofing opportunity from 2025 that we did not get; source notes are limited. "
            f"{dims} Treat as a commercial roof restoration lead with open seams, penetration details, aging fasteners, edge repairs, and repair allowance before coating. "
            f"Customer seemed to want a {warranty_text} {coating} option instead of tear-off if the substrate qualifies. "
            "Review primer, sealant, fabric, lift/access, generator/temp power, truck/travel, loading, seam/detail labor, possible granules/top-coat decision, and wet insulation or repair areas before pricing."
        )
    if "shelbyville" in case_id:
        return (
            "Shelbyville Municipal Water/Sewer Commission roof note. Historical source area looks unreliable, so do not use the 1 sq ft value as a real takeoff. "
            "Treat this as an ambiguous repair/restoration lead where dimensions must be confirmed before a final estimate. "
            "The source suggests roof coating/detail categories may have been considered, with primer, caulk/sealant, fabric/granules, lift or truck expense, generator/access, and miscellaneous roof details in play. "
            "Ask for roof dimensions, substrate, leak/ponding conditions, seam/penetration count, and whether this is repair-only or full restoration."
        )
    return (
        f"{case.get('customer') or 'Customer'} roof restoration note. {dims} "
        f"Review a {warranty_text} {coating} path, primer, seams, penetrations, caulk/detail, fabric/reinforcement, fasteners, access/equipment, generator, truck/travel, loading, and labor."
    )


def reviewed_note(case: dict[str, Any]) -> str:
    return insulation_note(case) if case.get("template_type") == "insulation" else roofing_note(case)


def write_xlsx(rows: list[dict[str, Any]], xlsx_path: Path) -> None:
    with pd.ExcelWriter(xlsx_path) as writer:
        pd.DataFrame(
            [
                {
                    "case_id": case.get("case_id"),
                    "template_type": case.get("template_type"),
                    "customer": case.get("customer"),
                    "job_name": case.get("job_name"),
                    "source_file": case.get("source_file"),
                    "reviewed_notes": case.get("generated_notes"),
                    "expected_decision_count": len(case.get("expected_decisions") or []),
                    "expected_workbook_rows": ",".join(str(row) for row in case.get("expected_workbook_rows") or []),
                }
                for case in rows
            ]
        ).to_excel(writer, sheet_name="Cases", index=False)
        pd.DataFrame([{"case_id": case.get("case_id"), **(case.get("area_trace") or {})} for case in rows]).to_excel(
            writer, sheet_name="Area Trace", index=False
        )
        decision_rows = []
        for case in rows:
            for decision in case.get("expected_decisions") or []:
                decision_rows.append({"case_id": case.get("case_id"), **decision})
        pd.DataFrame(decision_rows).to_excel(writer, sheet_name="Expected Decisions", index=False)
        pd.DataFrame(
            [
                {
                    "case_id": case.get("case_id"),
                    "review_method": (case.get("chat_review_metadata") or {}).get("review_method"),
                    "api_used": (case.get("chat_review_metadata") or {}).get("api_used"),
                    "updated_at": (case.get("chat_review_metadata") or {}).get("updated_at"),
                }
                for case in rows
            ]
        ).to_excel(writer, sheet_name="Review Metadata", index=False)


def regenerate(root: Path = DEFAULT_ROOT) -> None:
    jsonl_path = root / "generated_live_cases_chat_reviewed.jsonl"
    cases_dir = root / "cases"
    rows = [json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    updated_at = datetime.now(timezone.utc).isoformat()
    for case in rows:
        case["expected_decisions"] = generated_cases._dedupe_expected_decisions(case.get("expected_decisions") or [])
        case["expected_workbook_rows"] = sorted(
            {
                int(row["workbook_row"])
                for row in case["expected_decisions"]
                if row.get("workbook_row") not in (None, "") and generated_cases._safe_float(row.get("workbook_row"), -1) >= 0
            }
        )
        note = reviewed_note(case)
        case.setdefault("generated_notes_original", case.get("generated_notes") or "")
        case["generated_notes"] = note
        case["chat_review_metadata"] = {
            "review_method": "local_field_note_review_regeneration",
            "api_used": False,
            "updated_at": updated_at,
            "notes": "Reviewed notes are estimator field-note fixtures: dimensions/deductions plus natural decision clues.",
        }
        case_dir = cases_dir / generated_cases._slug(case["case_id"])
        case_dir.mkdir(parents=True, exist_ok=True)
        (case_dir / "notes_chat_reviewed.txt").write_text(note, encoding="utf-8")
        (case_dir / "source_decisions.json").write_text(
            json.dumps(case["expected_decisions"], indent=2, default=generated_cases._json_default),
            encoding="utf-8",
        )
        (case_dir / "area_trace.json").write_text(
            json.dumps(case.get("area_trace") or {}, indent=2, default=generated_cases._json_default),
            encoding="utf-8",
        )

    with jsonl_path.open("w", encoding="utf-8") as handle:
        for case in rows:
            handle.write(generated_cases._json_dumps(case) + "\n")
    (root / "eval_candidate_cases_chat_reviewed.json").write_text(
        json.dumps(
            [
                {
                    "case_id": case.get("case_id"),
                    "notes": case.get("generated_notes"),
                    "expected": case.get("expected_scope_fields") or {},
                    "metadata": {
                        "source_job_id": case.get("source_job_id"),
                        "source_file": case.get("source_file"),
                        "promotion_status": case.get("promotion_status"),
                        "review_method": (case.get("chat_review_metadata") or {}).get("review_method"),
                    },
                }
                for case in rows
            ],
            indent=2,
            default=generated_cases._json_default,
        ),
        encoding="utf-8",
    )
    write_xlsx(rows, root / "generated_live_cases_chat_reviewed.xlsx")
    print(f"regenerated {len(rows)} reviewed field-note cases")


if __name__ == "__main__":
    regenerate()
