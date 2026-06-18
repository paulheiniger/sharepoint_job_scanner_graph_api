from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import pandas as pd

PRICING_COLUMNS = [
    "source_file",
    "source_type",
    "vendor",
    "category",
    "product_name",
    "description",
    "unit_price",
    "unit_of_measure",
    "package_size",
    "price_basis",
    "price_per_gallon",
    "vendor_item_no",
    "details",
    "effective_date",
    "freight_terms",
    "notes",
    "parser_confidence",
    "needs_review",
]

REVIEW_COLUMNS = [
    "action_flags",
    "review_decision",
    "match_confidence",
    "match_score",
    "match_strategy",
    "source_file",
    "source_type",
    "vendor",
    "category",
    "product_name",
    "description",
    "current_product_name",
    "current_category",
    "current_vendor",
    "current_unit_price",
    "proposed_unit_price",
    "unit_of_measure",
    "package_size",
    "price_basis",
    "price_per_gallon",
    "vendor_item_no",
    "effective_date",
    "freight_terms",
    "notes",
    "details",
    "needs_review",
]

PRICE_RE = re.compile(r"(?<![\w.-])\$?\s*(\d{1,4}(?:,\d{3})*(?:\.\d{1,4})?)(?![\w.-])")
DATE_RE = re.compile(r"\b(\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}[./-]\d{1,2}[./-]\d{1,2})\b")
ITEM_NO_RE = re.compile(r"\b(?:item|sku|part|no\.?|#)\s*[:#-]?\s*([A-Z0-9][A-Z0-9.-]{2,})\b", re.I)
UNIT_WORDS = {
    "gal": "gallon",
    "gallon": "gallon",
    "gallons": "gallon",
    "pail": "pail",
    "bucket": "pail",
    "drum": "drum",
    "kit": "kit",
    "case": "case",
    "bag": "bag",
    "roll": "roll",
    "tube": "tube",
    "sausage": "sausage",
    "cartridge": "cartridge",
    "lb": "lb",
    "lbs": "lb",
    "sqft": "sqft",
    "sq": "sqft",
}


@dataclass
class MatchResult:
    master_index: int | None
    score: float
    strategy: str
    confidence: str


def blank(value: Any) -> bool:
    if value is None:
        return True
    text = str(value).strip()
    return not text or text.lower() in {"nan", "none", "null"}


def clean_text(value: Any) -> str:
    if blank(value):
        return ""
    return " ".join(str(value).replace("\xa0", " ").strip().split())


def parse_float(value: Any) -> float | None:
    if blank(value):
        return None
    raw = str(value).strip()
    if DATE_RE.fullmatch(raw):
        return None
    if re.search(r"[A-Za-z]", raw) and not re.match(r"^\s*\$?\s*-?\d[\d,]*(?:\.\d+)?\s*(?:/|per)?\s*[A-Za-z]*\s*$", raw):
        return None
    text = raw.replace("$", "").replace(",", "")
    text = re.sub(r"[^0-9.\-]", "", text)
    if text in {"", ".", "-", "-."}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def parse_date(value: Any) -> str:
    text = clean_text(value)
    if not text:
        return ""
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%m-%d-%y", "%m.%d.%Y", "%m.%d.%y", "%Y-%m-%d", "%B %Y", "%b %Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    return text


def effective_date_from_text(text: str) -> str:
    match = re.search(r"eff(?:ective)?\.?\s*(?:date)?\s*[:\-]?\s*([A-Za-z]+\s+\d{1,2},?\s+\d{4}|\d{1,2}[./-]\d{1,2}[./-]\d{2,4})", text, re.I)
    if match:
        return parse_date(match.group(1).replace(",", ""))
    match = DATE_RE.search(text)
    return parse_date(match.group(1)) if match else ""


def vendor_from_filename(path: Path) -> str:
    name = path.stem.lower()
    if "spray tec" in name or "spray-tec" in name:
        return "Spray-Tec"
    if "gaf" in name or "coatings" in name or "terr" in name:
        return "GAF"
    if "gaco" in name:
        return "Gaco"
    return ""


def normalize_product_key(value: Any) -> str:
    text = clean_text(value).lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    stop = {"the", "and", "with", "for"}
    return " ".join(part for part in text.split() if part not in stop)


def normalize_price_row(row: dict[str, Any]) -> dict[str, Any]:
    out = {column: "" for column in PRICING_COLUMNS}
    out.update({key: value for key, value in row.items() if key in out})
    for key in ("source_file", "source_type", "vendor", "category", "product_name", "description", "unit_of_measure", "package_size", "price_basis", "vendor_item_no", "details", "effective_date", "freight_terms", "notes"):
        out[key] = clean_text(out.get(key))
    for key in ("unit_price", "price_per_gallon", "parser_confidence"):
        value = parse_float(out.get(key))
        out[key] = "" if value is None else value
    out["needs_review"] = parse_bool(out.get("needs_review"))
    if out["parser_confidence"] == "":
        out["parser_confidence"] = 0.5 if out["needs_review"] else 0.8
    return out


def write_rows(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except FileNotFoundError:
        return left.absolute() == right.absolute()


def ensure_output_not_source(output_path: Path, source_paths: list[Path]) -> None:
    for source_path in source_paths:
        if same_path(output_path, source_path):
            raise SystemExit(f"Refusing to overwrite source pricing file: {source_path}")


def dataframe_from_sheet(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path, header=None, dtype=object)
    return pd.read_excel(path, header=None, dtype=object)


def looks_like_header(values: list[str]) -> bool:
    joined = " ".join(values).lower()
    return any(word in joined for word in ("product", "item", "description")) and any(word in joined for word in ("price", "cost"))


def canonical_header(value: Any) -> str:
    key = re.sub(r"[^a-z0-9]+", "_", clean_text(value).lower()).strip("_")
    mapping = {
        "item": "product_name",
        "item_name": "product_name",
        "material": "product_name",
        "product": "product_name",
        "product_name": "product_name",
        "description": "description",
        "cost": "unit_price",
        "price": "unit_price",
        "unit_price": "unit_price",
        "uom": "unit_of_measure",
        "unit": "unit_of_measure",
        "unit_of_measure": "unit_of_measure",
        "vendor": "vendor",
        "category": "category",
        "package": "package_size",
        "package_size": "package_size",
        "effective_date": "effective_date",
        "date": "effective_date",
        "vendor_item_no": "vendor_item_no",
        "sku": "vendor_item_no",
        "notes": "notes",
    }
    return mapping.get(key, key)


def extract_header_table(path: Path, df: pd.DataFrame, header_index: int, default_vendor: str, default_effective_date: str) -> list[dict[str, Any]]:
    raw_headers = [canonical_header(value) for value in df.iloc[header_index].tolist()]
    rows: list[dict[str, Any]] = []
    for row_number in range(header_index + 1, len(df)):
        raw_values = df.iloc[row_number].tolist()
        if all(blank(value) for value in raw_values):
            continue
        mapped = {raw_headers[index]: raw_values[index] for index in range(min(len(raw_headers), len(raw_values)))}
        product = clean_text(mapped.get("product_name") or mapped.get("description"))
        price = parse_float(mapped.get("unit_price"))
        if not product:
            continue
        rows.append(
            normalize_price_row(
                {
                    "source_file": path.name,
                    "source_type": path.suffix.lower().lstrip("."),
                    "vendor": mapped.get("vendor") or default_vendor,
                    "category": mapped.get("category"),
                    "product_name": product,
                    "description": mapped.get("description") if clean_text(mapped.get("description")) != product else "",
                    "unit_price": price,
                    "unit_of_measure": mapped.get("unit_of_measure"),
                    "package_size": mapped.get("package_size"),
                    "vendor_item_no": mapped.get("vendor_item_no"),
                    "effective_date": mapped.get("effective_date") or default_effective_date,
                    "notes": mapped.get("notes"),
                    "details": json.dumps({"row_number": row_number + 1}),
                    "parser_confidence": 0.9 if price is not None else 0.55,
                    "needs_review": price is None,
                }
            )
        )
    return rows


def extract_sectioned_sheet(path: Path, df: pd.DataFrame, default_vendor: str, default_effective_date: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    categories: dict[int, str] = {}
    for row_index in range(len(df)):
        values = [clean_text(value) for value in df.iloc[row_index].tolist()]
        nonblank_values = [value for value in values if value]
        if len(nonblank_values) == 1 and row_index > 0 and "effective date" not in nonblank_values[0].lower() and "vendor cost" not in nonblank_values[0].lower():
            categories[0] = nonblank_values[0]
            continue
        for col_index in range(len(values) - 1):
            value = values[col_index]
            next_value = values[col_index + 1].lower()
            if value and next_value in {"cost", "price"}:
                categories[col_index] = value
                continue
            if not value or next_value in {"cost", "price", "date"} or parse_float(value) is not None or DATE_RE.search(value):
                continue
            price = parse_float(values[col_index + 1] if col_index + 1 < len(values) else "")
            if price is None:
                continue
            date_value = values[col_index + 2] if col_index + 2 < len(values) else ""
            category = categories.get(col_index) or categories.get(0) or ""
            unit = infer_unit(value)
            rows.append(
                normalize_price_row(
                    {
                        "source_file": path.name,
                        "source_type": path.suffix.lower().lstrip("."),
                        "vendor": infer_vendor(value) or default_vendor,
                        "category": category,
                        "product_name": value,
                        "unit_price": price,
                        "unit_of_measure": unit,
                        "price_basis": "unit cost",
                        "effective_date": parse_date(date_value) or default_effective_date,
                        "details": json.dumps({"row_number": row_index + 1, "column_number": col_index + 1}),
                        "parser_confidence": 0.88,
                        "needs_review": False,
                    }
                )
            )
    return rows


def infer_vendor(text: str) -> str:
    lowered = text.lower()
    for vendor in ("Gaco", "GAF", "NCFI", "Accufoam", "Enverge", "Aldo", "Beacon", "3M", "SESCO"):
        if vendor.lower() in lowered:
            return vendor
    return ""


def infer_unit(text: str) -> str:
    lowered = text.lower()
    for word, unit in UNIT_WORDS.items():
        if re.search(rf"\b{re.escape(word)}\b", lowered):
            return unit
    return ""


def extract_sheet_file(path: Path) -> list[dict[str, Any]]:
    df = dataframe_from_sheet(path)
    text_blob = " ".join(clean_text(value) for value in df.to_numpy().flatten().tolist() if not blank(value))
    default_vendor = vendor_from_filename(path)
    default_effective_date = effective_date_from_text(text_blob)
    for index in range(min(len(df), 20)):
        values = [clean_text(value) for value in df.iloc[index].tolist()]
        if looks_like_header(values):
            rows = extract_header_table(path, df, index, default_vendor, default_effective_date)
            if rows:
                return rows
    return extract_sectioned_sheet(path, df, default_vendor, default_effective_date)


def pdf_pages(path: Path) -> list[tuple[int, str]]:
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        return [(index + 1, page.extract_text() or "") for index, page in enumerate(reader.pages)]
    except Exception:
        pass
    try:
        import pdfplumber

        with pdfplumber.open(str(path)) as pdf:
            return [(index + 1, page.extract_text() or "") for index, page in enumerate(pdf.pages)]
    except Exception:
        pass
    try:
        info = subprocess.run(["pdfinfo", str(path)], check=False, capture_output=True, text=True, timeout=10)
        pages = 1
        match = re.search(r"^Pages:\s+(\d+)", info.stdout, re.MULTILINE)
        if match:
            pages = int(match.group(1))
        out: list[tuple[int, str]] = []
        for page_number in range(1, pages + 1):
            result = subprocess.run(
                ["pdftotext", "-f", str(page_number), "-l", str(page_number), str(path), "-"],
                check=False,
                capture_output=True,
                text=True,
                timeout=20,
            )
            if result.stdout:
                out.append((page_number, result.stdout))
        if out:
            return out
    except Exception:
        pass
    try:
        text = path.read_text(encoding="utf-8")
        return [(1, text)]
    except Exception:
        return [(1, "")]


def clean_pdf_line(line: str) -> str:
    line = clean_text(line)
    line = re.sub(r"\s+", " ", line)
    return line


def parse_pdf_pricing_line(line: str) -> dict[str, Any] | None:
    text = clean_pdf_line(line)
    if len(text) < 4:
        return None
    price_matches = list(PRICE_RE.finditer(text))
    if not price_matches:
        if any(word in text.lower() for word in ("silicone", "coating", "foam", "primer", "price", "freight", "warranty")):
            return {
                "product_name": text[:120],
                "notes": "Ambiguous PDF text line without a clear price.",
                "parser_confidence": 0.35,
                "needs_review": True,
            }
        return None
    price_match = price_matches[0]
    if "$" in text:
        for candidate in price_matches:
            if "$" in text[max(0, candidate.start() - 3) : candidate.start() + 1]:
                price_match = candidate
                break
    price = parse_float(price_match.group(1))
    before = clean_text(text[: price_match.start()])
    after = clean_text(text[price_match.end() :])
    if not before or before.lower() in {"price", "cost"}:
        return None
    before = re.sub(r"\b(price|cost|list|net)\b\s*$", "", before, flags=re.I).strip(" :-")
    item_match = ITEM_NO_RE.search(text)
    unit = infer_unit(f"{before} {after}")
    package_match = re.search(r"\b(\d+(?:\.\d+)?)\s*(gal|gallon|lb|lbs|oz|case|pail|drum|roll|kit|bag)\b", text, re.I)
    per_gallon = None
    if unit == "gallon" or re.search(r"\bper\s*gal", text, re.I):
        per_gallon = price
    return {
        "product_name": before[:160],
        "description": text if len(text) > len(before) + 10 else "",
        "unit_price": price,
        "unit_of_measure": unit,
        "package_size": package_match.group(0) if package_match else "",
        "price_basis": "extracted line price",
        "price_per_gallon": per_gallon,
        "vendor_item_no": item_match.group(1) if item_match else "",
        "parser_confidence": 0.72 if unit else 0.62,
        "needs_review": not bool(unit),
    }


def line_is_price_only(line: str) -> bool:
    text = clean_pdf_line(line)
    return bool(re.fullmatch(r"\$?\s*\d[\d,]*(?:\.\d{1,4})?", text))


def line_price_value(line: str) -> float | None:
    if not line_is_price_only(line):
        return None
    return parse_float(line)


def line_is_unit(line: str) -> bool:
    text = clean_pdf_line(line).lower()
    return text in set(UNIT_WORDS) | set(UNIT_WORDS.values()) | {"each", "tote"}


def normalized_unit_from_line(line: str) -> str:
    text = clean_pdf_line(line).lower()
    if text == "each":
        return "each"
    if text == "tote":
        return "tote"
    return UNIT_WORDS.get(text, text)


def line_looks_like_heading(line: str) -> bool:
    text = clean_pdf_line(line).lower()
    return (
        not text
        or text in {"price", "uom", "unit info", "details", "effective date", "market"}
        or text.startswith("as of ")
        or text.startswith("-")
    )


def parse_pdf_table_lines(lines: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    current_category = ""
    index = 0
    while index < len(lines):
        line = clean_pdf_line(lines[index])
        lowered = line.lower()
        if "products" in lowered and not PRICE_RE.search(line):
            current_category = line
            index += 1
            continue
        if line_looks_like_heading(line) or line_is_price_only(line):
            index += 1
            continue
        if index + 1 < len(lines):
            price = line_price_value(lines[index + 1])
            unit = normalized_unit_from_line(lines[index + 2]) if index + 2 < len(lines) and line_is_unit(lines[index + 2]) else ""
            if price is not None:
                package_match = re.search(r"\b(\d+(?:\.\d+)?)\s*(gal|gallon|g|lb|lbs|oz|case|pail|drum|roll|kit|bag)\b", line, re.I)
                package_size = package_match.group(0) if package_match else ""
                gallons = None
                if package_size:
                    gallons_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:gal|gallon|g)\b", package_size, re.I)
                    gallons = parse_float(gallons_match.group(1)) if gallons_match else None
                price_per_gallon = round(price / gallons, 4) if gallons else None
                rows.append(
                    {
                        "product_name": line,
                        "unit_price": price,
                        "unit_of_measure": unit,
                        "package_size": package_size,
                        "price_basis": "PDF table price",
                        "price_per_gallon": price_per_gallon,
                        "category": infer_pdf_category(line) or current_category,
                        "parser_confidence": 0.82 if unit else 0.72,
                        "needs_review": not bool(unit),
                    }
                )
                index += 3 if unit else 2
                continue
        index += 1
    return rows


def extract_pdf_file(path: Path) -> list[dict[str, Any]]:
    pages = pdf_pages(path)
    vendor = vendor_from_filename(path)
    all_text = "\n".join(text for _page, text in pages)
    effective_date = effective_date_from_text(path.stem) or effective_date_from_text(all_text)
    rows: list[dict[str, Any]] = []
    if not all_text.strip():
        rows.append(
            normalize_price_row(
                {
                    "source_file": path.name,
                    "source_type": "pdf",
                    "vendor": vendor,
                    "product_name": path.stem,
                    "notes": "No extractable PDF text found. Install pypdf or pdfplumber, or review manually.",
                    "details": json.dumps({"page_number": None}),
                    "parser_confidence": 0.1,
                    "needs_review": True,
                }
            )
        )
        return rows
    for page_number, text in pages:
        lines = [clean_pdf_line(line) for line in text.splitlines() if clean_pdf_line(line)]
        table_rows = parse_pdf_table_lines(lines)
        consumed_products = {row["product_name"] for row in table_rows}
        for parsed in table_rows:
            parsed.update(
                {
                    "source_file": path.name,
                    "source_type": "pdf",
                    "vendor": parsed.get("vendor") or infer_vendor(parsed.get("product_name", "")) or vendor,
                    "effective_date": parsed.get("effective_date") or effective_date,
                    "details": json.dumps({"page_number": page_number, "source_line": parsed.get("product_name", "")[:500]}),
                }
            )
            rows.append(normalize_price_row(parsed))
        for line in lines:
            if line in consumed_products or line_is_price_only(line) or line_is_unit(line):
                continue
            parsed = parse_pdf_pricing_line(line)
            if not parsed:
                continue
            parsed.update(
                {
                    "source_file": path.name,
                    "source_type": "pdf",
                    "vendor": parsed.get("vendor") or infer_vendor(parsed.get("product_name", "")) or vendor,
                    "category": parsed.get("category") or infer_pdf_category(parsed.get("product_name", "")),
                    "effective_date": parsed.get("effective_date") or effective_date,
                    "details": json.dumps({"page_number": page_number, "source_line": clean_pdf_line(line)[:500]}),
                }
            )
            rows.append(normalize_price_row(parsed))
    return rows


def infer_pdf_category(text: str) -> str:
    lowered = text.lower()
    if "foam" in lowered:
        return "Foam"
    if "primer" in lowered:
        return "Primers"
    if "silicone" in lowered or "coating" in lowered or "urethane" in lowered:
        return "Coatings"
    if "granule" in lowered:
        return "Granules"
    return ""


def extract_pricing_file(path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix in {".csv", ".xlsx", ".xlsm", ".xls"}:
        return extract_sheet_file(path)
    if suffix == ".pdf":
        return extract_pdf_file(path)
    return []


def extract_pricing_dir(input_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(input_dir.iterdir()):
        if path.name.startswith(".") or not path.is_file():
            continue
        if path.suffix.lower() not in {".csv", ".xlsx", ".xlsm", ".xls", ".pdf"}:
            continue
        rows.extend(extract_pricing_file(path))
    return rows


def read_normalized_csv(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as f:
        return [normalize_price_row(row) for row in csv.DictReader(f)]


def load_master_rows(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() in {".csv", ".xlsx", ".xlsm", ".xls", ".pdf"}:
        rows = extract_pricing_file(path)
        if rows:
            return rows
    return read_normalized_csv(path)


def fuzzy_score(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def best_match(source: dict[str, Any], master_rows: list[dict[str, Any]], used_master: set[int]) -> MatchResult:
    source_key = normalize_product_key(source.get("product_name"))
    source_vendor = normalize_product_key(source.get("vendor"))
    source_category = normalize_product_key(source.get("category"))
    for index, master in enumerate(master_rows):
        if index in used_master:
            continue
        if source_key and source_key == normalize_product_key(master.get("product_name")):
            vendor_bonus = 0.03 if not source_vendor or source_vendor == normalize_product_key(master.get("vendor")) else 0
            category_bonus = 0.02 if not source_category or source_category == normalize_product_key(master.get("category")) else 0
            return MatchResult(index, min(1.0, 0.95 + vendor_bonus + category_bonus), "exact_product_name", "high")
    best = MatchResult(None, 0.0, "no_match", "none")
    for index, master in enumerate(master_rows):
        if index in used_master:
            continue
        score = fuzzy_score(source_key, normalize_product_key(master.get("product_name")))
        if source_category and source_category == normalize_product_key(master.get("category")):
            score += 0.03
        if source_vendor and source_vendor == normalize_product_key(master.get("vendor")):
            score += 0.03
        if score > best.score:
            confidence = "high" if score >= 0.88 else "medium" if score >= 0.72 else "low"
            best = MatchResult(index, min(score, 1.0), "fuzzy_product_name", confidence)
    if best.score < 0.72:
        return MatchResult(None, 0.0, "no_match", "none")
    return best


def price_changed(source_price: Any, master_price: Any) -> bool:
    source = parse_float(source_price)
    master = parse_float(master_price)
    if source is None or master is None:
        return False
    return abs(source - master) > 0.005


def action_flags_for(source: dict[str, Any], master: dict[str, Any] | None, match: MatchResult) -> list[str]:
    flags: list[str] = []
    if master is None:
        flags.append("new_item")
    elif match.strategy == "fuzzy_product_name" or match.confidence in {"low", "medium"}:
        flags.append("possible_duplicate")
    elif price_changed(source.get("unit_price"), master.get("unit_price")):
        flags.append("price_changed")
    if parse_bool(source.get("needs_review")) or parse_float(source.get("parser_confidence")) is None or float(source.get("parser_confidence") or 0) < 0.75:
        flags.append("needs_review")
    return flags


def review_row_from_source(source: dict[str, Any], master: dict[str, Any] | None, match: MatchResult, flags: list[str]) -> dict[str, Any]:
    return {
        "action_flags": ";".join(flags),
        "review_decision": "",
        "match_confidence": match.confidence,
        "match_score": round(match.score, 4),
        "match_strategy": match.strategy,
        "source_file": source.get("source_file", ""),
        "source_type": source.get("source_type", ""),
        "vendor": source.get("vendor", ""),
        "category": source.get("category", ""),
        "product_name": source.get("product_name", ""),
        "description": source.get("description", ""),
        "current_product_name": master.get("product_name", "") if master else "",
        "current_category": master.get("category", "") if master else "",
        "current_vendor": master.get("vendor", "") if master else "",
        "current_unit_price": master.get("unit_price", "") if master else "",
        "proposed_unit_price": source.get("unit_price", ""),
        "unit_of_measure": source.get("unit_of_measure", ""),
        "package_size": source.get("package_size", ""),
        "price_basis": source.get("price_basis", ""),
        "price_per_gallon": source.get("price_per_gallon", ""),
        "vendor_item_no": source.get("vendor_item_no", ""),
        "effective_date": source.get("effective_date", ""),
        "freight_terms": source.get("freight_terms", ""),
        "notes": source.get("notes", ""),
        "details": source.get("details", ""),
        "needs_review": "needs_review" in flags,
    }


def reconcile_pricing(master_rows: list[dict[str, Any]], source_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    normalized_master = [normalize_price_row(row) for row in master_rows if clean_text(row.get("product_name"))]
    normalized_source = [normalize_price_row(row) for row in source_rows if clean_text(row.get("product_name"))]
    used_master: set[int] = set()
    review_rows: list[dict[str, Any]] = []
    draft_rows = [dict(row) for row in normalized_master]
    for source in normalized_source:
        match = best_match(source, normalized_master, used_master)
        master = normalized_master[match.master_index] if match.master_index is not None else None
        if match.master_index is not None and match.confidence == "high":
            used_master.add(match.master_index)
        flags = action_flags_for(source, master, match)
        if flags:
            review_rows.append(review_row_from_source(source, master, match, flags))
        if master is None and "needs_review" not in flags:
            draft_rows.append(source)
        elif master is not None and match.master_index is not None and "price_changed" in flags and "needs_review" not in flags:
            draft_rows[match.master_index] = {**draft_rows[match.master_index], **{key: value for key, value in source.items() if value not in ("", None)}}
    source_keys = {normalize_product_key(row.get("product_name")) for row in normalized_source}
    for index, master in enumerate(normalized_master):
        key = normalize_product_key(master.get("product_name"))
        if key and key not in source_keys and index not in used_master:
            review_rows.append(review_row_from_source({}, master, MatchResult(index, 0, "not_in_source", "none"), ["missing_from_new_source"]))
    return review_rows, draft_rows


def run_extract_cli(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Extract vendor pricing files into normalized pricing rows.")
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("output/pricing/pricing_source_items.csv"))
    args = parser.parse_args(argv)
    ensure_output_not_source(args.out, [path for path in args.input_dir.iterdir() if path.is_file()])
    rows = extract_pricing_dir(args.input_dir)
    write_rows(args.out, rows, PRICING_COLUMNS)
    print(f"Pricing source rows: {len(rows)}")
    print(f"Wrote: {args.out}")


def run_reconcile_cli(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Compare extracted pricing rows to the master pricing sheet.")
    parser.add_argument("--master", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("output/pricing/pricing_master_update_review.csv"))
    parser.add_argument("--draft-out", type=Path, default=Path("output/pricing/pricing_master_updated_draft.csv"))
    args = parser.parse_args(argv)
    ensure_output_not_source(args.out, [args.master, args.source])
    ensure_output_not_source(args.draft_out, [args.master, args.source, args.out])
    master_rows = load_master_rows(args.master)
    source_rows = read_normalized_csv(args.source)
    review_rows, draft_rows = reconcile_pricing(master_rows, source_rows)
    write_rows(args.out, review_rows, REVIEW_COLUMNS)
    write_rows(args.draft_out, draft_rows, PRICING_COLUMNS)
    print(f"Master rows: {len(master_rows)}")
    print(f"Source rows: {len(source_rows)}")
    print(f"Review rows: {len(review_rows)}")
    print(f"Wrote review: {args.out}")
    print(f"Wrote draft master: {args.draft_out}")
