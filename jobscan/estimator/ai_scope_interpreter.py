from __future__ import annotations

import json
import os
import re
from typing import Any, Callable

from .dimensions import parse_dimensions
from .rules import first_nonblank, to_float


AI_SCOPE_FIELDS = (
    "project_type",
    "estimate_mode",
    "division",
    "building_type",
    "roof_type",
    "substrate",
    "gross_sqft",
    "deduction_sqft",
    "net_sqft",
    "gross_area_sqft",
    "deduction_area_sqft",
    "estimated_sqft",
    "dimension_evidence",
    "coating_type",
    "warranty_years",
    "warranty_target_years",
    "condition",
    "roof_condition",
    "roof_condition_raw_phrase",
    "roof_condition_reason",
    "condition_flags",
    "condition_detail_flags",
    "penetration_count",
    "penetration_complexity",
    "penetrations_complexity",
    "penetrations_complexity_reason",
    "access_complexity",
    "access_complexity_reason",
    "defects",
    "scope_triggers",
    "partial_scope",
    "scope_packages",
    "missing_info",
    "missing_questions",
    "review_flags",
    "evidence_by_field",
    "contradictions",
    "confidence_by_field",
)

PACKAGE_DECISION_VALUES = {True, False, "review", "light", "heavy"}
TRUE_ENV_VALUES = {"1", "true", "yes", "y", "on"}


def ai_scope_interpreter_enabled(env: dict[str, str] | None = None) -> bool:
    source = env if env is not None else os.environ
    if str(source.get("DISABLE_AI_SCOPE_INTERPRETER") or "").strip().lower() in TRUE_ENV_VALUES:
        return False
    configured = source.get("ENABLE_AI_SCOPE_INTERPRETER")
    if configured is not None and str(configured).strip().lower() in TRUE_ENV_VALUES:
        return True
    return bool(source.get("OPENAI_API_KEY"))


def _empty_scope() -> dict[str, Any]:
    return {
        "project_type": "",
        "estimate_mode": "unknown",
        "division": "",
        "building_type": "",
        "roof_type": "",
        "substrate": "",
        "gross_sqft": None,
        "deduction_sqft": None,
        "net_sqft": None,
        "gross_area_sqft": None,
        "deduction_area_sqft": None,
        "estimated_sqft": None,
        "dimension_evidence": [],
        "coating_type": "",
        "warranty_years": None,
        "warranty_target_years": None,
        "condition": "",
        "roof_condition": "",
        "roof_condition_raw_phrase": "",
        "roof_condition_reason": "",
        "condition_flags": [],
        "condition_detail_flags": [],
        "penetration_count": None,
        "penetration_complexity": "",
        "penetrations_complexity": "",
        "penetrations_complexity_reason": "",
        "access_complexity": "",
        "access_complexity_reason": "",
        "defects": {
            "rust": False,
            "rusted_fasteners": False,
            "open_seams": False,
            "leaks": False,
            "ponding": False,
            "failed_coating": False,
            "wet_insulation": False,
            "damaged_board": False,
            "edge_metal_issues": False,
            "curb/flashing_issues": False,
        },
        "scope_triggers": {
            "coating": False,
            "primer": False,
            "partial_primer": False,
            "seam_treatment": False,
            "fastener_treatment": False,
            "caulk_detail": False,
            "fabric": False,
            "board_stock": False,
            "lift": False,
            "generator": False,
            "travel": False,
            "inspection": False,
        },
        "partial_scope": {
            "primer_basis_sqft": None,
            "seam_lf": None,
            "fastener_count": None,
            "fabric_lf": None,
            "coating_basis_sqft": None,
        },
        "scope_packages": {},
        "missing_info": [],
        "missing_questions": [],
        "review_flags": [],
        "evidence_by_field": {},
        "contradictions": [],
        "confidence_by_field": {},
    }


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, (tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _as_number(value: Any) -> float | None:
    number = to_float(value)
    return number if number is not None and number > 0 else None


def _as_int(value: Any) -> int | None:
    number = _as_number(value)
    return int(number) if number is not None else None


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = first_nonblank(value).strip().lower()
    return text in {"true", "yes", "y", "1", "include", "included", "applies"}


def _normalize_complexity(value: Any, *, field: str) -> str:
    text = first_nonblank(value).strip().lower().replace("_", " ").replace("-", " ")
    aliases = {
        "easy": "low",
        "simple": "low",
        "few": "low",
        "low": "low",
        "medium": "medium",
        "moderate": "medium",
        "average": "medium",
        "high": "high",
        "hard": "high",
        "difficult": "high",
        "many": "high",
    }
    if field == "roof_condition":
        if text in {"fair with rusted fasteners", "fair/rusted", "fair rusted"}:
            return "fair_with_rusted_fasteners"
        return text
    return aliases.get(text, text)


def _normalize_package_value(value: Any) -> bool | str | None:
    if isinstance(value, bool):
        return value
    text = first_nonblank(value).strip().lower()
    if text in {"true", "yes", "y", "include", "included", "applies"}:
        return True
    if text in {"false", "no", "n", "exclude", "excluded", "none"}:
        return False
    if text in {"review", "light", "heavy"}:
        return text
    return None


def validate_ai_scope(payload: Any) -> tuple[dict[str, Any], list[str]]:
    if not isinstance(payload, dict):
        raise ValueError("AI scope response must be a JSON object")
    cleaned = _empty_scope()
    warnings: list[str] = []
    for field in AI_SCOPE_FIELDS:
        if field not in payload:
            continue
        value = payload.get(field)
        if field in {"gross_area_sqft", "deduction_area_sqft", "estimated_sqft", "gross_sqft", "deduction_sqft", "net_sqft"}:
            cleaned[field] = _as_number(value)
        elif field in {"warranty_target_years", "warranty_years", "penetration_count"}:
            cleaned[field] = _as_int(value)
        elif field in {"condition_detail_flags", "condition_flags", "missing_info", "missing_questions", "review_flags", "dimension_evidence", "contradictions"}:
            cleaned[field] = _as_list(value)
        elif field in {"confidence_by_field"}:
            confidence: dict[str, float] = {}
            if isinstance(value, dict):
                for key, raw_score in value.items():
                    score = to_float(raw_score)
                    if score is not None:
                        confidence[str(key)] = max(0.0, min(1.0, float(score)))
            cleaned[field] = confidence
        elif field == "evidence_by_field":
            evidence: dict[str, list[str]] = {}
            if isinstance(value, dict):
                for key, raw_evidence in value.items():
                    evidence[str(key)] = _as_list(raw_evidence)
            cleaned[field] = evidence
        elif field in {"defects", "scope_triggers"}:
            target = dict(cleaned[field])
            if isinstance(value, dict):
                for key, raw_decision in value.items():
                    canonical = str(key)
                    if field == "defects" and canonical in {"curb_flashing_issues", "flashing_issues"}:
                        canonical = "curb/flashing_issues"
                    if canonical in target:
                        target[canonical] = _as_bool(raw_decision)
            cleaned[field] = target
        elif field == "partial_scope":
            partial = dict(cleaned[field])
            if isinstance(value, dict):
                for key in partial:
                    partial[key] = _as_number(value.get(key))
            cleaned[field] = partial
        elif field == "scope_packages":
            packages: dict[str, bool | str] = {}
            if isinstance(value, dict):
                for package, raw_decision in value.items():
                    decision = _normalize_package_value(raw_decision)
                    if decision in PACKAGE_DECISION_VALUES:
                        packages[str(package)] = decision
                    else:
                        warnings.append(f"Ignored invalid AI package decision for {package}.")
            cleaned[field] = packages
        elif field in {"penetrations_complexity", "access_complexity"}:
            cleaned[field] = _normalize_complexity(value, field=field)
        elif field == "penetration_complexity":
            cleaned[field] = _normalize_complexity(value, field="penetrations_complexity")
        elif field == "roof_condition":
            cleaned[field] = _normalize_complexity(value, field=field)
        elif field == "condition":
            cleaned[field] = _normalize_complexity(value, field="roof_condition")
        else:
            cleaned[field] = first_nonblank(value)
    cleaned["gross_area_sqft"] = cleaned["gross_area_sqft"] or cleaned["gross_sqft"]
    cleaned["deduction_area_sqft"] = cleaned["deduction_area_sqft"] if cleaned["deduction_area_sqft"] is not None else cleaned["deduction_sqft"]
    cleaned["estimated_sqft"] = cleaned["estimated_sqft"] or cleaned["net_sqft"]
    cleaned["gross_sqft"] = cleaned["gross_sqft"] or cleaned["gross_area_sqft"]
    cleaned["deduction_sqft"] = cleaned["deduction_sqft"] if cleaned["deduction_sqft"] is not None else cleaned["deduction_area_sqft"]
    cleaned["net_sqft"] = cleaned["net_sqft"] or cleaned["estimated_sqft"]
    cleaned["warranty_target_years"] = cleaned["warranty_target_years"] or cleaned["warranty_years"]
    cleaned["warranty_years"] = cleaned["warranty_years"] or cleaned["warranty_target_years"]
    cleaned["roof_condition"] = cleaned["roof_condition"] or cleaned["condition"]
    cleaned["condition"] = cleaned["condition"] or cleaned["roof_condition"]
    cleaned["condition_detail_flags"] = sorted(set([*cleaned["condition_detail_flags"], *cleaned["condition_flags"]]))
    cleaned["condition_flags"] = list(cleaned["condition_detail_flags"])
    cleaned["penetrations_complexity"] = cleaned["penetrations_complexity"] or cleaned["penetration_complexity"]
    cleaned["penetration_complexity"] = cleaned["penetration_complexity"] or cleaned["penetrations_complexity"]
    cleaned["review_flags"].extend(warnings)
    return cleaned, warnings


def _prompt(notes: str, deterministic_scope: dict[str, Any] | None = None) -> list[dict[str, str]]:
    deterministic_json = json.dumps(deterministic_scope or {}, default=str, sort_keys=True)
    return [
        {
            "role": "system",
            "content": (
                "You interpret Spray-Tec estimator field notes into structured scope JSON. "
                "Return JSON only. Do not estimate prices, unit costs, final cost, or totals. "
                "Do not invent missing facts; use missing_questions and review_flags for uncertainty. "
                "Preserve short original text evidence for each key field in evidence_by_field."
            ),
        },
        {
            "role": "user",
            "content": (
                "Return exactly these fields: "
                + ", ".join(AI_SCOPE_FIELDS)
                + ". scope_packages values must be true, false, review, light, or heavy. "
                "scope_triggers and defects values are booleans. "
                "partial_scope fields are numbers or null. "
                "Use the deterministic scope only as context; explicit note phrases win. "
                "Never invent square footage; if notes lack sqft/dimensions, leave area fields null.\n\n"
                f"Deterministic scope:\n{deterministic_json}\n\nField notes:\n{notes}"
            ),
        },
    ]


def _call_openai_scope_interpreter(notes: str, deterministic_scope: dict[str, Any] | None = None) -> str:
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is not set")
    try:
        from openai import OpenAI  # type: ignore
    except Exception as exc:  # pragma: no cover - depends on optional dependency
        raise RuntimeError("openai package is not installed") from exc
    model = os.getenv("OPENAI_SCOPE_INTERPRETER_MODEL") or "gpt-4o-mini"
    client = OpenAI()
    response = client.chat.completions.create(
        model=model,
        temperature=0,
        response_format={"type": "json_object"},
        messages=_prompt(notes, deterministic_scope),
    )
    return response.choices[0].message.content or "{}"


def _sentence_containing(notes: str, pattern: str) -> str:
    match = re.search(pattern, notes, re.I)
    if not match:
        return ""
    start_candidates = [notes.rfind(marker, 0, match.start()) for marker in (".", ";", "\n")]
    start = max(start_candidates) + 1
    end_candidates = [idx for idx in (notes.find(marker, match.end()) for marker in (".", ";", "\n")) if idx != -1]
    end = min(end_candidates) if end_candidates else len(notes)
    return " ".join(notes[start:end].strip().split())


def _has_negated(text: str, term_pattern: str) -> bool:
    return bool(re.search(rf"\b(?:no|not|without|none|no visible)\b[^.;,\n]{{0,30}}{term_pattern}", text, re.I))


def _has_positive(text: str, term_pattern: str) -> bool:
    if not re.search(term_pattern, text, re.I):
        return False
    return not _has_negated(text, term_pattern)


def _number_word_value(value: str | None) -> float | None:
    if not value:
        return None
    text = value.strip().lower().replace(",", "")
    number = to_float(text)
    if number is not None:
        return number
    words = {
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
        "eleven": 11,
        "twelve": 12,
        "thirteen": 13,
        "fourteen": 14,
        "fifteen": 15,
        "sixteen": 16,
        "seventeen": 17,
        "eighteen": 18,
        "nineteen": 19,
        "twenty": 20,
        "thirty": 30,
        "forty": 40,
        "fifty": 50,
        "sixty": 60,
        "seventy": 70,
        "eighty": 80,
        "ninety": 90,
    }
    if text in words:
        return float(words[text])
    parts = re.split(r"[\s-]+", text)
    if all(part in words for part in parts):
        return float(sum(words[part] for part in parts))
    return None


def _extract_warranty(notes: str) -> int | None:
    number_word_pattern = (
        r"one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|"
        r"fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty"
    )
    match = re.search(rf"\b(?P<value>\d{{1,2}}|{number_word_pattern})\s*[- ]?\s*(?:year|yr)\b", notes, re.I)
    if not match:
        return None
    raw_value = match.group("value")
    parsed_value = _number_word_value(raw_value)
    if parsed_value is None:
        return None
    value = int(parsed_value)
    return value if 1 <= value <= 40 else None


def _extract_penetration_count(notes: str) -> int | None:
    total = 0
    pattern = (
        r"\b(?P<count>one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|\d{1,3})\s+"
        r"(?P<object>(?:plumbing\s+)?vents?|hvac\s+curbs?|curbs?|rtus?|rooftop\s+units?|drains?|skylights?|penetrations?)\b"
    )
    for match in re.finditer(pattern, notes, re.I):
        value = _number_word_value(match.group("count"))
        if value is not None:
            total += int(value)
    return total or None


def _extract_partial_primer(notes: str, net_sqft: float | None) -> float | None:
    if not net_sqft or not re.search(r"\b(?:primer|prime|priming)\b", notes, re.I):
        return None
    number_word_pattern = (
        r"(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|"
        r"fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|"
        r"eighty|ninety)(?:[-\s](?:one|two|three|four|five|six|seven|eight|nine))?"
    )
    numeric_or_word = rf"(?:\d+(?:\.\d+)?|{number_word_pattern})"
    for match in re.finditer(rf"(?:about|approximately|around|roughly)?\s*(?P<value>{numeric_or_word})\s*(?:%|percent)", notes, re.I):
        window = notes[max(0, match.start() - 120) : min(len(notes), match.end() + 120)]
        if not re.search(r"\b(?:primer|prime|priming)\b", window, re.I):
            continue
        if "%" not in match.group(0) and "percent" not in match.group(0).lower():
            continue
        percent = _number_word_value(match.group("value"))
        if percent is not None and 0 < percent <= 100:
            return round(float(net_sqft) * percent / 100, 2)
    for match in re.finditer(r"\b(?P<value>\d[\d,]*(?:\.\d+)?)\s*(?:sq\s*ft|sqft|square feet)\b", notes, re.I):
        window = notes[max(0, match.start() - 80) : min(len(notes), match.end() + 80)]
        if re.search(r"\b(?:primer|prime|priming)\b", window, re.I):
            sqft = _number_word_value(match.group("value"))
            if sqft is not None:
                return round(min(float(sqft), float(net_sqft)), 2)
    return None


def deterministic_scope_interpretation(notes: str, deterministic_scope: dict[str, Any] | None = None) -> dict[str, Any]:
    deterministic_scope = deterministic_scope or {}
    cleaned, _warnings = validate_ai_scope({})
    text = notes or ""
    lowered = text.lower()
    dimension_summary = parse_dimensions(text).to_dict()
    gross = _as_number(dimension_summary.get("gross_area_sqft")) or _as_number(deterministic_scope.get("gross_area_sqft"))
    deduction = _as_number(dimension_summary.get("deduction_area_sqft"))
    if deduction is None:
        deduction = _as_number(deterministic_scope.get("deduction_area_sqft"))
    net = _as_number(dimension_summary.get("net_area_sqft")) or _as_number(deterministic_scope.get("estimated_sqft")) or _as_number(deterministic_scope.get("surface_area_sqft"))

    coating = "silicone" if "silicone" in lowered else "acrylic" if "acrylic" in lowered else first_nonblank(deterministic_scope.get("coating_type"))
    substrate = first_nonblank(deterministic_scope.get("substrate"))
    if not substrate:
        if "standing seam" in lowered:
            substrate = "standing seam metal"
        elif "metal" in lowered:
            substrate = "metal"
        elif "tpo" in lowered:
            substrate = "tpo"
        elif "epdm" in lowered:
            substrate = "epdm"
        elif "modified bitumen" in lowered or "mod bit" in lowered:
            substrate = "modified bitumen"

    estimate_mode = "unknown"
    if any(term in lowered for term in ("spray foam", "insulation", "r-value", "thermal barrier", "dc315")):
        estimate_mode = "insulation"
    elif any(term in lowered for term in ("service call", "pipe boot", "patch", "repair", "leak call", "emergency")) and not any(term in lowered for term in ("full", "restoration", "coating system")):
        estimate_mode = "repair"
    elif any(term in lowered for term in ("maintenance coating", "extend the life")):
        estimate_mode = "maintenance"
    elif coating or any(term in lowered for term in ("restoration", "roof coating", "coating system")):
        estimate_mode = "restoration"

    project_type = "roof coating" if estimate_mode in {"restoration", "maintenance"} else first_nonblank(deterministic_scope.get("project_type"))
    warranty = _extract_warranty(text) or _as_int(deterministic_scope.get("warranty_target_years")) or _as_int(deterministic_scope.get("warranty_target"))
    defects = dict(cleaned["defects"])
    defects["rusted_fasteners"] = _has_positive(text, r"\brusted\s+fasteners?\b")
    defects["rust"] = _has_positive(text, r"\brust(?:ed|y)?\b") or defects["rusted_fasteners"]
    defects["open_seams"] = _has_positive(text, r"\b(?:open\s+seams?|failed\s+seams?|seams?\s+(?:opening|separating|beginning\s+to\s+separate))\b")
    defects["leaks"] = _has_positive(text, r"\b(?:leaks?|leaking)\b")
    defects["ponding"] = _has_positive(text, r"\bponding\b")
    defects["failed_coating"] = _has_positive(text, r"\b(?:failed|failing|peeling)\s+(?:coating|roof coating)\b")
    defects["wet_insulation"] = _has_positive(text, r"\bwet\s+insulation\b")
    defects["damaged_board"] = _has_positive(text, r"\b(?:damaged|soft|rotten)\s+(?:board|deck|cover board)\b")
    defects["edge_metal_issues"] = _has_positive(text, r"\b(?:edge metal|coping)\b[^.;\n]{0,40}\b(?:issue|loose|damage|repair)\b")
    defects["curb/flashing_issues"] = _has_positive(text, r"\b(?:curb|flashing|pipe boot|pitch pocket)\b")

    condition_flags = [key for key, value in defects.items() if value]
    if "excellent" in lowered:
        condition = "excellent"
    elif "good condition" in lowered or re.search(r"\bgood\b[^.;\n]{0,20}\bcondition\b", lowered):
        condition = "good"
    elif "fair" in lowered:
        condition = "fair_with_rusted_fasteners" if defects["rusted_fasteners"] else "fair"
    elif defects["rust"] or defects["open_seams"]:
        condition = "poor/rusted" if "widespread" in lowered or "severe" in lowered or "poor" in lowered else "fair"
    else:
        condition = first_nonblank(deterministic_scope.get("roof_condition"))

    penetration_count = _extract_penetration_count(text)
    if re.search(r"\bfew\s+penetrations?\b", text, re.I):
        penetration_complexity = "low"
    elif penetration_count is not None:
        penetration_complexity = "low" if penetration_count <= 2 else "medium" if penetration_count <= 8 else "high"
    else:
        penetration_complexity = first_nonblank(deterministic_scope.get("penetrations_complexity"), deterministic_scope.get("penetration_complexity"))
    if re.search(r"\beasy\s+access\b|\baccess\s+(?:is\s+)?easy\b", text, re.I):
        access = "low"
    elif re.search(r"\b(?:difficult|hard|poor)\s+access\b|\bcrane\b|\bboom\s+lift\b", text, re.I):
        access = "high"
    else:
        access = first_nonblank(deterministic_scope.get("access_complexity"))

    triggers = dict(cleaned["scope_triggers"])
    triggers["coating"] = bool(coating or "coating" in lowered or "restoration" in lowered)
    triggers["primer"] = bool(re.search(r"\b(?:primer|prime|priming|rust|oxidation|adhesion)\b", lowered))
    triggers["partial_primer"] = bool(triggers["primer"] and re.search(r"\b(?:only|partial|percent|%|south edge|north edge|east edge|west edge)\b", lowered))
    triggers["seam_treatment"] = defects["open_seams"]
    triggers["fastener_treatment"] = defects["rusted_fasteners"] or _has_positive(text, r"\bfasteners?\b")
    triggers["caulk_detail"] = defects["curb/flashing_issues"] or _has_positive(text, r"\b(?:penetrations?|curbs?|pipe boots?|pitch pockets?)\b")
    triggers["fabric"] = _has_positive(text, r"\bfabric\b")
    triggers["board_stock"] = defects["damaged_board"]
    triggers["lift"] = _has_positive(text, r"\b(?:lift|boom lift|scissor lift)\b")
    triggers["generator"] = _has_positive(text, r"\bgenerator\b")
    triggers["travel"] = _has_positive(text, r"\b(?:out of town|travel|hotel|lodging)\b")
    triggers["inspection"] = _has_positive(text, r"\b(?:inspection|inspect|site visit)\b")

    partial = dict(cleaned["partial_scope"])
    partial["primer_basis_sqft"] = _extract_partial_primer(text, net)
    partial["coating_basis_sqft"] = net if triggers["coating"] and net else None

    contradictions: list[str] = []
    for label, positive_pattern, negative_pattern in (
        ("rust", r"\brust(?:ed|y)?\b", r"\b(?:no|not|without|no visible)\b[^.;,\n]{0,30}\brust"),
        ("open_seams", r"\bopen\s+seams?\b", r"\b(?:no|not|without)\b[^.;,\n]{0,30}\bopen\s+seams?"),
        ("leaks", r"\bleaks?|leaking\b", r"\b(?:no|not|without)\b[^.;,\n]{0,30}\bleaks?"),
    ):
        if re.search(positive_pattern, lowered) and re.search(negative_pattern, lowered):
            contradictions.append(f"Potential contradiction around {label}; verify scope.")

    missing_questions: list[str] = []
    missing_info: list[str] = []
    if triggers["coating"] and not net:
        missing_info.append("estimated_sqft")
        missing_questions.extend(["Approximate roof square footage?", "Roof dimensions?"])
    if not substrate and estimate_mode in {"restoration", "maintenance", "repair"}:
        missing_info.append("substrate")
        missing_questions.append("Roof type/substrate?")
    if triggers["primer"] and triggers["partial_primer"] and partial["primer_basis_sqft"] is None:
        missing_questions.append("How many square feet or what percentage requires primer?")

    evidence_by_field = {
        "dimensions": [area.get("source_text", "") for area in (dimension_summary.get("included_areas") or []) if area.get("source_text")],
        "deductions": [area.get("source_text", "") for area in (dimension_summary.get("deducted_areas") or []) if area.get("source_text")],
        "substrate": [_sentence_containing(text, r"\b(?:standing seam|metal|tpo|epdm|modified bitumen|mod bit)\b")],
        "coating_type": [_sentence_containing(text, r"\b(?:silicone|acrylic|gaco|hydrostop)\b")],
        "condition": [_sentence_containing(text, r"\b(?:excellent|good|fair|poor|rust|oxidation|open seams?|leaks?|ponding)\b")],
        "access_complexity": [_sentence_containing(text, r"\baccess\b")],
        "penetration_complexity": [_sentence_containing(text, r"\b(?:penetrations?|curbs?|vents?|rtus?|drains?)\b")],
        "partial_scope": [_sentence_containing(text, r"\b(?:primer|prime|priming)\b")],
    }
    evidence_by_field = {key: [item for item in value if item] for key, value in evidence_by_field.items()}

    cleaned.update(
        {
            "project_type": project_type,
            "estimate_mode": estimate_mode,
            "division": "Roofing" if estimate_mode in {"restoration", "maintenance", "repair"} else "",
            "roof_type": substrate,
            "substrate": substrate,
            "gross_sqft": gross,
            "deduction_sqft": deduction,
            "net_sqft": net,
            "gross_area_sqft": gross,
            "deduction_area_sqft": deduction,
            "estimated_sqft": net,
            "dimension_evidence": evidence_by_field.get("dimensions", []),
            "coating_type": coating,
            "warranty_years": warranty,
            "warranty_target_years": warranty,
            "condition": condition,
            "roof_condition": condition,
            "condition_flags": condition_flags,
            "condition_detail_flags": condition_flags,
            "penetration_count": penetration_count,
            "penetration_complexity": penetration_complexity,
            "penetrations_complexity": penetration_complexity,
            "access_complexity": access,
            "defects": defects,
            "scope_triggers": triggers,
            "scope_packages": {
                "coating": True if triggers["coating"] else False,
                "primer": "review" if triggers["primer"] else False,
                "seam_treatment": "review" if triggers["seam_treatment"] else False,
                "fastener_treatment": "review" if triggers["fastener_treatment"] else False,
                "caulk_detail": "review" if triggers["caulk_detail"] else False,
                "fabric": "review" if triggers["fabric"] else False,
                "board_stock": "review" if triggers["board_stock"] else False,
            },
            "partial_scope": partial,
            "confidence_by_field": {
                "dimensions": float(dimension_summary.get("confidence") or 0),
                "substrate": 0.85 if substrate else 0.0,
                "coating_type": 0.85 if coating else 0.0,
                "condition": 0.8 if condition else 0.0,
                "access_complexity": 0.8 if access else 0.0,
                "penetration_complexity": 0.8 if penetration_complexity else 0.0,
            },
            "evidence_by_field": evidence_by_field,
            "contradictions": contradictions,
            "missing_info": missing_info,
            "missing_questions": missing_questions,
            "review_flags": contradictions[:],
        }
    )
    return cleaned


def interpret_field_notes_with_ai(
    notes: str,
    deterministic_scope: dict | None = None,
    *,
    provider: Callable[[str, dict[str, Any] | None], str | dict[str, Any]] | None = None,
) -> dict:
    fallback = deterministic_scope_interpretation(notes, deterministic_scope)
    enabled = ai_scope_interpreter_enabled()
    if provider is None and not os.getenv("OPENAI_API_KEY") and not enabled:
        return fallback
    if not enabled and provider is None:
        return fallback
    try:
        raw = provider(notes, deterministic_scope) if provider is not None else _call_openai_scope_interpreter(notes, deterministic_scope)
        payload = json.loads(raw) if isinstance(raw, str) else raw
        cleaned, warnings = validate_ai_scope(payload)
        cleaned["review_flags"].extend(warnings)
        return cleaned
    except Exception as exc:
        cleaned = deterministic_scope_interpretation(notes, deterministic_scope)
        cleaned["review_flags"].append(f"AI scope interpreter unavailable or invalid; deterministic parser used. ({type(exc).__name__})")
        return cleaned


def _dimension_math_is_high_confidence(deterministic_scope: dict[str, Any]) -> bool:
    summary = deterministic_scope.get("dimension_summary") or {}
    if not isinstance(summary, dict):
        return False
    net = _as_number(summary.get("net_area_sqft"))
    warnings = summary.get("warnings") or []
    return bool(net and not warnings and (summary.get("included_areas") or summary.get("deducted_areas")))


def _notes_have_area_or_dimensions(notes: str) -> bool:
    summary = parse_dimensions(notes).to_dict()
    return bool(
        summary.get("net_area_sqft")
        or summary.get("stated_sqft")
        or summary.get("included_areas")
        or re.search(r"\d[\d,]*(?:\.\d+)?\s*(?:sq\s*ft|sqft|sf|square feet)\b", notes, re.I)
    )


def _note_has_few_penetrations(notes: str) -> bool:
    return bool(re.search(r"\bfew\s+penetrations?\b", notes, re.I))


def _note_has_easy_access(notes: str) -> bool:
    return bool(re.search(r"\b(?:easy|low)\s+access\b|\baccess\s+(?:is|looks|seems)?\s*(?:easy|low)\b", notes, re.I))


def _note_has_fair_overall(notes: str) -> bool:
    return bool(re.search(r"\bfair\s+(?:overall|condition)\b", notes, re.I))


def _note_allows_poor_condition(notes: str) -> bool:
    return bool(re.search(r"\b(?:poor|severe|widespread|heavy)\b", notes, re.I))


def _condition_flags_from_notes(notes: str) -> list[str]:
    text = notes.lower()
    no_rust = bool(re.search(r"\b(?:no|without)\s+(?:visible\s+)?rust\b|\bno\s+rusted\s+fasteners?\b", text))
    no_open_seams = bool(re.search(r"\b(?:no|without)\s+(?:open\s+)?seam\s+issues?\b|\b(?:no|without)\s+open\s+seams?\b", text))
    no_leaks = bool(re.search(r"\b(?:no|without)\s+(?:interior\s+)?leaks?\b|\bno\s+leaking\b", text))
    flags: list[str] = []
    if not no_rust:
        if "rusted fastener" in text or "rusted fasteners" in text:
            flags.append("rusted_fasteners")
        elif "rust" in text or "rusted" in text:
            flags.append("rust")
    if not no_open_seams and ("open seam" in text or "open seams" in text or "seams opening" in text):
        flags.append("open_seams")
    if not no_leaks and re.search(r"\b(?:leak|leaks|leaking)\b", text):
        flags.append("leaks")
    if "ponding" in text:
        flags.append("ponding")
    if "minor dirt" in text:
        flags.append("minor_dirt")
    return flags


def merge_ai_scope_with_deterministic(
    notes: str,
    deterministic_scope: dict[str, Any],
    ai_scope: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    ai_scope = ai_scope or _empty_scope()
    final_scope = dict(deterministic_scope)
    merge_decisions: list[dict[str, Any]] = []
    ai_review_flags = list(ai_scope.get("review_flags") or [])

    def set_field(field: str, value: Any, reason: str) -> None:
        old = final_scope.get(field)
        if value in (None, "", [], {}):
            return
        if old == value:
            return
        final_scope[field] = value
        merge_decisions.append({"field": field, "from": old, "to": value, "decision": "accepted", "reason": reason})

    # Deterministic dimension math is source of truth when dimensions were parsed confidently.
    if _dimension_math_is_high_confidence(deterministic_scope):
        merge_decisions.append(
            {
                "field": "estimated_sqft",
                "from": ai_scope.get("estimated_sqft"),
                "to": final_scope.get("estimated_sqft"),
                "decision": "rejected",
                "reason": "Deterministic dimension math has high confidence.",
            }
        )
    elif _as_number(ai_scope.get("estimated_sqft")) and _notes_have_area_or_dimensions(notes):
        set_field("estimated_sqft", ai_scope.get("estimated_sqft"), "AI filled missing or low-confidence sqft.")
        set_field("surface_area_sqft", ai_scope.get("estimated_sqft"), "AI filled missing or low-confidence sqft.")
    elif _as_number(ai_scope.get("estimated_sqft")):
        ai_review_flags.append("AI supplied square footage without note evidence; ignored.")
        merge_decisions.append(
            {
                "field": "estimated_sqft",
                "from": ai_scope.get("estimated_sqft"),
                "to": final_scope.get("estimated_sqft"),
                "decision": "rejected",
                "reason": "AI may not invent square footage when notes lack area or dimension evidence.",
            }
        )

    for field in ("project_type", "division", "building_type", "substrate", "coating_type", "estimate_mode"):
        if ai_scope.get(field):
            set_field(field, ai_scope[field], f"AI interpreted {field}.")
    if ai_scope.get("roof_type"):
        set_field("roof_type", ai_scope["roof_type"], "AI interpreted roof type.")
        if not final_scope.get("substrate"):
            set_field("substrate", ai_scope["roof_type"], "AI interpreted roof type/substrate.")

    warranty_value = ai_scope.get("warranty_target_years") or ai_scope.get("warranty_years")
    if warranty_value:
        set_field("warranty_target_years", warranty_value, "AI interpreted warranty target.")
        set_field("warranty_target", warranty_value, "AI interpreted warranty target.")

    for field, ai_fields in (
        ("gross_area_sqft", ("gross_area_sqft", "gross_sqft")),
        ("deduction_area_sqft", ("deduction_area_sqft", "deduction_sqft")),
        ("net_area_sqft", ("net_sqft", "estimated_sqft")),
    ):
        value = next((ai_scope.get(ai_field) for ai_field in ai_fields if ai_scope.get(ai_field)), None)
        if not final_scope.get(field) and value and _notes_have_area_or_dimensions(notes):
            set_field(field, value, f"AI filled {field}.")

    ai_condition = first_nonblank(ai_scope.get("roof_condition"))
    if ai_condition:
        if ai_condition.startswith("poor") and _note_has_fair_overall(notes) and not _note_allows_poor_condition(notes):
            merge_decisions.append(
                {
                    "field": "roof_condition",
                    "from": ai_condition,
                    "to": final_scope.get("roof_condition"),
                    "decision": "rejected",
                    "reason": "Note says fair overall; AI cannot classify poor without explicit severe/poor language.",
                }
            )
        else:
            set_field("roof_condition", ai_condition, "AI interpreted roof condition from explicit note phrase.")
    elif _note_has_fair_overall(notes) and "rust" in notes.lower():
        set_field("roof_condition", "fair_with_rusted_fasteners", "Explicit note says fair overall with rusted fasteners.")

    if _note_has_fair_overall(notes) and "rust" in notes.lower() and str(final_scope.get("roof_condition") or "").startswith("poor"):
        old = final_scope.get("roof_condition")
        final_scope["roof_condition"] = "fair_with_rusted_fasteners"
        merge_decisions.append(
            {
                "field": "roof_condition",
                "from": old,
                "to": "fair_with_rusted_fasteners",
                "decision": "guardrail_override",
                "reason": "Explicit 'fair overall' phrase prevents rusted fasteners from making the whole roof poor.",
            }
        )

    ai_penetrations = first_nonblank(ai_scope.get("penetrations_complexity"))
    if _note_has_few_penetrations(notes):
        old = final_scope.get("penetrations_complexity")
        final_scope["penetrations_complexity"] = "low"
        merge_decisions.append(
            {
                "field": "penetrations_complexity",
                "from": ai_penetrations or old,
                "to": "low",
                "decision": "guardrail_override",
                "reason": "Explicit note phrase says few penetrations.",
            }
        )
    elif ai_penetrations:
        set_field("penetrations_complexity", ai_penetrations, "AI interpreted penetrations complexity.")

    ai_access = first_nonblank(ai_scope.get("access_complexity"))
    if _note_has_easy_access(notes):
        old = final_scope.get("access_complexity")
        final_scope["access_complexity"] = "low"
        merge_decisions.append(
            {
                "field": "access_complexity",
                "from": ai_access or old,
                "to": "low",
                "decision": "guardrail_override",
                "reason": "Explicit note phrase says easy access.",
            }
        )
    elif ai_access:
        set_field("access_complexity", ai_access, "AI interpreted access complexity.")

    flags = sorted(set(_condition_flags_from_notes(notes) + _as_list(ai_scope.get("condition_detail_flags"))))
    if flags:
        final_scope["condition_detail_flags"] = flags
        merge_decisions.append({"field": "condition_detail_flags", "to": flags, "decision": "accepted", "reason": "Merged note and AI condition detail flags."})

    for field in ("roof_condition_raw_phrase", "roof_condition_reason", "penetrations_complexity_reason", "access_complexity_reason"):
        if ai_scope.get(field):
            final_scope[field] = ai_scope[field]

    if isinstance(ai_scope.get("scope_packages"), dict):
        final_scope["ai_scope_packages"] = ai_scope["scope_packages"]
    for field in ("defects", "scope_triggers", "partial_scope", "confidence_by_field", "evidence_by_field", "contradictions", "missing_questions", "dimension_evidence"):
        value = ai_scope.get(field)
        if value not in (None, "", [], {}):
            final_scope[field] = value
            merge_decisions.append({"field": field, "to": value, "decision": "accepted", "reason": "AI scope interpreter structured field."})
    if ai_scope.get("penetration_count") is not None:
        set_field("penetration_count", ai_scope["penetration_count"], "AI interpreted penetration count.")
    if ai_scope.get("missing_info"):
        existing = list(final_scope.get("missing_info") or [])
        for item in ai_scope.get("missing_info") or []:
            if item not in existing:
                existing.append(item)
        final_scope["missing_info"] = existing
    if ai_scope.get("contradictions"):
        ai_review_flags.extend(str(item) for item in ai_scope.get("contradictions") or [])

    return final_scope, merge_decisions, ai_review_flags
