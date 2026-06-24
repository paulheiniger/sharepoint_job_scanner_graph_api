from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


DEFAULT_TRADE_TYPE = "foam_insulation"
TRADE_PROFILE_DIR = Path("configs/trades")


def available_trade_types() -> dict[str, str]:
    return {
        "foam_insulation": "Foam Insulation",
        "roofing": "Roofing",
    }


@lru_cache(maxsize=16)
def load_trade_profile(trade_type: str = DEFAULT_TRADE_TYPE) -> dict[str, Any]:
    key = (trade_type or DEFAULT_TRADE_TYPE).strip().lower()
    path = TRADE_PROFILE_DIR / f"{key}.yaml"
    if not path.exists():
        key = DEFAULT_TRADE_TYPE
        path = TRADE_PROFILE_DIR / f"{key}.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    data.setdefault("trade_type", key)
    data.setdefault("trade_name", available_trade_types().get(key, key.replace("_", " ").title()))
    data.setdefault("high_confidence_seed_keywords", [])
    data.setdefault("generic_keywords", [])
    data.setdefault("likely_measurement_page_types", [])
    data.setdefault("sheet_prefix_weights", {})
    data.setdefault("discipline_penalties", {})
    data.setdefault("output_guidance_templates", {})
    return data
