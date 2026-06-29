from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine


PARSER_VERSION = "vsimple-repair-export-v1"

TEXT_COLUMNS = [
    "scope_of_work",
    "scope_of_work_1",
    "scope_of_work_short",
    "description_of_work",
    "description_of_work_performed",
    "work_performed_long_text",
    "work_performed",
    "condition_of_roofissues_found",
    "priorityspecial_areas_to_address",
    "communication_with_the_customer",
    "special_notes",
    "materials_used",
]

FINANCIAL_PREFIXES = {
    "estimate",
    "gross",
    "labor",
    "overhead",
    "pre_update",
    "profit",
    "tax",
    "tech",
    "technician",
    "total",
    "final",
    "invoice",
    "gp",
    "subcontractor",
}

KNOWN_MATERIAL_BASES = {
    "12_fabric",
    "2_chip_brush",
    "4_roller",
    "40_fabric",
    "4x38_cover",
    "9_roller",
    "9x12_cover",
    "acrylic_coating",
    "acrylic_rf",
    "brushesrollers",
    "bucket_truck",
    "butyl_tape_50",
    "can_foam",
    "caulk_aldo",
    "caulk_dow",
    "dynomic",
    "edge_metal",
    "fabric",
    "fastener",
    "fleece_tape_50",
    "froth_pack",
    "granules",
    "iso_board",
    "mileage",
    "np1",
    "sf",
    "silicone",
    "silicone_44_900",
    "silicone_coating",
    "siloxane",
    "solvent",
    "trash_bags",
}

WORK_PHRASES = {
    "anchor": ["anchor", "anchors"],
    "caulk": ["caulk", "sealant", "np1"],
    "coating": ["coating", "top coat", "silicone", "acrylic"],
    "curb": ["curb", "hvac"],
    "drain": ["drain", "scupper"],
    "fabric_reinforcement": ["fabric", "reinforced"],
    "fastener": ["fastener", "screw", "lag"],
    "flashing": ["flashing", "edge metal", "counterflashing"],
    "gutter": ["gutter", "downspout"],
    "leak": ["leak", "leaking", "water intrusion"],
    "membrane_patch": ["membrane", "patch", "puncture", "tear"],
    "ponding": ["ponding", "standing water"],
    "roof_hatch": ["roof hatch", "hatch"],
    "seam": ["seam", "seams"],
    "skylight": ["skylight"],
}


@dataclass
class RepairTables:
    repair_jobs: pd.DataFrame
    repair_material_usage: pd.DataFrame
    repair_labor_usage: pd.DataFrame
    repair_scope_text: pd.DataFrame
    repair_outcomes: pd.DataFrame

    def as_dict(self) -> dict[str, pd.DataFrame]:
        return {
            "repair_jobs": self.repair_jobs,
            "repair_material_usage": self.repair_material_usage,
            "repair_labor_usage": self.repair_labor_usage,
            "repair_scope_text": self.repair_scope_text,
            "repair_outcomes": self.repair_outcomes,
        }


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    text = str(value).strip()
    if text.lower() in {"nan", "none", "null"}:
        return ""
    return text


def to_number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    text = str(value).strip().replace(",", "").replace("$", "").replace("%", "")
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def is_positive(value: Any) -> bool:
    number = to_number(value)
    return number is not None and abs(number) > 1e-9


def clean_repair_id(value: Any, fallback: str) -> str:
    text = clean_text(value)
    if not text:
        return fallback
    number = to_number(text)
    if number is not None and float(number).is_integer():
        return str(int(number))
    return re.sub(r"\s+", "-", text)


def first_value(row: pd.Series, names: list[str]) -> Any:
    for name in names:
        if name in row.index:
            value = row.get(name)
            if clean_text(value):
                return value
    return None


def combine_text(row: pd.Series, columns: list[str] = TEXT_COLUMNS) -> str:
    pieces = [clean_text(row.get(column)) for column in columns if column in row.index]
    return "\n".join(piece for piece in pieces if piece)


def normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def humanize_column_name(name: str) -> str:
    text = re.sub(r"_+", " ", name).strip()
    return text.upper() if text in {"np1", "sf"} else text.title()


def infer_material_package(name: str, raw_text: str = "") -> str:
    text = f"{name} {raw_text}".lower()
    if any(term in text for term in ["caulk", "sealant", "np1", "aldo", "dow"]):
        return "caulk_sealant"
    if any(term in text for term in ["silicone", "acrylic", "coating", "top coat", "sf"]):
        return "coating"
    if any(term in text for term in ["fabric", "fleece", "reinforced"]):
        return "fabric_reinforcement"
    if any(term in text for term in ["fastener", "screw", "lag"]):
        return "fasteners"
    if any(term in text for term in ["iso", "board", "foam", "froth"]):
        return "insulation_board_foam"
    if any(term in text for term in ["edge metal", "flashing"]):
        return "flashing_edge_metal"
    if any(term in text for term in ["granule"]):
        return "granules"
    if any(term in text for term in ["solvent", "dynomic", "siloxane"]):
        return "solvent_cleaner"
    if any(term in text for term in ["roller", "brush", "trash bag", "cover"]):
        return "supplies_tools"
    if any(term in text for term in ["bucket truck", "mileage", "lift"]):
        return "equipment_travel"
    return "misc_material"


def infer_unit(name: str) -> str:
    text = name.lower()
    if any(term in text for term in ["silicone", "acrylic", "coating", "solvent", "siloxane"]):
        return "gal"
    if any(term in text for term in ["caulk", "np1", "aldo", "dow"]):
        return "tube"
    if "fabric" in text or "tape" in text:
        return "roll"
    if any(term in text for term in ["fastener", "screw", "anchor"]):
        return "ea"
    if "mileage" in text:
        return "mile"
    if any(term in text for term in ["roller", "brush", "cover", "trash_bag", "bag"]):
        return "ea"
    return ""


def extract_work_phrase_patterns(text: str) -> list[str]:
    lowered = text.lower()
    matches = [
        phrase
        for phrase, terms in WORK_PHRASES.items()
        if any(term in lowered for term in terms)
    ]
    return sorted(matches)


def rebuild_date(row: pd.Series, prefix: str) -> str:
    year = to_number(row.get(f"{prefix} - Year"))
    day = to_number(row.get(f"{prefix} - Day"))
    month_value = clean_text(row.get(f"{prefix} - Month"))
    if year is None or day is None or not month_value:
        return ""
    try:
        month = pd.to_datetime(month_value, format="%B").month
    except Exception:
        try:
            month = int(float(month_value))
        except Exception:
            return ""
    try:
        return pd.Timestamp(year=int(year), month=int(month), day=int(day)).date().isoformat()
    except Exception:
        return ""


def discover_material_bases(columns: list[str]) -> list[str]:
    bases: set[str] = set()
    for column in columns:
        normalized = normalize_name(column)
        for suffix in ("_est", "_cost", "_total"):
            if normalized.endswith(suffix):
                base = normalized[: -len(suffix)]
                if base in KNOWN_MATERIAL_BASES or not any(base.startswith(prefix) for prefix in FINANCIAL_PREFIXES):
                    bases.add(base)
    bases.update(base for base in KNOWN_MATERIAL_BASES if any(base == normalize_name(c) or normalize_name(c).startswith(f"{base}_") for c in columns))
    return sorted(bases)


def source_row_number(index: int) -> int:
    return index + 2


def build_repair_jobs(df: pd.DataFrame, source_file: Path) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for index, row in df.iterrows():
        repair_id = clean_repair_id(first_value(row, ["id", "repair_id", "Name"]), f"row-{source_row_number(index)}")
        rows.append(
            {
                "repair_id": repair_id,
                "customer": clean_text(first_value(row, ["companycustomer_name", "customer", "company_information"])),
                "job_name": clean_text(first_value(row, ["Name", "job_name"])),
                "status": clean_text(first_value(row, ["Status Name", "status"])),
                "type_of_repair": clean_text(first_value(row, ["type_of_repair", "repair_type"])),
                "roof_type": clean_text(first_value(row, ["roof_type", "type_of_roof"])),
                "repair_address": clean_text(row.get("repair_address")),
                "city": clean_text(row.get("city")),
                "state": clean_text(row.get("state")),
                "zip": clean_text(row.get("zip")),
                "url": clean_text(first_value(row, ["URL", "link_to_job", "sharepoint_url"])),
                "sharepoint_url": clean_text(row.get("sharepoint_url")),
                "created_date": rebuild_date(row, "Created Date"),
                "completion_date": rebuild_date(row, "date_of_completion"),
                "source_file": str(source_file),
                "source_sheet": "Export",
                "source_row_number": source_row_number(index),
                "parser_version": PARSER_VERSION,
            }
        )
    return pd.DataFrame(rows)


def material_columns_for_base(df: pd.DataFrame, base: str) -> dict[str, str | None]:
    columns_by_normalized = {normalize_name(column): column for column in df.columns}
    return {
        "raw": columns_by_normalized.get(base),
        "est": columns_by_normalized.get(f"{base}_est"),
        "cost": columns_by_normalized.get(f"{base}_cost"),
        "total": columns_by_normalized.get(f"{base}_total"),
    }


def parse_material_text(repair_id: str, text: str, source_row: int) -> list[dict[str, Any]]:
    if not text:
        return []
    rows: list[dict[str, Any]] = []
    for idx, part in enumerate(re.split(r"[\n;]+", text), start=1):
        part = clean_text(part)
        if not part:
            continue
        match = re.match(
            r"^(?P<quantity>\d+(?:\.\d+)?)\s*(?P<unit>gal|gallon|gallons|tube|tubes|roll|rolls|ea|each|lf|ft|sf|sqft|case|cases|pail|pails)?\s*(?P<name>.+)$",
            part,
            flags=re.IGNORECASE,
        )
        quantity = to_number(match.group("quantity")) if match else None
        unit = clean_text(match.group("unit")).lower() if match else ""
        material_name = clean_text(match.group("name")) if match else part
        rows.append(
            {
                "repair_material_usage_id": f"{repair_id}:materials_used:{idx}",
                "repair_id": repair_id,
                "material_package": infer_material_package(material_name, part),
                "material_name": material_name,
                "quantity": quantity,
                "unit": unit,
                "unit_cost": None,
                "total_cost": None,
                "source_column": "materials_used",
                "raw_materials_used": text,
                "source_row_number": source_row,
            }
        )
    return rows


def build_material_usage(df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    material_bases = discover_material_bases(list(df.columns))
    for index, row in df.iterrows():
        repair_id = clean_repair_id(first_value(row, ["id", "repair_id", "Name"]), f"row-{source_row_number(index)}")
        for base in material_bases:
            columns = material_columns_for_base(df, base)
            quantity = to_number(row.get(columns["est"])) if columns["est"] else None
            if quantity is None and columns["raw"]:
                raw_number = to_number(row.get(columns["raw"]))
                quantity = raw_number if raw_number is not None and raw_number > 0 else None
            unit_cost = to_number(row.get(columns["cost"])) if columns["cost"] else None
            total_cost = to_number(row.get(columns["total"])) if columns["total"] else None
            if total_cost is None and quantity is not None and unit_cost is not None:
                total_cost = quantity * unit_cost
            if not any(is_positive(value) for value in [quantity, unit_cost, total_cost]):
                continue
            material_name = humanize_column_name(base)
            rows.append(
                {
                    "repair_material_usage_id": f"{repair_id}:{base}",
                    "repair_id": repair_id,
                    "material_package": infer_material_package(base),
                    "material_name": material_name,
                    "quantity": quantity,
                    "unit": infer_unit(base),
                    "unit_cost": unit_cost,
                    "total_cost": total_cost,
                    "source_column": base,
                    "raw_materials_used": clean_text(row.get("materials_used")),
                    "source_row_number": source_row_number(index),
                }
            )
        rows.extend(parse_material_text(repair_id, clean_text(row.get("materials_used")), source_row_number(index)))
    columns = [
        "repair_material_usage_id",
        "repair_id",
        "material_package",
        "material_name",
        "quantity",
        "unit",
        "unit_cost",
        "total_cost",
        "source_column",
        "raw_materials_used",
        "source_row_number",
    ]
    return pd.DataFrame(rows, columns=columns)


def build_labor_usage(df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for index, row in df.iterrows():
        repair_id = clean_repair_id(first_value(row, ["id", "repair_id", "Name"]), f"row-{source_row_number(index)}")
        total_hours = to_number(first_value(row, ["total_labor_hours", "labor_hours"]))
        labor_cost = to_number(row.get("labor_cost"))
        if is_positive(total_hours) or is_positive(labor_cost):
            rows.append(
                {
                    "repair_labor_usage_id": f"{repair_id}:labor:aggregate",
                    "repair_id": repair_id,
                    "labor_role": "aggregate",
                    "technician_name": "",
                    "labor_hours": total_hours,
                    "labor_cost": labor_cost,
                    "total_labor_hours": total_hours,
                    "source_column": "total_labor_hours",
                    "source_row_number": source_row_number(index),
                }
            )
        for technician_index in range(1, 5):
            name = clean_text(row.get(f"technician_{technician_index}_name"))
            hours = to_number(row.get(f"technician_{technician_index}_hours"))
            cost = to_number(row.get(f"technician_{technician_index}_cost"))
            if not (name or is_positive(hours) or is_positive(cost)):
                continue
            rows.append(
                {
                    "repair_labor_usage_id": f"{repair_id}:technician:{technician_index}",
                    "repair_id": repair_id,
                    "labor_role": "technician",
                    "technician_name": name,
                    "labor_hours": hours,
                    "labor_cost": cost,
                    "total_labor_hours": total_hours,
                    "source_column": f"technician_{technician_index}_hours",
                    "source_row_number": source_row_number(index),
                }
            )
    columns = [
        "repair_labor_usage_id",
        "repair_id",
        "labor_role",
        "technician_name",
        "labor_hours",
        "labor_cost",
        "total_labor_hours",
        "source_column",
        "source_row_number",
    ]
    return pd.DataFrame(rows, columns=columns)


def build_scope_text(df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for index, row in df.iterrows():
        repair_id = clean_repair_id(first_value(row, ["id", "repair_id", "Name"]), f"row-{source_row_number(index)}")
        combined = combine_text(row)
        rows.append(
            {
                "repair_id": repair_id,
                "scope_of_work": clean_text(first_value(row, ["scope_of_work", "scope_of_work_1", "scope_of_work_short"])),
                "work_performed_long_text": clean_text(first_value(row, ["work_performed_long_text", "work_performed", "description_of_work_performed"])),
                "special_notes": clean_text(row.get("special_notes")),
                "materials_used": clean_text(row.get("materials_used")),
                "combined_scope_text": combined,
                "work_phrase_patterns": json.dumps(extract_work_phrase_patterns(combined)),
                "source_row_number": source_row_number(index),
            }
        )
    return pd.DataFrame(rows)


def build_outcomes(df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for index, row in df.iterrows():
        repair_id = clean_repair_id(first_value(row, ["id", "repair_id", "Name"]), f"row-{source_row_number(index)}")
        rows.append(
            {
                "repair_id": repair_id,
                "status": clean_text(first_value(row, ["Status Name", "status"])),
                "total_bill_amount": to_number(row.get("total_bill_amount")),
                "invoice_amount": to_number(row.get("invoice_amount")),
                "gross_profit": to_number(row.get("gross_profit")),
                "gross_profit_percentage": to_number(row.get("gross_profit_percentage")),
                "final_cost": to_number(row.get("final_cost")),
                "gross_cost": to_number(row.get("gross_cost")),
                "total_st_cost": to_number(row.get("total_st_cost")),
                "estimate_total_material_cost": to_number(row.get("estimate_total_material_cost")),
                "estimate_total_labor_cost": to_number(row.get("estimate_total_labor_cost")),
                "completion_date": rebuild_date(row, "date_of_completion"),
                "source_row_number": source_row_number(index),
            }
        )
    return pd.DataFrame(rows)


def load_vsimple_repair_export(path: Path | str, sheet_name: str | None = None) -> RepairTables:
    source_path = Path(path)
    if not source_path.exists():
        raise FileNotFoundError(f"VSimple repair export not found: {source_path}")
    sheet = sheet_name or "Export"
    df = pd.read_excel(source_path, sheet_name=sheet)
    return RepairTables(
        repair_jobs=build_repair_jobs(df, source_path),
        repair_material_usage=build_material_usage(df),
        repair_labor_usage=build_labor_usage(df),
        repair_scope_text=build_scope_text(df),
        repair_outcomes=build_outcomes(df),
    )


def sanitize_frame_for_sql(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None:
        return pd.DataFrame()
    cleaned = frame.copy()

    def clean_value(value: Any) -> Any:
        if value is None:
            return None
        try:
            if pd.isna(value):
                return None
        except Exception:
            pass
        if isinstance(value, (dict, list, tuple, set)):
            return json.dumps(value, default=str, sort_keys=True)
        return value

    for column in cleaned.columns:
        if cleaned[column].dtype == "object":
            cleaned[column] = cleaned[column].map(clean_value)
    return cleaned


def write_repair_tables(tables: RepairTables, output_dir: Path | str) -> dict[str, Path]:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for table_name, frame in tables.as_dict().items():
        path = out_dir / f"{table_name}.csv"
        frame.to_csv(path, index=False)
        paths[table_name] = path
    return paths


def write_repair_tables_to_database(
    tables: RepairTables,
    engine: Engine,
    *,
    if_exists: str = "replace",
) -> None:
    inspector = inspect(engine)
    for table_name, frame in tables.as_dict().items():
        sql_mode = if_exists
        if if_exists == "replace" and inspector.has_table(table_name):
            with engine.begin() as connection:
                connection.execute(text(f"DELETE FROM {table_name}"))
            sql_mode = "append"
        sanitize_frame_for_sql(frame).to_sql(table_name, engine, if_exists=sql_mode, index=False, chunksize=1000)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Normalize a VSimple repair export into repair estimator tables.")
    parser.add_argument("--input", type=Path, default=Path("data/data.xlsx"), help="VSimple XLSX export. Defaults to data/data.xlsx.")
    parser.add_argument("--sheet", default=None, help="Workbook sheet to read. Defaults to Export.")
    parser.add_argument("--output-dir", "--out-dir", dest="output_dir", type=Path, default=Path("output/repair_estimator"), help="Directory for normalized repair CSV tables.")
    parser.add_argument("--db-url", default=None, help="Optional database URL for writing normalized repair tables.")
    parser.add_argument("--if-exists", choices=["replace", "append", "fail"], default="replace", help="Database to_sql if_exists behavior.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    args = parse_args(argv)
    tables = load_vsimple_repair_export(args.input, sheet_name=args.sheet)
    paths = write_repair_tables(tables, args.output_dir)
    if args.db_url:
        engine = create_engine(args.db_url)
        write_repair_tables_to_database(tables, engine, if_exists=args.if_exists)
    print(f"Wrote normalized repair tables to {args.output_dir}")
    for table_name, path in paths.items():
        print(f"- {table_name}: {path} ({len(tables.as_dict()[table_name])} rows)")


if __name__ == "__main__":
    main()
