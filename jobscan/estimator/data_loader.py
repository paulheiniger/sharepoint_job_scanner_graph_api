from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from .schemas import DEFAULT_STAGE_FILES, PRICING_CANDIDATES, EstimatorData


def _records_from_json(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    if isinstance(value, dict):
        for key in ("rows", "records", "data", "items"):
            rows = value.get(key)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
    return []


def read_json_dataframe(path: Path) -> pd.DataFrame:
    value = json.loads(path.read_text(encoding="utf-8"))
    return pd.DataFrame(_records_from_json(value))


def read_csv_dataframe(path: Path) -> pd.DataFrame:
    return pd.read_csv(path)


def load_estimator_data(base_dir: Path | str | None = None) -> EstimatorData:
    root = Path(base_dir or Path.cwd())
    data = EstimatorData()

    for attr, relative_path in DEFAULT_STAGE_FILES.items():
        path = root / relative_path
        if not path.exists():
            data.warnings.append(f"Missing staging file: {relative_path}")
            continue
        try:
            setattr(data, attr, read_json_dataframe(path))
            data.source_files_used.append(str(relative_path))
        except Exception as exc:
            data.warnings.append(f"Could not read {relative_path}: {exc}")

    for relative_path in PRICING_CANDIDATES:
        path = root / relative_path
        if not path.exists():
            continue
        try:
            data.pricing = read_csv_dataframe(path)
            data.source_files_used.append(str(relative_path))
            break
        except Exception as exc:
            data.warnings.append(f"Could not read {relative_path}: {exc}")

    if data.pricing.empty:
        data.warnings.append("No current pricing export found.")
    return data
