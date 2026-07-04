from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .decision_history import build_decision_recommendations, recommendation_lookup
from .formula_mirror import (
    calculate_insulation_foam,
    calculate_insulation_abaa_fee,
    calculate_insulation_bond,
    calculate_insulation_caulk_sealant,
    calculate_insulation_days_rate_cost,
    calculate_insulation_direct_cost,
    calculate_insulation_drum_disposal,
    calculate_insulation_equipment_cost,
    calculate_insulation_membrane,
    calculate_insulation_primer,
    calculate_insulation_thermal_barrier,
    calculate_insulation_thinner,
    calculate_insulation_travel_cost,
    calculate_mixed_labor,
    calculate_roofing_board_fasteners,
    calculate_roofing_board_stock,
    calculate_roofing_coating,
    calculate_roofing_days_rate_cost,
    calculate_roofing_direct_cost,
    calculate_roofing_dumpster,
    calculate_roofing_equipment_cost,
    calculate_roofing_fabric,
    calculate_roofing_granules,
    calculate_roofing_linear_feet_cost,
    calculate_roofing_detail_quantity,
    calculate_roofing_primer,
    calculate_roofing_thinner,
    calculate_roofing_travel_cost,
    calculate_roofing_units_cost,
    cell_preview_for_labor,
    cell_preview_for_material,
    decision_dict,
    positive_number,
)
from .insulation_surfaces import (
    apply_thickness_decisions,
    aggregate_surface_foam_outputs,
    build_insulation_surface_decisions,
    build_insulation_deductions,
    parse_r_value_targets,
)
from .insulation_performance import (
    build_area_calculation_explanation,
    build_area_calculation_trace,
    build_insulation_performance_specs,
)
from .materials import find_current_price
from .rules import first_nonblank, to_float
from .template_intelligence import FOAM_SELECTOR_MAP
from jobscan.products.product_matching import product_context_for_decision

DEFAULT_HOURLY_RATE = 72.0
DEFAULT_MIN_EVIDENCE_COUNT = 3
HIGH_VARIABILITY_THRESHOLD = 1.0
ADDER_MIN_RELIABLE_EVIDENCE = 5
MAX_ADDER_DEFAULT_COST = 25000.0
MAX_ADDER_DEFAULT_COST_PER_SQFT = 5.0

FILTER_RELAXATION_ORDER = [
    "penetrations_complexity",
    "access_complexity",
    "roof_condition",
    "source_year",
    "warranty_years",
    "coating_type",
    "area_bucket",
    "substrate",
    "project_type",
    "pipeline_status",
]

PROTECTED_FILTER_FIELDS = ["division", "template_type"]

MATERIAL_PACKAGES: list[dict[str, Any]] = [
    {"package": "coating", "label": "Silicone", "keywords": ["silicone", "coating"], "default_unit": "gal", "workbook_row": "26-28"},
    {"package": "primer", "label": "Primer", "keywords": ["primer"], "default_unit": "unit", "workbook_row": "39"},
    {"package": "seam_treatment", "label": "Seam Treatment", "keywords": ["seam", "sealant", "fabric"], "default_unit": "lf", "workbook_row": "47"},
    {"package": "fastener_treatment", "label": "Fastener Treatment", "keywords": ["fastener", "screw"], "default_unit": "ea", "workbook_row": "63"},
    {"package": "caulk_detail", "label": "Caulk / Detail", "keywords": ["caulk", "sealant", "detail"], "default_unit": "unit", "workbook_row": "43/45"},
    {"package": "fabric", "label": "Fabric", "keywords": ["fabric"], "default_unit": "roll", "workbook_row": "79"},
    {"package": "board_stock", "label": "Board Stock", "keywords": ["board", "cover board", "iso"], "default_unit": "board", "workbook_row": "58-60"},
    {"package": "plates", "label": "Plates", "keywords": ["plate", "plates"], "default_unit": "ea", "workbook_row": "65"},
    {"package": "edge_metal", "label": "Edge Metal", "keywords": ["edge metal", "coping", "metal"], "default_unit": "lf", "workbook_row": "82"},
    {"package": "gutter_downspouts", "label": "Gutter / Downspouts", "keywords": ["gutter", "downspout"], "default_unit": "lf", "workbook_row": "84/86"},
    {"package": "granules", "label": "Granules", "keywords": ["granules", "broadcast"], "default_unit": "bag", "workbook_row": "36"},
]

INSULATION_MATERIAL_PACKAGES: list[dict[str, Any]] = [
    {"package": "foam", "label": "Foam", "keywords": ["foam", "spray foam", "open cell", "closed cell"], "default_unit": "sqft", "workbook_row": "19-21"},
    {"package": "membrane", "label": "Membrane", "keywords": ["membrane"], "default_unit": "unit", "workbook_row": "24"},
    {"package": "primer", "label": "Primer", "keywords": ["primer"], "default_unit": "unit", "workbook_row": "26"},
    {"package": "thermal_barrier_coating", "label": "Thermal Barrier / DC315", "keywords": ["dc315", "thermal barrier", "ignition barrier", "coating"], "default_unit": "gal", "workbook_row": "30-32"},
    {"package": "thinner", "label": "Thinner", "keywords": ["thinner"], "default_unit": "unit", "workbook_row": "37"},
    {"package": "caulk_sealant", "label": "Caulk / Sealant", "keywords": ["caulk", "sealant"], "default_unit": "unit", "workbook_row": "41/43"},
    {"package": "lift", "label": "Lift", "keywords": ["lift"], "default_unit": "unit", "workbook_row": "47-48"},
    {"package": "delivery_fee", "label": "Delivery Fee", "keywords": ["delivery"], "default_unit": "each", "workbook_row": "50"},
    {"package": "generator", "label": "Generator", "keywords": ["generator"], "default_unit": "unit", "workbook_row": "53"},
    {"package": "space_heater", "label": "Space Heater", "keywords": ["space heater", "heater"], "default_unit": "unit", "workbook_row": "55"},
    {"package": "misc_materials", "label": "Materials / Misc.", "keywords": ["misc", "materials"], "default_unit": "unit", "workbook_row": "57"},
    {"package": "freight", "label": "Freight", "keywords": ["freight"], "default_unit": "unit", "workbook_row": "59"},
    {"package": "abaa_audit", "label": "ABAA Audit", "keywords": ["abaa", "audit"], "default_unit": "unit", "workbook_row": "61"},
    {"package": "drum_disposal", "label": "Drum Disposal", "keywords": ["drum", "disposal"], "default_unit": "unit", "workbook_row": "65"},
]

LABOR_PACKAGES: list[dict[str, Any]] = [
    {"package": "labor_prep", "label": "Prep", "workbook_row": "116"},
    {"package": "labor_prime", "label": "Prime", "workbook_row": "118"},
    {"package": "labor_base", "label": "Base Coat", "workbook_row": "122"},
    {"package": "labor_top_coat", "label": "Top Coat", "workbook_row": "124"},
    {"package": "labor_seam_sealer", "label": "Seam Treatment", "workbook_row": "120"},
    {"package": "labor_details", "label": "Details", "workbook_row": "128"},
    {"package": "labor_caulk", "label": "Caulk", "workbook_row": "126"},
    {"package": "labor_cleanup", "label": "Cleanup", "workbook_row": "132"},
    {"package": "labor_loading", "label": "Loading", "workbook_row": "136"},
    {"package": "labor_traveling", "label": "Travel", "workbook_row": "138"},
    {"package": "labor_meals_lodging", "label": "Meals / Hotel", "workbook_row": "144"},
    {"package": "labor_infrared_scan", "label": "Infrared", "workbook_row": "141"},
]

INSULATION_LABOR_PACKAGES: list[dict[str, Any]] = [
    {"package": "labor_set_up", "label": "Set Up", "workbook_row": "78"},
    {"package": "labor_mask", "label": "Mask", "workbook_row": "80"},
    {"package": "labor_prime", "label": "Prime", "workbook_row": "82"},
    {"package": "labor_membrane", "label": "Membrane", "workbook_row": "84"},
    {"package": "labor_foam", "label": "Foam", "workbook_row": "86"},
    {"package": "labor_dc_315", "label": "DC 315", "workbook_row": "88"},
    {"package": "labor_misc", "label": "Misc.", "workbook_row": "90"},
    {"package": "labor_clean_up", "label": "Clean Up", "workbook_row": "92"},
    {"package": "labor_loading", "label": "Loading", "workbook_row": "95"},
    {"package": "labor_traveling", "label": "Traveling", "workbook_row": "97"},
    {"package": "meals_lodging", "label": "Meals / Lodging", "workbook_row": "100"},
]

INSULATION_DECISION_SECTION_KEYS = (
    "insulation_detail_material_template_decisions",
    "insulation_thermal_barrier_template_decisions",
    "insulation_support_material_template_decisions",
    "insulation_equipment_logistics_template_decisions",
    "insulation_compliance_template_decisions",
    "insulation_labor_template_decisions",
    "insulation_pricing_template_decisions",
)

INSULATION_DETAIL_DECISION_SPECS: list[dict[str, Any]] = [
    {
        "decision_id": "insulation_membrane",
        "template_bucket": "membrane",
        "label": "Membrane",
        "workbook_row": "24",
        "formula": "membrane",
        "quantity_field": "linear_ft",
        "default_quantity": 0.0,
        "notes": "Membrane/detailing decision from Estimate row 24.",
    },
    {
        "decision_id": "insulation_primer",
        "template_bucket": "primer",
        "label": "Primer",
        "workbook_row": "26",
        "formula": "primer",
        "quantity_field": "basis_sqft",
        "default_coverage": 250.0,
        "notes": "Primer formula uses the workbook 250 sqft/unit assumption.",
    },
    {
        "decision_id": "insulation_caulk_sealant",
        "template_bucket": "caulk_sealant",
        "label": "Caulk / Sealant",
        "workbook_row": "41",
        "formula": "caulk_sealant",
        "quantity_field": "linear_ft",
        "default_feet_per_unit": 10.0,
        "selector_decision_id": "insulation_caulk_sealant",
        "notes": "Sealant units come from linear feet divided by feet per unit.",
    },
    {
        "decision_id": "insulation_caulk_sealant",
        "template_bucket": "caulk_sealant",
        "label": "Caulk / Sealant 2",
        "workbook_row": "43",
        "formula": "caulk_sealant",
        "quantity_field": "linear_ft",
        "default_feet_per_unit": 10.0,
        "selector_decision_id": "insulation_caulk_sealant",
        "notes": "Second sealant/detail row from Estimate row 43.",
    },
]

INSULATION_THERMAL_DECISION_SPECS: list[dict[str, Any]] = [
    {
        "decision_id": "insulation_thermal_barrier",
        "template_bucket": "thermal_barrier_coating",
        "label": "Thermal Barrier / DC315",
        "workbook_row": str(row),
        "selector_decision_id": "insulation_thermal_barrier",
        "formula": "thermal_barrier",
        "default_gal_per_100": 1.0,
        "default_waste_pct": 0.0,
    }
    for row in (30, 31, 32)
]

INSULATION_SUPPORT_DECISION_SPECS: list[dict[str, Any]] = [
    {
        "decision_id": "insulation_thinner",
        "template_bucket": "thinner",
        "label": "Thinner / Solvent",
        "workbook_row": "37",
        "formula": "thinner",
        "selector_decision_id": "insulation_thinner",
        "notes": "Thinner depends on thermal barrier/coating gallons.",
    },
    {
        "decision_id": "insulation_misc",
        "template_bucket": "misc_materials",
        "label": "Materials / Misc.",
        "workbook_row": "57",
        "formula": "direct",
        "notes": "Manual miscellaneous material allowance.",
    },
    {
        "decision_id": "insulation_drum_disposal",
        "template_bucket": "drum_disposal",
        "label": "Drum Disposal",
        "workbook_row": "65",
        "formula": "drum_disposal",
        "notes": "Drum disposal is calculated from primer, coating, thinner, and foam quantities.",
    },
]

INSULATION_EQUIPMENT_LOGISTICS_DECISION_SPECS: list[dict[str, Any]] = [
    {
        "decision_id": "insulation_lift_equipment",
        "template_bucket": "lift",
        "label": "Lift / Access Equipment",
        "workbook_row": "47",
        "formula": "equipment",
        "selector_decision_id": "insulation_lift_equipment",
        "default_margin_pct": 0.0,
    },
    {
        "decision_id": "insulation_lift_equipment",
        "template_bucket": "lift",
        "label": "Lift / Access Equipment 2",
        "workbook_row": "48",
        "formula": "equipment",
        "selector_decision_id": "insulation_lift_equipment",
        "default_margin_pct": 0.0,
    },
    {"decision_id": "insulation_delivery_fee", "template_bucket": "delivery_fee", "label": "Delivery Fee", "workbook_row": "50", "formula": "direct"},
    {"decision_id": "insulation_generator", "template_bucket": "generator", "label": "Generator", "workbook_row": "53", "formula": "days_rate"},
    {"decision_id": "insulation_space_heater", "template_bucket": "space_heater", "label": "Space Heater", "workbook_row": "55", "formula": "days_rate"},
    {"decision_id": "insulation_freight", "template_bucket": "freight", "label": "Freight", "workbook_row": "59", "formula": "direct"},
    {
        "decision_id": "insulation_sales_inspection_trips",
        "template_bucket": "sales_inspection_trips",
        "label": "Sales / Inspection Trips",
        "workbook_row": "68",
        "formula": "travel",
    },
    {"decision_id": "insulation_truck_expense", "template_bucket": "truck_expense", "label": "Truck Expense", "workbook_row": "70", "formula": "travel"},
]

INSULATION_COMPLIANCE_DECISION_SPECS: list[dict[str, Any]] = [
    {"decision_id": "insulation_abaa_audit", "template_bucket": "abaa_audit", "label": "ABAA Audit", "workbook_row": "61", "formula": "units_cost"},
    {"decision_id": "insulation_abaa_fee", "template_bucket": "abaa_fee", "label": "ABAA Fee", "workbook_row": "63", "formula": "abaa_fee"},
]

INSULATION_PRICING_DECISION_SPECS: list[dict[str, Any]] = [
    {"decision_id": "insulation_misc_insurance", "template_bucket": "misc_insurance", "label": "Miscellaneous Insurance", "workbook_row": "109", "formula": "direct"},
    {"decision_id": "insulation_permits", "template_bucket": "permits", "label": "Permits", "workbook_row": "111", "formula": "direct"},
    {"decision_id": "performance_payment_bonds", "template_bucket": "performance_payment_bonds", "label": "Performance / Payment Bonds", "workbook_row": "bond", "formula": "bond"},
    {"decision_id": "insulation_overhead", "template_bucket": "overhead", "label": "Overhead", "workbook_row": "118", "formula": "markup"},
    {"decision_id": "insulation_profit", "template_bucket": "profit", "label": "Profit", "workbook_row": "120", "formula": "markup"},
]

ADDER_ROWS: list[dict[str, Any]] = [
    {"adder": "travel", "label": "Travel", "workbook_row": "106/108"},
    {"adder": "lift", "label": "Lift", "workbook_row": "73/74"},
    {"adder": "generator", "label": "Generator", "workbook_row": "99"},
    {"adder": "dumpster", "label": "Dumpster", "workbook_row": "69"},
    {"adder": "hotel", "label": "Hotel", "workbook_row": "144"},
    {"adder": "inspection", "label": "Inspection", "workbook_row": "106"},
    {"adder": "infrared", "label": "Infrared", "workbook_row": "141"},
    {"adder": "mobilization", "label": "Mobilization", "workbook_row": "136/138"},
    {"adder": "freight", "label": "Freight", "workbook_row": "103"},
    {"adder": "truck_expense", "label": "Truck Expense", "workbook_row": "108"},
    {"adder": "sales_trips", "label": "Sales Trips", "workbook_row": "106"},
    {"adder": "misc", "label": "Misc.", "workbook_row": "101"},
]

PACKAGE_ALIASES: dict[str, set[str]] = {
    "coating": {"coating", "silicone", "roof coating", "acrylic coating"},
    "primer": {"primer", "prime"},
    "seam_treatment": {"seam_treatment", "seam treatment", "labor_seam_sealer", "seam sealer", "seams_misc", "misc_seams", "fabric"},
    "fastener_treatment": {"fastener_treatment", "fastener treatment", "fasteners", "screws", "plates"},
    "caulk_detail": {"caulk_detail", "caulk detail", "caulk_sealant", "caulk", "sealant", "details", "penetrations"},
    "fabric": {"fabric", "scrim"},
    "board_stock": {"board_stock", "board stock", "cover board", "iso", "insulation board", "board"},
    "plates": {"plates", "plate"},
    "edge_metal": {"edge_metal", "edge metal", "coping", "flashing"},
    "gutter_downspouts": {"gutter_downspouts", "gutter", "gutters", "downspout", "downspouts"},
    "granules": {"granules", "broadcast"},
    "foam": {"foam", "spray foam", "open cell", "open-cell", "closed cell", "closed-cell", "spf"},
    "membrane": {"membrane"},
    "thermal_barrier_coating": {"thermal_barrier_coating", "thermal barrier", "ignition barrier", "dc315", "dc 315"},
    "thinner": {"thinner"},
    "caulk_sealant": {"caulk_sealant", "caulk", "sealant"},
    "delivery_fee": {"delivery_fee", "delivery fee", "delivery"},
    "space_heater": {"space_heater", "space heater", "heater"},
    "misc_materials": {"misc_materials", "misc material", "misc materials", "misc"},
    "abaa_audit": {"abaa_audit", "abaa audit", "abaa fee", "abaa"},
    "drum_disposal": {"drum_disposal", "drum disposal", "disposal"},
    "labor_prep": {"labor_prep", "prep", "powerwash", "power wash", "set_up"},
    "labor_prime": {"labor_prime", "prime", "labor_prime"},
    "labor_base": {"labor_base", "base coat", "base"},
    "labor_top_coat": {"labor_top_coat", "top coat", "finish coat"},
    "labor_seam_sealer": {"labor_seam_sealer", "seam sealer", "seam treatment", "labor_seam"},
    "labor_details": {"labor_details", "details"},
    "labor_caulk": {"labor_caulk", "caulk", "caulk_sealant"},
    "labor_cleanup": {"labor_cleanup", "clean_up", "cleanup", "touch_cleanup", "touch up"},
    "labor_loading": {"labor_loading", "loading"},
    "labor_traveling": {"labor_traveling", "traveling", "travel labor"},
    "labor_meals_lodging": {"labor_meals_lodging", "meals_lodging", "meals lodging", "hotel", "lodging"},
    "labor_infrared_scan": {"labor_infrared_scan", "infrared_scan", "infrared", "ir scan", "thermal scan"},
    "labor_set_up": {"labor_set_up", "set_up", "setup", "set up"},
    "labor_mask": {"labor_mask", "mask", "masking"},
    "labor_membrane": {"labor_membrane", "membrane"},
    "labor_foam": {"labor_foam", "foam", "spray foam"},
    "labor_dc_315": {"labor_dc_315", "dc315", "dc 315", "thermal barrier"},
    "labor_misc": {"labor_misc", "misc"},
    "travel": {"travel", "sales_inspection_trips", "sales inspection travel", "truck_expense", "truck expense", "labor_traveling", "traveling"},
    "lift": {"lift", "lifts", "rental"},
    "generator": {"generator"},
    "dumpster": {"dumpster", "dumpsters", "disposal", "drum_disposal"},
    "hotel": {"hotel", "lodging", "meals_lodging", "meals lodging"},
    "inspection": {"inspection", "sales_inspection_trips", "sales inspection travel"},
    "infrared": {"infrared", "infrared_scan", "ir scan", "thermal scan"},
    "mobilization": {"mobilization", "loading", "labor_loading"},
    "freight": {"freight"},
    "truck_expense": {"truck_expense", "truck expense"},
    "sales_trips": {"sales_trips", "sales trips", "sales_inspection_trips", "sales inspection travel"},
    "misc": {"misc", "miscellaneous", "estimate_adder", "estimate_adder_no_markup", "misc_materials", "misc_equipment", "misc_insurance"},
}

NUMBER_WORDS: dict[str, float] = {
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
    "hundred": 100,
}

BASELINE_COATING_LABOR = {"labor_prep", "labor_base", "labor_top_coat", "labor_cleanup", "labor_loading"}

COATING_REQUIRED_POSITIVE_SIGNALS = [
    "roof coating",
    "coating",
    "high solids",
    "gaf high solids",
    "gaco",
    "ge enduris",
    "enduris",
    "unisil",
]

COATING_UNIT_SIGNALS = ["55 gal", "5 gal", "gallon", "gal", "pail", "bucket", "drum"]

COATING_FORBIDDEN_SIGNALS = [
    "sealant",
    "caulk",
    "flashing grade",
    "sausage",
    "tube",
    "cartridge",
    " oz",
    "oz ",
    "fabric",
    "fastener",
    "screw",
    "washer",
    "plate",
]

ROOFING_COATING_SELECTOR_MAP = {
    "11": "Gaco Silicone",
    "12": "Gaco Acrylic",
    "13": "Gaco Urethane",
    "21": "BASF Silicone",
    "22": "BASF Acrylic",
    "23": "BASF Urethane",
    "31": "AW Silicone",
    "32": "AW Acrylic",
    "33": "AW Urethane",
    "4": "Aluminum",
}

ROOFING_COATING_TEMPLATE_ROWS = [26, 27, 28]

ROOFING_PRIMER_SELECTOR_MAP = {
    "1": "Gaco E-5320",
    "2": "Red Zinc Oxide",
    "3": "Black Foam",
}

ROOFING_PRIMER_TEMPLATE_ROW = 39
ROOFING_PRIMER_DEFAULT_COVERAGE_SQFT_PER_UNIT = 250.0

ROOFING_FOAM_SELECTOR_MAP = {
    "11": "Gaco Roof 2.7",
    "21": "BASF Roof 2.7",
}
ROOFING_FOAM_TEMPLATE_ROWS = [19, 20, 21]
ROOFING_FOAM_DEFAULTS = {
    19: {"area_sqft": 0.0, "thickness_inches": 1.5, "yield_or_coverage": 2600.0, "unit_price": 2.25},
    20: {"area_sqft": 0.0, "thickness_inches": 1.25, "yield_or_coverage": 2900.0, "unit_price": 2.25},
    21: {"area_sqft": 0.0, "thickness_inches": 1.25, "yield_or_coverage": 2900.0, "unit_price": 2.25},
}

ROOFING_CAULK_SELECTOR_MAP = {
    "1": "Silicone Tube",
    "2": "Silicone Sausage",
    "3": "Urethane Tube",
    "4": "Urethane Sausage",
    "5": "Gaco SF-2000",
    "6": "Buttergrade",
}

ROOFING_CAULK_TEMPLATE_ROWS = [43, 45]
ROOFING_FABRIC_TEMPLATE_ROW = 79
ROOFING_DETAIL_QUANTITY_TEMPLATE_SPECS = [
    {
        "row": 47,
        "bucket": "seams_misc",
        "label": "Misc. / Seams",
        "quantity_field": "linear_ft",
        "write_cell": "C",
        "material_keys": ["seam_treatment", "fabric"],
        "signals": ["seam", "seams", "open seam", "misc seam", "misc./seams", "seam treatment"],
    },
    {
        "row": 49,
        "bucket": "penetrations",
        "label": "Penetrations",
        "quantity_field": "units",
        "write_cell": "D",
        "material_keys": ["caulk_detail", "penetrations"],
        "signals": ["penetration", "penetrations", "pipe boot", "pipe flashing", "vent", "vents"],
    },
    {
        "row": 51,
        "bucket": "hvac_units",
        "label": "HVAC Units",
        "quantity_field": "units",
        "write_cell": "D",
        "material_keys": ["caulk_detail", "hvac_units"],
        "signals": ["hvac", "rtu", "rtus", "rooftop unit", "unit curb"],
    },
    {
        "row": 53,
        "bucket": "drains",
        "label": "Drains",
        "quantity_field": "units",
        "write_cell": "D",
        "material_keys": ["caulk_detail", "drains"],
        "signals": ["drain", "drains", "roof drain"],
    },
]
ROOFING_BOARD_SELECTOR_MAP = {
    "1": "ISO Board",
    "2": "Wood Fiber",
    "3": "Dens Deck",
    "4": "Type X Gyp Board",
    "5": "Flute Filler",
}
ROOFING_BOARD_TEMPLATE_ROWS = [58, 59, 60]
ROOFING_FASTENER_TEMPLATE_ROW = 63
ROOFING_PLATES_TEMPLATE_ROW = 65
ROOFING_GRANULES_SELECTOR_MAP = {
    "1": "3M",
    "2": "SESCO",
}
ROOFING_GRANULES_TEMPLATE_ROW = 36
ROOFING_GRANULES_DEFAULT_COVERAGE_LBS_PER_100_SQFT = 50.0
ROOFING_GRANULES_DEFAULT_BAG_WEIGHT_LBS = 100.0
ROOFING_DUMPSTER_SELECTOR_MAP = {
    "1": "20 Yard",
    "2": "30 Yard",
    "3": "40 Yard",
}
ROOFING_LIFT_SELECTOR_MAP = {
    "1": "Forklift",
    "2": "Boom",
    "3": "Scissor",
    "4": "Articulating",
}
ROOFING_DUMPSTER_TEMPLATE_ROW = 69
ROOFING_LIFT_TEMPLATE_ROWS = [73, 74]
ROOFING_GENERATOR_TEMPLATE_ROW = 99
ROOFING_DELIVERY_FEE_TEMPLATE_ROW = 76
ROOFING_FREIGHT_TEMPLATE_ROW = 103
ROOFING_SALES_INSPECTION_TEMPLATE_ROW = 106
ROOFING_TRUCK_EXPENSE_TEMPLATE_ROW = 108
ROOFING_DUMPSTER_DEFAULT_UNIT_PRICE = 400.0
ROOFING_DUMPSTER_DEFAULT_MARGIN_PCT = 25.0
ROOFING_LIFT_DEFAULT_SIZE = "20'"
ROOFING_LIFT_DEFAULT_MARGIN_PCT = 20.0
ROOFING_GENERATOR_DEFAULT_DAYS = 7.0
ROOFING_GENERATOR_DEFAULT_UNIT_PRICE = 50.0
ROOFING_SALES_INSPECTION_DEFAULT_TRIPS = 9.0
ROOFING_TRUCK_EXPENSE_DEFAULT_TRIPS = 16.0
ROOFING_TRAVEL_DEFAULT_ROUND_TRIP_MILES = 20.0
ROOFING_SALES_INSPECTION_DEFAULT_RATE = 0.75
ROOFING_TRUCK_EXPENSE_DEFAULT_RATE = 1.0
ROOFING_THINNER_TEMPLATE_ROW = 33
ROOFING_MISC_TEMPLATE_ROW = 101
ROOFING_THINNER_SELECTOR_MAP = {
    "1": "Naphtha VM&P",
    "2": "Mineral Spirits",
    "3": "Xylene",
}
ROOFING_ACCESSORY_TEMPLATE_SPECS = [
    {
        "row": 82,
        "bucket": "edge_metal",
        "label": "Edge Metal",
        "formula": "linear_feet_unit_cost",
        "signals": ["edge metal", "coping", "metal edge", "perimeter metal"],
    },
    {
        "row": 84,
        "bucket": "gutter",
        "label": "Gutter",
        "formula": "linear_feet_unit_cost",
        "signals": ["gutter", "gutters"],
    },
    {
        "row": 86,
        "bucket": "downspouts",
        "label": "Downspouts",
        "formula": "linear_feet_unit_cost",
        "signals": ["downspout", "downspouts"],
    },
    {
        "row": 88,
        "bucket": "roof_hatch",
        "label": "Roof Hatch",
        "formula": "units_rate_cost",
        "signals": ["roof hatch", "hatch"],
    },
    {
        "row": 90,
        "bucket": "scuppers",
        "label": "Scuppers",
        "formula": "units_rate_cost",
        "signals": ["scupper", "scuppers"],
    },
    {
        "row": 92,
        "bucket": "curbs",
        "label": "Curbs",
        "formula": "units_rate_cost",
        "signals": ["curb", "curbs"],
    },
    {
        "row": 94,
        "bucket": "ladders",
        "label": "Ladders",
        "formula": "units_rate_cost",
        "signals": ["ladder", "ladders"],
    },
    {
        "row": 96,
        "bucket": "pitch_pockets",
        "label": "Pitch Pockets",
        "formula": "units_rate_cost",
        "signals": ["pitch pocket", "pitch pockets"],
    },
    {
        "row": ROOFING_MISC_TEMPLATE_ROW,
        "bucket": "misc",
        "label": "Misc.",
        "formula": "direct_cost",
        "signals": ["misc", "miscellaneous", "allowance"],
    },
]


def safe_number(value: Any, default: float = 0.0) -> float:
    number = to_float(value)
    if number is None or not math.isfinite(number):
        return default
    return float(number)


def _mode_text(values: list[Any]) -> str:
    counts: dict[str, int] = {}
    for value in values:
        text = str(value or "").strip()
        if text:
            counts[text] = counts.get(text, 0) + 1
    if not counts:
        return ""
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def optional_number(value: Any) -> float | None:
    number = to_float(value)
    if number is None or not math.isfinite(number):
        return None
    return float(number)


def _rec_value(recommendation: Any, key: str, default: Any = None) -> Any:
    if isinstance(recommendation, dict):
        return recommendation.get(key, default)
    return getattr(recommendation, key, default)


def _parsed_fields(recommendation: Any) -> dict[str, Any]:
    value = _rec_value(recommendation, "parsed_fields", {}) or {}
    return value if isinstance(value, dict) else {}


def _records(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, pd.DataFrame):
        return value.to_dict(orient="records")
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    return []


def _frame(data: Any, attr: str) -> pd.DataFrame:
    value = getattr(data, attr, pd.DataFrame()) if data is not None else pd.DataFrame()
    return value if isinstance(value, pd.DataFrame) else pd.DataFrame(value)


def _product_context(data: Any, *, item_name: str, decision_id: str, package: str) -> dict[str, Any]:
    catalog = _frame(data, "product_catalog")
    if catalog.empty or not item_name:
        return {}
    try:
        return product_context_for_decision(
            product_name=item_name,
            decision_id=decision_id,
            product_catalog=catalog,
            product_properties=_frame(data, "product_properties"),
            product_rules=_frame(data, "product_rules"),
            product_documents=_frame(data, "product_documents"),
            product_decision_links=_frame(data, "product_decision_links"),
            category=package,
        )
    except Exception:
        return {}


def _normalized(value: Any) -> str:
    return " ".join(str(value or "").lower().replace("_", " ").replace("-", " ").split())


def _is_insulation_scope(scope: dict[str, Any] | None) -> bool:
    scope = scope or {}
    division = _normalized(scope.get("division"))
    template_type = _normalized(scope.get("template_type"))
    estimate_mode = _normalized(scope.get("estimate_mode"))
    project_type = _normalized(scope.get("project_type"))
    if estimate_mode in {"roofing", "roof restoration", "roof coating", "restoration"}:
        return False
    if division == "roofing" or template_type == "roofing" or "roof coating" in project_type or "roof restoration" in project_type:
        return False
    if division == "insulation" or template_type == "insulation" or estimate_mode == "insulation":
        return True
    text = " ".join(
        _normalized(scope.get(key))
        for key in (
            "division",
            "template_type",
            "project_type",
            "estimate_mode",
            "building_type",
            "notes",
        )
    )
    return any(term in text for term in ("insulation", "spray foam", "foam sprayed", "dc315", "thermal barrier"))


def _history_label(scope: dict[str, Any] | None) -> str:
    return "Insulation" if _is_insulation_scope(scope) else "Roofing"


def _material_specs_for_scope(scope: dict[str, Any] | None) -> list[dict[str, Any]]:
    return INSULATION_MATERIAL_PACKAGES if _is_insulation_scope(scope) else MATERIAL_PACKAGES


def _labor_specs_for_scope(scope: dict[str, Any] | None) -> list[dict[str, Any]]:
    return INSULATION_LABOR_PACKAGES if _is_insulation_scope(scope) else LABOR_PACKAGES


def _decision_recommendation_lookup(data: Any, filters: dict[str, Any] | None) -> dict[tuple[str, str], dict[str, Any]]:
    try:
        recommendations = build_decision_recommendations(data, filters=filters, min_count=DEFAULT_MIN_EVIDENCE_COUNT)
    except Exception:
        return {}
    return recommendation_lookup(recommendations)


def _material_decision_id(package: str, scope: dict[str, Any]) -> str:
    if _is_insulation_scope(scope):
        if package == "foam":
            return "insulation_foam_system"
        if package == "thermal_barrier_coating":
            return "insulation_thermal_barrier"
        return f"insulation_{package}"
    if package == "coating":
        return "roofing_coating_system"
    return f"roofing_{package}"


def _labor_decision_id(package: str, scope: dict[str, Any]) -> str:
    return f"{'insulation' if _is_insulation_scope(scope) else 'roofing'}_{package}"


def _decision_value(decisions: dict[tuple[str, str], dict[str, Any]], decision_id: str, field_name: str, default: Any = None) -> Any:
    row = decisions.get((decision_id, field_name)) or {}
    value = row.get("recommended_value")
    if value in (None, ""):
        return default
    return value


def _decision_meta(decisions: dict[tuple[str, str], dict[str, Any]], decision_id: str, fields: list[str]) -> dict[str, Any]:
    rows = [decisions[(decision_id, field)] for field in fields if (decision_id, field) in decisions]
    if not rows:
        return {
            "decision_id": decision_id,
            "decision_evidence_count": 0,
            "decision_source_jobs_count": 0,
            "decision_confidence": "none",
            "decision_review_warning": "",
            "decision_recommendation_json": "{}",
            "decision_source_tables": "",
            "decision_filters_applied": "",
            "decision_filters_relaxed": "",
        }
    evidence = max(int(safe_number(row.get("evidence_count"), 0)) for row in rows)
    jobs = max(int(safe_number(row.get("source_jobs_count"), 0)) for row in rows)
    confidence_order = {"high": 3, "medium": 2, "low": 1, "none": 0, "": 0}
    confidence = max((str(row.get("confidence") or "") for row in rows), key=lambda item: confidence_order.get(item, 0))
    warning = "; ".join(str(row.get("review_warning") or "") for row in rows if row.get("review_warning"))
    source_tables = sorted({str(row.get("history_table") or "") for row in rows if row.get("history_table")})
    filters_applied = sorted({str(row.get("filters_applied") or "") for row in rows if row.get("filters_applied")})
    filters_relaxed = sorted({str(row.get("filters_relaxed") or "") for row in rows if row.get("filters_relaxed")})
    return {
        "decision_id": decision_id,
        "decision_evidence_count": evidence,
        "decision_source_jobs_count": jobs,
        "decision_confidence": confidence or "none",
        "decision_review_warning": warning,
        "decision_recommendation_json": json.dumps(rows, default=str, sort_keys=True),
        "decision_source_tables": ", ".join(source_tables),
        "decision_filters_applied": ", ".join(filters_applied),
        "decision_filters_relaxed": ", ".join(filters_relaxed),
    }


def _value_summary(value: Any) -> str:
    if isinstance(value, dict):
        parts = []
        for key, item in value.items():
            if item in (None, "", [], {}):
                continue
            parts.append(f"{key}={item}")
        return ", ".join(parts)
    if isinstance(value, list):
        return ", ".join(str(item) for item in value if item not in (None, ""))
    return "" if value is None else str(value)


def _material_decision_recommendation_summary(
    *,
    decision_output: dict[str, Any],
    item_name: str,
    evidence_count: int,
    confidence: str,
    package: str,
    unit: str,
) -> str:
    selected = first_nonblank(decision_output.get("selected_option"), item_name)
    details: list[str] = []
    if decision_output.get("thickness_inches"):
        details.append(f"thickness {decision_output.get('thickness_inches')} in")
    if decision_output.get("yield_or_coverage"):
        details.append(f"yield {decision_output.get('yield_or_coverage')}")
    if decision_output.get("gal_per_100_sqft"):
        details.append(f"{decision_output.get('gal_per_100_sqft')} gal/100 sqft")
    if decision_output.get("wet_mils_estimate"):
        details.append(f"{decision_output.get('wet_mils_estimate')} wet mils")
    base = f"{selected}"
    if details:
        base = f"{base}; " + "; ".join(details)
    return f"Historical {package.replace('_', ' ')} decision from {evidence_count} jobs ({confidence}). {base} [{unit}]"


def _labor_decision_recommendation_summary(decision_value: dict[str, Any], evidence_count: int, confidence: str, package: str) -> str:
    return (
        f"Historical {package.replace('_', ' ')} decision from {evidence_count} jobs ({confidence}). "
        f"{_value_summary(decision_value)}"
    )


def _product_guidance_summary(product_context: dict[str, Any]) -> str:
    if not product_context:
        return ""
    parts: list[str] = []
    if product_context.get("recommended_use"):
        parts.append(str(product_context.get("recommended_use")))
    if product_context.get("coverage"):
        parts.append(f"Coverage: {product_context.get('coverage')}")
    if product_context.get("important_limitations"):
        parts.append(f"Limitations: {product_context.get('important_limitations')}")
    warnings = product_context.get("warnings") or []
    if warnings:
        parts.append(f"Warnings: {_value_summary(warnings)}")
    return " ".join(parts)


def _package_aliases(package: str) -> set[str]:
    aliases = set(PACKAGE_ALIASES.get(package, set()))
    aliases.add(package)
    return {_normalized(alias) for alias in aliases if _normalized(alias)}


def _package_match_series(frame: pd.DataFrame, package: str) -> pd.Series:
    if frame.empty:
        return pd.Series(dtype=bool)
    aliases = _package_aliases(package)
    candidates = []
    for column in ("package", "labor_package", "template_bucket", "line_item_kind", "item_name", "selected_item_name", "row_label"):
        if column in frame.columns:
            candidates.append(frame[column].map(_normalized).isin(aliases))
    if not candidates:
        return pd.Series([False] * len(frame), index=frame.index)
    mask = candidates[0]
    for candidate in candidates[1:]:
        mask = mask | candidate
    return mask


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    number = optional_number(value)
    if number is not None:
        return number != 0
    return _normalized(value) in {"true", "yes", "y", "included", "physical quantity"}


def _numeric_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series([math.nan] * len(frame), index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce")


def _text_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series([""] * len(frame), index=frame.index, dtype=object)
    return frame[column].fillna("").astype(str)


def _item_name_from_row(row: pd.Series | dict[str, Any]) -> str:
    return str(
        first_nonblank(
            row.get("item_name") if isinstance(row, dict) else row.get("item_name"),
            row.get("line_item_name") if isinstance(row, dict) else row.get("line_item_name"),
            row.get("selected_item_name") if isinstance(row, dict) else row.get("selected_item_name"),
            row.get("normalized_item_name") if isinstance(row, dict) else row.get("normalized_item_name"),
            row.get("product_name") if isinstance(row, dict) else row.get("product_name"),
            row.get("row_label") if isinstance(row, dict) else row.get("row_label"),
            "",
        )
        or ""
    ).strip()


def _price_value_from_row(row: pd.Series | dict[str, Any], preferred: str = "unit_price") -> float:
    for column in (preferred, "matched_price", "price_per_unit", "unit_price", "price_per_gallon", "price_per_sqft"):
        value = row.get(column) if isinstance(row, dict) else row.get(column)
        number = optional_number(value)
        if number is not None and number > 0:
            return number
    return 0.0


def _unit_from_row(row: pd.Series | dict[str, Any], default_unit: str = "unit") -> str:
    return str(
        first_nonblank(
            row.get("unit") if isinstance(row, dict) else row.get("unit"),
            row.get("unit_of_measure") if isinstance(row, dict) else row.get("unit_of_measure"),
            row.get("price_basis") if isinstance(row, dict) else row.get("price_basis"),
            default_unit,
        )
        or default_unit
    )


def _positive_percentile(values: pd.Series, q: float) -> float:
    numeric = pd.to_numeric(values, errors="coerce")
    numeric = numeric[numeric.notna() & (numeric > 0)]
    if numeric.empty:
        return 0.0
    return float(numeric.quantile(q))


def _job_count(frame: pd.DataFrame) -> int:
    if frame.empty:
        return 0
    if "job_id" in frame.columns:
        return int(frame["job_id"].dropna().astype(str).nunique())
    return int(len(frame))


def _add_reason(reasons: dict[str, int], reason: str, count: int) -> None:
    if count > 0:
        reasons[reason] = reasons.get(reason, 0) + int(count)


def _format_reasons(reasons: dict[str, int]) -> str:
    if not reasons:
        return ""
    return "; ".join(f"{reason}: {count}" for reason, count in sorted(reasons.items()))


def _scope_filter_diagnostics(
    package_rows: pd.DataFrame,
    filters: dict[str, Any] | None = None,
) -> tuple[pd.DataFrame, dict[str, int]]:
    rows = package_rows.copy()
    reasons: dict[str, int] = {}
    filters = filters or {}
    division_filter = _normalized(_clean_filter_value(filters.get("division")) or "Roofing")
    template_filter = _normalized(_clean_filter_value(filters.get("template_type")) or "roofing")
    if "division" in rows.columns:
        wrong_division = ~rows["division"].map(_normalized).eq(division_filter)
        _add_reason(reasons, f"division_not_{division_filter or 'selected'}", int(wrong_division.sum()))
        rows = rows[~wrong_division].copy()
    else:
        _add_reason(reasons, "missing_division_column", len(rows))
    if "template_type" in rows.columns:
        wrong_template = ~rows["template_type"].map(_normalized).eq(template_filter)
        _add_reason(reasons, f"template_not_{template_filter or 'selected'}", int(wrong_template.sum()))
        rows = rows[~wrong_template].copy()
    else:
        _add_reason(reasons, "missing_template_type_column", len(rows))
    return rows, reasons


def _filter_field_mask(rows: pd.DataFrame, field: str, value: Any) -> pd.Series:
    cleaned = _clean_filter_value(value)
    if cleaned is None:
        return pd.Series([True] * len(rows), index=rows.index)
    if field == "area_bucket":
        expected_text = _normalized(cleaned)
        direct = rows["area_bucket"].map(_normalized).eq(expected_text) if "area_bucket" in rows.columns else pd.Series([False] * len(rows), index=rows.index)
        if "area_sqft" in rows.columns:
            by_area = _numeric_series(rows, "area_sqft").map(_area_bucket_for_sqft).map(_normalized).eq(expected_text)
            return direct | by_area
        return direct
    if field not in rows.columns:
        return pd.Series([True] * len(rows), index=rows.index)
    if field in {"warranty_years", "source_year"}:
        expected = optional_number(cleaned)
        if expected is None:
            return pd.Series([True] * len(rows), index=rows.index)
        actual = _numeric_series(rows, field)
        return actual.notna() & (actual.astype(float).round(4) == float(expected))
    expected_text = _normalized(cleaned)
    actual = rows[field].map(_normalized)
    return actual.eq(expected_text) | actual.str.contains(re.escape(expected_text), na=False) | actual.map(lambda item: item in expected_text if item else False)


def _contains_filter_mask(rows: pd.DataFrame, field: str, value: Any) -> pd.Series:
    if field not in rows.columns:
        return pd.Series([True] * len(rows), index=rows.index)
    cleaned = _clean_filter_value(value)
    if cleaned is None:
        return pd.Series([True] * len(rows), index=rows.index)
    expected_text = _normalized(cleaned)
    if not expected_text:
        return pd.Series([True] * len(rows), index=rows.index)
    actual = rows[field].map(_normalized)
    return actual.eq(expected_text) | actual.str.contains(re.escape(expected_text), na=False) | actual.map(lambda item: item in expected_text if item else False)


def _apply_one_filter(rows: pd.DataFrame, field: str, value: Any) -> pd.DataFrame:
    if rows.empty or _clean_filter_value(value) is None:
        return rows
    if field in {"project_type", "substrate", "coating_type", "roof_condition", "access_complexity", "penetrations_complexity", "pipeline_status"}:
        mask = _contains_filter_mask(rows, field, value)
    else:
        mask = _filter_field_mask(rows, field, value)
    return rows[mask].copy()


def _apply_non_relaxed_filters(rows: pd.DataFrame, filters: dict[str, Any] | None) -> tuple[pd.DataFrame, dict[str, int]]:
    filters = filters or {}
    filtered = rows.copy()
    reasons: dict[str, int] = {}
    if not bool(filters.get("include_repairs", True)):
        text_columns = [column for column in ("project_type", "package", "job_name", "scope_of_work") if column in filtered.columns]
        if text_columns:
            combined = pd.Series([""] * len(filtered), index=filtered.index)
            for column in text_columns:
                combined = combined + " " + filtered[column].fillna("").astype(str)
            repair_mask = combined.map(_normalized).str.contains("repair", na=False)
            _add_reason(reasons, "repairs_excluded_by_filter", int(repair_mask.sum()))
            filtered = filtered[~repair_mask].copy()
    if bool(filters.get("completed_only")):
        status_columns = [column for column in ("pipeline_status", "status") if column in filtered.columns]
        if status_columns:
            completed = pd.Series([False] * len(filtered), index=filtered.index)
            for column in status_columns:
                completed = completed | filtered[column].map(_normalized).str.contains("completed", na=False)
            _add_reason(reasons, "not_completed", int((~completed).sum()))
            filtered = filtered[completed].copy()
    return filtered, reasons


def _active_context_filters(filters: dict[str, Any] | None) -> dict[str, Any]:
    filters = filters or {}
    active: dict[str, Any] = {}
    for field in [*PROTECTED_FILTER_FIELDS, *FILTER_RELAXATION_ORDER]:
        value = _clean_filter_value(filters.get(field))
        if value is not None:
            active[field] = value
    return active


def _filter_rows_with_relaxation(
    rows: pd.DataFrame,
    filters: dict[str, Any] | None,
    accepted_count_fn,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    filters = filters or {}
    min_count = max(0, int(safe_number(filters.get("min_evidence_count"), DEFAULT_MIN_EVIDENCE_COUNT)))
    active = _active_context_filters(filters)
    filtered, fixed_reasons = _apply_non_relaxed_filters(rows, filters)
    relaxed: list[str] = []

    def apply_active(active_filters: dict[str, Any]) -> pd.DataFrame:
        result = filtered.copy()
        for field, value in active_filters.items():
            result = _apply_one_filter(result, field, value)
        return result

    current = apply_active(active)
    for field in FILTER_RELAXATION_ORDER:
        if accepted_count_fn(current) >= min_count:
            break
        if field not in active:
            continue
        relaxed.append(field)
        active.pop(field, None)
        current = apply_active(active)

    summary = {
        "filters_applied": {key: value for key, value in active.items()},
        "filters_requested": _active_context_filters(filters),
        "filters_relaxed": relaxed,
        "minimum_evidence_count": min_count,
        "fixed_filter_rejections": fixed_reasons,
        "filter_hash": historical_filter_hash(filters),
    }
    return current, summary


def _range_stats(p25: Any, median: Any, p75: Any) -> dict[str, Any]:
    p25_num = safe_number(p25, 0.0)
    median_num = safe_number(median, 0.0)
    p75_num = safe_number(p75, 0.0)
    width = max(p75_num - p25_num, 0.0)
    relative = width / median_num if median_num > 0 else 0.0
    return {
        "range_width": width,
        "relative_range_width": relative,
        "variability_warning": "Wide historical range. Consider tightening filters or estimator review."
        if relative >= HIGH_VARIABILITY_THRESHOLD
        else "",
    }


def _with_distribution_metadata(distribution: dict[str, Any], filter_summary: dict[str, Any] | None = None) -> dict[str, Any]:
    enriched = dict(distribution)
    enriched.update(_range_stats(enriched.get("p25"), enriched.get("median"), enriched.get("p75")))
    if filter_summary:
        enriched["filters_applied"] = ", ".join(f"{key}={value}" for key, value in filter_summary.get("filters_applied", {}).items())
        enriched["filters_relaxed"] = ", ".join(filter_summary.get("filters_relaxed", []))
        enriched["minimum_evidence_count"] = filter_summary.get("minimum_evidence_count", DEFAULT_MIN_EVIDENCE_COUNT)
        enriched["filter_hash"] = filter_summary.get("filter_hash", "")
        fixed = filter_summary.get("fixed_filter_rejections") or {}
        if fixed:
            existing = str(enriched.get("rejection_reasons") or "")
            fixed_text = _format_reasons(fixed)
            enriched["rejection_reasons"] = "; ".join(part for part in [existing, fixed_text] if part)
    return enriched


def _positive_any(frame: pd.DataFrame, columns: tuple[str, ...]) -> pd.Series:
    if frame.empty:
        return pd.Series(dtype=bool)
    mask = pd.Series([False] * len(frame), index=frame.index)
    for column in columns:
        if column in frame.columns:
            values = pd.to_numeric(frame[column], errors="coerce")
            mask = mask | (values.notna() & (values > 0))
    return mask


def _distinct_file_count(frame: pd.DataFrame) -> int:
    if frame.empty:
        return 0
    for column in ("source_file", "file_name", "estimate_file", "workbook_path", "document_name", "source_document_id"):
        if column in frame.columns:
            values = frame[column].dropna().astype(str).map(str.strip)
            count = values[values.ne("")].nunique()
            if count:
                return int(count)
    if "job_id" in frame.columns:
        return int(frame["job_id"].dropna().astype(str).nunique())
    return int(len(frame))


def _bucket_history_rows(data: Any, package: str, filters: dict[str, Any] | None) -> pd.DataFrame:
    filters = filters or {}
    for attr in ("template_rows", "job_package_summary"):
        source = _frame(data, attr)
        if source.empty:
            continue
        rows = source[_package_match_series(source, package)].copy()
        if rows.empty:
            continue
        division_filter = _normalized(_clean_filter_value(filters.get("division")) or "")
        template_filter = _normalized(_clean_filter_value(filters.get("template_type")) or "")
        if division_filter and "division" in rows.columns:
            scoped = rows[rows["division"].map(_normalized).eq(division_filter)].copy()
            if not scoped.empty:
                rows = scoped
        if template_filter and "template_type" in rows.columns:
            scoped = rows[rows["template_type"].map(_normalized).eq(template_filter)].copy()
            if not scoped.empty:
                rows = scoped
        return rows
    return pd.DataFrame()


def _bucket_history_diagnostics(data: Any, package: str, filters: dict[str, Any] | None) -> dict[str, Any]:
    rows = _bucket_history_rows(data, package, filters)
    if rows.empty:
        return {
            "total_insulation_rows_for_bucket": 0,
            "distinct_insulation_files_for_bucket": 0,
            "rows_with_quantity": 0,
            "rows_with_cost": 0,
            "rows_with_area": 0,
        }
    return {
        "total_insulation_rows_for_bucket": int(len(rows)),
        "distinct_insulation_files_for_bucket": _distinct_file_count(rows),
        "rows_with_quantity": int(_positive_any(rows, ("qty_per_sqft", "total_quantity", "quantity", "estimated_units", "calculated_quantity")).sum()),
        "rows_with_cost": int(_positive_any(rows, ("cost_per_sqft", "total_cost", "estimated_cost", "line_total", "extended_cost")).sum()),
        "rows_with_area": int(_positive_any(rows, ("area_sqft", "estimated_sqft", "surface_area_sqft", "basis_sqft")).sum()),
    }


def _insulation_foam_template_model_distribution(data: Any, package: str, filters: dict[str, Any] | None) -> dict[str, Any]:
    if package != "foam":
        return {}
    rows = _bucket_history_rows(data, package, filters)
    if rows.empty:
        return {}
    if "template_type" in rows.columns:
        scoped = rows[rows["template_type"].map(_normalized).eq("insulation")].copy()
        if not scoped.empty:
            rows = scoped
    row_number = _numeric_series(rows, "row_number")
    if row_number.notna().any():
        foam_rows = rows[row_number.isin([19, 20, 21])].copy()
        if not foam_rows.empty:
            rows = foam_rows
    area = _numeric_series(rows, "area_sqft")
    if area.isna().all():
        area = _numeric_series(rows, "quantity")
    thickness = _numeric_series(rows, "thickness_inches")
    estimated_units = _numeric_series(rows, "estimated_units")
    if estimated_units.isna().all():
        legacy_sets_or_units = _numeric_series(rows, "estimated_sets")
        legacy_median = legacy_sets_or_units[legacy_sets_or_units.notna()].median()
        if pd.notna(legacy_median) and legacy_median > 100:
            estimated_units = legacy_sets_or_units
        else:
            estimated_units = legacy_sets_or_units * 1000
    cost = _numeric_series(rows, "estimated_cost")
    yield_values = _numeric_series(rows, "yield_factor")
    if yield_values.isna().all():
        yield_values = _numeric_series(rows, "yield_or_coverage")
    unit_price = _numeric_series(rows, "unit_price")
    valid = rows[(area > 0) & (thickness > 0) & (estimated_units > 0)].copy()
    if valid.empty:
        return {}
    valid_area = area.loc[valid.index]
    valid_thickness = thickness.loc[valid.index]
    valid_units = estimated_units.loc[valid.index]
    units_rate = valid_units / (valid_area * valid_thickness)
    sets_rate = (valid_units / 1000) / (valid_area * valid_thickness)
    cost_rate = cost.loc[valid.index] / (valid_area * valid_thickness)
    cost_rate = cost_rate[cost_rate.notna() & (cost_rate > 0)]
    yield_rate = yield_values.loc[valid.index]
    yield_rate = yield_rate[yield_rate.notna() & (yield_rate > 0)]
    unit_price_rate = unit_price.loc[valid.index]
    unit_price_rate = unit_price_rate[unit_price_rate.notna() & (unit_price_rate > 0)]
    product_name = first_nonblank(
        _mode_text(valid.get("resolved_item_name", pd.Series(dtype=object)).dropna().astype(str).tolist())
        if "resolved_item_name" in valid.columns
        else "",
        _mode_text(valid.get("selected_item_name", pd.Series(dtype=object)).dropna().astype(str).tolist())
        if "selected_item_name" in valid.columns
        else "",
    )
    evidence_count = _job_count(valid)
    return {
        "foam_quantity_model": "foam_sets_from_area_thickness_yield",
        "median_units_per_sqft_per_inch": _positive_percentile(units_rate, 0.5),
        "median_sets_per_sqft_per_inch": _positive_percentile(sets_rate, 0.5),
        "p25_sets_per_sqft_per_inch": _positive_percentile(sets_rate, 0.25),
        "p75_sets_per_sqft_per_inch": _positive_percentile(sets_rate, 0.75),
        "median_cost_per_sqft_per_inch": _positive_percentile(cost_rate, 0.5),
        "median_foam_thickness_inches": _positive_percentile(valid_thickness, 0.5),
        "median_foam_yield": _positive_percentile(yield_rate, 0.5),
        "median_foam_unit_price": _positive_percentile(unit_price_rate, 0.5),
        "default_foam_product": product_name,
        "default_foam_density_lb": first_nonblank(
            _positive_percentile(_numeric_series(valid, "foam_density_lb"), 0.5),
            "",
        ),
        "foam_template_model_evidence_count": evidence_count,
        "foam_template_model_source": "estimate_template_rows_formula_model",
        "unit": "estimated_units",
    }


def _evidence_count_from_rows(rows: pd.DataFrame) -> int:
    if rows.empty:
        return 0
    for column in ("evidence_count", "job_count", "supporting_job_count", "n_jobs", "count"):
        if column in rows.columns:
            total = pd.to_numeric(rows[column], errors="coerce").fillna(0).sum()
            if total > 0:
                return int(total)
    return _job_count(rows)


def _estimate_area(scope: dict[str, Any]) -> float:
    return safe_number(
        first_nonblank(
            scope.get("net_sqft"),
            scope.get("estimated_sqft"),
            scope.get("net_insulation_area_sqft"),
            scope.get("gross_insulation_area_sqft"),
            scope.get("surface_area_sqft"),
            scope.get("net_area_sqft"),
            scope.get("C12_estimated_sqft"),
        ),
        0.0,
    )


def _foam_product_context_from_row(row: dict[str, Any] | None) -> dict[str, Any]:
    row = row or {}
    return {
        "product_name": row.get("item_name") or row.get("foam_product") or row.get("current_item") or "",
        "manufacturer": row.get("product_manufacturer") or "",
        "r_value_per_inch": first_nonblank(
            row.get("product_aged_r_value_per_inch"),
            row.get("product_r_value_per_inch"),
            row.get("product_initial_r_value_per_inch"),
        ),
        "r_value_per_inch_source": first_nonblank(
            row.get("product_aged_r_value_per_inch_source"),
            row.get("product_r_value_per_inch_source"),
            row.get("product_initial_r_value_per_inch_source"),
        ),
        "aged_r_value_per_inch": row.get("product_aged_r_value_per_inch"),
        "aged_r_value_per_inch_source": row.get("product_aged_r_value_per_inch_source"),
        "initial_r_value_per_inch": row.get("product_initial_r_value_per_inch"),
        "initial_r_value_per_inch_source": row.get("product_initial_r_value_per_inch_source"),
    }


def _foam_material_row(materials: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    for row in materials or []:
        if str(row.get("package_key") or row.get("template_bucket") or "").lower() == "foam":
            return row
    return None


def _coating_material_row(materials: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    for row in materials or []:
        if str(row.get("package_key") or row.get("template_bucket") or "").lower() == "coating":
            return row
    return None


def _primer_material_row(materials: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    for row in materials or []:
        if str(row.get("package_key") or row.get("template_bucket") or "").lower() == "primer":
            return row
    return None


def _caulk_detail_material_row(materials: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    for row in materials or []:
        key = str(row.get("package_key") or row.get("template_bucket") or "").lower()
        if key in {"caulk_detail", "caulk_sealant"}:
            return row
    return None


def _fabric_material_row(materials: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    for row in materials or []:
        if str(row.get("package_key") or row.get("template_bucket") or "").lower() == "fabric":
            return row
    return None


def _board_stock_material_row(materials: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    for row in materials or []:
        if str(row.get("package_key") or row.get("template_bucket") or "").lower() == "board_stock":
            return row
    return None


def _fastener_material_row(materials: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    for row in materials or []:
        if str(row.get("package_key") or row.get("template_bucket") or "").lower() in {"fastener_treatment", "fasteners"}:
            return row
    return None


def _plates_material_row(materials: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    for row in materials or []:
        if str(row.get("package_key") or row.get("template_bucket") or "").lower() == "plates":
            return row
    return None


def _granules_material_row(materials: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    for row in materials or []:
        if str(row.get("package_key") or row.get("template_bucket") or "").lower() == "granules":
            return row
    return None


def _foam_selector_options() -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    for code, label in sorted(FOAM_SELECTOR_MAP.items(), key=lambda item: int(item[0])):
        traits = _foam_traits(label)
        options.append(
            {
                "selector_code": str(code),
                "resolved_template_option": label,
                "foam_type": traits["foam_type"],
                "density_class": traits["density_class"],
                "application": traits["application"],
            }
        )
    return options


def _roofing_foam_selector_options(row_number: int | None = None) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    graph_path = Path("output/template_decision_graph_roofing.json")
    if graph_path.exists():
        try:
            payload = json.loads(graph_path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        for row in payload.get("selector_options") or []:
            if row.get("decision_id") != "roofing_foam":
                continue
            try:
                option_row_number = int(row.get("row_number"))
            except Exception:
                option_row_number = 0
            if row_number is not None and option_row_number != row_number:
                continue
            label = str(row.get("resolved_item_name") or "").strip()
            code = str(row.get("selector_code") or "").strip()
            if not label or not code:
                continue
            traits = _foam_traits(label)
            options.append(
                {
                    "selector_code": code,
                    "resolved_template_option": label,
                    "foam_type": traits["foam_type"],
                    "density_class": traits["density_class"],
                    "application": traits["application"] or "roofing",
                    "row_number": option_row_number,
                    "selector_cell": row.get("selector_cell") or (f"A{option_row_number}" if option_row_number else ""),
                }
            )
    if not options:
        row_numbers = [row_number] if row_number else ROOFING_FOAM_TEMPLATE_ROWS
        for option_row_number in row_numbers:
            for code, label in ROOFING_FOAM_SELECTOR_MAP.items():
                traits = _foam_traits(label)
                options.append(
                    {
                        "selector_code": code,
                        "resolved_template_option": label,
                        "foam_type": traits["foam_type"],
                        "density_class": traits["density_class"],
                        "application": "roofing",
                        "row_number": option_row_number,
                        "selector_cell": f"A{option_row_number}",
                    }
                )
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for option in options:
        key = (int(safe_number(option.get("row_number"), 0)), str(option.get("selector_code") or ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(option)
    deduped.sort(key=lambda option: (int(safe_number(option.get("row_number"), 0)), int(safe_number(option.get("selector_code"), 0))))
    return deduped


def _roofing_coating_selector_options(row_number: int | None = None) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    graph_path = Path("output/template_decision_graph_roofing.json")
    if graph_path.exists():
        try:
            payload = json.loads(graph_path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        for row in payload.get("selector_options") or []:
            if row.get("decision_id") != "roofing_coating_system":
                continue
            try:
                option_row_number = int(row.get("row_number"))
            except Exception:
                option_row_number = 0
            if row_number is not None and option_row_number != row_number:
                continue
            label = str(row.get("resolved_item_name") or "").strip()
            code = str(row.get("selector_code") or "").strip()
            if not label or not code:
                continue
            traits = _roofing_coating_traits(label)
            options.append(
                {
                    "selector_code": code,
                    "resolved_template_option": label,
                    "manufacturer": traits["manufacturer"],
                    "chemistry": traits["chemistry"],
                    "row_number": option_row_number,
                    "selector_cell": row.get("selector_cell") or (f"A{option_row_number}" if option_row_number else ""),
                }
            )
    if not options:
        for code, label in ROOFING_COATING_SELECTOR_MAP.items():
            for option_row_number in ([row_number] if row_number else ROOFING_COATING_TEMPLATE_ROWS):
                traits = _roofing_coating_traits(label)
                options.append(
                    {
                        "selector_code": str(code),
                        "resolved_template_option": label,
                        "manufacturer": traits["manufacturer"],
                        "chemistry": traits["chemistry"],
                        "row_number": option_row_number,
                        "selector_cell": f"A{option_row_number}",
                    }
                )
    deduped: dict[tuple[str, str, int], dict[str, Any]] = {}
    for option in options:
        key = (
            str(option.get("selector_code") or ""),
            _normalized(option.get("resolved_template_option")),
            int(safe_number(option.get("row_number"), 0)),
        )
        deduped.setdefault(key, option)
    return sorted(
        deduped.values(),
        key=lambda item: (int(safe_number(item.get("row_number"), 0)), int(safe_number(item.get("selector_code"), 999))),
    )


def _roofing_coating_traits(*values: Any) -> dict[str, str]:
    text = _normalized(" ".join(str(value or "") for value in values))
    manufacturer = ""
    if "gaco" in text:
        manufacturer = "Gaco"
    elif "basf" in text:
        manufacturer = "BASF"
    elif re.search(r"\baw\b", text):
        manufacturer = "AW"
    elif "aluminum" in text:
        manufacturer = "Aluminum"
    chemistry = ""
    if "silicone" in text:
        chemistry = "silicone"
    elif "acrylic" in text:
        chemistry = "acrylic"
    elif "urethane" in text:
        chemistry = "urethane"
    elif "aluminum" in text:
        chemistry = "aluminum"
    return {"manufacturer": manufacturer, "chemistry": chemistry}


def _roofing_selector_code_for_option(value: Any) -> str:
    normalized = _normalized(value)
    if not normalized:
        return ""
    for code, label in ROOFING_COATING_SELECTOR_MAP.items():
        if normalized == _normalized(label):
            return str(code)
    for option in _roofing_coating_selector_options():
        if normalized == _normalized(option.get("resolved_template_option")):
            return str(option.get("selector_code") or "")
    return ""


def _resolved_roofing_selector_option(selector_code: Any, fallback: Any = "") -> str:
    key = str(selector_code or "").strip()
    if key.endswith(".0"):
        key = key[:-2]
    if key in ROOFING_COATING_SELECTOR_MAP:
        return ROOFING_COATING_SELECTOR_MAP[key]
    for option in _roofing_coating_selector_options():
        if str(option.get("selector_code") or "").strip() == key:
            return str(option.get("resolved_template_option") or fallback or "")
    return str(fallback or "")


def _default_roofing_selector_code_for_scope(scope: dict[str, Any]) -> str:
    text = _normalized(" ".join(str(scope.get(key) or "") for key in ("coating_type", "project_type", "notes")))
    if "aluminum" in text:
        return "4"
    if "urethane" in text:
        return "13"
    if "acrylic" in text:
        return "12"
    return "11"


def _roofing_primer_selector_options() -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    graph_path = Path("output/template_decision_graph_roofing.json")
    if graph_path.exists():
        try:
            payload = json.loads(graph_path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        for row in payload.get("selector_options") or []:
            if row.get("decision_id") != "roofing_primer":
                continue
            label = str(row.get("resolved_item_name") or "").strip()
            code = str(row.get("selector_code") or "").strip()
            if not label or not code:
                continue
            options.append(
                {
                    "selector_code": code,
                    "resolved_template_option": label,
                    "row_number": int(safe_number(row.get("row_number"), ROOFING_PRIMER_TEMPLATE_ROW)),
                    "selector_cell": row.get("selector_cell") or "A39",
                }
            )
    if not options:
        for code, label in ROOFING_PRIMER_SELECTOR_MAP.items():
            options.append(
                {
                    "selector_code": str(code),
                    "resolved_template_option": label,
                    "row_number": ROOFING_PRIMER_TEMPLATE_ROW,
                    "selector_cell": "A39",
                }
            )
    deduped: dict[str, dict[str, Any]] = {}
    for option in options:
        deduped.setdefault(str(option.get("selector_code") or ""), option)
    return sorted(deduped.values(), key=lambda item: int(safe_number(item.get("selector_code"), 999)))


def _roofing_primer_selector_code_for_option(value: Any) -> str:
    normalized = _normalized(value)
    if not normalized:
        return ""
    for code, label in ROOFING_PRIMER_SELECTOR_MAP.items():
        if normalized == _normalized(label):
            return str(code)
    for option in _roofing_primer_selector_options():
        if normalized == _normalized(option.get("resolved_template_option")):
            return str(option.get("selector_code") or "")
    return ""


def _resolved_roofing_primer_option(selector_code: Any, fallback: Any = "") -> str:
    key = str(selector_code or "").strip()
    if key.endswith(".0"):
        key = key[:-2]
    if key in ROOFING_PRIMER_SELECTOR_MAP:
        return ROOFING_PRIMER_SELECTOR_MAP[key]
    for option in _roofing_primer_selector_options():
        if str(option.get("selector_code") or "").strip() == key:
            return str(option.get("resolved_template_option") or fallback or "")
    return str(fallback or "")


def _default_roofing_primer_selector_code_for_scope(scope: dict[str, Any]) -> str:
    text = _normalized(
        " ".join(
            str(scope.get(key) or "")
            for key in ("substrate", "roof_type_substrate", "roof_condition", "notes", "raw_input_notes")
        )
    )
    if "foam" in text or "spf" in text:
        return "3"
    if "rust" in text or "metal" in text or "oxid" in text:
        return "2"
    return "1"


def _roofing_caulk_selector_options(row_number: int | None = None) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    graph_path = Path("output/template_decision_graph_roofing.json")
    if graph_path.exists():
        try:
            payload = json.loads(graph_path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        for row in payload.get("selector_options") or []:
            if row.get("decision_id") != "roofing_caulk_sealant":
                continue
            if row_number and int(safe_number(row.get("row_number"), 0)) != int(row_number):
                continue
            label = str(row.get("resolved_item_name") or "").strip()
            code = str(row.get("selector_code") or "").strip()
            if not label or not code:
                continue
            options.append(
                {
                    "selector_code": code,
                    "resolved_template_option": label,
                    "row_number": int(safe_number(row.get("row_number"), row_number or 43)),
                    "selector_cell": row.get("selector_cell") or f"A{int(safe_number(row.get('row_number'), row_number or 43))}",
                }
            )
    if not options:
        for code, label in ROOFING_CAULK_SELECTOR_MAP.items():
            options.append(
                {
                    "selector_code": str(code),
                    "resolved_template_option": label,
                    "row_number": row_number or 43,
                    "selector_cell": f"A{row_number or 43}",
                }
            )
    deduped: dict[tuple[str, str], dict[str, Any]] = {}
    for option in options:
        deduped.setdefault((str(option.get("row_number") or ""), str(option.get("selector_code") or "")), option)
    return sorted(deduped.values(), key=lambda item: (int(safe_number(item.get("row_number"), 999)), int(safe_number(item.get("selector_code"), 999))))


def _roofing_caulk_selector_code_for_option(value: Any) -> str:
    normalized = _normalized(value)
    if not normalized:
        return ""
    for code, label in ROOFING_CAULK_SELECTOR_MAP.items():
        if normalized == _normalized(label):
            return str(code)
    for option in _roofing_caulk_selector_options():
        if normalized == _normalized(option.get("resolved_template_option")):
            return str(option.get("selector_code") or "")
    return ""


def _resolved_roofing_caulk_option(selector_code: Any, fallback: Any = "") -> str:
    key = str(selector_code or "").strip()
    if key.endswith(".0"):
        key = key[:-2]
    if key in ROOFING_CAULK_SELECTOR_MAP:
        return ROOFING_CAULK_SELECTOR_MAP[key]
    for option in _roofing_caulk_selector_options():
        if str(option.get("selector_code") or "").strip() == key:
            return str(option.get("resolved_template_option") or fallback or "")
    return str(fallback or "")


def _default_roofing_caulk_selector_code_for_scope(scope: dict[str, Any]) -> str:
    text = _normalized(" ".join(str(scope.get(key) or "") for key in ("coating_type", "project_type", "notes", "raw_input_notes")))
    if "urethane" in text:
        return "4"
    if "sausage" in text:
        return "2"
    if "buttergrade" in text:
        return "6"
    if "sf 2000" in text or "sf-2000" in text:
        return "5"
    return "2" if "silicone" in text else "1"


def _roofing_board_selector_options(row_number: int | None = None) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    graph_path = Path("output/template_decision_graph_roofing.json")
    if graph_path.exists():
        try:
            payload = json.loads(graph_path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        for row in payload.get("selector_options") or []:
            if row.get("decision_id") != "roofing_board_stock":
                continue
            if row_number and int(safe_number(row.get("row_number"), 0)) != int(row_number):
                continue
            label = str(row.get("resolved_item_name") or "").strip()
            code = str(row.get("selector_code") or "").strip()
            if not label or not code:
                continue
            resolved_row = int(safe_number(row.get("row_number"), row_number or 58))
            options.append(
                {
                    "selector_code": code,
                    "resolved_template_option": label,
                    "row_number": resolved_row,
                    "selector_cell": row.get("selector_cell") or f"A{resolved_row}",
                }
            )
    if not options:
        for code, label in ROOFING_BOARD_SELECTOR_MAP.items():
            options.append(
                {
                    "selector_code": str(code),
                    "resolved_template_option": label,
                    "row_number": row_number or ROOFING_BOARD_TEMPLATE_ROWS[0],
                    "selector_cell": f"A{row_number or ROOFING_BOARD_TEMPLATE_ROWS[0]}",
                }
            )
    deduped: dict[tuple[str, str], dict[str, Any]] = {}
    for option in options:
        deduped.setdefault((str(option.get("row_number") or ""), str(option.get("selector_code") or "")), option)
    return sorted(deduped.values(), key=lambda item: (int(safe_number(item.get("row_number"), 999)), int(safe_number(item.get("selector_code"), 999))))


def _roofing_board_selector_code_for_option(value: Any) -> str:
    normalized = _normalized(value)
    if not normalized:
        return ""
    for code, label in ROOFING_BOARD_SELECTOR_MAP.items():
        if normalized == _normalized(label):
            return str(code)
    for option in _roofing_board_selector_options():
        if normalized == _normalized(option.get("resolved_template_option")):
            return str(option.get("selector_code") or "")
    return ""


def _resolved_roofing_board_option(selector_code: Any, fallback: Any = "") -> str:
    key = str(selector_code or "").strip()
    if key.endswith(".0"):
        key = key[:-2]
    if key in ROOFING_BOARD_SELECTOR_MAP:
        return ROOFING_BOARD_SELECTOR_MAP[key]
    for option in _roofing_board_selector_options():
        if str(option.get("selector_code") or "").strip() == key:
            return str(option.get("resolved_template_option") or fallback or "")
    return str(fallback or "")


def _default_roofing_board_selector_code_for_scope(scope: dict[str, Any]) -> str:
    text = _normalized(
        " ".join(str(scope.get(key) or "") for key in ("notes", "raw_input_notes", "project_type", "roof_condition", "substrate", "roof_type_substrate"))
    )
    if "flute" in text:
        return "5"
    if "type x" in text or "gyp" in text or "gypsum" in text:
        return "4"
    if "dens" in text or "deck" in text:
        return "3"
    if "wood fiber" in text or "fiberboard" in text:
        return "2"
    return "1"


def _roofing_granules_selector_options(row_number: int | None = None) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    graph_path = Path("output/template_decision_graph_roofing.json")
    if graph_path.exists():
        try:
            payload = json.loads(graph_path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        for row in payload.get("selector_options") or []:
            if row.get("decision_id") != "roofing_granules":
                continue
            if row_number and int(safe_number(row.get("row_number"), 0)) != int(row_number):
                continue
            label = str(row.get("resolved_item_name") or "").strip()
            code = str(row.get("selector_code") or "").strip()
            if not label or not code:
                continue
            resolved_row = int(safe_number(row.get("row_number"), row_number or ROOFING_GRANULES_TEMPLATE_ROW))
            options.append(
                {
                    "selector_code": code,
                    "resolved_template_option": label,
                    "row_number": resolved_row,
                    "selector_cell": row.get("selector_cell") or f"A{resolved_row}",
                }
            )
    if not options:
        for code, label in ROOFING_GRANULES_SELECTOR_MAP.items():
            options.append(
                {
                    "selector_code": str(code),
                    "resolved_template_option": label,
                    "row_number": row_number or ROOFING_GRANULES_TEMPLATE_ROW,
                    "selector_cell": f"A{row_number or ROOFING_GRANULES_TEMPLATE_ROW}",
                }
            )
    deduped: dict[tuple[str, str], dict[str, Any]] = {}
    for option in options:
        deduped.setdefault((str(option.get("row_number") or ""), str(option.get("selector_code") or "")), option)
    return sorted(deduped.values(), key=lambda item: (int(safe_number(item.get("row_number"), 999)), int(safe_number(item.get("selector_code"), 999))))


def _roofing_granules_selector_code_for_option(value: Any) -> str:
    normalized = _normalized(value)
    if not normalized:
        return ""
    for code, label in ROOFING_GRANULES_SELECTOR_MAP.items():
        if normalized == _normalized(label):
            return str(code)
    for option in _roofing_granules_selector_options():
        if normalized == _normalized(option.get("resolved_template_option")):
            return str(option.get("selector_code") or "")
    return ""


def _resolved_roofing_granules_option(selector_code: Any, fallback: Any = "") -> str:
    key = str(selector_code or "").strip()
    if key.endswith(".0"):
        key = key[:-2]
    if key in ROOFING_GRANULES_SELECTOR_MAP:
        return ROOFING_GRANULES_SELECTOR_MAP[key]
    for option in _roofing_granules_selector_options():
        if str(option.get("selector_code") or "").strip() == key:
            return str(option.get("resolved_template_option") or fallback or "")
    return str(fallback or "")


def _default_roofing_granules_selector_code_for_scope(scope: dict[str, Any]) -> str:
    text = _normalized(" ".join(str(scope.get(key) or "") for key in ("notes", "raw_input_notes", "project_type", "coating_type")))
    if "sesco" in text or "snow white" in text:
        return "2"
    if "3m" in text or "mineral shield" in text or "lr9300" in text:
        return "1"
    return "1"


def _selector_options_from_roofing_graph(
    decision_id: str,
    fallback_map: dict[str, str],
    *,
    row_number: int | None = None,
) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    graph_path = Path("output/template_decision_graph_roofing.json")
    if graph_path.exists():
        try:
            payload = json.loads(graph_path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        for row in payload.get("selector_options") or []:
            if row.get("decision_id") != decision_id:
                continue
            resolved_row = int(safe_number(row.get("row_number"), row_number or 0))
            if row_number and resolved_row != int(row_number):
                continue
            label = str(row.get("resolved_item_name") or "").strip()
            code = str(row.get("selector_code") or "").strip()
            if not label or not code:
                continue
            options.append(
                {
                    "selector_code": code,
                    "resolved_template_option": label,
                    "row_number": resolved_row or row_number,
                    "selector_cell": row.get("selector_cell") or f"A{resolved_row or row_number or ''}",
                }
            )
    if not options:
        for code, label in fallback_map.items():
            options.append(
                {
                    "selector_code": str(code),
                    "resolved_template_option": label,
                    "row_number": row_number,
                    "selector_cell": f"A{row_number or ''}",
                }
            )
    deduped: dict[tuple[str, str], dict[str, Any]] = {}
    for option in options:
        deduped.setdefault((str(option.get("row_number") or ""), str(option.get("selector_code") or "")), option)
    return sorted(
        deduped.values(),
        key=lambda item: (int(safe_number(item.get("row_number"), row_number or 999)), int(safe_number(item.get("selector_code"), 999))),
    )


def _selector_code_for_roofing_option(value: Any, fallback_map: dict[str, str], options: list[dict[str, Any]]) -> str:
    normalized = _normalized(value)
    if not normalized:
        return ""
    for code, label in fallback_map.items():
        if normalized == _normalized(label):
            return str(code)
    for option in options:
        if normalized == _normalized(option.get("resolved_template_option")):
            return str(option.get("selector_code") or "")
    return ""


def _resolved_roofing_equipment_option(selector_code: Any, fallback_map: dict[str, str], options: list[dict[str, Any]], fallback: Any = "") -> str:
    key = str(selector_code or "").strip()
    if key.endswith(".0"):
        key = key[:-2]
    if key in fallback_map:
        return fallback_map[key]
    for option in options:
        if str(option.get("selector_code") or "").strip() == key:
            return str(option.get("resolved_template_option") or fallback or "")
    return str(fallback or "")


def _default_roofing_dumpster_selector_code_for_scope(scope: dict[str, Any]) -> str:
    text = _normalized(" ".join(str(scope.get(key) or "") for key in ("notes", "raw_input_notes", "scope_of_work", "project_type")))
    if "20 yard" in text or "20yd" in text:
        return "1"
    if "30 yard" in text or "30yd" in text:
        return "2"
    if "40 yard" in text or "40yd" in text:
        return "3"
    area = _estimate_area(scope)
    if area >= 15000:
        return "3"
    if area >= 5000:
        return "2"
    return "1"


def _default_roofing_lift_selector_code_for_scope(scope: dict[str, Any]) -> str:
    text = _normalized(" ".join(str(scope.get(key) or "") for key in ("notes", "raw_input_notes", "scope_of_work", "access_complexity")))
    if "articulating" in text:
        return "4"
    if "scissor" in text:
        return "3"
    if "boom" in text:
        return "2"
    if "forklift" in text:
        return "1"
    if "difficult" in text or "high access" in text or "lift" in text:
        return "2"
    return "1"


def _selector_code_for_option(value: Any) -> str:
    normalized = _normalized(value)
    if not normalized:
        return ""
    for code, label in FOAM_SELECTOR_MAP.items():
        if normalized == _normalized(label):
            return str(code)
    return ""


def _resolved_selector_option(selector_code: Any, fallback: Any = "") -> str:
    key = str(selector_code or "").strip()
    if key and key.endswith(".0"):
        key = key[:-2]
    return str(FOAM_SELECTOR_MAP.get(key) or fallback or "")


def _roofing_foam_selector_code_for_option(value: Any) -> str:
    normalized = _normalized(value)
    if not normalized:
        return ""
    for code, label in ROOFING_FOAM_SELECTOR_MAP.items():
        if normalized == _normalized(label):
            return str(code)
    return ""


def _resolved_roofing_foam_selector_option(selector_code: Any, fallback: Any = "") -> str:
    key = str(selector_code or "").strip()
    if key and key.endswith(".0"):
        key = key[:-2]
    return str(ROOFING_FOAM_SELECTOR_MAP.get(key) or fallback or "")


def _foam_traits(*values: Any) -> dict[str, str]:
    text = _normalized(" ".join(str(value or "") for value in values))
    density = ""
    match = re.search(r"\b(?P<density>\d+(?:\.\d+)?)\s*lb\b", text)
    if match:
        density_value = safe_number(match.group("density"), 0.0)
        density = f"{density_value:g} lb" if density_value else ""
    elif re.search(r"\b2\.7\b", text):
        density = "2.7 lb"
    elif re.search(r"\b2(?:\.0)?\b", text) and "lb" in text:
        density = "2 lb"
    elif re.search(r"\b0\.5\b", text) and "lb" in text:
        density = "0.5 lb"

    foam_type = "unknown"
    if "open cell" in text or "open-cell" in text or density.startswith("0.5"):
        foam_type = "open_cell"
    elif "closed cell" in text or "closed-cell" in text or density.startswith("2") or density.startswith("3"):
        foam_type = "closed_cell"

    application = "unknown"
    if any(term in text for term in ("roof foam", "roofing foam", "roof repair", "repair foam", "roof kit", "roof deck", "roofing")):
        application = "roofing"
    elif any(term in text for term in ("wall", "ceiling", "insulation", "spray foam", "closed cell", "open cell")):
        application = "wall_ceiling_insulation"
    return {"foam_type": foam_type, "density_class": density, "application": application}


def _foam_candidate_compatibility(
    *,
    template_option: str,
    candidate: dict[str, Any],
    scope: dict[str, Any],
    product_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    product_context = product_context or {}
    template_traits = _foam_traits(template_option)
    candidate_traits = _foam_traits(
        candidate.get("item_name"),
        candidate.get("unit"),
        candidate.get("category"),
        product_context.get("category"),
        product_context.get("recommended_use"),
        product_context.get("product_family"),
    )
    warnings: list[str] = []
    if template_traits["foam_type"] != "unknown" and candidate_traits["foam_type"] != "unknown" and template_traits["foam_type"] != candidate_traits["foam_type"]:
        warnings.append(
            "Foam type mismatch: template option is "
            f"{template_traits['foam_type'].replace('_', '-')} but pricing candidate appears "
            f"{candidate_traits['foam_type'].replace('_', '-')}."
        )
    if template_traits["density_class"] and candidate_traits["density_class"] and template_traits["density_class"] != candidate_traits["density_class"]:
        warnings.append(
            f"Density mismatch: template option is {template_traits['density_class']} but pricing candidate appears {candidate_traits['density_class']}."
        )
    scope_text = _normalized(" ".join(str(scope.get(key) or "") for key in ("project_type", "building_type", "substrate", "notes", "raw_input_notes")))
    if candidate_traits["application"] == "roofing" and any(term in scope_text for term in ("wall", "ceiling", "metal building", "insulation")):
        warnings.append("Pricing candidate appears to be roofing foam; confirm fit for wall/ceiling insulation.")
    if not product_context.get("product_id"):
        warnings.append("No product data sheet match is available for this pricing candidate.")
    status = "compatible" if not warnings else "review"
    if any("mismatch" in warning.lower() or "roofing foam" in warning.lower() for warning in warnings):
        status = "spec_mismatch"
    return {
        "compatibility_status": status,
        "compatibility_warnings": warnings,
        "template_traits": template_traits,
        "candidate_traits": candidate_traits,
    }


def _candidate_guidance_summary(product_context: dict[str, Any]) -> str:
    if not product_context:
        return ""
    parts = []
    if product_context.get("recommended_use"):
        parts.append(str(product_context.get("recommended_use")))
    if product_context.get("r_value_per_inch"):
        parts.append(f"R/in {product_context.get('r_value_per_inch')}")
    if product_context.get("coverage"):
        parts.append(f"Coverage {product_context.get('coverage')}")
    if product_context.get("important_limitations"):
        parts.append(f"Limitations: {product_context.get('important_limitations')}")
    if product_context.get("warnings"):
        parts.append("Warnings available.")
    return " ".join(parts)


def _material_item_options(row: dict[str, Any]) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    try:
        payload = json.loads(row.get("item_options_json") or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = []
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict) and item.get("item_name"):
                options.append(dict(item))
    if row.get("item_name") and not any(_normalized(option.get("item_name")) == _normalized(row.get("item_name")) for option in options):
        options.append(
            {
                "item_name": row.get("item_name"),
                "unit": row.get("unit"),
                "unit_price": row.get("current_unit_price"),
                "pricing_item_id": row.get("pricing_item_id"),
                "source": row.get("item_source") or "selected_item",
                "selected_item_reason": row.get("selected_item_reason"),
            }
        )
    return options


def _foam_pricing_candidates(row: dict[str, Any], scope: dict[str, Any], data: Any = None, template_option: str = "") -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for option in _material_item_options(row):
        item_name = str(option.get("item_name") or "").strip()
        if not item_name:
            continue
        context = _product_context(data, item_name=item_name, decision_id="insulation_foam_system", package="foam") if data is not None else {}
        compatibility = _foam_candidate_compatibility(template_option=template_option, candidate=option, scope=scope, product_context=context)
        candidates.append(
            {
                "item_name": item_name,
                "pricing_item_id": option.get("pricing_item_id"),
                "unit": option.get("unit"),
                "unit_price": safe_number(option.get("unit_price"), 0.0),
                "source": option.get("source") or option.get("item_source") or "pricing_or_history",
                "why_suggested": option.get("selected_item_reason") or option.get("source") or "",
                "product_id": context.get("product_id") or "",
                "product_name": context.get("product_name") or "",
                "manufacturer": context.get("manufacturer") or "",
                "product_guidance": _candidate_guidance_summary(context),
                "product_source_documents": context.get("source_documents") or [],
                "product_match_score": context.get("match_score") or 0.0,
                **compatibility,
            }
        )
    candidates.sort(
        key=lambda candidate: (
            _foam_candidate_rank(candidate),
            not _is_roofing_foam_candidate(candidate),
            safe_number(candidate.get("unit_price"), 0.0) > 0,
            safe_number(candidate.get("product_match_score"), 0.0),
            candidate.get("item_name") or "",
        ),
        reverse=True,
    )
    return candidates[:8]


def _roofing_foam_candidate_compatibility(
    *,
    template_option: str,
    candidate: dict[str, Any],
    product_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    product_context = product_context or {}
    base = _foam_candidate_compatibility(
        template_option=template_option,
        candidate=candidate,
        scope={"division": "Roofing", "template_type": "roofing", "project_type": "roof foam"},
        product_context=product_context,
    )
    warnings = list(base.get("compatibility_warnings") or [])
    candidate_traits = base.get("candidate_traits") or {}
    if candidate_traits.get("application") == "wall_ceiling_insulation":
        warnings.append("Pricing candidate appears to be wall/ceiling insulation foam; confirm roofing SPF application.")
    if candidate_traits.get("foam_type") == "open_cell":
        warnings.append("Open-cell foam is not a normal roofing SPF selection; estimator review required.")
    if not _is_roofing_foam_candidate({**candidate, **base}):
        warnings.append("Pricing candidate does not clearly look like roofing SPF foam; estimator should verify product application.")
    status = "compatible" if not warnings else "review"
    if any("open-cell" in warning.lower() or "wall/ceiling" in warning.lower() or "does not clearly look" in warning.lower() for warning in warnings):
        status = "spec_mismatch"
    return {
        **base,
        "compatibility_status": status,
        "compatibility_warnings": list(dict.fromkeys(warnings)),
    }


def _roofing_foam_pricing_candidates(
    row: dict[str, Any],
    scope: dict[str, Any],
    data: Any = None,
    template_option: str = "",
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for option in _material_item_options(row):
        item_name = str(option.get("item_name") or "").strip()
        if not item_name:
            continue
        context = _product_context(data, item_name=item_name, decision_id="roofing_foam", package="foam") if data is not None else {}
        compatibility = _roofing_foam_candidate_compatibility(
            template_option=template_option,
            candidate=option,
            product_context=context,
        )
        candidates.append(
            {
                "item_name": item_name,
                "pricing_item_id": option.get("pricing_item_id"),
                "unit": option.get("unit"),
                "unit_price": safe_number(option.get("unit_price"), 0.0),
                "source": option.get("source") or option.get("item_source") or "pricing_or_history",
                "why_suggested": option.get("selected_item_reason") or option.get("source") or "",
                "product_id": context.get("product_id") or "",
                "product_name": context.get("product_name") or "",
                "manufacturer": context.get("manufacturer") or "",
                "product_guidance": _candidate_guidance_summary(context),
                "product_source_documents": context.get("source_documents") or [],
                "product_match_score": context.get("match_score") or 0.0,
                **compatibility,
            }
        )
    candidates.sort(
        key=lambda candidate: (
            1 if _is_roofing_foam_candidate(candidate) else 0,
            _foam_candidate_rank(candidate),
            safe_number(candidate.get("product_match_score"), 0.0),
            safe_number(candidate.get("unit_price"), 0.0) > 0,
            candidate.get("item_name") or "",
        ),
        reverse=True,
    )
    return candidates[:8]


def _selected_roofing_foam_candidate(candidates: list[dict[str, Any]], selected_name: Any) -> dict[str, Any]:
    normalized = _normalized(selected_name)
    if normalized:
        for candidate in candidates:
            if _normalized(candidate.get("item_name")) == normalized:
                return candidate
    for candidate in candidates:
        if _is_roofing_foam_candidate(candidate) and str(candidate.get("compatibility_status") or "").lower() != "spec_mismatch":
            return candidate
    for candidate in candidates:
        if _is_roofing_foam_candidate(candidate):
            return candidate
    return candidates[0] if candidates else {}


def _roofing_coating_candidate_compatibility(
    *,
    template_option: str,
    candidate: dict[str, Any],
    product_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    product_context = product_context or {}
    template_traits = _roofing_coating_traits(template_option)
    candidate_traits = _roofing_coating_traits(
        candidate.get("item_name"),
        candidate.get("unit"),
        candidate.get("category"),
        product_context.get("category"),
        product_context.get("recommended_use"),
        product_context.get("product_family"),
    )
    warnings: list[str] = []
    if not _is_valid_coating_option(candidate):
        warnings.append("Pricing candidate does not look like a main roof coating product; estimator should select a coating product.")
    if (
        template_traits["chemistry"]
        and candidate_traits["chemistry"]
        and template_traits["chemistry"] != candidate_traits["chemistry"]
    ):
        warnings.append(
            f"Chemistry mismatch: template option is {template_traits['chemistry']} but pricing candidate appears {candidate_traits['chemistry']}."
        )
    if not product_context.get("product_id"):
        warnings.append("No product data sheet match is available for this pricing candidate.")
    status = "compatible" if not warnings else "review"
    if any("does not look like" in warning.lower() or "chemistry mismatch" in warning.lower() for warning in warnings):
        status = "spec_mismatch"
    return {
        "compatibility_status": status,
        "compatibility_warnings": warnings,
        "template_traits": template_traits,
        "candidate_traits": candidate_traits,
    }


def _roofing_coating_pricing_candidates(
    row: dict[str, Any],
    scope: dict[str, Any],
    data: Any = None,
    template_option: str = "",
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for option in _material_item_options(row):
        item_name = str(option.get("item_name") or "").strip()
        if not item_name:
            continue
        context = _product_context(data, item_name=item_name, decision_id="roofing_coating_system", package="coating") if data is not None else {}
        compatibility = _roofing_coating_candidate_compatibility(
            template_option=template_option,
            candidate=option,
            product_context=context,
        )
        score, reasons = _package_item_fit_details("coating", option, scope)
        candidates.append(
            {
                "item_name": item_name,
                "pricing_item_id": option.get("pricing_item_id"),
                "unit": option.get("unit"),
                "unit_price": safe_number(option.get("unit_price"), 0.0),
                "source": option.get("source") or option.get("item_source") or "pricing_or_history",
                "why_suggested": option.get("selected_item_reason") or option.get("source") or "; ".join(reasons),
                "product_id": context.get("product_id") or "",
                "product_name": context.get("product_name") or "",
                "manufacturer": context.get("manufacturer") or "",
                "product_guidance": _candidate_guidance_summary(context),
                "product_source_documents": context.get("source_documents") or [],
                "product_match_score": context.get("match_score") or 0.0,
                "fit_score": round(score, 4),
                "fit_reasons": reasons,
                **compatibility,
            }
        )
    candidates.sort(
        key=lambda candidate: (
            1 if candidate.get("compatibility_status") == "compatible" else 0,
            1 if _is_valid_coating_option(candidate) else 0,
            safe_number(candidate.get("fit_score"), 0.0),
            safe_number(candidate.get("product_match_score"), 0.0),
            safe_number(candidate.get("unit_price"), 0.0) > 0,
            candidate.get("item_name") or "",
        ),
        reverse=True,
    )
    return candidates[:8]


def _roofing_primer_candidate_compatibility(
    *,
    template_option: str,
    candidate: dict[str, Any],
    product_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    product_context = product_context or {}
    warnings: list[str] = []
    text = _normalized(
        " ".join(
            str(value or "")
            for value in (
                candidate.get("item_name"),
                candidate.get("unit"),
                candidate.get("category"),
                product_context.get("category"),
                product_context.get("recommended_use"),
                product_context.get("product_family"),
            )
        )
    )
    template_text = _normalized(template_option)
    if not _contains_any_text(text, ["primer", "prime", "rust inhibitive", "epoxy", "zinc oxide", "foam primer"]):
        warnings.append("Pricing candidate does not look like a primer product; estimator should verify the selected item.")
    if "foam" in template_text and "foam" not in text:
        warnings.append("Template option is foam primer but pricing candidate does not clearly reference foam primer.")
    if "zinc" in template_text and not _contains_any_text(text, ["zinc", "metal", "rust", "oxide"]):
        warnings.append("Template option is zinc oxide primer but pricing candidate does not clearly reference metal/rust primer.")
    if not product_context.get("product_id"):
        warnings.append("No product data sheet match is available for this primer candidate.")
    status = "compatible" if not warnings else "review"
    if any("does not look" in warning.lower() for warning in warnings):
        status = "spec_mismatch"
    return {
        "compatibility_status": status,
        "compatibility_warnings": warnings,
    }


def _roofing_primer_pricing_candidates(
    row: dict[str, Any],
    scope: dict[str, Any],
    data: Any = None,
    template_option: str = "",
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for option in _material_item_options(row):
        item_name = str(option.get("item_name") or "").strip()
        if not item_name:
            continue
        context = _product_context(data, item_name=item_name, decision_id="roofing_primer", package="primer") if data is not None else {}
        score, reasons = _package_item_fit_details("primer", option, scope)
        compatibility = _roofing_primer_candidate_compatibility(
            template_option=template_option,
            candidate=option,
            product_context=context,
        )
        candidates.append(
            {
                "item_name": item_name,
                "pricing_item_id": option.get("pricing_item_id"),
                "unit": option.get("unit"),
                "unit_price": safe_number(option.get("unit_price"), 0.0),
                "source": option.get("source") or option.get("item_source") or "pricing_or_history",
                "why_suggested": option.get("selected_item_reason") or option.get("source") or "; ".join(reasons),
                "product_id": context.get("product_id") or "",
                "product_name": context.get("product_name") or "",
                "manufacturer": context.get("manufacturer") or "",
                "product_guidance": _candidate_guidance_summary(context),
                "product_source_documents": context.get("source_documents") or [],
                "product_match_score": context.get("match_score") or 0.0,
                "fit_score": round(score, 4),
                "fit_reasons": reasons,
                **compatibility,
            }
        )
    candidates.sort(
        key=lambda candidate: (
            1 if candidate.get("compatibility_status") == "compatible" else 0,
            safe_number(candidate.get("fit_score"), 0.0),
            safe_number(candidate.get("product_match_score"), 0.0),
            safe_number(candidate.get("unit_price"), 0.0) > 0,
            candidate.get("item_name") or "",
        ),
        reverse=True,
    )
    return candidates[:8]


def _selected_roofing_coating_candidate(candidates: list[dict[str, Any]], selected_name: Any) -> dict[str, Any]:
    normalized = _normalized(selected_name)
    if normalized:
        for candidate in candidates:
            if _normalized(candidate.get("item_name")) == normalized:
                return candidate
    for candidate in candidates:
        if candidate.get("compatibility_status") == "compatible" and _is_valid_coating_option(candidate):
            return candidate
    for candidate in candidates:
        if _is_valid_coating_option(candidate):
            return candidate
    return candidates[0] if candidates else {}


def _selected_roofing_primer_candidate(candidates: list[dict[str, Any]], selected_name: Any) -> dict[str, Any]:
    normalized = _normalized(selected_name)
    if normalized:
        for candidate in candidates:
            if _normalized(candidate.get("item_name")) == normalized:
                return candidate
    for candidate in candidates:
        if candidate.get("compatibility_status") == "compatible":
            return candidate
    for candidate in candidates:
        if safe_number(candidate.get("fit_score"), 0.0) > 0:
            return candidate
    return candidates[0] if candidates else {}


def _roofing_detail_candidate_compatibility(
    *,
    package: str,
    template_option: str,
    candidate: dict[str, Any],
    product_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    product_context = product_context or {}
    warnings: list[str] = []
    text = _normalized(
        " ".join(
            str(value or "")
            for value in (
                candidate.get("item_name"),
                candidate.get("unit"),
                candidate.get("category"),
                product_context.get("category"),
                product_context.get("recommended_use"),
                product_context.get("product_family"),
            )
        )
    )
    if package == "fabric":
        if not _contains_any_text(text, ["fabric", "roll", "reinforcement", "scrim"]):
            warnings.append("Pricing candidate does not look like a reinforcement fabric product; estimator should verify the selected item.")
    else:
        if not _contains_any_text(text, ["sealant", "caulk", "flashing", "sausage", "tube", "buttergrade", "sf-2000", "sf 2000"]):
            warnings.append("Pricing candidate does not look like a caulk/sealant product; estimator should verify the selected item.")
        option_text = _normalized(template_option)
        if "urethane" in option_text and "urethane" not in text:
            warnings.append("Template option is urethane but pricing candidate does not clearly reference urethane.")
        if "silicone" in option_text and "silicone" not in text:
            warnings.append("Template option is silicone but pricing candidate does not clearly reference silicone.")
    if not product_context.get("product_id"):
        warnings.append("No product data sheet match is available for this detail candidate.")
    status = "compatible" if not warnings else "review"
    if any("does not look" in warning.lower() for warning in warnings):
        status = "spec_mismatch"
    return {
        "compatibility_status": status,
        "compatibility_warnings": warnings,
    }


def _roofing_board_candidate_compatibility(
    *,
    package: str,
    template_option: str,
    candidate: dict[str, Any],
    product_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    product_context = product_context or {}
    warnings: list[str] = []
    text = _normalized(
        " ".join(
            str(value or "")
            for value in (
                candidate.get("item_name"),
                candidate.get("unit"),
                candidate.get("category"),
                product_context.get("category"),
                product_context.get("recommended_use"),
                product_context.get("product_family"),
            )
        )
    )
    if package == "board_stock":
        if not _contains_any_text(text, ["board", "iso", "wood fiber", "fiberboard", "dens", "deck", "gyp", "gypsum", "flute", "cover board"]):
            warnings.append("Pricing candidate does not look like a board stock product; estimator should verify the selected item.")
        option_text = _normalized(template_option)
        if "iso" in option_text and not _contains_any_text(text, ["iso", "polyiso", "insulation board"]):
            warnings.append("Template option is ISO Board but pricing candidate does not clearly reference ISO/polyiso board.")
        if "dens" in option_text and not _contains_any_text(text, ["dens", "deck", "gypsum", "gyp"]):
            warnings.append("Template option is Dens Deck but pricing candidate does not clearly reference Dens Deck/gypsum board.")
        if "wood fiber" in option_text and not _contains_any_text(text, ["wood fiber", "fiberboard"]):
            warnings.append("Template option is Wood Fiber but pricing candidate does not clearly reference wood fiber board.")
        if "flute" in option_text and "flute" not in text:
            warnings.append("Template option is Flute Filler but pricing candidate does not clearly reference flute filler.")
    elif package == "plates":
        if "plate" not in text:
            warnings.append("Pricing candidate does not look like a plate product; estimator should verify the selected item.")
    else:
        if not _contains_any_text(text, ["fastener", "screw"]):
            warnings.append("Pricing candidate does not look like a roofing fastener/screw product; estimator should verify the selected item.")
    if not product_context.get("product_id"):
        warnings.append("No product data sheet match is available for this board/fastener candidate.")
    status = "compatible" if not warnings else "review"
    if any("does not look" in warning.lower() for warning in warnings):
        status = "spec_mismatch"
    return {
        "compatibility_status": status,
        "compatibility_warnings": warnings,
    }


def _roofing_granules_candidate_compatibility(
    *,
    template_option: str,
    candidate: dict[str, Any],
    product_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    product_context = product_context or {}
    warnings: list[str] = []
    text = _normalized(
        " ".join(
            str(value or "")
            for value in (
                candidate.get("item_name"),
                candidate.get("unit"),
                candidate.get("category"),
                product_context.get("category"),
                product_context.get("recommended_use"),
                product_context.get("product_family"),
                product_context.get("manufacturer"),
            )
        )
    )
    if not _contains_any_text(text, ["granule", "granules", "broadcast", "mineral", "snow white", "lr9300", "bag"]):
        warnings.append("Pricing candidate does not look like a granules/broadcast product; estimator should verify the selected item.")
    option_text = _normalized(template_option)
    if "3m" in option_text and not _contains_any_text(text, ["3m", "mineral", "lr9300"]):
        warnings.append("Template option is 3M but pricing candidate does not clearly reference 3M/mineral granules.")
    if "sesco" in option_text and not _contains_any_text(text, ["sesco", "snow white"]):
        warnings.append("Template option is SESCO but pricing candidate does not clearly reference SESCO/Snow White granules.")
    if _contains_any_text(text, ["roof coating", "silicone", "primer", "sealant", "caulk", "tube"]):
        warnings.append("Pricing candidate includes coating/primer/sealant signals; granules row needs estimator review.")
    if not product_context.get("product_id"):
        warnings.append("No product data sheet match is available for this granules candidate.")
    status = "compatible" if not warnings else "review"
    if any("does not look" in warning.lower() or "coating/primer/sealant" in warning.lower() for warning in warnings):
        status = "spec_mismatch"
    return {
        "compatibility_status": status,
        "compatibility_warnings": warnings,
    }


def _roofing_detail_pricing_candidates(
    row: dict[str, Any],
    scope: dict[str, Any],
    *,
    package: str,
    decision_id: str,
    data: Any = None,
    template_option: str = "",
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for option in _material_item_options(row):
        item_name = str(option.get("item_name") or "").strip()
        if not item_name:
            continue
        context = _product_context(data, item_name=item_name, decision_id=decision_id, package=package) if data is not None else {}
        score, reasons = _package_item_fit_details(package, option, scope)
        compatibility = _roofing_detail_candidate_compatibility(
            package=package,
            template_option=template_option,
            candidate=option,
            product_context=context,
        )
        candidates.append(
            {
                "item_name": item_name,
                "pricing_item_id": option.get("pricing_item_id"),
                "unit": option.get("unit"),
                "unit_price": safe_number(option.get("unit_price"), 0.0),
                "source": option.get("source") or option.get("item_source") or "pricing_or_history",
                "why_suggested": option.get("selected_item_reason") or option.get("source") or "; ".join(reasons),
                "product_id": context.get("product_id") or "",
                "product_name": context.get("product_name") or "",
                "manufacturer": context.get("manufacturer") or "",
                "product_guidance": _candidate_guidance_summary(context),
                "product_source_documents": context.get("source_documents") or [],
                "product_match_score": context.get("match_score") or 0.0,
                "fit_score": round(score, 4),
                "fit_reasons": reasons,
                **compatibility,
            }
        )
    candidates.sort(
        key=lambda candidate: (
            1 if candidate.get("compatibility_status") == "compatible" else 0,
            safe_number(candidate.get("fit_score"), 0.0),
            safe_number(candidate.get("product_match_score"), 0.0),
            safe_number(candidate.get("unit_price"), 0.0) > 0,
            candidate.get("item_name") or "",
        ),
        reverse=True,
    )
    return candidates[:8]


def _roofing_board_pricing_candidates(
    row: dict[str, Any],
    scope: dict[str, Any],
    *,
    package: str,
    decision_id: str,
    data: Any = None,
    template_option: str = "",
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    fit_package = "fastener_treatment" if package == "fasteners" else package
    for option in _material_item_options(row):
        item_name = str(option.get("item_name") or "").strip()
        if not item_name:
            continue
        context = _product_context(data, item_name=item_name, decision_id=decision_id, package=fit_package) if data is not None else {}
        score, reasons = _package_item_fit_details(fit_package, option, scope)
        compatibility = _roofing_board_candidate_compatibility(
            package=package,
            template_option=template_option,
            candidate=option,
            product_context=context,
        )
        candidates.append(
            {
                "item_name": item_name,
                "pricing_item_id": option.get("pricing_item_id"),
                "unit": option.get("unit"),
                "unit_price": safe_number(option.get("unit_price"), 0.0),
                "source": option.get("source") or option.get("item_source") or "pricing_or_history",
                "why_suggested": option.get("selected_item_reason") or option.get("source") or "; ".join(reasons),
                "product_id": context.get("product_id") or "",
                "product_name": context.get("product_name") or "",
                "manufacturer": context.get("manufacturer") or "",
                "product_guidance": _candidate_guidance_summary(context),
                "product_source_documents": context.get("source_documents") or [],
                "product_match_score": context.get("match_score") or 0.0,
                "fit_score": round(score, 4),
                "fit_reasons": reasons,
                **compatibility,
            }
        )
    candidates.sort(
        key=lambda candidate: (
            1 if candidate.get("compatibility_status") == "compatible" else 0,
            safe_number(candidate.get("fit_score"), 0.0),
            safe_number(candidate.get("product_match_score"), 0.0),
            safe_number(candidate.get("unit_price"), 0.0) > 0,
            candidate.get("item_name") or "",
        ),
        reverse=True,
    )
    return candidates[:8]


def _roofing_granules_pricing_candidates(
    row: dict[str, Any],
    scope: dict[str, Any],
    *,
    data: Any = None,
    template_option: str = "",
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for option in _material_item_options(row):
        item_name = str(option.get("item_name") or "").strip()
        if not item_name:
            continue
        context = _product_context(data, item_name=item_name, decision_id="roofing_granules", package="granules") if data is not None else {}
        score, reasons = _package_item_fit_details("granules", option, scope)
        compatibility = _roofing_granules_candidate_compatibility(
            template_option=template_option,
            candidate=option,
            product_context=context,
        )
        candidates.append(
            {
                "item_name": item_name,
                "pricing_item_id": option.get("pricing_item_id"),
                "unit": option.get("unit"),
                "unit_price": safe_number(option.get("unit_price"), 0.0),
                "source": option.get("source") or option.get("item_source") or "pricing_or_history",
                "why_suggested": option.get("selected_item_reason") or option.get("source") or "; ".join(reasons),
                "product_id": context.get("product_id") or "",
                "product_name": context.get("product_name") or "",
                "manufacturer": context.get("manufacturer") or "",
                "product_guidance": _candidate_guidance_summary(context),
                "product_source_documents": context.get("source_documents") or [],
                "product_match_score": context.get("match_score") or 0.0,
                "fit_score": round(score, 4),
                "fit_reasons": reasons,
                **compatibility,
            }
        )
    candidates.sort(
        key=lambda candidate: (
            1 if candidate.get("compatibility_status") == "compatible" else 0,
            safe_number(candidate.get("fit_score"), 0.0),
            safe_number(candidate.get("product_match_score"), 0.0),
            safe_number(candidate.get("unit_price"), 0.0) > 0,
            candidate.get("item_name") or "",
        ),
        reverse=True,
    )
    return candidates[:8]


def _selected_roofing_detail_candidate(candidates: list[dict[str, Any]], selected_name: Any) -> dict[str, Any]:
    normalized = _normalized(selected_name)
    if normalized:
        for candidate in candidates:
            if _normalized(candidate.get("item_name")) == normalized:
                return candidate
    for candidate in candidates:
        if candidate.get("compatibility_status") == "compatible":
            return candidate
    for candidate in candidates:
        if safe_number(candidate.get("fit_score"), 0.0) > 0:
            return candidate
    return candidates[0] if candidates else {}


def _selected_roofing_board_candidate(candidates: list[dict[str, Any]], selected_name: Any) -> dict[str, Any]:
    normalized = _normalized(selected_name)
    if normalized:
        for candidate in candidates:
            if _normalized(candidate.get("item_name")) == normalized:
                return candidate
    for candidate in candidates:
        if candidate.get("compatibility_status") == "compatible":
            return candidate
    for candidate in candidates:
        if safe_number(candidate.get("fit_score"), 0.0) > 0:
            return candidate
    return candidates[0] if candidates else {}


def _selected_roofing_granules_candidate(candidates: list[dict[str, Any]], selected_name: Any) -> dict[str, Any]:
    normalized = _normalized(selected_name)
    if normalized:
        for candidate in candidates:
            if _normalized(candidate.get("item_name")) == normalized:
                return candidate
    for candidate in candidates:
        if candidate.get("compatibility_status") == "compatible":
            return candidate
    for candidate in candidates:
        if safe_number(candidate.get("fit_score"), 0.0) > 0:
            return candidate
    return candidates[0] if candidates else {}


def _foam_candidate_rank(candidate: dict[str, Any]) -> int:
    status = str(candidate.get("compatibility_status") or "").lower()
    if status == "compatible":
        return 3
    if status == "review":
        return 2
    if status == "spec_mismatch":
        return 0
    return 1


def _is_roofing_foam_candidate(candidate: dict[str, Any]) -> bool:
    text = _normalized(
        " ".join(
            [
                str(candidate.get("item_name") or ""),
                " ".join(str(warning) for warning in candidate.get("compatibility_warnings") or []),
                str((candidate.get("candidate_traits") or {}).get("application") or ""),
            ]
        )
    )
    return any(
        term in text
        for term in (
            "roofing foam",
            "roof foam",
            "roof repair",
            "repair foam",
            "roof kit",
            "gacorooffoam",
            "gaco roof",
            "basf roof",
            "f2733",
            "f2780",
            "2.7 lb",
        )
    )


def _is_bad_default_foam_candidate(candidate: dict[str, Any]) -> bool:
    if not candidate:
        return True
    return str(candidate.get("compatibility_status") or "").lower() == "spec_mismatch" or _is_roofing_foam_candidate(candidate)


def _selected_foam_candidate(candidates: list[dict[str, Any]], selected_name: Any, *, preserve_bad_selection: bool = False) -> dict[str, Any]:
    normalized = _normalized(selected_name)
    requested: dict[str, Any] | None = None
    if normalized:
        for candidate in candidates:
            if _normalized(candidate.get("item_name")) == normalized:
                requested = candidate
                break
    if requested and (preserve_bad_selection or not _is_bad_default_foam_candidate(requested)):
        return requested
    for candidate in candidates:
        if not _is_bad_default_foam_candidate(candidate):
            return candidate
    if requested:
        return requested
    return candidates[0] if candidates else {}


def _build_insulation_foam_template_decisions(
    *,
    scope: dict[str, Any],
    foam_row: dict[str, Any] | None,
    existing_rows: list[dict[str, Any]] | None = None,
    data: Any = None,
) -> list[dict[str, Any]]:
    if not foam_row:
        return []
    existing = (existing_rows or [{}])[0] if existing_rows else {}
    historical_option = first_nonblank(
        existing.get("historical_selector_recommendation"),
        foam_row.get("recommended_decision_value"),
        (foam_row.get("decision_values") or {}).get("selected_option") if isinstance(foam_row.get("decision_values"), dict) else "",
        _resolved_selector_option(foam_row.get("selector_code")),
        "Gaco 2.0 lb.",
    )
    selector_code = first_nonblank(
        existing.get("editable_selector_code"),
        existing.get("selector_code"),
        foam_row.get("selector_code"),
        _selector_code_for_option(historical_option),
        "11",
    )
    resolved_option = _resolved_selector_option(selector_code, historical_option)
    basis_sqft = safe_number(first_nonblank(existing.get("basis_sqft"), foam_row.get("editable_basis_sqft"), foam_row.get("default_basis_sqft")), 0.0)
    material_thickness = safe_number(foam_row.get("thickness_inches"), 0.0)
    material_synced_thickness = safe_number(foam_row.get("foam_thickness_inches"), 0.0)
    direct_material_thickness_edit = material_thickness > 0 and material_synced_thickness > 0 and abs(material_thickness - material_synced_thickness) > 1e-9
    thickness = safe_number(
        first_nonblank(
            foam_row.get("thickness_inches") if direct_material_thickness_edit else "",
            existing.get("thickness_inches"),
            foam_row.get("thickness_inches"),
            foam_row.get("foam_thickness_inches"),
        ),
        0.0,
    )
    yield_or_coverage = safe_number(first_nonblank(existing.get("yield_or_coverage"), foam_row.get("yield_factor"), foam_row.get("median_foam_yield")), 0.0)
    selected_candidate_name = first_nonblank(existing.get("selected_pricing_candidate"), foam_row.get("item_name"), foam_row.get("current_item"))
    stored_candidates = existing.get("pricing_candidates") if isinstance(existing.get("pricing_candidates"), list) else []
    if not stored_candidates:
        try:
            parsed_candidates = json.loads(existing.get("pricing_candidates_json") or "[]")
            stored_candidates = parsed_candidates if isinstance(parsed_candidates, list) else []
        except (TypeError, ValueError, json.JSONDecodeError):
            stored_candidates = []
    candidates = stored_candidates if data is None and stored_candidates else _foam_pricing_candidates(foam_row, scope, data=data, template_option=resolved_option)
    selected_candidate = _selected_foam_candidate(
        candidates,
        selected_candidate_name,
        preserve_bad_selection=bool(first_nonblank(existing.get("selected_pricing_candidate"))),
    )
    unit_price = safe_number(first_nonblank(existing.get("unit_price"), selected_candidate.get("unit_price"), foam_row.get("current_unit_price")), 0.0)
    include = bool(existing["include"]) if "include" in existing else bool(foam_row.get("include"))
    formula = calculate_insulation_foam(
        area_sqft=basis_sqft,
        thickness_inches=thickness,
        yield_or_coverage=yield_or_coverage,
        unit_price=unit_price,
        include=include,
    )
    compatibility = _foam_candidate_compatibility(template_option=resolved_option, candidate=selected_candidate, scope=scope, product_context=selected_candidate)
    warnings = list(dict.fromkeys([*(selected_candidate.get("compatibility_warnings") or []), *(compatibility.get("compatibility_warnings") or [])]))
    if yield_or_coverage <= 0:
        warnings.append("Yield/coverage is missing; template formula output requires estimator review.")
    return [
        {
            "include": include,
            "section": "insulation_foam_template_decisions",
            "decision_id": "insulation_foam_template_selector",
            "template_bucket": "foam",
            "workbook_row": "19-21",
            "template_rows": "19,20,21",
            "selector_cell": "A19",
            "selector_code": str(selector_code),
            "editable_selector_code": str(selector_code),
            "resolved_template_option": resolved_option,
            "selector_options": _foam_selector_options(),
            "selector_options_json": json.dumps(_foam_selector_options(), default=str),
            "historical_selector_recommendation": historical_option,
            "historical_selector_code": _selector_code_for_option(historical_option),
            "historical_selector_evidence_count": int(safe_number(foam_row.get("decision_evidence_count") or foam_row.get("evidence_count"), 0)),
            "historical_selector_confidence": foam_row.get("decision_confidence") or foam_row.get("confidence") or "",
            "basis_sqft": round(basis_sqft, 2),
            "thickness_inches": round(thickness, 4),
            "yield_or_coverage": round(yield_or_coverage, 4),
            "unit_price": round(unit_price, 4),
            "estimated_units": formula.get("estimated_units"),
            "estimated_sets": formula.get("estimated_sets"),
            "estimated_cost": formula.get("estimated_cost"),
            "formula_model": formula.get("formula_model"),
            "formula_source": formula.get("formula_source"),
            "selected_pricing_candidate": selected_candidate.get("item_name") or str(selected_candidate_name or ""),
            "selected_pricing_item_id": selected_candidate.get("pricing_item_id"),
            "pricing_candidates": candidates,
            "pricing_candidates_json": json.dumps(candidates, default=str),
            "compatibility_status": "review" if warnings and compatibility.get("compatibility_status") == "compatible" else compatibility.get("compatibility_status"),
            "compatibility_warnings": warnings,
            "product_guidance": selected_candidate.get("product_guidance") or "",
            "product_source_documents": selected_candidate.get("product_source_documents") or [],
            "notes": (
                "Template selector is the estimator decision. Pricing/product candidate is shown separately for review. "
                + (" ".join(warnings) if warnings else "No clear template/product compatibility warnings.")
            ),
            "decision_values": {
                "selector_code": str(selector_code),
                "resolved_template_option": resolved_option,
                "selected_pricing_candidate": selected_candidate.get("item_name") or str(selected_candidate_name or ""),
                "basis_sqft": round(basis_sqft, 2),
                "thickness_inches": round(thickness, 4),
                "yield_or_coverage": round(yield_or_coverage, 4),
                "unit_price": round(unit_price, 4),
            },
            "editable_decision_value": {
                "selector_code": str(selector_code),
                "resolved_template_option": resolved_option,
                "selected_pricing_candidate": selected_candidate.get("item_name") or str(selected_candidate_name or ""),
                "basis_sqft": round(basis_sqft, 2),
                "thickness_inches": round(thickness, 4),
                "yield_or_coverage": round(yield_or_coverage, 4),
                "unit_price": round(unit_price, 4),
            },
            "recommended_decision_value": {
                "selector_code": _selector_code_for_option(historical_option),
                "resolved_template_option": historical_option,
                "evidence_count": int(safe_number(foam_row.get("decision_evidence_count") or foam_row.get("evidence_count"), 0)),
            },
            "calculated_output": formula.get("estimated_cost"),
            "calculated_output_summary": _value_summary(
                {
                    "units": formula.get("estimated_units"),
                    "sets": formula.get("estimated_sets"),
                    "cost": formula.get("estimated_cost"),
                }
            ),
            "workbook_cell_write_preview": [
                {"cell": "Estimate!A19", "field": "selector_code", "value": str(selector_code)},
                {"cell": "Estimate!C19", "field": "area_sqft", "value": round(basis_sqft, 2)},
                {"cell": "Estimate!D19", "field": "thickness_inches", "value": round(thickness, 4)},
                {"cell": "Estimate!E19", "field": "unit_price", "value": round(unit_price, 4)},
                {"cell": "Estimate!F19", "field": "yield_or_coverage", "value": round(yield_or_coverage, 4)},
                {"cell": "Estimate!G19", "field": "estimated_units_formula_output", "value": formula.get("estimated_units")},
            ],
        }
    ]


def _insulation_graph_selector_options(decision_id: str, *, workbook_row: Any = None) -> list[dict[str, Any]]:
    graph_path = Path("output/template_decision_graph_insulation.json")
    options: list[dict[str, Any]] = []
    if graph_path.exists():
        try:
            payload = json.loads(graph_path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        row_text = str(workbook_row or "").strip()
        for row in payload.get("selector_options") or []:
            if row.get("decision_id") != decision_id:
                continue
            if row_text and str(row.get("resolved_cell") or "").strip():
                resolved_cell = str(row.get("resolved_cell") or "")
                if not resolved_cell.endswith(row_text):
                    continue
            code = str(row.get("selector_code") or "").strip()
            label = str(row.get("resolved_item_name") or "").strip()
            if not code or not label:
                continue
            options.append(
                {
                    "selector_code": code,
                    "resolved_template_option": label,
                    "resolved_cell": row.get("resolved_cell") or "",
                    "source_type": row.get("source_type") or "template_selector",
                }
            )
    deduped: dict[str, dict[str, Any]] = {}
    for option in options:
        deduped.setdefault(str(option.get("selector_code") or ""), option)
    return sorted(deduped.values(), key=lambda item: (int(safe_number(item.get("selector_code"), 999)), item.get("resolved_template_option") or ""))


def _selector_choice(
    *,
    decision_id: str | None,
    workbook_row: Any,
    existing: dict[str, Any],
    source_row: dict[str, Any] | None = None,
    default_code: str = "1",
) -> tuple[str, str, list[dict[str, Any]]]:
    if not decision_id:
        return "", "", []
    options = _insulation_graph_selector_options(decision_id, workbook_row=workbook_row)
    code = str(
        first_nonblank(
            existing.get("editable_selector_code"),
            existing.get("selector_code"),
            source_row.get("selector_code") if source_row else "",
            default_code,
        )
    ).strip()
    if code.endswith(".0"):
        code = code[:-2]
    label = str(
        first_nonblank(
            existing.get("resolved_template_option"),
            source_row.get("resolved_template_option") if source_row else "",
            source_row.get("template_selector_option") if source_row else "",
            "",
        )
    ).strip()
    for option in options:
        if str(option.get("selector_code") or "") == code:
            label = str(option.get("resolved_template_option") or label)
            break
    if not label and options:
        code = str(options[0].get("selector_code") or code or default_code)
        label = str(options[0].get("resolved_template_option") or "")
    return code, label, options


def _row_for_bucket(rows: list[dict[str, Any]] | None, bucket: str) -> dict[str, Any] | None:
    bucket_key = _normalized(bucket)
    aliases = _package_aliases(bucket)
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        values = {
            _normalized(row.get("template_bucket")),
            _normalized(row.get("package_key")),
            _normalized(row.get("package")),
            _normalized(row.get("adder_key")),
            _normalized(row.get("adder")),
            _normalized(row.get("labor_package")),
        }
        if bucket_key in values or values.intersection(aliases):
            return row
    return None


def _existing_decision_rows(existing_rows: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for row in existing_rows or []:
        if not isinstance(row, dict):
            continue
        for key in (
            row.get("decision_id"),
            row.get("template_bucket"),
            row.get("package_key"),
            row.get("workbook_row"),
        ):
            if key not in (None, ""):
                index[str(key)] = row
    return index


def _source_for_insulation_decision(
    spec: dict[str, Any],
    *,
    materials: list[dict[str, Any]] | None,
    adders: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    bucket = str(spec.get("template_bucket") or "")
    return _row_for_bucket(materials, bucket) or _row_for_bucket(adders, bucket) or {}


def _insulation_product_context_for_row(
    *,
    data: Any,
    decision_id: str,
    bucket: str,
    item_name: Any,
) -> dict[str, Any]:
    return _product_context(data, item_name=str(item_name or ""), decision_id=decision_id, package=bucket) if data is not None else {}


def _insulation_product_guidance_fields(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "product_id": context.get("product_id") or "",
        "product_name": context.get("product_name") or "",
        "product_manufacturer": context.get("manufacturer") or "",
        "product_guidance": _candidate_guidance_summary(context) or _product_guidance_summary(context),
        "product_guidance_status": "matched" if context.get("product_id") else "missing",
        "product_warning_summary": _value_summary(context.get("warnings") or []),
        "product_source_documents": context.get("source_documents") or [],
        "product_match_score": context.get("match_score") or 0.0,
    }


def _decision_output_summary(formula: dict[str, Any]) -> str:
    return _value_summary(
        {
            "quantity": first_nonblank(
                formula.get("estimated_units"),
                formula.get("estimated_gallons"),
                formula.get("estimated_drums"),
                formula.get("calculated_quantity"),
            ),
            "hours": formula.get("total_hours"),
            "cost": formula.get("estimated_cost"),
            "source": formula.get("formula_source"),
        }
    )


def _insulation_material_preview(row: dict[str, Any]) -> list[dict[str, Any]]:
    workbook_row = str(row.get("workbook_row") or "")
    first_row = int(safe_number(workbook_row.split("-")[0].split("/")[0], 0)) if workbook_row and workbook_row[0].isdigit() else 0
    if first_row <= 0:
        return []
    preview: list[dict[str, Any]] = []
    if row.get("editable_selector_code"):
        preview.append({"cell": f"Estimate!A{first_row}", "field": "selector_code", "value": row.get("editable_selector_code")})
    field_to_cell = {
        "basis_sqft": "C",
        "linear_ft": "C",
        "quantity": "C",
        "days": "C",
        "period": "D" if row.get("template_bucket") == "lift" else "C",
        "gal_per_100_sqft": "D",
        "feet_per_unit": "D",
        "unit_price": "E",
        "margin_pct": "F",
        "estimated_units": "G",
        "estimated_gallons": "G",
        "estimated_cost": "H",
    }
    for field, column in field_to_cell.items():
        if row.get(field) not in (None, ""):
            preview.append({"cell": f"Estimate!{column}{first_row}", "field": field, "value": row.get(field)})
    return preview


def _calculate_insulation_decision_formula(
    spec: dict[str, Any],
    *,
    include: bool,
    source_row: dict[str, Any],
    existing: dict[str, Any],
    area: float,
    dependencies: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    formula_kind = str(spec.get("formula") or "direct")
    unit_price = safe_number(first_nonblank(existing.get("unit_price"), source_row.get("current_unit_price"), source_row.get("current_price"), source_row.get("unit_price")), 0.0)
    amount = safe_number(first_nonblank(existing.get("amount"), existing.get("editable_value"), source_row.get("editable_value"), source_row.get("estimated_cost")), 0.0)
    basis_sqft = safe_number(first_nonblank(existing.get("basis_sqft"), source_row.get("editable_basis_sqft"), source_row.get("default_basis_sqft"), area), 0.0)
    linear_ft = safe_number(first_nonblank(existing.get("linear_ft"), source_row.get("linear_ft"), source_row.get("calculated_quantity")), 0.0)
    quantity = safe_number(first_nonblank(existing.get("quantity"), source_row.get("quantity"), source_row.get("calculated_quantity"), amount), 0.0)
    days = safe_number(first_nonblank(existing.get("days"), source_row.get("days"), source_row.get("editable_days"), 0), 0.0)
    period = safe_number(first_nonblank(existing.get("period"), existing.get("rental_period"), source_row.get("period"), source_row.get("rental_period"), days), 0.0)
    margin_pct = safe_number(first_nonblank(existing.get("margin_pct"), source_row.get("margin_pct"), spec.get("default_margin_pct"), 0), 0.0)
    gal_per_100 = safe_number(first_nonblank(existing.get("gal_per_100_sqft"), source_row.get("gal_per_100_sqft"), spec.get("default_gal_per_100"), 0), 0.0)
    waste_pct = safe_number(first_nonblank(existing.get("waste_factor_pct"), source_row.get("waste_factor_pct"), source_row.get("margin_pct"), spec.get("default_waste_pct"), 0), 0.0)
    feet_per_unit = safe_number(first_nonblank(existing.get("feet_per_unit"), source_row.get("feet_per_unit"), spec.get("default_feet_per_unit"), 0), 0.0)
    trip_count = safe_number(first_nonblank(existing.get("trip_count"), source_row.get("trip_count"), 0), 0.0)
    round_trip_miles = safe_number(first_nonblank(existing.get("round_trip_miles"), source_row.get("round_trip_miles"), 0), 0.0)
    coverage = safe_number(first_nonblank(existing.get("coverage_sqft_per_unit"), source_row.get("coverage_sqft_per_unit"), spec.get("default_coverage"), 250), 250.0)

    if formula_kind == "membrane":
        formula = calculate_insulation_membrane(linear_ft=linear_ft, unit_price=unit_price, include=include)
    elif formula_kind == "primer":
        formula = calculate_insulation_primer(area_sqft=basis_sqft, coverage_sqft_per_unit=coverage, unit_price=unit_price, include=include)
    elif formula_kind == "caulk_sealant":
        formula = calculate_insulation_caulk_sealant(linear_ft=linear_ft, feet_per_unit=feet_per_unit, unit_price=unit_price, include=include)
    elif formula_kind == "thermal_barrier":
        formula = calculate_insulation_thermal_barrier(area_sqft=basis_sqft, gal_per_100_sqft=gal_per_100, waste_factor_pct=waste_pct, unit_price=unit_price, include=include)
    elif formula_kind == "thinner":
        formula = calculate_insulation_thinner(total_coating_gallons=dependencies.get("thermal_gallons"), unit_price=unit_price, include=include)
    elif formula_kind == "drum_disposal":
        formula = calculate_insulation_drum_disposal(
            primer_units=dependencies.get("primer_units"),
            coating_gallons=dependencies.get("thermal_gallons"),
            thinner_units=dependencies.get("thinner_units"),
            foam_units=dependencies.get("foam_units"),
            unit_price=unit_price,
            include=include,
        )
    elif formula_kind == "equipment":
        formula = calculate_insulation_equipment_cost(period=period, unit_price=unit_price, margin_pct=margin_pct, include=include)
    elif formula_kind == "days_rate":
        formula = calculate_insulation_days_rate_cost(days=days or period, unit_price=unit_price, include=include)
    elif formula_kind == "travel":
        formula = calculate_insulation_travel_cost(trip_count=trip_count, round_trip_miles=round_trip_miles, unit_price=unit_price, include=include)
    elif formula_kind == "units_cost":
        formula = calculate_roofing_units_cost(units=quantity, unit_price=unit_price, include=include, formula_model="insulation_units_cost")
    elif formula_kind == "abaa_fee":
        formula = calculate_insulation_abaa_fee(area_sqft=basis_sqft, unit_price=unit_price, include=include)
    elif formula_kind == "bond":
        formula = calculate_insulation_bond(project_total=dependencies.get("pre_pricing_total"), include=include)
    elif formula_kind == "markup":
        pct = safe_number(first_nonblank(existing.get("percentage"), existing.get("markup_pct"), source_row.get("percentage"), amount), 0.0)
        base = safe_number(dependencies.get("pre_pricing_total"), 0.0)
        formula = calculate_insulation_direct_cost(amount=base * pct / 100.0, include=include)
        formula["formula_model"] = "insulation_markup_from_total_pct"
        formula["percentage"] = round(pct, 6)
    else:
        formula = calculate_insulation_direct_cost(amount=amount, include=include)

    inputs = {
        "basis_sqft": round(basis_sqft, 4),
        "linear_ft": round(linear_ft, 4),
        "quantity": round(quantity, 4),
        "days": round(days, 4),
        "period": round(period, 4),
        "unit_price": round(unit_price, 4),
        "margin_pct": round(margin_pct, 4),
        "gal_per_100_sqft": round(gal_per_100, 4),
        "waste_factor_pct": round(waste_pct, 4),
        "feet_per_unit": round(feet_per_unit, 4),
        "trip_count": round(trip_count, 4),
        "round_trip_miles": round(round_trip_miles, 4),
        "coverage_sqft_per_unit": round(coverage, 4),
        "amount": round(amount, 2),
    }
    return formula, inputs


def _build_insulation_decision_rows(
    *,
    section: str,
    specs: list[dict[str, Any]],
    scope: dict[str, Any],
    materials: list[dict[str, Any]] | None = None,
    adders: list[dict[str, Any]] | None = None,
    existing_rows: list[dict[str, Any]] | None = None,
    data: Any = None,
    dependencies: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    existing_index = _existing_decision_rows(existing_rows)
    area = _estimate_area(scope)
    notes_text = _normalized(" ".join(str(scope.get(key) or "") for key in ("notes", "raw_input_notes", "project_type", "building_type")))
    rows: list[dict[str, Any]] = []
    deps = dict(dependencies or {})
    for spec in specs:
        source = _source_for_insulation_decision(spec, materials=materials, adders=adders)
        existing = existing_index.get(str(spec.get("workbook_row"))) or existing_index.get(str(spec.get("template_bucket"))) or existing_index.get(str(spec.get("decision_id"))) or {}
        bucket = str(spec.get("template_bucket") or "")
        workbook_row = str(spec.get("workbook_row") or "")
        include_default = bool(source.get("include"))
        trigger_terms = _package_aliases(bucket)
        if any(term and term in notes_text for term in trigger_terms):
            include_default = True
        if bucket in {"drum_disposal"} and any(safe_number(deps.get(key), 0.0) > 0 for key in ("foam_units", "thermal_gallons", "primer_units", "thinner_units")):
            include_default = True
        include = bool(existing["include"]) if "include" in existing else include_default
        selector_code, resolved_option, selector_options = _selector_choice(
            decision_id=spec.get("selector_decision_id"),
            workbook_row=workbook_row,
            existing=existing,
            source_row=source,
        )
        formula, inputs = _calculate_insulation_decision_formula(spec, include=include, source_row=source, existing=existing, area=area, dependencies=deps)
        item_name = first_nonblank(
            existing.get("selected_pricing_candidate"),
            source.get("item_name"),
            source.get("current_item"),
            resolved_option,
            spec.get("label"),
        )
        product_context = _insulation_product_context_for_row(data=data, decision_id=str(spec.get("decision_id")), bucket=bucket, item_name=item_name)
        guidance = _insulation_product_guidance_fields(product_context)
        warnings: list[str] = []
        if include and safe_number(formula.get("estimated_cost"), 0.0) <= 0 and str(formula.get("formula_source")) != "not_included":
            warnings.append("Formula preview needs estimator input before it can calculate cost.")
        if bucket in {"thermal_barrier_coating", "primer", "caulk_sealant"} and not guidance.get("product_id"):
            warnings.append("No product data sheet match is available for this decision.")
        row = {
            "include": include,
            "section": section,
            "decision_id": f"{spec.get('decision_id')}_row_{workbook_row}",
            "source_decision_id": spec.get("decision_id"),
            "template_bucket": bucket,
            "package_key": bucket,
            "workbook_row": workbook_row,
            "template_line": spec.get("label"),
            "editable_selector_code": selector_code,
            "selector_code": selector_code,
            "resolved_template_option": resolved_option,
            "selector_options": selector_options,
            "selector_options_json": json.dumps(selector_options, default=str),
            "selected_pricing_candidate": item_name,
            "unit_price": inputs.get("unit_price"),
            "basis_sqft": inputs.get("basis_sqft"),
            "linear_ft": inputs.get("linear_ft"),
            "quantity": inputs.get("quantity"),
            "days": inputs.get("days"),
            "period": inputs.get("period"),
            "margin_pct": inputs.get("margin_pct"),
            "gal_per_100_sqft": inputs.get("gal_per_100_sqft"),
            "waste_factor_pct": inputs.get("waste_factor_pct"),
            "feet_per_unit": inputs.get("feet_per_unit"),
            "trip_count": inputs.get("trip_count"),
            "round_trip_miles": inputs.get("round_trip_miles"),
            "amount": inputs.get("amount"),
            "formula_model": formula.get("formula_model"),
            "formula_source": formula.get("formula_source"),
            "estimated_units": safe_number(first_nonblank(formula.get("estimated_units"), formula.get("estimated_drums"), formula.get("calculated_quantity")), 0.0),
            "estimated_gallons": safe_number(formula.get("estimated_gallons"), 0.0),
            "estimated_drums": safe_number(formula.get("estimated_drums"), 0.0),
            "estimated_cost": safe_number(formula.get("estimated_cost"), 0.0),
            "calculated_output": safe_number(formula.get("calculated_output"), 0.0),
            "calculated_output_summary": _decision_output_summary(formula),
            "historical_recommendation": source.get("historical_recommendation") or "",
            "historical_selector_recommendation": first_nonblank(source.get("historical_selector_recommendation"), resolved_option),
            "historical_selector_evidence_count": int(safe_number(source.get("decision_evidence_count") or source.get("evidence_count"), 0)),
            "historical_selector_confidence": source.get("decision_confidence") or source.get("confidence") or "",
            "decision_evidence_count": int(safe_number(source.get("decision_evidence_count") or source.get("evidence_count"), 0)),
            "decision_confidence": source.get("decision_confidence") or source.get("confidence") or "",
            "confidence": source.get("confidence") or "",
            "compatibility_status": "review" if warnings else ("compatible" if include else "not_included"),
            "compatibility_warnings": warnings,
            **guidance,
            "notes": f"{spec.get('notes') or spec.get('label')} Workbook row {workbook_row}. "
            + (" ".join(warnings) if warnings else "Template formula preview is available for estimator review."),
            "decision_values": {**inputs, "selector_code": selector_code, "resolved_template_option": resolved_option},
            "editable_decision_value": {**inputs, "selector_code": selector_code, "resolved_template_option": resolved_option},
            "recommended_decision_value": {
                "resolved_template_option": first_nonblank(source.get("historical_selector_recommendation"), resolved_option),
                "evidence_count": int(safe_number(source.get("decision_evidence_count") or source.get("evidence_count"), 0)),
            },
            "row_traceability": f"Estimate row {workbook_row}",
        }
        row["workbook_cell_write_preview"] = _insulation_material_preview(row)
        rows.append(row)
    return rows


def _insulation_labor_crew_options() -> list[dict[str, Any]]:
    options = _insulation_graph_selector_options("insulation_crew_rate_selection")
    if not options:
        for code in range(1, 9):
            column_letter = chr(ord("C") + code)
            options.append(
                {
                    "selector_code": str(code),
                    "resolved_template_option": f"{code} person crew daily rate",
                    "resolved_cell": f"People!{column_letter}12",
                    "source_type": "fallback_people_daily_rate_selector",
                }
            )
    return options


def _insulation_labor_daily_rate_cell(crew_size: Any) -> str:
    key = str(int(safe_number(crew_size, 0))) if safe_number(crew_size, 0) > 0 else ""
    for option in _insulation_labor_crew_options():
        if str(option.get("selector_code") or "") == key:
            return str(option.get("resolved_cell") or "")
    return f"People!{chr(ord('C') + int(key))}12" if key else ""


def _build_insulation_labor_template_decisions(
    *,
    scope: dict[str, Any],
    labor_rows: list[dict[str, Any]] | None = None,
    existing_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if not _is_insulation_scope(scope):
        return []
    existing_by_key = _existing_decision_rows(existing_rows)
    crew_options = _insulation_labor_crew_options()
    area = _estimate_area(scope)
    baseline = {"labor_set_up", "labor_foam", "labor_clean_up", "labor_loading"}
    notes = _normalized(" ".join(str(scope.get(key) or "") for key in ("notes", "raw_input_notes", "site_address", "address")))
    decisions: list[dict[str, Any]] = []
    for spec in INSULATION_LABOR_PACKAGES:
        package = str(spec["package"])
        workbook_row = str(spec["workbook_row"])
        labor = _row_for_bucket(labor_rows, package) or {}
        existing = existing_by_key.get(package) or existing_by_key.get(workbook_row) or {}
        include_default = bool(labor.get("include")) or package in baseline or (package == "labor_traveling" and bool(notes))
        include = bool(existing["include"]) if "include" in existing else include_default
        crew_size = int(safe_number(first_nonblank(existing.get("crew_size"), labor.get("crew_size"), 3), 3) or 3)
        days = safe_number(first_nonblank(existing.get("days"), existing.get("editable_days"), labor.get("days"), labor.get("editable_days"), 0), 0.0)
        hours_per_1000 = safe_number(first_nonblank(existing.get("editable_hours_per_1000_sqft"), labor.get("editable_hours_per_1000_sqft"), 0), 0.0)
        total_hours = safe_number(first_nonblank(existing.get("total_hours"), existing.get("editable_total_hours"), labor.get("calculated_hours"), labor.get("total_hours"), 0), 0.0)
        hourly_rate = safe_number(first_nonblank(existing.get("hourly_rate"), existing.get("labor_rate"), labor.get("hourly_rate"), labor.get("labor_rate"), DEFAULT_HOURLY_RATE), DEFAULT_HOURLY_RATE)
        daily_rate = safe_number(first_nonblank(existing.get("daily_rate"), labor.get("daily_rate"), 0), 0.0)
        formula_mode = str(first_nonblank(existing.get("formula_mode"), labor.get("formula_mode"), "mixed_formula"))
        formula = calculate_mixed_labor(
            days=days,
            crew_size=crew_size,
            total_hours=total_hours,
            hours_per_1000_sqft=hours_per_1000,
            area_sqft=area,
            daily_rate=daily_rate,
            hourly_rate=hourly_rate,
            formula_mode=formula_mode,
            include=include,
        )
        row_number = int(safe_number(workbook_row, 0))
        selected_cell = _insulation_labor_daily_rate_cell(crew_size)
        preview = [
            {"cell": f"Estimate!B{row_number}", "field": "days", "value": formula.get("days")},
            {"cell": f"Estimate!C{row_number}", "field": "crew_selector_code", "value": crew_size},
            {"cell": f"Estimate!D{row_number}", "field": "hourly_rate", "value": formula.get("hourly_rate")},
            {"cell": f"Estimate!G{row_number}", "field": "total_hours", "value": formula.get("total_hours")},
            {"cell": f"Estimate!J{row_number}", "field": "daily_rate_formula_output", "value": formula.get("daily_rate")},
        ]
        warnings = []
        if include and safe_number(formula.get("estimated_cost"), 0.0) <= 0:
            warnings.append("Labor formula preview needs days, hours, or rate input.")
        decisions.append(
            {
                "include": include,
                "section": "insulation_labor_template_decisions",
                "decision_id": f"insulation_{package}_row_{workbook_row}",
                "source_decision_id": f"insulation_{package}",
                "template_bucket": package,
                "package_key": package,
                "workbook_row": workbook_row,
                "labor_task": spec["label"],
                "labor_package": spec["label"],
                "days": formula.get("days"),
                "editable_days": formula.get("days"),
                "crew_size": crew_size,
                "crew_people_selection": crew_size,
                "crew_selector_code": crew_size,
                "crew_selector_options": crew_options,
                "crew_selector_options_json": json.dumps(crew_options, default=str),
                "selected_daily_rate_cell": selected_cell,
                "daily_rate": formula.get("daily_rate"),
                "hourly_rate": formula.get("hourly_rate"),
                "labor_rate": formula.get("hourly_rate"),
                "editable_hours_per_1000_sqft": round(hours_per_1000, 4),
                "total_hours": formula.get("total_hours"),
                "calculated_hours": formula.get("total_hours"),
                "editable_total_hours": formula.get("total_hours"),
                "formula_mode": formula.get("formula_mode"),
                "formula_model": formula.get("formula_model"),
                "formula_source": formula.get("formula_source"),
                "estimated_cost": formula.get("estimated_cost"),
                "calculated_output": formula.get("calculated_output"),
                "calculated_output_summary": _decision_output_summary(formula),
                "historical_recommendation": labor.get("historical_recommendation") or "",
                "historical_selector_evidence_count": int(safe_number(labor.get("decision_evidence_count") or labor.get("evidence_count"), 0)),
                "historical_selector_confidence": labor.get("decision_confidence") or labor.get("confidence") or "",
                "decision_evidence_count": int(safe_number(labor.get("decision_evidence_count") or labor.get("evidence_count"), 0)),
                "decision_confidence": labor.get("decision_confidence") or labor.get("confidence") or "",
                "confidence": labor.get("confidence") or "",
                "compatibility_status": "review" if warnings else ("compatible" if include else "not_included"),
                "compatibility_warnings": warnings,
                "notes": "Labor decision mirrors the insulation workbook mixed formula. "
                + (" ".join(warnings) if warnings else "Template formula preview is available for estimator review."),
                "decision_values": {
                    "days": formula.get("days"),
                    "crew_size": crew_size,
                    "daily_rate": formula.get("daily_rate"),
                    "hourly_rate": formula.get("hourly_rate"),
                    "total_hours": formula.get("total_hours"),
                    "formula_mode": formula.get("formula_mode"),
                },
                "editable_decision_value": {
                    "days": formula.get("days"),
                    "crew_size": crew_size,
                    "daily_rate": formula.get("daily_rate"),
                    "hourly_rate": formula.get("hourly_rate"),
                    "total_hours": formula.get("total_hours"),
                    "formula_mode": formula.get("formula_mode"),
                },
                "recommended_decision_value": labor.get("recommended_decision_value") or {},
                "row_traceability": f"Estimate row {workbook_row}; daily rate from {selected_cell or 'People sheet selector'}",
                "workbook_cell_write_preview": preview,
            }
        )
    return decisions


def _insulation_dependency_totals(workbench: dict[str, Any] | None = None, *, rows: dict[str, list[dict[str, Any]]] | None = None) -> dict[str, Any]:
    workbench = workbench or {}
    section_rows = rows or {key: workbench.get(key) or [] for key in INSULATION_DECISION_SECTION_KEYS}
    foam_rows = workbench.get("insulation_foam_template_decisions") or []
    deps = {
        "foam_units": sum(safe_number(row.get("estimated_units"), 0.0) for row in foam_rows if isinstance(row, dict) and row.get("include")),
        "thermal_gallons": 0.0,
        "primer_units": 0.0,
        "thinner_units": 0.0,
        "pre_pricing_total": 0.0,
    }
    for section, section_list in section_rows.items():
        for row in section_list or []:
            if not isinstance(row, dict) or not row.get("include"):
                continue
            bucket = str(row.get("template_bucket") or "")
            if bucket == "thermal_barrier_coating":
                deps["thermal_gallons"] += safe_number(row.get("estimated_gallons"), 0.0)
            elif bucket == "primer":
                deps["primer_units"] += safe_number(row.get("estimated_units"), 0.0)
            elif bucket == "thinner":
                deps["thinner_units"] += safe_number(row.get("estimated_units"), 0.0)
            if section != "insulation_pricing_template_decisions":
                deps["pre_pricing_total"] += safe_number(row.get("estimated_cost"), 0.0)
    deps["pre_pricing_total"] += sum(safe_number(row.get("estimated_cost"), 0.0) for row in foam_rows if isinstance(row, dict) and row.get("include"))
    return deps


def _apply_foam_template_decision_to_materials(workbench: dict[str, Any]) -> None:
    foam_row = _foam_material_row(workbench.get("materials"))
    decisions = workbench.get("insulation_foam_template_decisions") or []
    decision = decisions[0] if decisions else {}
    if not foam_row or not decision:
        return
    previous_basis = safe_number(foam_row.get("editable_basis_sqft"), 0.0)
    foam_row["selector_code"] = decision.get("editable_selector_code") or decision.get("selector_code")
    foam_row["resolved_template_option"] = decision.get("resolved_template_option")
    foam_row["template_selector_option"] = decision.get("resolved_template_option")
    if decision.get("selected_pricing_candidate"):
        foam_row["item_name"] = decision.get("selected_pricing_candidate")
    for source_field, target_field in (
        ("basis_sqft", "editable_basis_sqft"),
        ("basis_sqft", "default_basis_sqft"),
        ("thickness_inches", "thickness_inches"),
        ("thickness_inches", "foam_thickness_inches"),
        ("yield_or_coverage", "yield_factor"),
        ("yield_or_coverage", "median_foam_yield"),
        ("unit_price", "current_unit_price"),
    ):
        value = decision.get(source_field)
        if value not in (None, ""):
            foam_row[target_field] = value
    decision_basis = safe_number(decision.get("basis_sqft"), 0.0)
    foam_row["_foam_template_basis_override"] = bool(decision_basis > 0 and previous_basis > 0 and abs(decision_basis - previous_basis) > 1e-9)
    foam_row["editable_decision_value"] = {
        "selector_code": decision.get("editable_selector_code") or decision.get("selector_code"),
        "resolved_template_option": decision.get("resolved_template_option"),
        "selected_pricing_candidate": decision.get("selected_pricing_candidate"),
        "thickness_inches": decision.get("thickness_inches"),
        "yield_or_coverage": decision.get("yield_or_coverage"),
    }


def _roofing_foam_source_row(scope: dict[str, Any], data: Any = None, filters: dict[str, Any] | None = None) -> dict[str, Any]:
    package_spec = {
        "package": "foam",
        "label": "Roofing SPF Foam",
        "keywords": ["roof foam", "roofing foam", "spray foam", "spf", "gaco roof", "basf roof", "2.7"],
        "default_unit": "set",
    }
    pricing_options = _pricing_options_for_package(_frame(data, "pricing_catalog"), package_spec, scope) if data is not None else []
    historical_options = _historical_item_options(data, "foam", filters or historical_filters_from_scope(scope), "set") if data is not None else []
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for option in [*pricing_options, *historical_options]:
        key = _normalized(option.get("item_name"))
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(dict(option))
    selected_name = first_nonblank(
        next((option.get("item_name") for option in historical_options if _is_roofing_foam_candidate(option)), ""),
        next((option.get("item_name") for option in pricing_options if _is_roofing_foam_candidate(option)), ""),
        next((option.get("item_name") for option in merged), ""),
    )
    return {
        "package_key": "roofing_foam",
        "template_bucket": "foam",
        "item_name": selected_name,
        "item_options_json": json.dumps(merged, default=str),
        "evidence_count": max([int(safe_number(option.get("evidence_count"), 0)) for option in historical_options] or [0]),
    }


def _roofing_foam_decision_defaults(data: Any, filters: dict[str, Any] | None) -> dict[str, Any]:
    decisions = _decision_recommendation_lookup(data, filters) if data is not None else {}
    decision_id = "roofing_foam"
    return {
        "selector_code": _decision_value(decisions, decision_id, "selector_code", ""),
        "resolved_item_name": _decision_value(decisions, decision_id, "resolved_item_name", ""),
        "area_sqft": _decision_value(decisions, decision_id, "area_sqft", ""),
        "thickness_inches": _decision_value(decisions, decision_id, "thickness_inches", ""),
        "unit_price": _decision_value(decisions, decision_id, "unit_price", ""),
        "yield_or_coverage": _decision_value(decisions, decision_id, "yield_or_coverage", ""),
        "meta": _decision_meta(decisions, decision_id, ["selector_code", "resolved_item_name", "area_sqft", "thickness_inches", "unit_price", "yield_or_coverage"]),
    }


def _build_roofing_foam_template_decisions(
    *,
    scope: dict[str, Any],
    data: Any = None,
    existing_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    filters = historical_filters_from_scope(scope)
    defaults = _roofing_foam_decision_defaults(data, filters)
    source_row = _roofing_foam_source_row(scope, data, filters)
    existing_by_row = {
        str(row.get("workbook_row")): row
        for row in existing_rows or []
        if isinstance(row, dict) and row.get("workbook_row")
    }
    note_text = _normalized(
        " ".join(
            str(scope.get(key) or "")
            for key in (
                "notes",
                "raw_input_notes",
                "project_type",
                "recommended_scope",
                "scope_description",
            )
        )
    )
    foam_scope = _contains_any_text(note_text, ["roof foam", "roofing foam", "spf", "spray polyurethane foam", "spray foam roof", "foam roof"])
    default_area = safe_number(_estimate_area(scope), 0.0)
    historical_option = first_nonblank(
        defaults.get("resolved_item_name"),
        _resolved_roofing_foam_selector_option(defaults.get("selector_code")),
        "Gaco Roof 2.7",
    )
    historical_code = first_nonblank(
        defaults.get("selector_code"),
        _roofing_foam_selector_code_for_option(historical_option),
        "11",
    )
    meta = defaults.get("meta") if isinstance(defaults.get("meta"), dict) else {}
    rows: list[dict[str, Any]] = []
    for row_number in ROOFING_FOAM_TEMPLATE_ROWS:
        row_key = str(row_number)
        existing = existing_by_row.get(row_key, {})
        template_defaults = ROOFING_FOAM_DEFAULTS.get(row_number, {})
        selector_code = str(
            first_nonblank(
                existing.get("editable_selector_code"),
                existing.get("selector_code"),
                _roofing_foam_selector_code_for_option(existing.get("resolved_template_option")),
                historical_code,
                "11",
            )
        )
        resolved_option = _resolved_roofing_foam_selector_option(selector_code, historical_option)
        stored_candidates = _stored_candidates_from_row(existing)
        candidates = stored_candidates if data is None and stored_candidates else _roofing_foam_pricing_candidates(source_row, scope, data=data, template_option=resolved_option)
        selected_candidate = _selected_roofing_foam_candidate(
            candidates,
            first_nonblank(existing.get("selected_pricing_candidate"), source_row.get("item_name")),
        )
        include = bool(existing["include"]) if "include" in existing else bool(foam_scope and row_number == 19)
        basis_sqft = positive_number(
            existing.get("basis_sqft"),
            defaults.get("area_sqft"),
            default_area if include else "",
            template_defaults.get("area_sqft"),
            default=0.0,
        )
        thickness = positive_number(
            existing.get("thickness_inches"),
            defaults.get("thickness_inches"),
            template_defaults.get("thickness_inches"),
            default=0.0,
        )
        yield_or_coverage = positive_number(
            existing.get("yield_or_coverage"),
            defaults.get("yield_or_coverage"),
            template_defaults.get("yield_or_coverage"),
            default=0.0,
        )
        unit_price = positive_number(
            existing.get("unit_price"),
            selected_candidate.get("unit_price"),
            defaults.get("unit_price"),
            template_defaults.get("unit_price"),
            default=0.0,
        )
        formula = calculate_insulation_foam(
            area_sqft=basis_sqft,
            thickness_inches=thickness,
            yield_or_coverage=yield_or_coverage,
            unit_price=unit_price,
            include=include,
        )
        compatibility = _roofing_foam_candidate_compatibility(
            template_option=resolved_option,
            candidate=selected_candidate,
            product_context=selected_candidate,
        )
        warnings = list(
            dict.fromkeys([*(selected_candidate.get("compatibility_warnings") or []), *(compatibility.get("compatibility_warnings") or [])])
        )
        if yield_or_coverage <= 0:
            warnings.append("Yield/coverage is missing; template formula output requires estimator review.")
        rows.append(
            {
                "include": include,
                "section": "roofing_foam_template_decisions",
                "decision_id": f"roofing_foam_row_{row_number}",
                "template_bucket": "roofing_foam",
                "workbook_row": row_key,
                "selector_cell": f"A{row_number}",
                "selector_code": selector_code,
                "editable_selector_code": selector_code,
                "resolved_template_option": resolved_option,
                "selector_options": _roofing_foam_selector_options(row_number),
                "selector_options_json": json.dumps(_roofing_foam_selector_options(row_number), default=str),
                "historical_selector_recommendation": historical_option,
                "historical_selector_code": str(historical_code),
                "historical_selector_evidence_count": int(safe_number(meta.get("decision_evidence_count") or source_row.get("evidence_count"), 0)),
                "historical_selector_confidence": meta.get("decision_confidence") or ("medium" if source_row.get("evidence_count") else "none"),
                "basis_sqft": round(basis_sqft, 2),
                "thickness_inches": round(thickness, 4),
                "yield_or_coverage": round(yield_or_coverage, 4),
                "unit_price": round(unit_price, 4),
                "estimated_units": formula.get("estimated_units"),
                "estimated_sets": formula.get("estimated_sets"),
                "estimated_cost": formula.get("estimated_cost"),
                "formula_model": formula.get("formula_model"),
                "formula_source": formula.get("formula_source"),
                "selected_pricing_candidate": selected_candidate.get("item_name") or str(source_row.get("item_name") or ""),
                "selected_pricing_item_id": selected_candidate.get("pricing_item_id"),
                "pricing_candidates": candidates,
                "pricing_candidates_json": json.dumps(candidates, default=str),
                "compatibility_status": "review" if warnings and compatibility.get("compatibility_status") == "compatible" else compatibility.get("compatibility_status"),
                "compatibility_warnings": warnings,
                "product_guidance_status": "matched" if selected_candidate.get("product_id") else "missing",
                "product_id": selected_candidate.get("product_id") or "",
                "product_name": selected_candidate.get("product_name") or "",
                "product_manufacturer": selected_candidate.get("manufacturer") or "",
                "product_guidance": selected_candidate.get("product_guidance") or "",
                "product_source_documents": selected_candidate.get("product_source_documents") or [],
                "notes": (
                    "Roofing SPF template selector is the estimator decision. Pricing/product candidate is supporting context. "
                    + (" ".join(warnings) if warnings else "Current foam candidate fits the selected roofing foam option.")
                ),
                "decision_values": {
                    "selector_code": selector_code,
                    "resolved_template_option": resolved_option,
                    "selected_pricing_candidate": selected_candidate.get("item_name") or str(source_row.get("item_name") or ""),
                    "basis_sqft": round(basis_sqft, 2),
                    "thickness_inches": round(thickness, 4),
                    "yield_or_coverage": round(yield_or_coverage, 4),
                    "unit_price": round(unit_price, 4),
                },
                "editable_decision_value": {
                    "selector_code": selector_code,
                    "resolved_template_option": resolved_option,
                    "selected_pricing_candidate": selected_candidate.get("item_name") or str(source_row.get("item_name") or ""),
                    "basis_sqft": round(basis_sqft, 2),
                    "thickness_inches": round(thickness, 4),
                    "yield_or_coverage": round(yield_or_coverage, 4),
                    "unit_price": round(unit_price, 4),
                },
                "recommended_decision_value": {
                    "selector_code": str(historical_code),
                    "resolved_template_option": historical_option,
                    "evidence_count": int(safe_number(meta.get("decision_evidence_count") or source_row.get("evidence_count"), 0)),
                },
                "calculated_output": formula.get("estimated_cost"),
                "calculated_output_summary": _value_summary(
                    {
                        "units": formula.get("estimated_units"),
                        "sets": formula.get("estimated_sets"),
                        "cost": formula.get("estimated_cost"),
                    }
                ),
                "workbook_cell_write_preview": [
                    {"cell": f"Estimate!A{row_number}", "field": "selector_code", "value": selector_code},
                    {"cell": f"Estimate!C{row_number}", "field": "area_sqft", "value": round(basis_sqft, 2)},
                    {"cell": f"Estimate!D{row_number}", "field": "thickness_inches", "value": round(thickness, 4)},
                    {"cell": f"Estimate!E{row_number}", "field": "unit_price", "value": round(unit_price, 4)},
                    {"cell": f"Estimate!F{row_number}", "field": "yield_or_coverage", "value": round(yield_or_coverage, 4)},
                    {"cell": f"Estimate!G{row_number}", "field": "estimated_units_formula_output", "value": formula.get("estimated_units")},
                ],
            }
        )
    return rows


def _apply_roofing_foam_template_decisions_to_materials(workbench: dict[str, Any]) -> None:
    decisions = [
        row
        for row in workbench.get("roofing_foam_template_decisions") or []
        if isinstance(row, dict)
    ]
    if not decisions:
        return
    materials = workbench.setdefault("materials", [])
    materials[:] = [
        row
        for row in materials
        if str(row.get("package_key") or row.get("template_bucket") or "").lower() not in {"roofing_foam", "foam"}
    ]
    for decision in decisions:
        if not decision.get("include"):
            continue
        materials.append(
            {
                "include": True,
                "package": "Roofing SPF Foam",
                "package_key": "roofing_foam",
                "template_bucket": "roofing_foam",
                "workbook_row": decision.get("workbook_row"),
                "item_name": first_nonblank(decision.get("selected_pricing_candidate"), decision.get("resolved_template_option"), "Roofing SPF foam"),
                "selector_code": decision.get("editable_selector_code") or decision.get("selector_code"),
                "resolved_template_option": decision.get("resolved_template_option"),
                "editable_basis_sqft": decision.get("basis_sqft"),
                "default_basis_sqft": decision.get("basis_sqft"),
                "thickness_inches": decision.get("thickness_inches"),
                "yield_factor": decision.get("yield_or_coverage"),
                "current_unit_price": decision.get("unit_price"),
                "calculated_quantity": decision.get("estimated_units"),
                "estimated_units": decision.get("estimated_units"),
                "estimated_sets": decision.get("estimated_sets"),
                "estimated_cost": decision.get("estimated_cost"),
                "formula_model": decision.get("formula_model"),
                "formula_source": "roofing_foam_template_decisions",
                "calculated_output_summary": decision.get("calculated_output_summary"),
                "workbook_cell_write_preview": decision.get("workbook_cell_write_preview") or [],
                "evidence_count": decision.get("historical_selector_evidence_count") or 0,
                "confidence": decision.get("historical_selector_confidence") or "none",
                "notes": decision.get("notes") or "Roofing SPF foam template decision.",
            }
        )


def _coating_historical_option(coating_row: dict[str, Any] | None, scope: dict[str, Any], existing: dict[str, Any] | None = None) -> str:
    coating_row = coating_row or {}
    existing = existing or {}
    decision_values = coating_row.get("decision_values") if isinstance(coating_row.get("decision_values"), dict) else {}
    recommended = coating_row.get("recommended_decision_value")
    if isinstance(recommended, dict):
        recommended_value = first_nonblank(recommended.get("resolved_template_option"), recommended.get("selected_option"))
    else:
        recommended_value = recommended
    selector_code = first_nonblank(
        existing.get("historical_selector_code"),
        existing.get("editable_selector_code"),
        existing.get("selector_code"),
        coating_row.get("selector_code"),
        decision_values.get("selector_code"),
    )
    return str(
        first_nonblank(
            existing.get("historical_selector_recommendation"),
            existing.get("resolved_template_option"),
            _resolved_roofing_selector_option(selector_code),
            decision_values.get("selected_option"),
            recommended_value if _roofing_selector_code_for_option(recommended_value) else "",
            _resolved_roofing_selector_option(_default_roofing_selector_code_for_scope(scope)),
            "Gaco Silicone",
        )
    )


def _stored_candidates_from_row(row: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(row.get("pricing_candidates"), list):
        return [dict(item) for item in row.get("pricing_candidates") or [] if isinstance(item, dict)]
    try:
        parsed = json.loads(row.get("pricing_candidates_json") or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        parsed = []
    return [dict(item) for item in parsed if isinstance(item, dict)]


def _build_roofing_coating_template_decisions(
    *,
    scope: dict[str, Any],
    coating_row: dict[str, Any] | None,
    existing_rows: list[dict[str, Any]] | None = None,
    data: Any = None,
) -> list[dict[str, Any]]:
    if not coating_row or _is_insulation_scope(scope):
        return []

    existing_by_row = {str(row.get("workbook_row") or ""): row for row in existing_rows or [] if isinstance(row, dict)}
    coating_scope = bool(coating_row.get("include")) or bool(scope.get("coating_type")) or "coating" in _normalized(scope.get("project_type"))
    default_basis = safe_number(first_nonblank(coating_row.get("editable_basis_sqft"), coating_row.get("default_basis_sqft"), _estimate_area(scope)), 0.0)
    base_historical_option = _coating_historical_option(coating_row, scope)
    base_selector_code = first_nonblank(
        coating_row.get("selector_code"),
        _roofing_selector_code_for_option(base_historical_option),
        _default_roofing_selector_code_for_scope(scope),
    )
    decision_values = coating_row.get("decision_values") if isinstance(coating_row.get("decision_values"), dict) else {}
    material_rate = safe_number(coating_row.get("editable_qty_per_sqft"), 0.0)
    historical_rate = safe_number(coating_row.get("historical_qty_per_sqft"), 0.0)
    existing_formula_total_gallons = 0.0
    for existing_row in existing_rows or []:
        if not isinstance(existing_row, dict) or not existing_row.get("include"):
            continue
        existing_formula = calculate_roofing_coating(
            area_sqft=existing_row.get("basis_sqft"),
            gal_per_100_sqft=existing_row.get("gal_per_100_sqft"),
            waste_factor_pct=existing_row.get("waste_factor_pct"),
            include=True,
        )
        existing_formula_total_gallons += safe_number(existing_formula.get("estimated_gallons"), 0.0)
    material_total_gallons = material_rate * default_basis if material_rate > 0 and default_basis > 0 else 0.0
    material_rate_override = (
        material_rate > 0
        and historical_rate > 0
        and abs(material_rate - historical_rate) > 1e-9
        and (existing_formula_total_gallons <= 0 or abs(material_total_gallons - existing_formula_total_gallons) > 0.01)
    )
    existing_included_rows = {
        int(safe_number(row.get("workbook_row"), 0))
        for row in existing_rows or []
        if isinstance(row, dict) and row.get("include") and int(safe_number(row.get("workbook_row"), 0)) in ROOFING_COATING_TEMPLATE_ROWS
    }
    default_included_rows = existing_included_rows or ({26, 27} if coating_scope else set())
    default_include_count = max(1, len(default_included_rows))
    default_total_gal_per_100 = positive_number(
        safe_number(coating_row.get("editable_qty_per_sqft"), 0.0) * 100,
        safe_number(coating_row.get("historical_qty_per_sqft"), 0.0) * 100,
        decision_values.get("gal_per_100_sqft"),
        coating_row.get("gal_per_100_sqft"),
        default=1.0,
    )
    default_gal_per_100 = default_total_gal_per_100 / default_include_count
    default_waste = safe_number(first_nonblank(coating_row.get("waste_factor_pct"), decision_values.get("waste_factor_pct"), 0), 0.0)
    default_selected_candidate = first_nonblank(coating_row.get("item_name"), coating_row.get("current_item"))

    rows: list[dict[str, Any]] = []
    for row_number in ROOFING_COATING_TEMPLATE_ROWS:
        row_key = str(row_number)
        existing = existing_by_row.get(row_key, {})
        historical_option = _coating_historical_option(coating_row, scope, existing)
        selector_code = str(
            first_nonblank(
                existing.get("editable_selector_code"),
                existing.get("selector_code"),
                _roofing_selector_code_for_option(existing.get("resolved_template_option")),
                base_selector_code,
            )
        )
        resolved_option = _resolved_roofing_selector_option(selector_code, historical_option)
        candidates = _stored_candidates_from_row(existing)
        if not (data is None and candidates):
            candidates = _roofing_coating_pricing_candidates(coating_row, scope, data=data, template_option=resolved_option)
        selected_candidate = _selected_roofing_coating_candidate(
            candidates,
            first_nonblank(existing.get("selected_pricing_candidate"), default_selected_candidate),
        )
        unit_price = safe_number(
            first_nonblank(
                existing.get("unit_price"),
                selected_candidate.get("unit_price"),
                coating_row.get("current_unit_price"),
                coating_row.get("current_price"),
            ),
            0.0,
        )
        include = bool(existing["include"]) if "include" in existing else bool(row_number in default_included_rows)
        basis_sqft = safe_number(first_nonblank(existing.get("basis_sqft"), default_basis), 0.0)
        gal_per_100 = positive_number(
            "" if material_rate_override else existing.get("gal_per_100_sqft"),
            default_gal_per_100,
            default=0.0,
        )
        waste_pct = safe_number(first_nonblank(existing.get("waste_factor_pct"), default_waste), 0.0)
        formula = calculate_roofing_coating(
            area_sqft=basis_sqft,
            gal_per_100_sqft=gal_per_100,
            unit_price=unit_price,
            waste_factor_pct=waste_pct,
            cost_per_sqft=coating_row.get("historical_cost_per_sqft"),
            include=include,
        )
        compatibility = _roofing_coating_candidate_compatibility(
            template_option=resolved_option,
            candidate=selected_candidate,
            product_context=selected_candidate,
        )
        warnings = list(
            dict.fromkeys([*(selected_candidate.get("compatibility_warnings") or []), *(compatibility.get("compatibility_warnings") or [])])
        )
        if gal_per_100 <= 0:
            warnings.append("Gallons per 100 sqft is missing; formula output requires estimator review.")
        product_context_status = "matched" if selected_candidate.get("product_id") else "missing"
        rows.append(
            {
                "include": include,
                "section": "roofing_coating_template_decisions",
                "decision_id": f"roofing_coating_system_row_{row_number}",
                "template_bucket": "coating",
                "workbook_row": row_key,
                "selector_cell": f"A{row_number}",
                "selector_code": selector_code,
                "editable_selector_code": selector_code,
                "resolved_template_option": resolved_option,
                "selector_options": _roofing_coating_selector_options(row_number),
                "selector_options_json": json.dumps(_roofing_coating_selector_options(row_number), default=str),
                "historical_selector_recommendation": historical_option,
                "historical_selector_code": _roofing_selector_code_for_option(historical_option),
                "historical_selector_evidence_count": int(safe_number(coating_row.get("decision_evidence_count") or coating_row.get("evidence_count"), 0)),
                "historical_selector_confidence": coating_row.get("decision_confidence") or coating_row.get("confidence") or "",
                "basis_sqft": round(basis_sqft, 2),
                "gal_per_100_sqft": round(gal_per_100, 6),
                "gal_per_sqft": round(safe_number(formula.get("gal_per_sqft"), 0.0), 8),
                "waste_factor_pct": round(waste_pct, 4),
                "wet_mils_estimate": formula.get("wet_mils_estimate"),
                "unit_price": round(unit_price, 4),
                "estimated_gallons": formula.get("estimated_gallons"),
                "estimated_cost": formula.get("estimated_cost"),
                "formula_model": formula.get("formula_model"),
                "formula_source": formula.get("formula_source"),
                "selected_pricing_candidate": selected_candidate.get("item_name") or str(default_selected_candidate or ""),
                "selected_pricing_item_id": selected_candidate.get("pricing_item_id"),
                "pricing_candidates": candidates,
                "pricing_candidates_json": json.dumps(candidates, default=str),
                "compatibility_status": "review" if warnings and compatibility.get("compatibility_status") == "compatible" else compatibility.get("compatibility_status"),
                "compatibility_warnings": warnings,
                "product_guidance_status": product_context_status,
                "product_id": selected_candidate.get("product_id") or "",
                "product_name": selected_candidate.get("product_name") or "",
                "product_manufacturer": selected_candidate.get("manufacturer") or "",
                "product_guidance": selected_candidate.get("product_guidance") or "",
                "product_source_documents": selected_candidate.get("product_source_documents") or [],
                "notes": (
                    "Template selector is the estimator decision. Pricing/product candidate is supporting context. "
                    + (" ".join(warnings) if warnings else "Current coating candidate fits the selected template option.")
                ),
                "decision_values": {
                    "selector_code": selector_code,
                    "resolved_template_option": resolved_option,
                    "selected_pricing_candidate": selected_candidate.get("item_name") or str(default_selected_candidate or ""),
                    "basis_sqft": round(basis_sqft, 2),
                    "gal_per_100_sqft": round(gal_per_100, 6),
                    "waste_factor_pct": round(waste_pct, 4),
                    "unit_price": round(unit_price, 4),
                },
                "editable_decision_value": {
                    "selector_code": selector_code,
                    "resolved_template_option": resolved_option,
                    "selected_pricing_candidate": selected_candidate.get("item_name") or str(default_selected_candidate or ""),
                    "basis_sqft": round(basis_sqft, 2),
                    "gal_per_100_sqft": round(gal_per_100, 6),
                    "waste_factor_pct": round(waste_pct, 4),
                    "unit_price": round(unit_price, 4),
                },
                "recommended_decision_value": {
                    "selector_code": _roofing_selector_code_for_option(historical_option),
                    "resolved_template_option": historical_option,
                    "evidence_count": int(safe_number(coating_row.get("decision_evidence_count") or coating_row.get("evidence_count"), 0)),
                },
                "calculated_output": formula.get("estimated_cost"),
                "calculated_output_summary": _value_summary(
                    {
                        "gallons": formula.get("estimated_gallons"),
                        "wet_mils": formula.get("wet_mils_estimate"),
                        "cost": formula.get("estimated_cost"),
                    }
                ),
                "workbook_cell_write_preview": [
                    {"cell": f"Estimate!A{row_number}", "field": "selector_code", "value": selector_code},
                    {"cell": f"Estimate!C{row_number}", "field": "area_sqft", "value": round(basis_sqft, 2)},
                    {"cell": f"Estimate!D{row_number}", "field": "gal_per_100_sqft", "value": round(gal_per_100, 6)},
                    {"cell": f"Estimate!E{row_number}", "field": "unit_price", "value": round(unit_price, 4)},
                    {"cell": "Estimate!A30", "field": "waste_factor_pct", "value": round(waste_pct, 4)},
                    {"cell": f"Estimate!G{row_number}", "field": "estimated_gallons_formula_output", "value": formula.get("estimated_gallons")},
                ],
            }
        )
    return rows


def _apply_roofing_coating_template_decisions_to_materials(workbench: dict[str, Any]) -> None:
    coating_row = _coating_material_row(workbench.get("materials"))
    decisions = [row for row in workbench.get("roofing_coating_template_decisions") or [] if isinstance(row, dict)]
    if not coating_row or not decisions:
        return
    included = [row for row in decisions if row.get("include")]
    primary = included[0] if included else decisions[0]
    total_gallons = sum(safe_number(row.get("estimated_gallons"), 0.0) for row in included)
    total_cost = sum(safe_number(row.get("estimated_cost"), 0.0) for row in included)
    basis = safe_number(primary.get("basis_sqft"), safe_number(coating_row.get("editable_basis_sqft"), 0.0))
    coating_row["include"] = bool(included)
    coating_row["selector_code"] = primary.get("editable_selector_code") or primary.get("selector_code")
    coating_row["resolved_template_option"] = primary.get("resolved_template_option")
    coating_row["template_selector_option"] = primary.get("resolved_template_option")
    if primary.get("selected_pricing_candidate"):
        coating_row["item_name"] = primary.get("selected_pricing_candidate")
        coating_row["current_item"] = primary.get("selected_pricing_candidate")
    coating_row["editable_basis_sqft"] = round(basis, 2)
    coating_row["default_basis_sqft"] = round(basis, 2)
    coating_row["estimated_gallons"] = round(total_gallons, 2)
    coating_row["calculated_quantity"] = round(total_gallons, 2)
    coating_row["estimated_cost"] = round(total_cost, 2)
    coating_row["current_unit_price"] = safe_number(primary.get("unit_price"), 0.0)
    coating_row["current_price"] = coating_row["current_unit_price"]
    coating_row["gal_per_100_sqft"] = safe_number(primary.get("gal_per_100_sqft"), 0.0)
    coating_row["gal_per_sqft"] = safe_number(primary.get("gal_per_sqft"), 0.0)
    coating_row["editable_qty_per_sqft"] = round(total_gallons / basis, 8) if basis > 0 and total_gallons > 0 else safe_number(primary.get("gal_per_sqft"), 0.0)
    coating_row["editable_default"] = coating_row["editable_qty_per_sqft"]
    coating_row["waste_factor_pct"] = safe_number(primary.get("waste_factor_pct"), 0.0)
    coating_row["wet_mils_estimate"] = safe_number(primary.get("wet_mils_estimate"), 0.0)
    coating_row["formula_model"] = primary.get("formula_model")
    coating_row["formula_source"] = "roofing_coating_template_decisions"
    coating_row["price_source"] = "current_pricing" if coating_row["current_unit_price"] > 0 else "current_pricing_missing"
    coating_row["decision_values"] = {
        "selector_code": coating_row["selector_code"],
        "resolved_template_option": coating_row.get("resolved_template_option"),
        "selected_pricing_candidate": coating_row.get("item_name"),
        "basis_sqft": round(basis, 2),
        "gal_per_100_sqft": coating_row["gal_per_100_sqft"],
        "waste_factor_pct": coating_row["waste_factor_pct"],
        "estimated_gallons": round(total_gallons, 2),
        "estimated_cost": round(total_cost, 2),
    }
    coating_row["editable_decision_value"] = dict(coating_row["decision_values"])
    coating_row["calculated_output"] = coating_row["estimated_cost"]
    coating_row["calculated_output_summary"] = _value_summary(
        {"gallons": round(total_gallons, 2), "cost": round(total_cost, 2), "rows": len(included)}
    )
    coating_row["workbook_cell_write_preview"] = [
        write for decision in included for write in (decision.get("workbook_cell_write_preview") or [])
    ]
    coating_row["notes"] = (
        f"Synced from {len(included)} included roof coating template decision row(s). "
        "Template selector rows are the primary estimator-facing controls."
    )


def _primer_historical_option(primer_row: dict[str, Any] | None, scope: dict[str, Any], existing: dict[str, Any] | None = None) -> str:
    primer_row = primer_row or {}
    existing = existing or {}
    decision_values = primer_row.get("decision_values") if isinstance(primer_row.get("decision_values"), dict) else {}
    recommended = primer_row.get("recommended_decision_value")
    if isinstance(recommended, dict):
        recommended_value = first_nonblank(recommended.get("resolved_template_option"), recommended.get("selected_option"))
    else:
        recommended_value = recommended
    selector_code = first_nonblank(
        existing.get("historical_selector_code"),
        existing.get("editable_selector_code"),
        existing.get("selector_code"),
        primer_row.get("selector_code"),
        decision_values.get("selector_code"),
    )
    return str(
        first_nonblank(
            existing.get("historical_selector_recommendation"),
            existing.get("resolved_template_option"),
            _resolved_roofing_primer_option(selector_code),
            decision_values.get("selected_option"),
            recommended_value if _roofing_primer_selector_code_for_option(recommended_value) else "",
            _resolved_roofing_primer_option(_default_roofing_primer_selector_code_for_scope(scope)),
            "Gaco E-5320",
        )
    )


def _build_roofing_primer_template_decisions(
    *,
    scope: dict[str, Any],
    primer_row: dict[str, Any] | None,
    existing_rows: list[dict[str, Any]] | None = None,
    data: Any = None,
) -> list[dict[str, Any]]:
    if not primer_row or _is_insulation_scope(scope):
        return []
    existing = (existing_rows or [{}])[0] if existing_rows else {}
    historical_option = _primer_historical_option(primer_row, scope, existing)
    selector_code = str(
        first_nonblank(
            existing.get("editable_selector_code"),
            existing.get("selector_code"),
            _roofing_primer_selector_code_for_option(existing.get("resolved_template_option")),
            primer_row.get("selector_code"),
            _roofing_primer_selector_code_for_option(historical_option),
            _default_roofing_primer_selector_code_for_scope(scope),
        )
    )
    resolved_option = _resolved_roofing_primer_option(selector_code, historical_option)
    area = _estimate_area(scope)
    notes = _normalized(" ".join(str(scope.get(key) or "") for key in ("notes", "raw_input_notes", "roof_condition", "project_type")))
    explicit_include_signal = bool(
        re.search(r"\b(include|included|add|apply)\s+(?:\w+\s+){0,4}(primer|priming)\b", notes)
        or re.search(r"\b(primer|priming)\s+(?:is\s+)?included\b", notes)
    )
    default_include = bool(
        primer_row.get("include")
        or str(primer_row.get("suggested_by_notes_rules") or "").lower() == "yes"
        or explicit_include_signal
    )
    include = bool(existing["include"]) if "include" in existing else default_include
    basis_sqft = positive_number(
        existing.get("basis_sqft"),
        primer_row.get("editable_basis_sqft"),
        primer_row.get("default_basis_sqft"),
        area if include else "",
        0.0,
    )
    coverage = positive_number(
        existing.get("coverage_sqft_per_unit"),
        primer_row.get("coverage_sqft_per_unit"),
        ROOFING_PRIMER_DEFAULT_COVERAGE_SQFT_PER_UNIT,
        default=ROOFING_PRIMER_DEFAULT_COVERAGE_SQFT_PER_UNIT,
    )
    stored_candidates = _stored_candidates_from_row(existing)
    candidates = stored_candidates if data is None and stored_candidates else _roofing_primer_pricing_candidates(
        primer_row,
        scope,
        data=data,
        template_option=resolved_option,
    )
    selected_candidate = _selected_roofing_primer_candidate(
        candidates,
        first_nonblank(existing.get("selected_pricing_candidate"), primer_row.get("item_name"), primer_row.get("current_item")),
    )
    unit_price = safe_number(
        first_nonblank(
            existing.get("unit_price"),
            selected_candidate.get("unit_price"),
            primer_row.get("current_unit_price"),
            primer_row.get("current_price"),
        ),
        0.0,
    )
    formula = calculate_roofing_primer(
        area_sqft=basis_sqft,
        coverage_sqft_per_unit=coverage,
        unit_price=unit_price,
        cost_per_sqft=primer_row.get("historical_cost_per_sqft"),
        include=include,
    )
    compatibility = _roofing_primer_candidate_compatibility(
        template_option=resolved_option,
        candidate=selected_candidate,
        product_context=selected_candidate,
    )
    warnings = list(
        dict.fromkeys([*(selected_candidate.get("compatibility_warnings") or []), *(compatibility.get("compatibility_warnings") or [])])
    )
    if coverage <= 0:
        warnings.append("Primer coverage is missing; formula output requires estimator review.")
    product_context_status = "matched" if selected_candidate.get("product_id") else "missing"
    selected_name = selected_candidate.get("item_name") or str(first_nonblank(primer_row.get("item_name"), primer_row.get("current_item"), ""))
    return [
        {
            "include": include,
            "section": "roofing_primer_template_decisions",
            "decision_id": "roofing_primer_system_row_39",
            "template_bucket": "primer",
            "workbook_row": str(ROOFING_PRIMER_TEMPLATE_ROW),
            "selector_cell": "A39",
            "selector_code": selector_code,
            "editable_selector_code": selector_code,
            "resolved_template_option": resolved_option,
            "selector_options": _roofing_primer_selector_options(),
            "selector_options_json": json.dumps(_roofing_primer_selector_options(), default=str),
            "historical_selector_recommendation": historical_option,
            "historical_selector_code": _roofing_primer_selector_code_for_option(historical_option),
            "historical_selector_evidence_count": int(safe_number(primer_row.get("decision_evidence_count") or primer_row.get("evidence_count"), 0)),
            "historical_selector_confidence": primer_row.get("decision_confidence") or primer_row.get("confidence") or "",
            "basis_sqft": round(basis_sqft, 2),
            "coverage_sqft_per_unit": round(coverage, 4),
            "unit_price": round(unit_price, 4),
            "estimated_units": formula.get("estimated_units"),
            "estimated_cost": formula.get("estimated_cost"),
            "formula_model": formula.get("formula_model"),
            "formula_source": formula.get("formula_source"),
            "selected_pricing_candidate": selected_name,
            "selected_pricing_item_id": selected_candidate.get("pricing_item_id"),
            "pricing_candidates": candidates,
            "pricing_candidates_json": json.dumps(candidates, default=str),
            "compatibility_status": "review" if warnings and compatibility.get("compatibility_status") == "compatible" else compatibility.get("compatibility_status"),
            "compatibility_warnings": warnings,
            "product_guidance_status": product_context_status,
            "product_id": selected_candidate.get("product_id") or "",
            "product_name": selected_candidate.get("product_name") or "",
            "product_manufacturer": selected_candidate.get("manufacturer") or "",
            "product_guidance": selected_candidate.get("product_guidance") or "",
            "product_source_documents": selected_candidate.get("product_source_documents") or [],
            "notes": (
                "Template selector is the estimator decision. Pricing/product candidate is supporting context. "
                + (" ".join(warnings) if warnings else "Current primer candidate fits the selected template option.")
            ),
            "decision_values": {
                "selector_code": selector_code,
                "resolved_template_option": resolved_option,
                "selected_pricing_candidate": selected_name,
                "basis_sqft": round(basis_sqft, 2),
                "coverage_sqft_per_unit": round(coverage, 4),
                "unit_price": round(unit_price, 4),
            },
            "editable_decision_value": {
                "selector_code": selector_code,
                "resolved_template_option": resolved_option,
                "selected_pricing_candidate": selected_name,
                "basis_sqft": round(basis_sqft, 2),
                "coverage_sqft_per_unit": round(coverage, 4),
                "unit_price": round(unit_price, 4),
            },
            "recommended_decision_value": {
                "selector_code": _roofing_primer_selector_code_for_option(historical_option),
                "resolved_template_option": historical_option,
                "evidence_count": int(safe_number(primer_row.get("decision_evidence_count") or primer_row.get("evidence_count"), 0)),
            },
            "calculated_output": formula.get("estimated_cost"),
            "calculated_output_summary": _value_summary(
                {
                    "units": formula.get("estimated_units"),
                    "cost": formula.get("estimated_cost"),
                }
            ),
            "workbook_cell_write_preview": [
                {"cell": "Estimate!A39", "field": "selector_code", "value": selector_code},
                {"cell": "Estimate!C39", "field": "area_sqft", "value": round(basis_sqft, 2)},
                {"cell": "Estimate!E39", "field": "unit_price", "value": round(unit_price, 4)},
                {"cell": "Estimate!G39", "field": "estimated_units_formula_output", "value": formula.get("estimated_units")},
            ],
        }
    ]


def _apply_roofing_primer_template_decisions_to_materials(workbench: dict[str, Any]) -> None:
    primer_row = _primer_material_row(workbench.get("materials"))
    decisions = [row for row in workbench.get("roofing_primer_template_decisions") or [] if isinstance(row, dict)]
    if not primer_row or not decisions:
        return
    included = [row for row in decisions if row.get("include")]
    if not included:
        return
    primary = included[0] if included else decisions[0]
    basis = safe_number(primary.get("basis_sqft"), safe_number(primer_row.get("editable_basis_sqft"), 0.0))
    units = sum(safe_number(row.get("estimated_units"), 0.0) for row in included)
    cost = sum(safe_number(row.get("estimated_cost"), 0.0) for row in included)
    primer_row["include"] = bool(included)
    primer_row["selector_code"] = primary.get("editable_selector_code") or primary.get("selector_code")
    primer_row["resolved_template_option"] = primary.get("resolved_template_option")
    primer_row["template_selector_option"] = primary.get("resolved_template_option")
    if primary.get("selected_pricing_candidate"):
        primer_row["item_name"] = primary.get("selected_pricing_candidate")
        primer_row["current_item"] = primary.get("selected_pricing_candidate")
    primer_row["editable_basis_sqft"] = round(basis, 2)
    primer_row["default_basis_sqft"] = round(basis, 2)
    primer_row["coverage_sqft_per_unit"] = safe_number(primary.get("coverage_sqft_per_unit"), ROOFING_PRIMER_DEFAULT_COVERAGE_SQFT_PER_UNIT)
    primer_row["estimated_units"] = round(units, 2)
    primer_row["calculated_quantity"] = round(units, 2)
    primer_row["estimated_cost"] = round(cost, 2)
    primer_row["current_unit_price"] = safe_number(primary.get("unit_price"), 0.0)
    primer_row["current_price"] = primer_row["current_unit_price"]
    primer_row["editable_qty_per_sqft"] = round(units / basis, 8) if basis > 0 and units > 0 else 0.0
    primer_row["editable_default"] = primer_row["editable_qty_per_sqft"]
    primer_row["unit"] = "unit"
    primer_row["formula_model"] = primary.get("formula_model")
    primer_row["formula_source"] = "roofing_primer_template_decisions"
    primer_row["price_source"] = "current_pricing" if primer_row["current_unit_price"] > 0 else "current_pricing_missing"
    primer_row["decision_values"] = {
        "selector_code": primer_row["selector_code"],
        "resolved_template_option": primer_row.get("resolved_template_option"),
        "selected_pricing_candidate": primer_row.get("item_name"),
        "basis_sqft": round(basis, 2),
        "coverage_sqft_per_unit": primer_row["coverage_sqft_per_unit"],
        "estimated_units": round(units, 2),
        "estimated_cost": round(cost, 2),
    }
    primer_row["editable_decision_value"] = dict(primer_row["decision_values"])
    primer_row["calculated_output"] = primer_row["estimated_cost"]
    primer_row["calculated_output_summary"] = _value_summary({"units": round(units, 2), "cost": round(cost, 2), "rows": len(included)})
    primer_row["workbook_cell_write_preview"] = [
        write for decision in included for write in (decision.get("workbook_cell_write_preview") or [])
    ]
    primer_row["notes"] = (
        f"Synced from {len(included)} included roofing primer template decision row(s). "
        "Template selector row is the primary estimator-facing control."
    )


def _build_roofing_detail_template_decisions(
    *,
    scope: dict[str, Any],
    caulk_row: dict[str, Any] | None,
    fabric_row: dict[str, Any] | None,
    existing_rows: list[dict[str, Any]] | None = None,
    data: Any = None,
) -> list[dict[str, Any]]:
    if _is_insulation_scope(scope):
        return []
    existing_by_row = {str(row.get("workbook_row") or ""): row for row in existing_rows or [] if isinstance(row, dict)}
    notes = _normalized(" ".join(str(scope.get(key) or "") for key in ("notes", "raw_input_notes", "roof_condition", "project_type")))
    detail_signal = _has_positive_note_signal(
        notes,
        [
            "open seam",
            "open seams",
            "seam repair",
            "failed seam",
            "separate",
            "separating",
            "curb",
            "penetration",
            "pipe boot",
            "pitch pocket",
            "detail",
            "caulk",
            "sealant",
            "fabric",
            "reinforce",
        ],
    )
    caulk_signal = detail_signal or bool((caulk_row or {}).get("include"))
    fabric_signal = _has_positive_note_signal(notes, ["fabric", "reinforce", "reinforcement", "open seam", "open seams", "seam repair"])
    rows: list[dict[str, Any]] = []

    for row_number in ROOFING_CAULK_TEMPLATE_ROWS:
        row_key = str(row_number)
        existing = existing_by_row.get(row_key, {})
        default_include = bool(caulk_signal and row_number == ROOFING_CAULK_TEMPLATE_ROWS[0])
        include = bool(existing["include"]) if "include" in existing else default_include
        selector_code = str(
            first_nonblank(
                existing.get("editable_selector_code"),
                existing.get("selector_code"),
                _roofing_caulk_selector_code_for_option(existing.get("resolved_template_option")),
                (caulk_row or {}).get("selector_code"),
                _default_roofing_caulk_selector_code_for_scope(scope),
            )
        )
        resolved_option = _resolved_roofing_caulk_option(selector_code, "Silicone Sausage")
        stored_candidates = _stored_candidates_from_row(existing)
        candidates = stored_candidates if data is None and stored_candidates else _roofing_detail_pricing_candidates(
            caulk_row or {},
            scope,
            package="caulk_detail",
            decision_id="roofing_caulk_sealant",
            data=data,
            template_option=resolved_option,
        )
        selected_candidate = _selected_roofing_detail_candidate(
            candidates,
            first_nonblank(existing.get("selected_pricing_candidate"), (caulk_row or {}).get("item_name"), (caulk_row or {}).get("current_item")),
        )
        unit_price = safe_number(
            first_nonblank(
                existing.get("unit_price"),
                selected_candidate.get("unit_price"),
                (caulk_row or {}).get("current_unit_price"),
                (caulk_row or {}).get("current_price"),
            ),
            0.0,
        )
        units = positive_number(
            existing.get("units"),
            existing.get("estimated_units"),
            existing.get("calculated_quantity"),
            (caulk_row or {}).get("calculated_quantity") if include else "",
            default=0.0,
        )
        formula = calculate_roofing_units_cost(
            units=units,
            unit_price=unit_price,
            include=include,
            formula_model="sealant_units_cost_from_template_inputs",
        )
        compatibility = _roofing_detail_candidate_compatibility(
            package="caulk_detail",
            template_option=resolved_option,
            candidate=selected_candidate,
            product_context=selected_candidate,
        )
        warnings = list(
            dict.fromkeys([*(selected_candidate.get("compatibility_warnings") or []), *(compatibility.get("compatibility_warnings") or [])])
        )
        if include and units <= 0:
            warnings.append("Sealant units are missing; formula output requires estimator review.")
        selected_name = selected_candidate.get("item_name") or str(first_nonblank((caulk_row or {}).get("item_name"), (caulk_row or {}).get("current_item"), ""))
        rows.append(
            {
                "include": include,
                "section": "roofing_detail_template_decisions",
                "decision_id": f"roofing_caulk_sealant_row_{row_number}",
                "template_bucket": "caulk_detail",
                "workbook_row": row_key,
                "selector_cell": f"A{row_number}",
                "selector_code": selector_code,
                "editable_selector_code": selector_code,
                "resolved_template_option": resolved_option,
                "selector_options": _roofing_caulk_selector_options(row_number),
                "selector_options_json": json.dumps(_roofing_caulk_selector_options(row_number), default=str),
                "historical_selector_recommendation": first_nonblank((caulk_row or {}).get("recommended_decision_value"), resolved_option),
                "historical_selector_code": _roofing_caulk_selector_code_for_option(first_nonblank((caulk_row or {}).get("recommended_decision_value"), resolved_option)),
                "historical_selector_evidence_count": int(safe_number((caulk_row or {}).get("decision_evidence_count") or (caulk_row or {}).get("evidence_count"), 0)),
                "historical_selector_confidence": (caulk_row or {}).get("decision_confidence") or (caulk_row or {}).get("confidence") or "",
                "units": round(units, 4),
                "estimated_units": formula.get("units"),
                "unit_price": round(unit_price, 4),
                "estimated_cost": formula.get("estimated_cost"),
                "formula_model": formula.get("formula_model"),
                "formula_source": formula.get("formula_source"),
                "selected_pricing_candidate": selected_name,
                "selected_pricing_item_id": selected_candidate.get("pricing_item_id"),
                "pricing_candidates": candidates,
                "pricing_candidates_json": json.dumps(candidates, default=str),
                "compatibility_status": "review" if warnings and compatibility.get("compatibility_status") == "compatible" else compatibility.get("compatibility_status"),
                "compatibility_warnings": warnings,
                "product_guidance_status": "matched" if selected_candidate.get("product_id") else "missing",
                "product_id": selected_candidate.get("product_id") or "",
                "product_name": selected_candidate.get("product_name") or "",
                "product_manufacturer": selected_candidate.get("manufacturer") or "",
                "product_guidance": selected_candidate.get("product_guidance") or "",
                "product_source_documents": selected_candidate.get("product_source_documents") or [],
                "notes": (
                    "Template selector is the estimator decision. Pricing/product candidate is supporting context. "
                    + (" ".join(warnings) if warnings else "Current sealant candidate fits the selected template option.")
                ),
                "decision_values": {
                    "selector_code": selector_code,
                    "resolved_template_option": resolved_option,
                    "selected_pricing_candidate": selected_name,
                    "units": round(units, 4),
                    "unit_price": round(unit_price, 4),
                },
                "editable_decision_value": {
                    "selector_code": selector_code,
                    "resolved_template_option": resolved_option,
                    "selected_pricing_candidate": selected_name,
                    "units": round(units, 4),
                    "unit_price": round(unit_price, 4),
                },
                "recommended_decision_value": {
                    "selector_code": _roofing_caulk_selector_code_for_option(first_nonblank((caulk_row or {}).get("recommended_decision_value"), resolved_option)),
                    "resolved_template_option": first_nonblank((caulk_row or {}).get("recommended_decision_value"), resolved_option),
                    "evidence_count": int(safe_number((caulk_row or {}).get("decision_evidence_count") or (caulk_row or {}).get("evidence_count"), 0)),
                },
                "calculated_output": formula.get("estimated_cost"),
                "calculated_output_summary": _value_summary({"units": formula.get("units"), "cost": formula.get("estimated_cost")}),
                "workbook_cell_write_preview": [
                    {"cell": f"Estimate!A{row_number}", "field": "selector_code", "value": selector_code},
                    {"cell": f"Estimate!E{row_number}", "field": "unit_price", "value": round(unit_price, 4)},
                    {"cell": f"Estimate!G{row_number}", "field": "units", "value": formula.get("units")},
                ],
            }
        )

    existing = existing_by_row.get(str(ROOFING_FABRIC_TEMPLATE_ROW), {})
    fabric_include = bool(existing["include"]) if "include" in existing else bool(fabric_signal or (fabric_row or {}).get("include"))
    stored_candidates = _stored_candidates_from_row(existing)
    candidates = stored_candidates if data is None and stored_candidates else _roofing_detail_pricing_candidates(
        fabric_row or {},
        scope,
        package="fabric",
        decision_id="roofing_fabric",
        data=data,
        template_option="Fabric",
    )
    selected_candidate = _selected_roofing_detail_candidate(
        candidates,
        first_nonblank(existing.get("selected_pricing_candidate"), (fabric_row or {}).get("item_name"), (fabric_row or {}).get("current_item")),
    )
    unit_price = safe_number(
        first_nonblank(
            existing.get("unit_price"),
            selected_candidate.get("unit_price"),
            (fabric_row or {}).get("current_unit_price"),
            (fabric_row or {}).get("current_price"),
        ),
        0.0,
    )
    linear_ft = positive_number(
        existing.get("linear_ft"),
        existing.get("units"),
        existing.get("estimated_units"),
        existing.get("calculated_quantity"),
        (fabric_row or {}).get("calculated_quantity") if fabric_include else "",
        default=0.0,
    )
    formula = calculate_roofing_fabric(linear_ft=linear_ft, unit_price=unit_price, include=fabric_include)
    compatibility = _roofing_detail_candidate_compatibility(
        package="fabric",
        template_option="Fabric",
        candidate=selected_candidate,
        product_context=selected_candidate,
    )
    warnings = list(
        dict.fromkeys([*(selected_candidate.get("compatibility_warnings") or []), *(compatibility.get("compatibility_warnings") or [])])
    )
    if fabric_include and linear_ft <= 0:
        warnings.append("Fabric linear feet are missing; formula output requires estimator review.")
    selected_name = selected_candidate.get("item_name") or str(first_nonblank((fabric_row or {}).get("item_name"), (fabric_row or {}).get("current_item"), ""))
    rows.append(
        {
            "include": fabric_include,
            "section": "roofing_detail_template_decisions",
            "decision_id": "roofing_fabric_row_79",
            "template_bucket": "fabric",
            "workbook_row": str(ROOFING_FABRIC_TEMPLATE_ROW),
            "selector_cell": "",
            "selector_code": "",
            "editable_selector_code": "",
            "resolved_template_option": "Fabric",
            "selector_options": [],
            "selector_options_json": "[]",
            "historical_selector_recommendation": first_nonblank((fabric_row or {}).get("recommended_decision_value"), "Fabric"),
            "historical_selector_code": "",
            "historical_selector_evidence_count": int(safe_number((fabric_row or {}).get("decision_evidence_count") or (fabric_row or {}).get("evidence_count"), 0)),
            "historical_selector_confidence": (fabric_row or {}).get("decision_confidence") or (fabric_row or {}).get("confidence") or "",
            "linear_ft": round(linear_ft, 4),
            "units": round(linear_ft, 4),
            "estimated_units": formula.get("units"),
            "unit_price": round(unit_price, 4),
            "estimated_cost": formula.get("estimated_cost"),
            "formula_model": formula.get("formula_model"),
            "formula_source": formula.get("formula_source"),
            "selected_pricing_candidate": selected_name,
            "selected_pricing_item_id": selected_candidate.get("pricing_item_id"),
            "pricing_candidates": candidates,
            "pricing_candidates_json": json.dumps(candidates, default=str),
            "compatibility_status": "review" if warnings and compatibility.get("compatibility_status") == "compatible" else compatibility.get("compatibility_status"),
            "compatibility_warnings": warnings,
            "product_guidance_status": "matched" if selected_candidate.get("product_id") else "missing",
            "product_id": selected_candidate.get("product_id") or "",
            "product_name": selected_candidate.get("product_name") or "",
            "product_manufacturer": selected_candidate.get("manufacturer") or "",
            "product_guidance": selected_candidate.get("product_guidance") or "",
            "product_source_documents": selected_candidate.get("product_source_documents") or [],
            "notes": (
                "Fabric linear feet are the estimator input. Pricing/product candidate is supporting context. "
                + (" ".join(warnings) if warnings else "Current fabric candidate fits the selected template row.")
            ),
            "decision_values": {
                "resolved_template_option": "Fabric",
                "selected_pricing_candidate": selected_name,
                "linear_ft": round(linear_ft, 4),
                "unit_price": round(unit_price, 4),
            },
            "editable_decision_value": {
                "resolved_template_option": "Fabric",
                "selected_pricing_candidate": selected_name,
                "linear_ft": round(linear_ft, 4),
                "unit_price": round(unit_price, 4),
            },
            "recommended_decision_value": {
                "resolved_template_option": first_nonblank((fabric_row or {}).get("recommended_decision_value"), "Fabric"),
                "evidence_count": int(safe_number((fabric_row or {}).get("decision_evidence_count") or (fabric_row or {}).get("evidence_count"), 0)),
            },
            "calculated_output": formula.get("estimated_cost"),
            "calculated_output_summary": _value_summary({"linear_ft": formula.get("linear_ft"), "cost": formula.get("estimated_cost")}),
            "workbook_cell_write_preview": [
                {"cell": f"Estimate!C{ROOFING_FABRIC_TEMPLATE_ROW}", "field": "linear_ft", "value": formula.get("linear_ft")},
                {"cell": f"Estimate!E{ROOFING_FABRIC_TEMPLATE_ROW}", "field": "unit_price", "value": round(unit_price, 4)},
            ],
        }
    )
    return rows


def _apply_roofing_detail_template_decisions_to_materials(workbench: dict[str, Any]) -> None:
    decisions = [row for row in workbench.get("roofing_detail_template_decisions") or [] if isinstance(row, dict) and row.get("include")]
    if not decisions:
        return
    caulk_rows = [row for row in decisions if str(row.get("template_bucket") or "") in {"caulk_detail", "caulk_sealant"}]
    fabric_rows = [row for row in decisions if str(row.get("template_bucket") or "") == "fabric"]
    caulk_material = _caulk_detail_material_row(workbench.get("materials"))
    if caulk_material and caulk_rows:
        primary = caulk_rows[0]
        units = sum(safe_number(row.get("estimated_units") or row.get("units"), 0.0) for row in caulk_rows)
        cost = sum(safe_number(row.get("estimated_cost"), 0.0) for row in caulk_rows)
        area = _estimate_area(workbench.get("scope") or {})
        caulk_material["include"] = True
        caulk_material["selector_code"] = primary.get("editable_selector_code") or primary.get("selector_code")
        caulk_material["resolved_template_option"] = primary.get("resolved_template_option")
        caulk_material["template_selector_option"] = primary.get("resolved_template_option")
        caulk_material["item_name"] = first_nonblank(primary.get("selected_pricing_candidate"), caulk_material.get("item_name"), caulk_material.get("current_item"))
        caulk_material["current_item"] = caulk_material["item_name"]
        caulk_material["estimated_units"] = round(units, 2)
        caulk_material["calculated_quantity"] = round(units, 2)
        caulk_material["estimated_cost"] = round(cost, 2)
        caulk_material["current_unit_price"] = safe_number(primary.get("unit_price"), 0.0)
        caulk_material["current_price"] = caulk_material["current_unit_price"]
        caulk_material["editable_basis_sqft"] = round(area, 2) if area else safe_number(caulk_material.get("editable_basis_sqft"), 0.0)
        caulk_material["editable_qty_per_sqft"] = round(units / area, 8) if area > 0 and units > 0 else 0.0
        caulk_material["editable_default"] = caulk_material["editable_qty_per_sqft"]
        caulk_material["unit"] = primary.get("unit") or "unit"
        caulk_material["formula_model"] = primary.get("formula_model")
        caulk_material["formula_source"] = "roofing_detail_template_decisions"
        caulk_material["price_source"] = "current_pricing" if caulk_material["current_unit_price"] > 0 else "current_pricing_missing"
        caulk_material["decision_values"] = {
            "selector_code": caulk_material["selector_code"],
            "resolved_template_option": caulk_material.get("resolved_template_option"),
            "selected_pricing_candidate": caulk_material.get("item_name"),
            "units": round(units, 2),
            "estimated_cost": round(cost, 2),
        }
        caulk_material["editable_decision_value"] = dict(caulk_material["decision_values"])
        caulk_material["calculated_output"] = caulk_material["estimated_cost"]
        caulk_material["calculated_output_summary"] = _value_summary({"units": round(units, 2), "cost": round(cost, 2), "rows": len(caulk_rows)})
        caulk_material["workbook_cell_write_preview"] = [
            write for decision in caulk_rows for write in (decision.get("workbook_cell_write_preview") or [])
        ]
        caulk_material["notes"] = "Synced from included roofing caulk/sealant template decision row(s)."

    fabric_material = _fabric_material_row(workbench.get("materials"))
    if fabric_material and fabric_rows:
        primary = fabric_rows[0]
        linear_ft = sum(safe_number(row.get("linear_ft") or row.get("estimated_units") or row.get("units"), 0.0) for row in fabric_rows)
        cost = sum(safe_number(row.get("estimated_cost"), 0.0) for row in fabric_rows)
        area = _estimate_area(workbench.get("scope") or {})
        fabric_material["include"] = True
        fabric_material["item_name"] = first_nonblank(primary.get("selected_pricing_candidate"), fabric_material.get("item_name"), fabric_material.get("current_item"))
        fabric_material["current_item"] = fabric_material["item_name"]
        fabric_material["linear_ft"] = round(linear_ft, 2)
        fabric_material["estimated_units"] = round(linear_ft, 2)
        fabric_material["calculated_quantity"] = round(linear_ft, 2)
        fabric_material["estimated_cost"] = round(cost, 2)
        fabric_material["current_unit_price"] = safe_number(primary.get("unit_price"), 0.0)
        fabric_material["current_price"] = fabric_material["current_unit_price"]
        fabric_material["editable_basis_sqft"] = round(area, 2) if area else safe_number(fabric_material.get("editable_basis_sqft"), 0.0)
        fabric_material["editable_qty_per_sqft"] = round(linear_ft / area, 8) if area > 0 and linear_ft > 0 else 0.0
        fabric_material["editable_default"] = fabric_material["editable_qty_per_sqft"]
        fabric_material["unit"] = primary.get("unit") or "lf"
        fabric_material["formula_model"] = primary.get("formula_model")
        fabric_material["formula_source"] = "roofing_detail_template_decisions"
        fabric_material["price_source"] = "current_pricing" if fabric_material["current_unit_price"] > 0 else "current_pricing_missing"
        fabric_material["decision_values"] = {
            "selected_pricing_candidate": fabric_material.get("item_name"),
            "linear_ft": round(linear_ft, 2),
            "estimated_cost": round(cost, 2),
        }
        fabric_material["editable_decision_value"] = dict(fabric_material["decision_values"])
        fabric_material["calculated_output"] = fabric_material["estimated_cost"]
        fabric_material["calculated_output_summary"] = _value_summary({"linear_ft": round(linear_ft, 2), "cost": round(cost, 2), "rows": len(fabric_rows)})
        fabric_material["workbook_cell_write_preview"] = [
            write for decision in fabric_rows for write in (decision.get("workbook_cell_write_preview") or [])
        ]
        fabric_material["notes"] = "Synced from included roofing fabric template decision row(s)."


def _build_roofing_detail_quantity_template_decisions(
    *,
    scope: dict[str, Any],
    materials: list[dict[str, Any]] | None = None,
    existing_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if _is_insulation_scope(scope):
        return []
    materials = materials or []
    existing_by_row = {str(row.get("workbook_row") or ""): row for row in existing_rows or [] if isinstance(row, dict)}
    material_by_key = {str(row.get("package_key") or row.get("template_bucket") or "").lower(): row for row in materials if isinstance(row, dict)}
    notes = _normalized(
        " ".join(
            str(scope.get(key) or "")
            for key in ("notes", "raw_input_notes", "scope_of_work", "project_type", "penetrations_complexity")
        )
    )
    rows: list[dict[str, Any]] = []
    for spec in ROOFING_DETAIL_QUANTITY_TEMPLATE_SPECS:
        row_number = int(spec["row"])
        row_key = str(row_number)
        bucket = str(spec["bucket"])
        quantity_field = str(spec["quantity_field"])
        existing = existing_by_row.get(row_key, {})
        related_material = {}
        for material_key in spec.get("material_keys") or []:
            related_material = material_by_key.get(str(material_key)) or {}
            if related_material:
                break
        signal = bool((related_material or {}).get("include") or _has_positive_note_signal(notes, spec.get("signals") or []))
        include = bool(existing["include"]) if "include" in existing else signal
        if quantity_field == "linear_ft":
            quantity = positive_number(
                existing.get("linear_ft"),
                existing.get("units"),
                existing.get("estimated_units"),
                existing.get("calculated_quantity"),
                related_material.get("calculated_quantity"),
                default=0.0,
            )
        else:
            quantity = positive_number(
                existing.get("units"),
                existing.get("estimated_units"),
                existing.get("linear_ft"),
                existing.get("calculated_quantity"),
                related_material.get("calculated_quantity"),
                default=0.0,
            )
        amount = safe_number(first_nonblank(existing.get("amount"), existing.get("estimated_cost")), 0.0)
        formula = calculate_roofing_detail_quantity(
            quantity=quantity,
            amount=amount,
            include=include,
            quantity_role=quantity_field,
        )
        warnings = []
        if include and quantity <= 0:
            warnings.append("Detail quantity is missing.")
        rows.append(
            {
                "include": include,
                "section": "roofing_detail_quantity_template_decisions",
                "decision_id": f"roofing_{bucket}_row_{row_number}",
                "template_bucket": bucket,
                "workbook_row": row_key,
                "resolved_template_option": spec["label"],
                "linear_ft": formula.get("linear_ft"),
                "units": formula.get("units"),
                "estimated_units": formula.get("estimated_units"),
                "amount": round(amount, 2),
                "estimated_cost": formula.get("estimated_cost"),
                "formula_model": formula.get("formula_model"),
                "formula_source": formula.get("formula_source"),
                "compatibility_status": "review" if warnings else "compatible",
                "compatibility_warnings": warnings,
                "notes": f"{spec['label']} preserves the workbook detail quantity input for row {row_number}."
                + (" " + " ".join(warnings) if warnings else ""),
                "decision_values": {
                    quantity_field: round(quantity, 4),
                    "amount": round(amount, 2),
                },
                "editable_decision_value": {
                    quantity_field: round(quantity, 4),
                    "amount": round(amount, 2),
                },
                "recommended_decision_value": {"resolved_template_option": spec["label"]},
                "calculated_output": formula.get("estimated_cost"),
                "calculated_output_summary": _value_summary(
                    {
                        quantity_field: round(quantity, 4),
                        "cost": formula.get("estimated_cost"),
                    }
                ),
                "workbook_cell_write_preview": [
                    {
                        "cell": f"Estimate!{spec['write_cell']}{row_number}",
                        "field": quantity_field,
                        "value": round(quantity, 4),
                    },
                    {"cell": f"Estimate!H{row_number}", "field": "estimated_cost", "value": formula.get("estimated_cost")},
                ],
            }
        )
    return rows


def _apply_roofing_detail_quantity_template_decisions_to_materials(workbench: dict[str, Any]) -> None:
    decisions = [
        row
        for row in workbench.get("roofing_detail_quantity_template_decisions") or []
        if isinstance(row, dict) and row.get("include")
    ]
    if not decisions:
        return
    materials = workbench.setdefault("materials", [])
    by_key = {str(row.get("package_key") or row.get("template_bucket") or "").lower(): row for row in materials if isinstance(row, dict)}
    for decision in decisions:
        key = str(decision.get("template_bucket") or "").lower()
        material = by_key.get(key)
        if not material:
            material = {
                "package": decision.get("resolved_template_option") or key,
                "package_key": key,
                "template_bucket": key,
                "workbook_row": decision.get("workbook_row"),
                "historical_qty_per_sqft": 0.0,
                "editable_qty_per_sqft": 0.0,
                "historical_cost_per_sqft": 0.0,
                "evidence_count": 0,
                "confidence": "review",
                "source": "roofing_detail_quantity_template_decisions",
            }
            materials.append(material)
            by_key[key] = material
        quantity = safe_number(first_nonblank(decision.get("linear_ft"), decision.get("units"), decision.get("estimated_units")), 0.0)
        material["include"] = True
        material["item_name"] = decision.get("resolved_template_option")
        material["current_item"] = decision.get("resolved_template_option")
        material["workbook_row"] = decision.get("workbook_row")
        material["calculated_quantity"] = quantity
        material["estimated_units"] = quantity
        material["linear_ft"] = safe_number(decision.get("linear_ft"), 0.0)
        material["amount"] = safe_number(decision.get("amount"), 0.0)
        material["estimated_cost"] = safe_number(decision.get("estimated_cost"), 0.0)
        material["formula_model"] = decision.get("formula_model")
        material["formula_source"] = "roofing_detail_quantity_template_decisions"
        material["unit"] = "lf" if material["linear_ft"] else "unit"
        material["decision_values"] = decision.get("decision_values")
        material["editable_decision_value"] = decision.get("editable_decision_value")
        material["calculated_output"] = material["estimated_cost"]
        material["calculated_output_summary"] = decision.get("calculated_output_summary")
        material["workbook_cell_write_preview"] = decision.get("workbook_cell_write_preview") or []
        material["notes"] = f"Synced from roofing detail quantity template decision row {decision.get('workbook_row')}."


def _build_roofing_board_fastener_template_decisions(
    *,
    scope: dict[str, Any],
    board_row: dict[str, Any] | None,
    fastener_row: dict[str, Any] | None,
    plates_row: dict[str, Any] | None,
    existing_rows: list[dict[str, Any]] | None = None,
    data: Any = None,
) -> list[dict[str, Any]]:
    if _is_insulation_scope(scope):
        return []

    board_row = board_row or {}
    fastener_row = fastener_row or {}
    plates_row = plates_row or {}
    existing_by_row = {str(row.get("workbook_row") or ""): row for row in existing_rows or [] if isinstance(row, dict)}
    notes = _normalized(
        " ".join(
            str(scope.get(key) or "")
            for key in ("notes", "raw_input_notes", "roof_condition", "project_type", "substrate", "roof_type_substrate")
        )
    )
    board_signal = bool(
        board_row.get("include")
        or _has_positive_note_signal(
            notes,
            [
                "cover board",
                "iso board",
                "dens deck",
                "deck board",
                "board stock",
                "flute filler",
                "wood fiber",
                "wet insulation",
                "damaged board",
                "replace board",
                "recover board",
                "tear off",
                "tearoff",
            ],
        )
    )
    default_selector = str(first_nonblank(board_row.get("selector_code"), _default_roofing_board_selector_code_for_scope(scope), "1"))
    default_area = positive_number(
        board_row.get("editable_basis_sqft"),
        board_row.get("default_basis_sqft"),
        _estimate_area(scope),
        default=0.0,
    )
    default_selected_candidate = first_nonblank(board_row.get("item_name"), board_row.get("current_item"))

    rows: list[dict[str, Any]] = []
    for row_number in ROOFING_BOARD_TEMPLATE_ROWS:
        row_key = str(row_number)
        existing = existing_by_row.get(row_key, {})
        include = bool(existing["include"]) if "include" in existing else bool(board_signal and row_number == ROOFING_BOARD_TEMPLATE_ROWS[0])
        selector_code = str(
            first_nonblank(
                existing.get("editable_selector_code"),
                existing.get("selector_code"),
                _roofing_board_selector_code_for_option(existing.get("resolved_template_option")),
                default_selector,
            )
        )
        resolved_option = _resolved_roofing_board_option(selector_code, _resolved_roofing_board_option(default_selector, "ISO Board"))
        candidates = _stored_candidates_from_row(existing)
        if not (data is None and candidates):
            candidates = _roofing_board_pricing_candidates(
                board_row,
                scope,
                package="board_stock",
                decision_id="roofing_board_stock",
                data=data,
                template_option=resolved_option,
            )
        selected_candidate = _selected_roofing_board_candidate(
            candidates,
            first_nonblank(existing.get("selected_pricing_candidate"), default_selected_candidate),
        )
        price_per_square = safe_number(
            first_nonblank(
                existing.get("price_per_square"),
                existing.get("unit_price"),
                selected_candidate.get("unit_price"),
                board_row.get("current_unit_price"),
                board_row.get("current_price"),
            ),
            0.0,
        )
        basis_sqft = positive_number(existing.get("basis_sqft"), board_row.get("editable_basis_sqft"), default_area if include else "", default=0.0)
        thickness = safe_number(first_nonblank(existing.get("thickness_inches"), board_row.get("thickness_inches")), 0.0)
        formula = calculate_roofing_board_stock(
            area_sqft=basis_sqft,
            thickness_inches=thickness,
            price_per_square=price_per_square,
            include=include,
        )
        compatibility = _roofing_board_candidate_compatibility(
            package="board_stock",
            template_option=resolved_option,
            candidate=selected_candidate,
            product_context=selected_candidate,
        )
        warnings = list(
            dict.fromkeys([*(selected_candidate.get("compatibility_warnings") or []), *(compatibility.get("compatibility_warnings") or [])])
        )
        if include and basis_sqft <= 0:
            warnings.append("Board area is missing; board cost formula requires estimator review.")
        selected_name = selected_candidate.get("item_name") or str(default_selected_candidate or "")
        rows.append(
            {
                "include": include,
                "section": "roofing_board_fastener_template_decisions",
                "decision_id": f"roofing_board_stock_row_{row_number}",
                "template_bucket": "board_stock",
                "workbook_row": row_key,
                "selector_cell": f"A{row_number}",
                "selector_code": selector_code,
                "editable_selector_code": selector_code,
                "resolved_template_option": resolved_option,
                "selector_options": _roofing_board_selector_options(row_number),
                "selector_options_json": json.dumps(_roofing_board_selector_options(row_number), default=str),
                "historical_selector_recommendation": _resolved_roofing_board_option(default_selector, resolved_option),
                "historical_selector_code": default_selector,
                "historical_selector_evidence_count": int(safe_number(board_row.get("decision_evidence_count") or board_row.get("evidence_count"), 0)),
                "historical_selector_confidence": board_row.get("decision_confidence") or board_row.get("confidence") or "",
                "basis_sqft": round(basis_sqft, 2),
                "thickness_inches": round(thickness, 4),
                "price_per_square": round(price_per_square, 4),
                "unit_price": round(price_per_square, 4),
                "estimated_squares": formula.get("estimated_squares"),
                "estimated_cost": formula.get("estimated_cost"),
                "formula_model": formula.get("formula_model"),
                "formula_source": formula.get("formula_source"),
                "selected_pricing_candidate": selected_name,
                "selected_pricing_item_id": selected_candidate.get("pricing_item_id"),
                "pricing_candidates": candidates,
                "pricing_candidates_json": json.dumps(candidates, default=str),
                "compatibility_status": "review" if warnings and compatibility.get("compatibility_status") == "compatible" else compatibility.get("compatibility_status"),
                "compatibility_warnings": warnings,
                "product_guidance_status": "matched" if selected_candidate.get("product_id") else "missing",
                "product_id": selected_candidate.get("product_id") or "",
                "product_name": selected_candidate.get("product_name") or "",
                "product_manufacturer": selected_candidate.get("manufacturer") or "",
                "product_guidance": selected_candidate.get("product_guidance") or "",
                "product_source_documents": selected_candidate.get("product_source_documents") or [],
                "notes": (
                    "Template selector is the estimator decision. Board area and price per square feed the workbook formula. "
                    + (" ".join(warnings) if warnings else "Current board candidate fits the selected template option.")
                ),
                "decision_values": {
                    "selector_code": selector_code,
                    "resolved_template_option": resolved_option,
                    "selected_pricing_candidate": selected_name,
                    "basis_sqft": round(basis_sqft, 2),
                    "thickness_inches": round(thickness, 4),
                    "price_per_square": round(price_per_square, 4),
                },
                "editable_decision_value": {
                    "selector_code": selector_code,
                    "resolved_template_option": resolved_option,
                    "selected_pricing_candidate": selected_name,
                    "basis_sqft": round(basis_sqft, 2),
                    "thickness_inches": round(thickness, 4),
                    "price_per_square": round(price_per_square, 4),
                },
                "recommended_decision_value": {
                    "selector_code": default_selector,
                    "resolved_template_option": _resolved_roofing_board_option(default_selector, resolved_option),
                    "evidence_count": int(safe_number(board_row.get("decision_evidence_count") or board_row.get("evidence_count"), 0)),
                },
                "calculated_output": formula.get("estimated_cost"),
                "calculated_output_summary": _value_summary({"squares": formula.get("estimated_squares"), "cost": formula.get("estimated_cost")}),
                "workbook_cell_write_preview": [
                    {"cell": f"Estimate!A{row_number}", "field": "selector_code", "value": selector_code},
                    {"cell": f"Estimate!C{row_number}", "field": "area_sqft", "value": round(basis_sqft, 2)},
                    {"cell": f"Estimate!D{row_number}", "field": "thickness_inches", "value": round(thickness, 4)},
                    {"cell": f"Estimate!E{row_number}", "field": "price_per_square", "value": round(price_per_square, 4)},
                    {"cell": f"Estimate!H{row_number}", "field": "estimated_cost_formula_output", "value": formula.get("estimated_cost")},
                ],
            }
        )

    included_board_rows = [row for row in rows if row.get("template_bucket") == "board_stock" and row.get("include")]
    primary_board_area = safe_number(included_board_rows[0].get("basis_sqft"), 0.0) if included_board_rows else 0.0
    board_area_for_fasteners = positive_number(
        *(row.get("basis_sqft") for row in included_board_rows),
        *(row.get("board_area_sqft") for row in existing_rows or [] if isinstance(row, dict)),
        default=0.0,
    )
    if board_area_for_fasteners <= 0:
        board_area_for_fasteners = primary_board_area

    for row_number, package, label, source_row, decision_id in (
        (ROOFING_FASTENER_TEMPLATE_ROW, "fasteners", "Fasteners", fastener_row, "roofing_fasteners"),
        (ROOFING_PLATES_TEMPLATE_ROW, "plates", "Plates", plates_row, "roofing_plates"),
    ):
        row_key = str(row_number)
        existing = existing_by_row.get(row_key, {})
        include = bool(existing["include"]) if "include" in existing else bool(included_board_rows)
        candidates = _stored_candidates_from_row(existing)
        if not (data is None and candidates):
            candidates = _roofing_board_pricing_candidates(
                source_row,
                scope,
                package=package,
                decision_id=decision_id,
                data=data,
                template_option=label,
            )
        selected_candidate = _selected_roofing_board_candidate(
            candidates,
            first_nonblank(existing.get("selected_pricing_candidate"), source_row.get("item_name"), source_row.get("current_item")),
        )
        unit_price = safe_number(
            first_nonblank(
                existing.get("unit_price_per_thousand"),
                existing.get("unit_price"),
                selected_candidate.get("unit_price"),
                source_row.get("current_unit_price"),
                source_row.get("current_price"),
            ),
            0.0,
        )
        board_area = positive_number(existing.get("board_area_sqft"), board_area_for_fasteners, default=0.0)
        formula = calculate_roofing_board_fasteners(
            board_area_sqft=board_area,
            unit_price_per_thousand=unit_price,
            include=include,
        )
        compatibility = _roofing_board_candidate_compatibility(
            package=package,
            template_option=label,
            candidate=selected_candidate,
            product_context=selected_candidate,
        )
        warnings = list(
            dict.fromkeys([*(selected_candidate.get("compatibility_warnings") or []), *(compatibility.get("compatibility_warnings") or [])])
        )
        if include and board_area <= 0:
            warnings.append(f"{label} board area is missing; workbook formula requires estimator review.")
        selected_name = selected_candidate.get("item_name") or str(first_nonblank(source_row.get("item_name"), source_row.get("current_item"), ""))
        rows.append(
            {
                "include": include,
                "section": "roofing_board_fastener_template_decisions",
                "decision_id": f"{decision_id}_row_{row_number}",
                "template_bucket": package,
                "workbook_row": row_key,
                "selector_cell": "",
                "selector_code": "",
                "editable_selector_code": "",
                "resolved_template_option": label,
                "selector_options": [],
                "selector_options_json": "[]",
                "historical_selector_recommendation": first_nonblank(source_row.get("recommended_decision_value"), label),
                "historical_selector_code": "",
                "historical_selector_evidence_count": int(safe_number(source_row.get("decision_evidence_count") or source_row.get("evidence_count"), 0)),
                "historical_selector_confidence": source_row.get("decision_confidence") or source_row.get("confidence") or "",
                "board_area_sqft": round(board_area, 2),
                "unit_price_per_thousand": round(unit_price, 4),
                "unit_price": round(unit_price, 4),
                "estimated_units": formula.get("estimated_units"),
                "estimated_cost": formula.get("estimated_cost"),
                "formula_model": formula.get("formula_model"),
                "formula_source": formula.get("formula_source"),
                "selected_pricing_candidate": selected_name,
                "selected_pricing_item_id": selected_candidate.get("pricing_item_id"),
                "pricing_candidates": candidates,
                "pricing_candidates_json": json.dumps(candidates, default=str),
                "compatibility_status": "review" if warnings and compatibility.get("compatibility_status") == "compatible" else compatibility.get("compatibility_status"),
                "compatibility_warnings": warnings,
                "product_guidance_status": "matched" if selected_candidate.get("product_id") else "missing",
                "product_id": selected_candidate.get("product_id") or "",
                "product_name": selected_candidate.get("product_name") or "",
                "product_manufacturer": selected_candidate.get("manufacturer") or "",
                "product_guidance": selected_candidate.get("product_guidance") or "",
                "product_source_documents": selected_candidate.get("product_source_documents") or [],
                "notes": (
                    f"{label} are calculated from the primary included board area using the workbook fastening pattern. "
                    + (" ".join(warnings) if warnings else f"Current {label.lower()} candidate fits the selected template row.")
                ),
                "decision_values": {
                    "resolved_template_option": label,
                    "selected_pricing_candidate": selected_name,
                    "board_area_sqft": round(board_area, 2),
                    "unit_price_per_thousand": round(unit_price, 4),
                },
                "editable_decision_value": {
                    "resolved_template_option": label,
                    "selected_pricing_candidate": selected_name,
                    "board_area_sqft": round(board_area, 2),
                    "unit_price_per_thousand": round(unit_price, 4),
                },
                "recommended_decision_value": {
                    "resolved_template_option": first_nonblank(source_row.get("recommended_decision_value"), label),
                    "evidence_count": int(safe_number(source_row.get("decision_evidence_count") or source_row.get("evidence_count"), 0)),
                },
                "calculated_output": formula.get("estimated_cost"),
                "calculated_output_summary": _value_summary({"units": formula.get("estimated_units"), "cost": formula.get("estimated_cost")}),
                "workbook_cell_write_preview": [
                    {"cell": f"Estimate!E{row_number}", "field": "unit_price_per_thousand", "value": round(unit_price, 4)},
                    {"cell": f"Estimate!G{row_number}", "field": "estimated_units_formula_output", "value": formula.get("estimated_units")},
                ],
            }
        )
    return rows


def _apply_roofing_board_fastener_template_decisions_to_materials(workbench: dict[str, Any]) -> None:
    decisions = [
        row
        for row in workbench.get("roofing_board_fastener_template_decisions") or []
        if isinstance(row, dict) and row.get("include")
    ]
    if not decisions:
        return
    board_rows = [row for row in decisions if str(row.get("template_bucket") or "") == "board_stock"]
    fastener_rows = [row for row in decisions if str(row.get("template_bucket") or "") == "fasteners"]
    plate_rows = [row for row in decisions if str(row.get("template_bucket") or "") == "plates"]

    board_material = _board_stock_material_row(workbench.get("materials"))
    if board_material and board_rows:
        primary = board_rows[0]
        total_area = sum(safe_number(row.get("basis_sqft"), 0.0) for row in board_rows)
        total_squares = sum(safe_number(row.get("estimated_squares"), 0.0) for row in board_rows)
        total_cost = sum(safe_number(row.get("estimated_cost"), 0.0) for row in board_rows)
        board_material["include"] = True
        board_material["selector_code"] = primary.get("editable_selector_code") or primary.get("selector_code")
        board_material["resolved_template_option"] = primary.get("resolved_template_option")
        board_material["template_selector_option"] = primary.get("resolved_template_option")
        board_material["item_name"] = first_nonblank(primary.get("selected_pricing_candidate"), board_material.get("item_name"), board_material.get("current_item"))
        board_material["current_item"] = board_material["item_name"]
        board_material["editable_basis_sqft"] = round(total_area, 2)
        board_material["default_basis_sqft"] = round(total_area, 2)
        board_material["thickness_inches"] = safe_number(primary.get("thickness_inches"), 0.0)
        board_material["estimated_squares"] = round(total_squares, 4)
        board_material["calculated_quantity"] = round(total_squares, 4)
        board_material["estimated_units"] = round(total_squares, 4)
        board_material["estimated_cost"] = round(total_cost, 2)
        board_material["current_unit_price"] = safe_number(primary.get("price_per_square") or primary.get("unit_price"), 0.0)
        board_material["current_price"] = board_material["current_unit_price"]
        board_material["editable_qty_per_sqft"] = round(total_squares / total_area, 8) if total_area > 0 and total_squares > 0 else 0.0
        board_material["editable_default"] = board_material["editable_qty_per_sqft"]
        board_material["unit"] = primary.get("unit") or "square"
        board_material["formula_model"] = primary.get("formula_model")
        board_material["formula_source"] = "roofing_board_fastener_template_decisions"
        board_material["decision_values"] = {
            "selector_code": board_material["selector_code"],
            "resolved_template_option": board_material.get("resolved_template_option"),
            "selected_pricing_candidate": board_material.get("item_name"),
            "basis_sqft": round(total_area, 2),
            "estimated_squares": round(total_squares, 4),
            "estimated_cost": round(total_cost, 2),
        }
        board_material["editable_decision_value"] = dict(board_material["decision_values"])
        board_material["calculated_output"] = board_material["estimated_cost"]
        board_material["calculated_output_summary"] = _value_summary({"squares": round(total_squares, 4), "cost": round(total_cost, 2), "rows": len(board_rows)})
        board_material["workbook_cell_write_preview"] = [write for decision in board_rows for write in (decision.get("workbook_cell_write_preview") or [])]
        board_material["notes"] = "Synced from included roofing board stock template decision row(s)."

    fastener_material = _fastener_material_row(workbench.get("materials"))
    if fastener_material and fastener_rows:
        primary = fastener_rows[0]
        units = sum(safe_number(row.get("estimated_units"), 0.0) for row in fastener_rows)
        cost = sum(safe_number(row.get("estimated_cost"), 0.0) for row in fastener_rows)
        fastener_material["include"] = True
        fastener_material["template_bucket"] = "fasteners"
        fastener_material["package_key"] = "fasteners"
        fastener_material["item_name"] = first_nonblank(primary.get("selected_pricing_candidate"), fastener_material.get("item_name"), fastener_material.get("current_item"))
        fastener_material["current_item"] = fastener_material["item_name"]
        fastener_material["calculated_quantity"] = round(units, 2)
        fastener_material["estimated_units"] = round(units, 2)
        fastener_material["estimated_cost"] = round(cost, 2)
        fastener_material["current_unit_price"] = safe_number(primary.get("unit_price_per_thousand") or primary.get("unit_price"), 0.0)
        fastener_material["current_price"] = fastener_material["current_unit_price"]
        fastener_material["unit"] = primary.get("unit") or "m"
        fastener_material["formula_model"] = primary.get("formula_model")
        fastener_material["formula_source"] = "roofing_board_fastener_template_decisions"
        fastener_material["decision_values"] = {
            "selected_pricing_candidate": fastener_material.get("item_name"),
            "board_area_sqft": primary.get("board_area_sqft"),
            "estimated_units": round(units, 2),
            "estimated_cost": round(cost, 2),
        }
        fastener_material["editable_decision_value"] = dict(fastener_material["decision_values"])
        fastener_material["calculated_output"] = fastener_material["estimated_cost"]
        fastener_material["calculated_output_summary"] = _value_summary({"units": round(units, 2), "cost": round(cost, 2)})
        fastener_material["workbook_cell_write_preview"] = [write for decision in fastener_rows for write in (decision.get("workbook_cell_write_preview") or [])]
        fastener_material["notes"] = "Synced from included roofing fastener template decision row(s)."

    plates_material = _plates_material_row(workbench.get("materials"))
    if plates_material and plate_rows:
        primary = plate_rows[0]
        units = sum(safe_number(row.get("estimated_units"), 0.0) for row in plate_rows)
        cost = sum(safe_number(row.get("estimated_cost"), 0.0) for row in plate_rows)
        plates_material["include"] = True
        plates_material["item_name"] = first_nonblank(primary.get("selected_pricing_candidate"), plates_material.get("item_name"), plates_material.get("current_item"))
        plates_material["current_item"] = plates_material["item_name"]
        plates_material["calculated_quantity"] = round(units, 2)
        plates_material["estimated_units"] = round(units, 2)
        plates_material["estimated_cost"] = round(cost, 2)
        plates_material["current_unit_price"] = safe_number(primary.get("unit_price_per_thousand") or primary.get("unit_price"), 0.0)
        plates_material["current_price"] = plates_material["current_unit_price"]
        plates_material["unit"] = primary.get("unit") or "m"
        plates_material["formula_model"] = primary.get("formula_model")
        plates_material["formula_source"] = "roofing_board_fastener_template_decisions"
        plates_material["decision_values"] = {
            "selected_pricing_candidate": plates_material.get("item_name"),
            "board_area_sqft": primary.get("board_area_sqft"),
            "estimated_units": round(units, 2),
            "estimated_cost": round(cost, 2),
        }
        plates_material["editable_decision_value"] = dict(plates_material["decision_values"])
        plates_material["calculated_output"] = plates_material["estimated_cost"]
        plates_material["calculated_output_summary"] = _value_summary({"units": round(units, 2), "cost": round(cost, 2)})
        plates_material["workbook_cell_write_preview"] = [write for decision in plate_rows for write in (decision.get("workbook_cell_write_preview") or [])]
        plates_material["notes"] = "Synced from included roofing plate template decision row(s)."


def _build_roofing_granules_template_decisions(
    *,
    scope: dict[str, Any],
    granules_row: dict[str, Any] | None,
    existing_rows: list[dict[str, Any]] | None = None,
    data: Any = None,
) -> list[dict[str, Any]]:
    if _is_insulation_scope(scope):
        return []

    granules_row = granules_row or {}
    existing = (existing_rows or [{}])[0] if existing_rows else {}
    notes = _normalized(
        " ".join(str(scope.get(key) or "") for key in ("notes", "raw_input_notes", "project_type", "coating_type", "scope_of_work"))
    )
    granules_signal = bool(
        granules_row.get("include")
        or _has_positive_note_signal(notes, ["granule", "granules", "broadcast", "mineral shield", "snow white", "walkway"])
    )
    include = bool(existing["include"]) if isinstance(existing, dict) and "include" in existing else granules_signal
    default_selector = str(first_nonblank(granules_row.get("selector_code"), _default_roofing_granules_selector_code_for_scope(scope), "1"))
    selector_code = str(
        first_nonblank(
            existing.get("editable_selector_code") if isinstance(existing, dict) else "",
            existing.get("selector_code") if isinstance(existing, dict) else "",
            _roofing_granules_selector_code_for_option(existing.get("resolved_template_option") if isinstance(existing, dict) else ""),
            default_selector,
        )
    )
    resolved_option = _resolved_roofing_granules_option(selector_code, _resolved_roofing_granules_option(default_selector, "3M"))
    default_area = positive_number(
        granules_row.get("editable_basis_sqft"),
        granules_row.get("default_basis_sqft"),
        _estimate_area(scope),
        default=0.0,
    )
    basis_sqft = positive_number(
        existing.get("basis_sqft") if isinstance(existing, dict) else None,
        granules_row.get("editable_basis_sqft"),
        default_area if include else "",
        default=0.0,
    )
    coverage = positive_number(
        existing.get("coverage_lbs_per_100_sqft") if isinstance(existing, dict) else None,
        granules_row.get("coverage_lbs_per_100_sqft"),
        ROOFING_GRANULES_DEFAULT_COVERAGE_LBS_PER_100_SQFT,
        default=ROOFING_GRANULES_DEFAULT_COVERAGE_LBS_PER_100_SQFT,
    )
    bag_weight = positive_number(
        existing.get("bag_weight_lbs") if isinstance(existing, dict) else None,
        granules_row.get("bag_weight_lbs"),
        ROOFING_GRANULES_DEFAULT_BAG_WEIGHT_LBS,
        default=ROOFING_GRANULES_DEFAULT_BAG_WEIGHT_LBS,
    )
    stored_candidates = _stored_candidates_from_row(existing) if isinstance(existing, dict) else []
    candidates = stored_candidates if data is None and stored_candidates else _roofing_granules_pricing_candidates(
        granules_row,
        scope,
        data=data,
        template_option=resolved_option,
    )
    selected_candidate = _selected_roofing_granules_candidate(
        candidates,
        first_nonblank(
            existing.get("selected_pricing_candidate") if isinstance(existing, dict) else "",
            granules_row.get("item_name"),
            granules_row.get("current_item"),
        ),
    )
    unit_price = safe_number(
        first_nonblank(
            existing.get("unit_price") if isinstance(existing, dict) else "",
            selected_candidate.get("unit_price"),
            granules_row.get("current_unit_price"),
            granules_row.get("current_price"),
        ),
        0.0,
    )
    formula = calculate_roofing_granules(
        area_sqft=basis_sqft,
        coverage_lbs_per_100_sqft=coverage,
        bag_weight_lbs=bag_weight,
        unit_price=unit_price,
        cost_per_sqft=granules_row.get("historical_cost_per_sqft"),
        include=include,
    )
    compatibility = _roofing_granules_candidate_compatibility(
        template_option=resolved_option,
        candidate=selected_candidate,
        product_context=selected_candidate,
    )
    warnings = list(
        dict.fromkeys([*(selected_candidate.get("compatibility_warnings") or []), *(compatibility.get("compatibility_warnings") or [])])
    )
    if include and basis_sqft <= 0:
        warnings.append("Granules area is missing; workbook formula requires estimator review.")
    if include and unit_price <= 0:
        warnings.append("Granules unit price is missing; cost preview is zero until pricing is selected.")
    selected_name = selected_candidate.get("item_name") or str(first_nonblank(granules_row.get("item_name"), granules_row.get("current_item"), ""))
    row = {
        "include": include,
        "section": "roofing_granules_template_decisions",
        "decision_id": f"roofing_granules_row_{ROOFING_GRANULES_TEMPLATE_ROW}",
        "template_bucket": "granules",
        "workbook_row": str(ROOFING_GRANULES_TEMPLATE_ROW),
        "selector_cell": f"A{ROOFING_GRANULES_TEMPLATE_ROW}",
        "selector_code": selector_code,
        "editable_selector_code": selector_code,
        "resolved_template_option": resolved_option,
        "selector_options": _roofing_granules_selector_options(ROOFING_GRANULES_TEMPLATE_ROW),
        "selector_options_json": json.dumps(_roofing_granules_selector_options(ROOFING_GRANULES_TEMPLATE_ROW), default=str),
        "historical_selector_recommendation": _resolved_roofing_granules_option(default_selector, resolved_option),
        "historical_selector_code": default_selector,
        "historical_selector_evidence_count": int(safe_number(granules_row.get("decision_evidence_count") or granules_row.get("evidence_count"), 0)),
        "historical_selector_confidence": granules_row.get("decision_confidence") or granules_row.get("confidence") or "",
        "basis_sqft": round(basis_sqft, 2),
        "coverage_lbs_per_100_sqft": round(coverage, 4),
        "bag_weight_lbs": round(bag_weight, 4),
        "unit_price": round(unit_price, 4),
        "estimated_units": formula.get("estimated_units"),
        "calculated_quantity": formula.get("calculated_quantity"),
        "estimated_cost": formula.get("estimated_cost"),
        "formula_model": formula.get("formula_model"),
        "formula_source": formula.get("formula_source"),
        "selected_pricing_candidate": selected_name,
        "selected_pricing_item_id": selected_candidate.get("pricing_item_id"),
        "pricing_candidates": candidates,
        "pricing_candidates_json": json.dumps(candidates, default=str),
        "compatibility_status": "review" if warnings and compatibility.get("compatibility_status") == "compatible" else compatibility.get("compatibility_status"),
        "compatibility_warnings": warnings,
        "product_guidance_status": "matched" if selected_candidate.get("product_id") else "missing",
        "product_id": selected_candidate.get("product_id") or "",
        "product_name": selected_candidate.get("product_name") or "",
        "product_manufacturer": selected_candidate.get("manufacturer") or "",
        "product_guidance": selected_candidate.get("product_guidance") or "",
        "product_source_documents": selected_candidate.get("product_source_documents") or [],
        "notes": (
            "Template selector is the estimator decision. Area, coverage rate, bag weight, and unit price feed the workbook formula. "
            + (" ".join(warnings) if warnings else "Current granules candidate fits the selected template option.")
        ),
        "decision_values": {
            "selector_code": selector_code,
            "resolved_template_option": resolved_option,
            "selected_pricing_candidate": selected_name,
            "basis_sqft": round(basis_sqft, 2),
            "coverage_lbs_per_100_sqft": round(coverage, 4),
            "bag_weight_lbs": round(bag_weight, 4),
            "unit_price": round(unit_price, 4),
        },
        "editable_decision_value": {
            "selector_code": selector_code,
            "resolved_template_option": resolved_option,
            "selected_pricing_candidate": selected_name,
            "basis_sqft": round(basis_sqft, 2),
            "coverage_lbs_per_100_sqft": round(coverage, 4),
            "bag_weight_lbs": round(bag_weight, 4),
            "unit_price": round(unit_price, 4),
        },
        "recommended_decision_value": {
            "selector_code": default_selector,
            "resolved_template_option": _resolved_roofing_granules_option(default_selector, resolved_option),
            "evidence_count": int(safe_number(granules_row.get("decision_evidence_count") or granules_row.get("evidence_count"), 0)),
        },
        "calculated_output": formula.get("estimated_cost"),
        "calculated_output_summary": _value_summary({"bags": formula.get("estimated_units"), "cost": formula.get("estimated_cost")}),
        "workbook_cell_write_preview": [
            {"cell": f"Estimate!A{ROOFING_GRANULES_TEMPLATE_ROW}", "field": "selector_code", "value": selector_code},
            {"cell": f"Estimate!C{ROOFING_GRANULES_TEMPLATE_ROW}", "field": "area_sqft", "value": round(basis_sqft, 2)},
            {"cell": f"Estimate!E{ROOFING_GRANULES_TEMPLATE_ROW}", "field": "unit_price", "value": round(unit_price, 4)},
            {"cell": f"Estimate!G{ROOFING_GRANULES_TEMPLATE_ROW}", "field": "estimated_units_formula_output", "value": formula.get("estimated_units")},
        ],
    }
    return [row]


def _apply_roofing_granules_template_decisions_to_materials(workbench: dict[str, Any]) -> None:
    decisions = [
        row
        for row in workbench.get("roofing_granules_template_decisions") or []
        if isinstance(row, dict) and row.get("include")
    ]
    if not decisions:
        return
    material = _granules_material_row(workbench.get("materials"))
    if not material:
        return
    primary = decisions[0]
    area = safe_number(primary.get("basis_sqft"), 0.0)
    units = sum(safe_number(row.get("estimated_units"), 0.0) for row in decisions)
    cost = sum(safe_number(row.get("estimated_cost"), 0.0) for row in decisions)
    material["include"] = True
    material["selector_code"] = primary.get("editable_selector_code") or primary.get("selector_code")
    material["resolved_template_option"] = primary.get("resolved_template_option")
    material["template_selector_option"] = primary.get("resolved_template_option")
    material["item_name"] = first_nonblank(primary.get("selected_pricing_candidate"), material.get("item_name"), material.get("current_item"))
    material["current_item"] = material["item_name"]
    material["editable_basis_sqft"] = round(area, 2)
    material["default_basis_sqft"] = round(area, 2)
    material["coverage_lbs_per_100_sqft"] = safe_number(primary.get("coverage_lbs_per_100_sqft"), ROOFING_GRANULES_DEFAULT_COVERAGE_LBS_PER_100_SQFT)
    material["bag_weight_lbs"] = safe_number(primary.get("bag_weight_lbs"), ROOFING_GRANULES_DEFAULT_BAG_WEIGHT_LBS)
    material["calculated_quantity"] = round(units, 4)
    material["estimated_units"] = round(units, 4)
    material["estimated_cost"] = round(cost, 2)
    material["current_unit_price"] = safe_number(primary.get("unit_price"), 0.0)
    material["current_price"] = material["current_unit_price"]
    material["editable_qty_per_sqft"] = round(units / area, 8) if area > 0 and units > 0 else 0.0
    material["editable_default"] = material["editable_qty_per_sqft"]
    material["unit"] = primary.get("unit") or "bag"
    material["formula_model"] = primary.get("formula_model")
    material["formula_source"] = "roofing_granules_template_decisions"
    material["decision_values"] = {
        "selector_code": material["selector_code"],
        "resolved_template_option": material.get("resolved_template_option"),
        "selected_pricing_candidate": material.get("item_name"),
        "basis_sqft": round(area, 2),
        "coverage_lbs_per_100_sqft": material["coverage_lbs_per_100_sqft"],
        "bag_weight_lbs": material["bag_weight_lbs"],
        "estimated_units": round(units, 4),
        "estimated_cost": round(cost, 2),
    }
    material["editable_decision_value"] = dict(material["decision_values"])
    material["calculated_output"] = material["estimated_cost"]
    material["calculated_output_summary"] = _value_summary({"bags": round(units, 4), "cost": round(cost, 2)})
    material["workbook_cell_write_preview"] = [write for decision in decisions for write in (decision.get("workbook_cell_write_preview") or [])]
    material["notes"] = "Synced from included roofing granules template decision row(s)."


def _build_roofing_equipment_template_decisions(
    *,
    scope: dict[str, Any],
    adders: list[dict[str, Any]] | None = None,
    existing_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if _is_insulation_scope(scope):
        return []

    adders = adders or []
    existing_by_row = {str(row.get("workbook_row") or ""): row for row in existing_rows or [] if isinstance(row, dict)}
    adder_by_key = {str(row.get("adder_key") or row.get("template_bucket") or "").lower(): row for row in adders if isinstance(row, dict)}
    notes = _normalized(
        " ".join(
            str(scope.get(key) or "")
            for key in ("notes", "raw_input_notes", "scope_of_work", "project_type", "roof_condition", "access_complexity")
        )
    )
    area_default = _estimate_area(scope)

    def adder_default(key: str) -> float:
        row = adder_by_key.get(key) or {}
        return safe_number(first_nonblank(row.get("editable_value"), row.get("historical_default_value"), row.get("median_cost_when_used")), 0.0)

    rows: list[dict[str, Any]] = []

    dumpster_options = _selector_options_from_roofing_graph(
        "roofing_dumpsters",
        ROOFING_DUMPSTER_SELECTOR_MAP,
        row_number=ROOFING_DUMPSTER_TEMPLATE_ROW,
    )
    existing = existing_by_row.get(str(ROOFING_DUMPSTER_TEMPLATE_ROW), {})
    dumpster_signal = bool(
        (adder_by_key.get("dumpster") or {}).get("include")
        or _has_positive_note_signal(notes, ["dumpster", "dumpsters", "tear off", "tearoff", "disposal", "remove wet", "wet insulation"])
    )
    include = bool(existing["include"]) if "include" in existing else dumpster_signal
    default_selector = _default_roofing_dumpster_selector_code_for_scope(scope)
    selector_code = str(
        first_nonblank(
            existing.get("editable_selector_code"),
            existing.get("selector_code"),
            _selector_code_for_roofing_option(existing.get("resolved_template_option"), ROOFING_DUMPSTER_SELECTOR_MAP, dumpster_options),
            default_selector,
        )
    )
    resolved_option = _resolved_roofing_equipment_option(selector_code, ROOFING_DUMPSTER_SELECTOR_MAP, dumpster_options, "40 Yard")
    basis_sqft = positive_number(existing.get("basis_sqft"), area_default if include else "", default=0.0)
    thickness = safe_number(first_nonblank(existing.get("thickness_inches"), existing.get("roof_thickness_inches")), 0.0)
    unit_price = positive_number(
        existing.get("unit_price"),
        adder_default("dumpster"),
        ROOFING_DUMPSTER_DEFAULT_UNIT_PRICE,
        default=ROOFING_DUMPSTER_DEFAULT_UNIT_PRICE,
    )
    margin_pct = safe_number(first_nonblank(existing.get("margin_pct"), ROOFING_DUMPSTER_DEFAULT_MARGIN_PCT), ROOFING_DUMPSTER_DEFAULT_MARGIN_PCT)
    formula = calculate_roofing_dumpster(
        area_sqft=basis_sqft,
        thickness_inches=thickness,
        selector_code=selector_code,
        unit_price=unit_price,
        margin_pct=margin_pct,
        include=include,
    )
    warnings = []
    if include and basis_sqft <= 0:
        warnings.append("Dumpster area is missing.")
    if include and thickness <= 0:
        warnings.append("Roof thickness is missing; dumpster count preview is zero until thickness is entered.")
    if include and unit_price <= 0:
        warnings.append("Dumpster unit price is missing.")
    rows.append(
        {
            "include": include,
            "section": "roofing_equipment_template_decisions",
            "decision_id": f"roofing_dumpsters_row_{ROOFING_DUMPSTER_TEMPLATE_ROW}",
            "template_bucket": "dumpster",
            "workbook_row": str(ROOFING_DUMPSTER_TEMPLATE_ROW),
            "selector_cell": f"A{ROOFING_DUMPSTER_TEMPLATE_ROW}",
            "selector_code": selector_code,
            "editable_selector_code": selector_code,
            "resolved_template_option": resolved_option,
            "selector_options": dumpster_options,
            "selector_options_json": json.dumps(dumpster_options, default=str),
            "historical_selector_recommendation": _resolved_roofing_equipment_option(default_selector, ROOFING_DUMPSTER_SELECTOR_MAP, dumpster_options, resolved_option),
            "historical_selector_code": default_selector,
            "historical_selector_evidence_count": int(safe_number((adder_by_key.get("dumpster") or {}).get("evidence_count"), 0)),
            "historical_selector_confidence": (adder_by_key.get("dumpster") or {}).get("confidence") or "",
            "basis_sqft": round(basis_sqft, 2),
            "thickness_inches": round(thickness, 4),
            "unit_price": round(unit_price, 4),
            "margin_pct": round(margin_pct, 4),
            "estimated_units": formula.get("estimated_units"),
            "calculated_quantity": formula.get("calculated_quantity"),
            "estimated_cost": formula.get("estimated_cost"),
            "formula_model": formula.get("formula_model"),
            "formula_source": formula.get("formula_source"),
            "selected_pricing_candidate": resolved_option,
            "compatibility_status": "review" if warnings else "compatible",
            "compatibility_warnings": warnings,
            "product_guidance": "",
            "notes": "Template selector is the estimator decision. Area, roof thickness, unit price, and margin feed the dumpster formula."
            + (" " + " ".join(warnings) if warnings else ""),
            "decision_values": {
                "selector_code": selector_code,
                "resolved_template_option": resolved_option,
                "basis_sqft": round(basis_sqft, 2),
                "thickness_inches": round(thickness, 4),
                "unit_price": round(unit_price, 4),
                "margin_pct": round(margin_pct, 4),
            },
            "editable_decision_value": {
                "selector_code": selector_code,
                "resolved_template_option": resolved_option,
                "basis_sqft": round(basis_sqft, 2),
                "thickness_inches": round(thickness, 4),
                "unit_price": round(unit_price, 4),
                "margin_pct": round(margin_pct, 4),
            },
            "recommended_decision_value": {"selector_code": default_selector, "resolved_template_option": resolved_option},
            "calculated_output": formula.get("estimated_cost"),
            "calculated_output_summary": _value_summary({"dumpsters": formula.get("estimated_units"), "cost": formula.get("estimated_cost")}),
            "workbook_cell_write_preview": [
                {"cell": f"Estimate!A{ROOFING_DUMPSTER_TEMPLATE_ROW}", "field": "selector_code", "value": selector_code},
                {"cell": f"Estimate!C{ROOFING_DUMPSTER_TEMPLATE_ROW}", "field": "area_sqft", "value": round(basis_sqft, 2)},
                {"cell": f"Estimate!D{ROOFING_DUMPSTER_TEMPLATE_ROW}", "field": "thickness_inches", "value": round(thickness, 4)},
                {"cell": f"Estimate!E{ROOFING_DUMPSTER_TEMPLATE_ROW}", "field": "unit_price", "value": round(unit_price, 4)},
                {"cell": f"Estimate!F{ROOFING_DUMPSTER_TEMPLATE_ROW}", "field": "margin_pct", "value": round(margin_pct, 4)},
                {"cell": f"Estimate!G{ROOFING_DUMPSTER_TEMPLATE_ROW}", "field": "estimated_units_formula_output", "value": formula.get("estimated_units")},
            ],
        }
    )

    lift_options = _selector_options_from_roofing_graph("roofing_lift_equipment", ROOFING_LIFT_SELECTOR_MAP)
    lift_signal = bool(
        (adder_by_key.get("lift") or {}).get("include")
        or _has_positive_note_signal(notes, ["lift", "boom", "scissor", "forklift", "articulating", "difficult access", "high access"])
    )
    lift_default_selector = _default_roofing_lift_selector_code_for_scope(scope)
    lift_default_cost = adder_default("lift")
    for row_number in ROOFING_LIFT_TEMPLATE_ROWS:
        row_options = [option for option in lift_options if int(safe_number(option.get("row_number"), row_number)) == row_number] or _selector_options_from_roofing_graph(
            "roofing_lift_equipment",
            ROOFING_LIFT_SELECTOR_MAP,
            row_number=row_number,
        )
        existing = existing_by_row.get(str(row_number), {})
        include = bool(existing["include"]) if "include" in existing else bool(lift_signal and row_number == ROOFING_LIFT_TEMPLATE_ROWS[0])
        selector_code = str(
            first_nonblank(
                existing.get("editable_selector_code"),
                existing.get("selector_code"),
                _selector_code_for_roofing_option(existing.get("resolved_template_option"), ROOFING_LIFT_SELECTOR_MAP, row_options),
                lift_default_selector,
            )
        )
        resolved_option = _resolved_roofing_equipment_option(selector_code, ROOFING_LIFT_SELECTOR_MAP, row_options, "Forklift")
        margin_pct = safe_number(first_nonblank(existing.get("margin_pct"), ROOFING_LIFT_DEFAULT_MARGIN_PCT), ROOFING_LIFT_DEFAULT_MARGIN_PCT)
        period = safe_number(first_nonblank(existing.get("period"), existing.get("rental_period")), 0.0)
        unit_price = safe_number(first_nonblank(existing.get("unit_price")), 0.0)
        if include and period <= 0 and unit_price <= 0 and lift_default_cost > 0 and row_number == ROOFING_LIFT_TEMPLATE_ROWS[0]:
            period = 1.0
            unit_price = lift_default_cost / (1.0 + margin_pct / 100.0) if margin_pct > -100 else lift_default_cost
        size = str(first_nonblank(existing.get("size"), ROOFING_LIFT_DEFAULT_SIZE))
        formula = calculate_roofing_equipment_cost(period=period, unit_price=unit_price, margin_pct=margin_pct, include=include)
        warnings = []
        if include and period <= 0:
            warnings.append("Rental period is missing.")
        if include and unit_price <= 0:
            warnings.append("Lift unit price is missing.")
        rows.append(
            {
                "include": include,
                "section": "roofing_equipment_template_decisions",
                "decision_id": f"roofing_lift_equipment_row_{row_number}",
                "template_bucket": "lift",
                "workbook_row": str(row_number),
                "selector_cell": f"A{row_number}",
                "selector_code": selector_code,
                "editable_selector_code": selector_code,
                "resolved_template_option": resolved_option,
                "selector_options": row_options,
                "selector_options_json": json.dumps(row_options, default=str),
                "historical_selector_recommendation": _resolved_roofing_equipment_option(lift_default_selector, ROOFING_LIFT_SELECTOR_MAP, row_options, resolved_option),
                "historical_selector_code": lift_default_selector,
                "historical_selector_evidence_count": int(safe_number((adder_by_key.get("lift") or {}).get("evidence_count"), 0)),
                "historical_selector_confidence": (adder_by_key.get("lift") or {}).get("confidence") or "",
                "size": size,
                "period": round(period, 4),
                "unit_price": round(unit_price, 4),
                "margin_pct": round(margin_pct, 4),
                "estimated_cost": formula.get("estimated_cost"),
                "formula_model": formula.get("formula_model"),
                "formula_source": formula.get("formula_source"),
                "selected_pricing_candidate": resolved_option,
                "compatibility_status": "review" if warnings else "compatible",
                "compatibility_warnings": warnings,
                "product_guidance": "",
                "notes": "Template selector is the estimator decision. Size, rental period, unit price, and margin feed the lift formula."
                + (" " + " ".join(warnings) if warnings else ""),
                "decision_values": {
                    "selector_code": selector_code,
                    "resolved_template_option": resolved_option,
                    "size": size,
                    "period": round(period, 4),
                    "unit_price": round(unit_price, 4),
                    "margin_pct": round(margin_pct, 4),
                },
                "editable_decision_value": {
                    "selector_code": selector_code,
                    "resolved_template_option": resolved_option,
                    "size": size,
                    "period": round(period, 4),
                    "unit_price": round(unit_price, 4),
                    "margin_pct": round(margin_pct, 4),
                },
                "recommended_decision_value": {"selector_code": lift_default_selector, "resolved_template_option": resolved_option},
                "calculated_output": formula.get("estimated_cost"),
                "calculated_output_summary": _value_summary({"period": formula.get("period"), "cost": formula.get("estimated_cost")}),
                "workbook_cell_write_preview": [
                    {"cell": f"Estimate!A{row_number}", "field": "selector_code", "value": selector_code},
                    {"cell": f"Estimate!C{row_number}", "field": "size", "value": size},
                    {"cell": f"Estimate!D{row_number}", "field": "period", "value": round(period, 4)},
                    {"cell": f"Estimate!E{row_number}", "field": "unit_price", "value": round(unit_price, 4)},
                    {"cell": f"Estimate!F{row_number}", "field": "margin_pct", "value": round(margin_pct, 4)},
                    {"cell": f"Estimate!H{row_number}", "field": "estimated_cost_formula_output", "value": formula.get("estimated_cost")},
                ],
            }
        )

    existing = existing_by_row.get(str(ROOFING_GENERATOR_TEMPLATE_ROW), {})
    generator_signal = bool((adder_by_key.get("generator") or {}).get("include") or _has_positive_note_signal(notes, ["generator", "no power", "power unavailable"]))
    include = bool(existing["include"]) if "include" in existing else generator_signal
    days = safe_number(first_nonblank(existing.get("days"), existing.get("period"), ROOFING_GENERATOR_DEFAULT_DAYS if include else 0), 0.0)
    unit_price = safe_number(first_nonblank(existing.get("unit_price"), ROOFING_GENERATOR_DEFAULT_UNIT_PRICE), ROOFING_GENERATOR_DEFAULT_UNIT_PRICE)
    formula = calculate_roofing_days_rate_cost(days=days, unit_price=unit_price, include=include)
    warnings = []
    if include and days <= 0:
        warnings.append("Generator days are missing.")
    if include and unit_price <= 0:
        warnings.append("Generator daily price is missing.")
    rows.append(
        {
            "include": include,
            "section": "roofing_equipment_template_decisions",
            "decision_id": f"roofing_generator_row_{ROOFING_GENERATOR_TEMPLATE_ROW}",
            "template_bucket": "generator",
            "workbook_row": str(ROOFING_GENERATOR_TEMPLATE_ROW),
            "resolved_template_option": "Generator",
            "historical_selector_recommendation": "Generator",
            "historical_selector_evidence_count": int(safe_number((adder_by_key.get("generator") or {}).get("evidence_count"), 0)),
            "historical_selector_confidence": (adder_by_key.get("generator") or {}).get("confidence") or "",
            "days": round(days, 4),
            "unit_price": round(unit_price, 4),
            "estimated_cost": formula.get("estimated_cost"),
            "formula_model": formula.get("formula_model"),
            "formula_source": formula.get("formula_source"),
            "selected_pricing_candidate": "Generator",
            "compatibility_status": "review" if warnings else "compatible",
            "compatibility_warnings": warnings,
            "product_guidance": "",
            "notes": "Generator days and daily price feed the workbook formula." + (" " + " ".join(warnings) if warnings else ""),
            "decision_values": {"days": round(days, 4), "unit_price": round(unit_price, 4)},
            "editable_decision_value": {"days": round(days, 4), "unit_price": round(unit_price, 4)},
            "recommended_decision_value": {"days": ROOFING_GENERATOR_DEFAULT_DAYS, "unit_price": ROOFING_GENERATOR_DEFAULT_UNIT_PRICE},
            "calculated_output": formula.get("estimated_cost"),
            "calculated_output_summary": _value_summary({"days": formula.get("days"), "cost": formula.get("estimated_cost")}),
            "workbook_cell_write_preview": [
                {"cell": f"Estimate!C{ROOFING_GENERATOR_TEMPLATE_ROW}", "field": "days", "value": round(days, 4)},
                {"cell": f"Estimate!E{ROOFING_GENERATOR_TEMPLATE_ROW}", "field": "unit_price", "value": round(unit_price, 4)},
                {"cell": f"Estimate!H{ROOFING_GENERATOR_TEMPLATE_ROW}", "field": "estimated_cost_formula_output", "value": formula.get("estimated_cost")},
            ],
        }
    )
    return rows


def _apply_roofing_equipment_template_decisions_to_adders(workbench: dict[str, Any]) -> None:
    decisions = [
        row
        for row in workbench.get("roofing_equipment_template_decisions") or []
        if isinstance(row, dict) and row.get("include")
    ]
    if not decisions:
        return
    adders = workbench.setdefault("adders", [])
    by_key = {str(row.get("adder_key") or row.get("template_bucket") or "").lower(): row for row in adders if isinstance(row, dict)}
    label_by_key = {"dumpster": "Dumpster", "lift": "Lift", "generator": "Generator"}
    row_by_key = {"dumpster": "69", "lift": "73/74", "generator": "99"}
    for key in ("dumpster", "lift", "generator"):
        selected = [row for row in decisions if str(row.get("template_bucket") or "").lower() == key]
        if not selected:
            continue
        total = round(sum(safe_number(row.get("estimated_cost"), 0.0) for row in selected), 2)
        adder = by_key.get(key)
        if not adder:
            adder = {
                "adder": label_by_key[key],
                "adder_key": key,
                "template_bucket": key,
                "workbook_row": row_by_key[key],
                "historical_default_value": 0.0,
                "median_cost_when_used": 0.0,
                "evidence_count": 0,
                "confidence": "review",
            }
            adders.append(adder)
            by_key[key] = adder
        adder["include"] = True
        adder["editable_value"] = total
        adder["editable_default"] = total
        adder["estimated_cost"] = total
        adder["manual_override"] = True
        adder["source"] = "roofing_equipment_template_decisions"
        adder["confidence"] = "review"
        adder["notes"] = f"Synced from included roofing equipment template decision row(s): {', '.join(str(row.get('workbook_row')) for row in selected)}."
        adder["decision_values"] = [row.get("decision_values") for row in selected]
        adder["calculated_output_summary"] = _value_summary({"cost": total})
        adder["workbook_cell_write_preview"] = [write for row in selected for write in (row.get("workbook_cell_write_preview") or [])]


def _build_roofing_travel_freight_template_decisions(
    *,
    scope: dict[str, Any],
    adders: list[dict[str, Any]] | None = None,
    existing_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if _is_insulation_scope(scope):
        return []
    adders = adders or []
    existing_by_row = {str(row.get("workbook_row") or ""): row for row in existing_rows or [] if isinstance(row, dict)}
    adder_by_key = {str(row.get("adder_key") or row.get("template_bucket") or "").lower(): row for row in adders if isinstance(row, dict)}
    notes = _normalized(
        " ".join(
            str(scope.get(key) or "")
            for key in ("notes", "raw_input_notes", "scope_of_work", "project_type", "site_address", "address")
        )
    )

    def adder_default(*keys: str) -> float:
        for key in keys:
            row = adder_by_key.get(key) or {}
            value = safe_number(first_nonblank(row.get("editable_value"), row.get("historical_default_value"), row.get("median_cost_when_used")), 0.0)
            if value > 0:
                return value
        return 0.0

    rows: list[dict[str, Any]] = []

    existing = existing_by_row.get(str(ROOFING_DELIVERY_FEE_TEMPLATE_ROW), {})
    delivery_signal = bool(
        (adder_by_key.get("delivery_fee") or {}).get("include")
        or _has_positive_note_signal(notes, ["delivery", "deliver", "material drop", "supplier delivery"])
    )
    include = bool(existing["include"]) if "include" in existing else delivery_signal
    delivery_default = adder_default("delivery_fee")
    units = safe_number(first_nonblank(existing.get("estimated_units"), existing.get("units"), existing.get("quantity")), 0.0)
    unit_price = safe_number(first_nonblank(existing.get("unit_price")), 0.0)
    if include and units <= 0 and unit_price <= 0 and delivery_default > 0:
        units = 1.0
        unit_price = delivery_default
    formula = calculate_roofing_units_cost(
        units=units,
        unit_price=unit_price,
        include=include,
        formula_model="delivery_fee_units_rate_cost",
    )
    warnings = []
    if include and units <= 0:
        warnings.append("Delivery unit count is missing.")
    if include and unit_price <= 0:
        warnings.append("Delivery unit price is missing.")
    rows.append(
        {
            "include": include,
            "section": "roofing_travel_freight_template_decisions",
            "decision_id": f"roofing_delivery_fee_row_{ROOFING_DELIVERY_FEE_TEMPLATE_ROW}",
            "template_bucket": "delivery_fee",
            "workbook_row": str(ROOFING_DELIVERY_FEE_TEMPLATE_ROW),
            "resolved_template_option": "Delivery Fee",
            "estimated_units": formula.get("estimated_units") or formula.get("units"),
            "units": formula.get("units"),
            "unit_price": round(unit_price, 4),
            "estimated_cost": formula.get("estimated_cost"),
            "formula_model": formula.get("formula_model"),
            "formula_source": formula.get("formula_source"),
            "compatibility_status": "review" if warnings else "compatible",
            "compatibility_warnings": warnings,
            "notes": "Delivery fee uses the workbook units x unit price formula." + (" " + " ".join(warnings) if warnings else ""),
            "decision_values": {"units": formula.get("units"), "unit_price": round(unit_price, 4)},
            "editable_decision_value": {"units": formula.get("units"), "unit_price": round(unit_price, 4)},
            "recommended_decision_value": {"historical_default_amount": delivery_default},
            "calculated_output": formula.get("estimated_cost"),
            "calculated_output_summary": _value_summary({"units": formula.get("units"), "cost": formula.get("estimated_cost")}),
            "workbook_cell_write_preview": [
                {"cell": f"Estimate!E{ROOFING_DELIVERY_FEE_TEMPLATE_ROW}", "field": "unit_price", "value": round(unit_price, 4)},
                {"cell": f"Estimate!G{ROOFING_DELIVERY_FEE_TEMPLATE_ROW}", "field": "estimated_units", "value": formula.get("units")},
                {"cell": f"Estimate!H{ROOFING_DELIVERY_FEE_TEMPLATE_ROW}", "field": "estimated_cost_formula_output", "value": formula.get("estimated_cost")},
            ],
        }
    )

    existing = existing_by_row.get(str(ROOFING_FREIGHT_TEMPLATE_ROW), {})
    freight_signal = bool((adder_by_key.get("freight") or {}).get("include") or _has_positive_note_signal(notes, ["freight", "shipping"]))
    include = bool(existing["include"]) if "include" in existing else freight_signal
    freight_amount = safe_number(
        first_nonblank(existing.get("amount"), existing.get("estimated_cost"), existing.get("unit_price"), adder_default("freight")),
        0.0,
    )
    formula = calculate_roofing_direct_cost(amount=freight_amount, include=include)
    warnings = ["Freight amount is missing."] if include and freight_amount <= 0 else []
    rows.append(
        {
            "include": include,
            "section": "roofing_travel_freight_template_decisions",
            "decision_id": f"roofing_freight_row_{ROOFING_FREIGHT_TEMPLATE_ROW}",
            "template_bucket": "freight",
            "workbook_row": str(ROOFING_FREIGHT_TEMPLATE_ROW),
            "resolved_template_option": "Freight",
            "amount": round(freight_amount, 2),
            "unit_price": round(freight_amount, 2),
            "estimated_cost": formula.get("estimated_cost"),
            "formula_model": formula.get("formula_model"),
            "formula_source": formula.get("formula_source"),
            "compatibility_status": "review" if warnings else "compatible",
            "compatibility_warnings": warnings,
            "notes": "Freight uses the direct workbook amount in row 103." + (" " + " ".join(warnings) if warnings else ""),
            "decision_values": {"amount": round(freight_amount, 2)},
            "editable_decision_value": {"amount": round(freight_amount, 2)},
            "recommended_decision_value": {"historical_default_amount": adder_default("freight")},
            "calculated_output": formula.get("estimated_cost"),
            "calculated_output_summary": _value_summary({"cost": formula.get("estimated_cost")}),
            "workbook_cell_write_preview": [
                {"cell": f"Estimate!E{ROOFING_FREIGHT_TEMPLATE_ROW}", "field": "estimated_cost", "value": round(freight_amount, 2)},
                {"cell": f"Estimate!H{ROOFING_FREIGHT_TEMPLATE_ROW}", "field": "estimated_cost_formula_output", "value": formula.get("estimated_cost")},
            ],
        }
    )

    travel_specs = [
        (
            ROOFING_SALES_INSPECTION_TEMPLATE_ROW,
            "sales_trips",
            "Sales / Inspection Trips",
            ["sales trip", "sales trips", "inspection", "inspect", "site visit"],
            ROOFING_SALES_INSPECTION_DEFAULT_TRIPS,
            ROOFING_SALES_INSPECTION_DEFAULT_RATE,
            ("sales_trips", "inspection"),
        ),
        (
            ROOFING_TRUCK_EXPENSE_TEMPLATE_ROW,
            "truck_expense",
            "Truck Expense",
            ["truck", "truck expense", "travel", "miles", "mobilization"],
            ROOFING_TRUCK_EXPENSE_DEFAULT_TRIPS,
            ROOFING_TRUCK_EXPENSE_DEFAULT_RATE,
            ("truck_expense", "travel"),
        ),
    ]
    for row_number, bucket, label, signal_terms, default_trips, default_rate, adder_keys in travel_specs:
        existing = existing_by_row.get(str(row_number), {})
        signal = bool(any((adder_by_key.get(key) or {}).get("include") for key in adder_keys) or _has_positive_note_signal(notes, signal_terms))
        include = bool(existing["include"]) if "include" in existing else signal
        default_amount = adder_default(*adder_keys)
        trips = safe_number(first_nonblank(existing.get("trip_count"), existing.get("trips")), 0.0)
        miles = safe_number(first_nonblank(existing.get("round_trip_miles"), existing.get("miles")), 0.0)
        rate = safe_number(first_nonblank(existing.get("unit_price"), existing.get("rate")), 0.0)
        if include and trips <= 0:
            trips = default_trips
        if include and miles <= 0:
            miles = ROOFING_TRAVEL_DEFAULT_ROUND_TRIP_MILES
        if include and rate <= 0:
            rate = default_amount / (trips * miles) if default_amount > 0 and trips > 0 and miles > 0 else default_rate
        formula = calculate_roofing_travel_cost(
            trip_count=trips,
            round_trip_miles=miles,
            unit_price=rate,
            include=include,
        )
        warnings = []
        if include and trips <= 0:
            warnings.append("Trip count is missing.")
        if include and miles <= 0:
            warnings.append("Round-trip miles are missing.")
        if include and rate <= 0:
            warnings.append("Mileage rate is missing.")
        rows.append(
            {
                "include": include,
                "section": "roofing_travel_freight_template_decisions",
                "decision_id": f"roofing_{bucket}_row_{row_number}",
                "template_bucket": bucket,
                "workbook_row": str(row_number),
                "resolved_template_option": label,
                "trip_count": formula.get("trip_count"),
                "round_trip_miles": formula.get("round_trip_miles"),
                "unit_price": formula.get("unit_price"),
                "estimated_cost": formula.get("estimated_cost"),
                "formula_model": formula.get("formula_model"),
                "formula_source": formula.get("formula_source"),
                "compatibility_status": "review" if warnings else "compatible",
                "compatibility_warnings": warnings,
                "notes": f"{label} uses trips x round-trip miles x rate in the workbook." + (" " + " ".join(warnings) if warnings else ""),
                "decision_values": {
                    "trip_count": formula.get("trip_count"),
                    "round_trip_miles": formula.get("round_trip_miles"),
                    "unit_price": formula.get("unit_price"),
                },
                "editable_decision_value": {
                    "trip_count": formula.get("trip_count"),
                    "round_trip_miles": formula.get("round_trip_miles"),
                    "unit_price": formula.get("unit_price"),
                },
                "recommended_decision_value": {"historical_default_amount": default_amount},
                "calculated_output": formula.get("estimated_cost"),
                "calculated_output_summary": _value_summary(
                    {
                        "trips": formula.get("trip_count"),
                        "miles": formula.get("round_trip_miles"),
                        "cost": formula.get("estimated_cost"),
                    }
                ),
                "workbook_cell_write_preview": [
                    {"cell": f"Estimate!B{row_number}", "field": "trip_count", "value": formula.get("trip_count")},
                    {"cell": f"Estimate!C{row_number}", "field": "round_trip_miles", "value": formula.get("round_trip_miles")},
                    {"cell": f"Estimate!E{row_number}", "field": "unit_price", "value": formula.get("unit_price")},
                    {"cell": f"Estimate!H{row_number}", "field": "estimated_cost_formula_output", "value": formula.get("estimated_cost")},
                ],
            }
        )
    return rows


def _apply_roofing_travel_freight_template_decisions_to_adders(workbench: dict[str, Any]) -> None:
    decisions = [
        row
        for row in workbench.get("roofing_travel_freight_template_decisions") or []
        if isinstance(row, dict) and row.get("include")
    ]
    if not decisions:
        return
    adders = workbench.setdefault("adders", [])
    by_key = {str(row.get("adder_key") or row.get("template_bucket") or "").lower(): row for row in adders if isinstance(row, dict)}
    label_by_key = {
        "delivery_fee": "Delivery Fee",
        "freight": "Freight",
        "sales_trips": "Sales Trips",
        "truck_expense": "Truck Expense",
    }
    row_by_key = {"delivery_fee": "76", "freight": "103", "sales_trips": "106", "truck_expense": "108"}
    for key in ("delivery_fee", "freight", "sales_trips", "truck_expense"):
        selected = [row for row in decisions if str(row.get("template_bucket") or "").lower() == key]
        if not selected:
            continue
        total = round(sum(safe_number(row.get("estimated_cost"), 0.0) for row in selected), 2)
        adder = by_key.get(key)
        if not adder:
            adder = {
                "adder": label_by_key[key],
                "adder_key": key,
                "template_bucket": key,
                "workbook_row": row_by_key[key],
                "historical_default_value": 0.0,
                "median_cost_when_used": 0.0,
                "evidence_count": 0,
                "confidence": "review",
            }
            adders.append(adder)
            by_key[key] = adder
        adder["include"] = True
        adder["editable_value"] = total
        adder["editable_default"] = total
        adder["estimated_cost"] = total
        adder["manual_override"] = True
        adder["source"] = "roofing_travel_freight_template_decisions"
        adder["confidence"] = "review"
        adder["notes"] = f"Synced from included roofing travel/freight template decision row(s): {', '.join(str(row.get('workbook_row')) for row in selected)}."
        adder["decision_values"] = [row.get("decision_values") for row in selected]
        adder["calculated_output_summary"] = _value_summary({"cost": total})
        adder["workbook_cell_write_preview"] = [write for row in selected for write in (row.get("workbook_cell_write_preview") or [])]


def _roofing_thinner_selector_options(row_number: int = ROOFING_THINNER_TEMPLATE_ROW) -> list[dict[str, Any]]:
    return [
        {
            "selector_code": code,
            "resolved_template_option": label,
            "resolved_item_name": label,
            "row_number": row_number,
            "selector_cell": f"A{row_number}",
            "resolved_cell": f"B{row_number}",
            "decision_id": "roofing_thinner",
            "template_type": "roofing",
            "source_type": "row_selector_map",
        }
        for code, label in ROOFING_THINNER_SELECTOR_MAP.items()
    ]


def _resolved_roofing_thinner_option(selector_code: Any, fallback: str = "Naphtha VM&P") -> str:
    code = str(first_nonblank(selector_code, "1")).strip()
    if code.endswith(".0"):
        code = code[:-2]
    return ROOFING_THINNER_SELECTOR_MAP.get(code, fallback)


def _coating_gallons_from_decisions(decisions: list[dict[str, Any]] | None, materials: list[dict[str, Any]] | None) -> float:
    gallons = sum(
        safe_number(row.get("estimated_gallons") or row.get("calculated_quantity"), 0.0)
        for row in decisions or []
        if isinstance(row, dict) and row.get("include")
    )
    if gallons > 0:
        return gallons
    return sum(
        safe_number(row.get("estimated_gallons") or row.get("calculated_quantity"), 0.0)
        for row in materials or []
        if isinstance(row, dict) and row.get("include") and str(row.get("package_key") or row.get("template_bucket") or "").lower() == "coating"
    )


def _build_roofing_accessory_template_decisions(
    *,
    scope: dict[str, Any],
    materials: list[dict[str, Any]] | None = None,
    coating_decisions: list[dict[str, Any]] | None = None,
    existing_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if _is_insulation_scope(scope):
        return []
    materials = materials or []
    existing_by_row = {str(row.get("workbook_row") or ""): row for row in existing_rows or [] if isinstance(row, dict)}
    material_by_key = {str(row.get("package_key") or row.get("template_bucket") or "").lower(): row for row in materials if isinstance(row, dict)}
    notes = _normalized(" ".join(str(scope.get(key) or "") for key in ("notes", "raw_input_notes", "scope_of_work", "project_type")))
    rows: list[dict[str, Any]] = []

    existing = existing_by_row.get(str(ROOFING_THINNER_TEMPLATE_ROW), {})
    thinner_material = material_by_key.get("thinner") or {}
    thinner_signal = bool((thinner_material or {}).get("include") or _has_positive_note_signal(notes, ["thinner", "solvent", "xylene", "mineral spirits", "naphtha"]))
    include = bool(existing["include"]) if "include" in existing else thinner_signal
    selector_code = str(first_nonblank(existing.get("editable_selector_code"), existing.get("selector_code"), "1")).strip()
    if selector_code.endswith(".0"):
        selector_code = selector_code[:-2]
    resolved_option = _resolved_roofing_thinner_option(selector_code)
    unit_price = safe_number(first_nonblank(existing.get("unit_price"), thinner_material.get("current_unit_price"), thinner_material.get("current_price")), 0.0)
    coating_gallons = safe_number(first_nonblank(existing.get("total_coating_gallons")), 0.0)
    if coating_gallons <= 0:
        coating_gallons = _coating_gallons_from_decisions(coating_decisions, materials)
    formula = calculate_roofing_thinner(total_coating_gallons=coating_gallons, unit_price=unit_price, include=include)
    warnings = []
    if include and coating_gallons <= 0:
        warnings.append("Coating gallons are missing; thinner formula depends on rows 26-28.")
    if include and unit_price <= 0:
        warnings.append("Thinner unit price is missing.")
    rows.append(
        {
            "include": include,
            "section": "roofing_accessory_template_decisions",
            "decision_id": f"roofing_thinner_row_{ROOFING_THINNER_TEMPLATE_ROW}",
            "template_bucket": "thinner",
            "workbook_row": str(ROOFING_THINNER_TEMPLATE_ROW),
            "selector_cell": f"A{ROOFING_THINNER_TEMPLATE_ROW}",
            "selector_code": selector_code,
            "editable_selector_code": selector_code,
            "resolved_template_option": resolved_option,
            "selector_options": _roofing_thinner_selector_options(),
            "selector_options_json": json.dumps(_roofing_thinner_selector_options(), default=str),
            "total_coating_gallons": round(coating_gallons, 4),
            "estimated_units": formula.get("estimated_units"),
            "unit_price": round(unit_price, 4),
            "estimated_cost": formula.get("estimated_cost"),
            "formula_model": formula.get("formula_model"),
            "formula_source": formula.get("formula_source"),
            "compatibility_status": "review" if warnings else "compatible",
            "compatibility_warnings": warnings,
            "notes": "Thinner uses the workbook formula ((coating gallons rows 26-28)/55)*4." + (" " + " ".join(warnings) if warnings else ""),
            "decision_values": {
                "selector_code": selector_code,
                "resolved_template_option": resolved_option,
                "total_coating_gallons": round(coating_gallons, 4),
                "unit_price": round(unit_price, 4),
            },
            "editable_decision_value": {
                "selector_code": selector_code,
                "resolved_template_option": resolved_option,
                "total_coating_gallons": round(coating_gallons, 4),
                "unit_price": round(unit_price, 4),
            },
            "recommended_decision_value": {"selector_code": selector_code, "resolved_template_option": resolved_option},
            "calculated_output": formula.get("estimated_cost"),
            "calculated_output_summary": _value_summary({"units": formula.get("estimated_units"), "cost": formula.get("estimated_cost")}),
            "workbook_cell_write_preview": [
                {"cell": f"Estimate!A{ROOFING_THINNER_TEMPLATE_ROW}", "field": "selector_code", "value": selector_code},
                {"cell": f"Estimate!E{ROOFING_THINNER_TEMPLATE_ROW}", "field": "unit_price", "value": round(unit_price, 4)},
                {"cell": f"Estimate!G{ROOFING_THINNER_TEMPLATE_ROW}", "field": "estimated_units_formula_output", "value": formula.get("estimated_units")},
            ],
        }
    )

    material_key_by_bucket = {"gutter": "gutter_downspouts", "downspouts": "gutter_downspouts"}
    for spec in ROOFING_ACCESSORY_TEMPLATE_SPECS:
        row_number = int(spec["row"])
        bucket = str(spec["bucket"])
        row_key = str(row_number)
        existing = existing_by_row.get(row_key, {})
        material = material_by_key.get(material_key_by_bucket.get(bucket, bucket)) or {}
        signal = bool((material or {}).get("include") or _has_positive_note_signal(notes, spec.get("signals") or []))
        include = bool(existing["include"]) if "include" in existing else signal
        formula_model = str(spec["formula"])
        unit_price = safe_number(first_nonblank(existing.get("unit_price"), material.get("current_unit_price"), material.get("current_price")), 0.0)
        amount = safe_number(first_nonblank(existing.get("amount"), existing.get("estimated_cost"), material.get("estimated_cost")), 0.0)
        if formula_model == "linear_feet_unit_cost":
            units = positive_number(
                existing.get("linear_ft"),
                existing.get("units"),
                existing.get("estimated_units"),
                existing.get("calculated_quantity"),
                material.get("calculated_quantity"),
                default=0.0,
            )
        else:
            units = positive_number(
                existing.get("estimated_units"),
                existing.get("units"),
                existing.get("linear_ft"),
                existing.get("calculated_quantity"),
                material.get("calculated_quantity"),
                default=0.0,
            )
        if formula_model == "direct_cost":
            formula = calculate_roofing_direct_cost(amount=amount, include=include)
        elif formula_model == "linear_feet_unit_cost":
            formula = calculate_roofing_linear_feet_cost(linear_ft=units, unit_price=unit_price, include=include)
        else:
            formula = calculate_roofing_units_cost(
                units=units,
                unit_price=unit_price,
                include=include,
                formula_model=formula_model,
            )
        warnings = []
        if include and formula_model != "direct_cost" and units <= 0:
            warnings.append("Quantity is missing.")
        if include and formula_model != "direct_cost" and unit_price <= 0:
            warnings.append("Unit price is missing.")
        if include and formula_model == "direct_cost" and amount <= 0:
            warnings.append("Amount is missing.")
        decision_values = {"unit_price": round(unit_price, 4)}
        if formula_model == "linear_feet_unit_cost":
            decision_values["linear_ft"] = formula.get("linear_ft")
        elif formula_model == "direct_cost":
            decision_values["amount"] = round(amount, 2)
        else:
            decision_values["units"] = formula.get("units")
        rows.append(
            {
                "include": include,
                "section": "roofing_accessory_template_decisions",
                "decision_id": f"roofing_{bucket}_row_{row_number}",
                "template_bucket": bucket,
                "workbook_row": row_key,
                "resolved_template_option": spec["label"],
                "linear_ft": formula.get("linear_ft"),
                "units": formula.get("units"),
                "estimated_units": formula.get("estimated_units") or formula.get("units"),
                "amount": round(amount, 2) if formula_model == "direct_cost" else 0.0,
                "unit_price": round(unit_price, 4),
                "estimated_cost": formula.get("estimated_cost"),
                "formula_model": formula.get("formula_model"),
                "formula_source": formula.get("formula_source"),
                "compatibility_status": "review" if warnings else "compatible",
                "compatibility_warnings": warnings,
                "notes": f"{spec['label']} uses the workbook {formula_model} formula." + (" " + " ".join(warnings) if warnings else ""),
                "decision_values": decision_values,
                "editable_decision_value": dict(decision_values),
                "recommended_decision_value": {"resolved_template_option": spec["label"]},
                "calculated_output": formula.get("estimated_cost"),
                "calculated_output_summary": _value_summary({**decision_values, "cost": formula.get("estimated_cost")}),
                "workbook_cell_write_preview": _accessory_cell_preview(row_number, bucket, formula_model, decision_values, formula),
            }
        )
    return rows


def _accessory_cell_preview(
    row_number: int,
    bucket: str,
    formula_model: str,
    decision_values: dict[str, Any],
    formula: dict[str, Any],
) -> list[dict[str, Any]]:
    if bucket == "thinner":
        return []
    if formula_model == "direct_cost":
        amount = decision_values.get("amount")
        return [
            {"cell": f"Estimate!E{row_number}", "field": "amount", "value": amount},
            {"cell": f"Estimate!H{row_number}", "field": "estimated_cost_formula_output", "value": formula.get("estimated_cost")},
        ]
    if formula_model == "linear_feet_unit_cost":
        return [
            {"cell": f"Estimate!C{row_number}", "field": "linear_ft", "value": decision_values.get("linear_ft")},
            {"cell": f"Estimate!E{row_number}", "field": "unit_price", "value": decision_values.get("unit_price")},
            {"cell": f"Estimate!H{row_number}", "field": "estimated_cost_formula_output", "value": formula.get("estimated_cost")},
        ]
    return [
        {"cell": f"Estimate!E{row_number}", "field": "unit_price", "value": decision_values.get("unit_price")},
        {"cell": f"Estimate!G{row_number}", "field": "units", "value": decision_values.get("units")},
        {"cell": f"Estimate!H{row_number}", "field": "estimated_cost_formula_output", "value": formula.get("estimated_cost")},
    ]


def _apply_roofing_accessory_template_decisions_to_materials(workbench: dict[str, Any]) -> None:
    decisions = [
        row
        for row in workbench.get("roofing_accessory_template_decisions") or []
        if isinstance(row, dict) and row.get("include")
    ]
    if not decisions:
        return
    materials = workbench.setdefault("materials", [])
    by_key = {str(row.get("package_key") or row.get("template_bucket") or "").lower(): row for row in materials if isinstance(row, dict)}
    for decision in decisions:
        key = str(decision.get("template_bucket") or "").lower()
        material = by_key.get(key)
        if not material:
            material = {
                "package": decision.get("resolved_template_option") or key,
                "package_key": key,
                "template_bucket": key,
                "workbook_row": decision.get("workbook_row"),
                "historical_qty_per_sqft": 0.0,
                "editable_qty_per_sqft": 0.0,
                "historical_cost_per_sqft": 0.0,
                "evidence_count": 0,
                "confidence": "review",
                "source": "roofing_accessory_template_decisions",
            }
            materials.append(material)
            by_key[key] = material
        quantity = safe_number(
            first_nonblank(decision.get("estimated_units"), decision.get("units"), decision.get("linear_ft")),
            0.0,
        )
        material["include"] = True
        material["item_name"] = decision.get("resolved_template_option")
        material["current_item"] = decision.get("resolved_template_option")
        material["workbook_row"] = decision.get("workbook_row")
        material["calculated_quantity"] = quantity
        material["estimated_units"] = quantity
        material["linear_ft"] = safe_number(decision.get("linear_ft"), 0.0)
        material["amount"] = safe_number(decision.get("amount"), 0.0)
        material["current_unit_price"] = safe_number(decision.get("unit_price"), 0.0)
        material["current_price"] = material["current_unit_price"]
        material["estimated_cost"] = safe_number(decision.get("estimated_cost"), 0.0)
        material["formula_model"] = decision.get("formula_model")
        material["formula_source"] = "roofing_accessory_template_decisions"
        material["selector_code"] = decision.get("editable_selector_code") or decision.get("selector_code")
        material["resolved_template_option"] = decision.get("resolved_template_option")
        material["unit"] = "lf" if material["linear_ft"] else "unit"
        material["decision_values"] = decision.get("decision_values")
        material["editable_decision_value"] = decision.get("editable_decision_value")
        material["calculated_output"] = material["estimated_cost"]
        material["calculated_output_summary"] = decision.get("calculated_output_summary")
        material["workbook_cell_write_preview"] = decision.get("workbook_cell_write_preview") or []
        material["notes"] = f"Synced from roofing accessory template decision row {decision.get('workbook_row')}."


def _roofing_labor_crew_options() -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    graph_path = Path("output/template_decision_graph_roofing.json")
    if graph_path.exists():
        try:
            payload = json.loads(graph_path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        for row in payload.get("selector_options") or []:
            if row.get("decision_id") != "roofing_crew_rate_selection":
                continue
            code = str(row.get("selector_code") or "").strip()
            label = str(row.get("resolved_item_name") or "").strip()
            if not code or not label:
                continue
            options.append(
                {
                    "selector_code": code,
                    "resolved_template_option": label,
                    "resolved_cell": row.get("resolved_cell") or "",
                    "source_type": row.get("source_type") or "people_daily_rate_selector",
                }
            )
    if not options:
        for code in range(1, 9):
            column_letter = chr(ord("C") + code)
            options.append(
                {
                    "selector_code": str(code),
                    "resolved_template_option": f"{code} person crew daily rate",
                    "resolved_cell": f"People!{column_letter}12",
                    "source_type": "fallback_people_daily_rate_selector",
                }
            )
    deduped: dict[str, dict[str, Any]] = {}
    for option in options:
        deduped.setdefault(str(option.get("selector_code") or ""), option)
    return sorted(deduped.values(), key=lambda item: int(safe_number(item.get("selector_code"), 999)))


def _resolved_roofing_labor_crew_option(crew_size: Any) -> str:
    key = str(int(safe_number(crew_size, 0))) if safe_number(crew_size, 0) > 0 else ""
    for option in _roofing_labor_crew_options():
        if str(option.get("selector_code") or "") == key:
            return str(option.get("resolved_template_option") or "")
    return f"{key} person crew daily rate" if key else ""


def _roofing_labor_daily_rate_cell(crew_size: Any) -> str:
    key = str(int(safe_number(crew_size, 0))) if safe_number(crew_size, 0) > 0 else ""
    for option in _roofing_labor_crew_options():
        if str(option.get("selector_code") or "") == key:
            return str(option.get("resolved_cell") or "")
    if key:
        column_letter = chr(ord("C") + int(key))
        return f"People!{column_letter}12"
    return ""


def _build_roofing_labor_template_decisions(
    *,
    scope: dict[str, Any],
    labor_rows: list[dict[str, Any]] | None = None,
    existing_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if _is_insulation_scope(scope):
        return []
    labor_rows = labor_rows or []
    existing_by_key = {
        str(first_nonblank(row.get("template_bucket"), row.get("package_key"), row.get("workbook_row"))): row
        for row in existing_rows or []
        if isinstance(row, dict)
    }
    crew_options = _roofing_labor_crew_options()
    area = _estimate_area(scope)
    decisions: list[dict[str, Any]] = []
    for labor in labor_rows:
        if not isinstance(labor, dict):
            continue
        package = str(labor.get("package_key") or labor.get("template_bucket") or "")
        workbook_row = str(labor.get("workbook_row") or "")
        if not package or not workbook_row:
            continue
        existing = existing_by_key.get(package) or existing_by_key.get(workbook_row) or {}
        include = bool(existing["include"]) if "include" in existing else bool(labor.get("include"))
        crew_size = int(
            safe_number(
                first_nonblank(
                    existing.get("crew_size"),
                    existing.get("crew_people_selection"),
                    existing.get("crew_selector_code"),
                    labor.get("crew_size"),
                    labor.get("crew_people_selection"),
                    4,
                ),
                4,
            )
            or 4
        )
        labor_days_value = safe_number(first_nonblank(labor.get("days"), labor.get("editable_days")), 0.0)
        existing_days_value = safe_number(first_nonblank(existing.get("days"), existing.get("editable_days")), 0.0)
        existing_days_changed = bool(existing) and abs(existing_days_value - labor_days_value) > 1e-9
        days_was_explicit = bool(existing.get("days_was_explicit") or labor.get("days_was_explicit") or existing_days_changed)
        days = existing_days_value if (existing and (existing.get("days_was_explicit") or existing_days_changed)) else labor_days_value
        labor_hours_rate = safe_number(labor.get("editable_hours_per_1000_sqft"), 0.0)
        existing_hours_rate = safe_number(existing.get("editable_hours_per_1000_sqft"), labor_hours_rate)
        flat_hours_rate_changed = bool(existing) and abs(labor_hours_rate - existing_hours_rate) > 1e-9
        hours_per_1000 = labor_hours_rate if flat_hours_rate_changed else safe_number(first_nonblank(existing.get("editable_hours_per_1000_sqft"), labor_hours_rate), 0.0)
        total_hours = safe_number(
            first_nonblank(
                None if flat_hours_rate_changed else existing.get("total_hours"),
                None if flat_hours_rate_changed else existing.get("editable_total_hours"),
                None if flat_hours_rate_changed else labor.get("calculated_hours"),
                None if flat_hours_rate_changed else labor.get("total_hours"),
            ),
            0.0,
        )
        hourly_rate = safe_number(first_nonblank(existing.get("hourly_rate"), existing.get("labor_rate"), labor.get("hourly_rate"), labor.get("labor_rate")), DEFAULT_HOURLY_RATE)
        daily_rate = safe_number(first_nonblank(existing.get("daily_rate"), labor.get("daily_rate")), 0.0)
        formula_mode = str(first_nonblank(existing.get("formula_mode"), labor.get("formula_mode"), "mixed_formula"))
        formula = calculate_mixed_labor(
            days=days,
            crew_size=crew_size,
            total_hours=total_hours,
            hours_per_1000_sqft=hours_per_1000,
            area_sqft=area,
            daily_rate=daily_rate,
            hourly_rate=hourly_rate,
            formula_mode=formula_mode,
            include=include,
        )
        calculated_hours = safe_number(formula.get("total_hours"), 0.0)
        calculated_days = safe_number(formula.get("days"), days)
        calculated_daily_rate = safe_number(formula.get("daily_rate"), daily_rate)
        calculated_hourly_rate = safe_number(formula.get("hourly_rate"), hourly_rate)
        selected_cell = _roofing_labor_daily_rate_cell(crew_size)
        row_number = int(safe_number(workbook_row, 0))
        preview = [
            {"cell": f"Estimate!B{row_number}", "field": "days", "value": round(calculated_days, 4)},
            {"cell": f"Estimate!C{row_number}", "field": "crew_selector_code", "value": crew_size},
            {"cell": f"Estimate!D{row_number}", "field": "hourly_rate", "value": round(calculated_hourly_rate, 4)},
            {"cell": f"Estimate!G{row_number}", "field": "total_hours", "value": round(calculated_hours, 4)},
            {"cell": f"Estimate!J{row_number}", "field": "daily_rate_formula_output", "value": round(calculated_daily_rate, 4)},
        ]
        task_label = str(first_nonblank(labor.get("labor_package"), package.replace("_", " ").title()))
        warnings = []
        if include and calculated_hours <= 0 and calculated_days <= 0:
            warnings.append("Labor days or total hours are missing.")
        if include and calculated_hourly_rate <= 0 and calculated_daily_rate <= 0:
            warnings.append("Labor rate is missing.")
        decisions.append(
            {
                "include": include,
                "section": "roofing_labor_template_decisions",
                "decision_id": f"roofing_{package}_row_{workbook_row}",
                "template_bucket": package,
                "package_key": package,
                "workbook_row": workbook_row,
                "labor_task": task_label,
                "labor_package": task_label,
                "days": round(calculated_days, 4),
                "editable_days": round(calculated_days, 4),
                "crew_size": crew_size,
                "crew_people_selection": crew_size,
                "crew_selector_code": crew_size,
                "crew_selector_options": crew_options,
                "crew_selector_options_json": json.dumps(crew_options, default=str),
                "crew_selection": _resolved_roofing_labor_crew_option(crew_size),
                "selected_daily_rate_cell": selected_cell,
                "daily_rate": round(calculated_daily_rate, 4),
                "hourly_rate": round(calculated_hourly_rate, 4),
                "labor_rate": round(calculated_hourly_rate, 4),
                "editable_hours_per_1000_sqft": round(hours_per_1000, 4),
                "total_hours": round(calculated_hours, 4),
                "calculated_hours": round(calculated_hours, 4),
                "editable_total_hours": round(calculated_hours, 4),
                "formula_mode": str(formula.get("formula_mode") or formula_mode),
                "formula_model": str(formula.get("formula_model") or "labor_cost_from_days_crew_rate"),
                "formula_source": str(formula.get("formula_source") or ""),
                "estimated_cost": formula.get("estimated_cost"),
                "days_was_explicit": days_was_explicit,
                "calculated_output": formula.get("estimated_cost"),
                "calculated_output_summary": _value_summary(
                    {
                        "days": round(calculated_days, 4),
                        "hours": round(calculated_hours, 4),
                        "cost": formula.get("estimated_cost"),
                        "formula_mode": str(formula.get("formula_mode") or formula_mode),
                    }
                ),
                "historical_recommendation": labor.get("historical_recommendation") or "",
                "historical_selector_recommendation": _resolved_roofing_labor_crew_option(crew_size),
                "historical_selector_evidence_count": int(safe_number(labor.get("decision_evidence_count") or labor.get("evidence_count"), 0)),
                "historical_selector_confidence": labor.get("decision_confidence") or labor.get("confidence") or "",
                "decision_evidence_count": int(safe_number(labor.get("decision_evidence_count") or labor.get("evidence_count"), 0)),
                "decision_confidence": labor.get("decision_confidence") or labor.get("confidence") or "",
                "evidence_count": int(safe_number(labor.get("evidence_count"), 0)),
                "confidence": labor.get("confidence") or "",
                "compatibility_status": "review" if warnings else "compatible",
                "compatibility_warnings": warnings,
                "notes": "Labor decision mirrors the workbook mixed formula: if total hours are zero, cost uses days x daily rate; otherwise cost uses hourly rate x total hours."
                + (" " + " ".join(warnings) if warnings else ""),
                "decision_values": {
                    "days": round(calculated_days, 4),
                    "days_was_explicit": days_was_explicit,
                    "crew_size": crew_size,
                    "crew_selector_code": crew_size,
                    "daily_rate": round(calculated_daily_rate, 4),
                    "hourly_rate": round(calculated_hourly_rate, 4),
                    "total_hours": round(calculated_hours, 4),
                    "formula_mode": str(formula.get("formula_mode") or formula_mode),
                    "formula_model": str(formula.get("formula_model") or "labor_cost_from_days_crew_rate"),
                    "formula_source": str(formula.get("formula_source") or ""),
                    "estimated_cost": formula.get("estimated_cost"),
                },
                "editable_decision_value": {
                    "days": round(calculated_days, 4),
                    "days_was_explicit": days_was_explicit,
                    "crew_size": crew_size,
                    "daily_rate": round(calculated_daily_rate, 4),
                    "hourly_rate": round(calculated_hourly_rate, 4),
                    "total_hours": round(calculated_hours, 4),
                    "formula_mode": str(formula.get("formula_mode") or formula_mode),
                },
                "recommended_decision_value": labor.get("recommended_decision_value") or {},
                "row_traceability": f"Estimate row {workbook_row}; daily rate from {selected_cell or 'People sheet selector'}",
                "workbook_cell_write_preview": preview,
            }
        )
    return decisions


def _apply_roofing_labor_template_decisions_to_labor(workbench: dict[str, Any]) -> None:
    decisions = {
        str(first_nonblank(row.get("template_bucket"), row.get("package_key"))): row
        for row in workbench.get("roofing_labor_template_decisions") or []
        if isinstance(row, dict)
    }
    if not decisions:
        return
    for labor in workbench.get("labor") or []:
        if not isinstance(labor, dict):
            continue
        key = str(first_nonblank(labor.get("template_bucket"), labor.get("package_key")))
        decision = decisions.get(key)
        if not decision:
            continue
        labor["include"] = bool(decision.get("include"))
        labor["days"] = safe_number(decision.get("days"), 0.0)
        labor["editable_days"] = labor["days"]
        labor["crew_size"] = int(safe_number(decision.get("crew_size"), 0) or 0)
        labor["crew_people_selection"] = labor["crew_size"]
        labor["daily_rate"] = safe_number(decision.get("daily_rate"), 0.0)
        labor["hourly_rate"] = safe_number(decision.get("hourly_rate"), 0.0)
        labor["labor_rate"] = labor["hourly_rate"]
        labor["editable_hours_per_1000_sqft"] = safe_number(decision.get("editable_hours_per_1000_sqft"), labor.get("editable_hours_per_1000_sqft"))
        labor["calculated_hours"] = safe_number(decision.get("calculated_hours"), 0.0)
        labor["total_hours"] = labor["calculated_hours"]
        labor["editable_total_hours"] = labor["calculated_hours"]
        labor["formula_mode"] = str(decision.get("formula_mode") or labor.get("formula_mode") or "mixed_formula")
        labor["formula_model"] = str(decision.get("formula_model") or "labor_cost_from_days_crew_rate")
        labor["formula_source"] = str(decision.get("formula_source") or "")
        labor["days_was_explicit"] = bool(decision.get("days_was_explicit"))
        labor["estimated_cost"] = safe_number(decision.get("estimated_cost"), 0.0) if labor["include"] else 0.0
        labor["calculated_output"] = labor["estimated_cost"]
        labor["decision_values"] = dict(decision.get("decision_values") or {})
        labor["editable_decision_value"] = dict(decision.get("editable_decision_value") or {})
        labor["calculated_output_summary"] = decision.get("calculated_output_summary")
        labor["workbook_cell_write_preview"] = decision.get("workbook_cell_write_preview") or []
        labor["notes"] = "Synced from roofing labor template decision row."


def _ai_scope_debug_context(recommendation: Any | None) -> dict[str, Any]:
    debug = _rec_value(recommendation, "debug", {}) or {}
    if not isinstance(debug, dict):
        return {}
    ai_debug = debug.get("ai_scope_interpreter") or {}
    return ai_debug if isinstance(ai_debug, dict) else {}


def _build_insulation_surface_rows_for_workbench(
    scope: dict[str, Any],
    *,
    notes: str = "",
    foam_row: dict[str, Any] | None = None,
    existing_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if not _is_insulation_scope(scope):
        return []
    product_context = _foam_product_context_from_row(foam_row)
    default_thickness = first_nonblank(
        (foam_row or {}).get("thickness_inches"),
        (foam_row or {}).get("foam_thickness_inches"),
        scope.get("foam_thickness_inches"),
    )
    if existing_rows:
        return apply_thickness_decisions(
            _records(existing_rows),
            product_context=product_context,
            foam_type=scope.get("foam_type"),
            default_thickness_inches=default_thickness,
        )
    return build_insulation_surface_decisions(
        scope,
        notes=notes,
        product_context=product_context,
        default_thickness_inches=default_thickness,
    )


def _area_bucket_for_sqft(area: Any) -> str:
    sqft = safe_number(area, 0.0)
    if sqft <= 0:
        return ""
    if sqft < 5_000:
        return "under_5k"
    if sqft < 15_000:
        return "5k_15k"
    if sqft < 50_000:
        return "15k_50k"
    return "50k_plus"


def _clean_filter_value(value: Any) -> Any:
    number = optional_number(value)
    if number is not None and number == 0:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def historical_filter_hash(filters: dict[str, Any] | None) -> str:
    payload = {key: _clean_filter_value(value) for key, value in (filters or {}).items()}
    payload = {key: value for key, value in payload.items() if value is not None}
    return hashlib.sha1(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:10]


def historical_filters_from_scope(scope: dict[str, Any] | None) -> dict[str, Any]:
    scope = scope or {}
    warranty_years = optional_number(first_nonblank(scope.get("warranty_years"), scope.get("warranty_target_years")))
    if _is_insulation_scope(scope):
        return {
            "division": "Insulation",
            "template_type": "insulation",
            "project_type": "",
            "substrate": "",
            "coating_type": first_nonblank(scope.get("coating_type"), ""),
            "warranty_years": None,
            "roof_condition": first_nonblank(scope.get("roof_condition"), ""),
            "access_complexity": first_nonblank(scope.get("access_complexity"), ""),
            "penetrations_complexity": first_nonblank(scope.get("penetrations_complexity"), scope.get("penetration_complexity"), ""),
            "area_bucket": "",
            "source_year": None,
            "pipeline_status": "",
            "completed_only": False,
            "include_repairs": True,
            "min_evidence_count": DEFAULT_MIN_EVIDENCE_COUNT,
        }
    return {
        "division": "Roofing",
        "template_type": "roofing",
        "project_type": first_nonblank(scope.get("project_type"), "roof coating"),
        "substrate": first_nonblank(scope.get("roof_type_substrate"), scope.get("substrate"), ""),
        "coating_type": first_nonblank(scope.get("coating_type"), ""),
        "warranty_years": warranty_years if warranty_years and warranty_years > 0 else None,
        "roof_condition": first_nonblank(scope.get("roof_condition"), ""),
        "access_complexity": first_nonblank(scope.get("access_complexity"), ""),
        "penetrations_complexity": first_nonblank(scope.get("penetrations_complexity"), scope.get("penetration_complexity"), ""),
        "area_bucket": _area_bucket_for_sqft(_estimate_area(scope)),
        "source_year": None,
        "pipeline_status": "",
        "completed_only": False,
        "include_repairs": True,
        "min_evidence_count": DEFAULT_MIN_EVIDENCE_COUNT,
    }


def _scope_from_recommendation(recommendation: Any) -> dict[str, Any]:
    parsed = dict(_rec_value(recommendation, "parsed_fields", {}) or {})
    dimension_summary = parsed.get("dimension_summary") or {}
    scope = {
        "division": first_nonblank(parsed.get("division"), ""),
        "template_type": first_nonblank(parsed.get("template_type"), ""),
        "project_type": first_nonblank(parsed.get("project_type"), "roof coating"),
        "roof_type_substrate": first_nonblank(parsed.get("substrate"), parsed.get("building_type"), parsed.get("roof_type"), ""),
        "building_type": first_nonblank(parsed.get("building_type"), ""),
        "gross_sqft": safe_number(
            parsed.get("gross_insulation_area_sqft")
            or parsed.get("gross_area_sqft")
            or dimension_summary.get("gross_insulation_area_sqft")
            or dimension_summary.get("gross_area_sqft"),
            0.0,
        ),
        "deduction_sqft": safe_number(
            parsed.get("opening_area_known_sqft")
            or parsed.get("deduction_area_sqft")
            or dimension_summary.get("opening_area_known_sqft")
            or dimension_summary.get("deduction_area_sqft"),
            0.0,
        ),
        "net_sqft": safe_number(
            parsed.get("net_insulation_area_sqft")
            or parsed.get("estimated_sqft")
            or parsed.get("surface_area_sqft")
            or parsed.get("net_area_sqft")
            or dimension_summary.get("net_insulation_area_sqft")
            or dimension_summary.get("net_area_sqft"),
            0.0,
        ),
        "warranty_years": safe_number(parsed.get("warranty_target_years") or parsed.get("warranty_years"), 0.0),
        "coating_type": first_nonblank(parsed.get("coating_type"), ""),
        "roof_condition": first_nonblank(parsed.get("roof_condition"), ""),
        "access_complexity": first_nonblank(parsed.get("access_complexity"), ""),
        "penetrations_complexity": first_nonblank(parsed.get("penetrations_complexity"), parsed.get("penetration_complexity"), ""),
        "penetration_count": parsed.get("penetration_count"),
        "notes": first_nonblank(parsed.get("notes"), parsed.get("raw_notes"), parsed.get("field_notes"), parsed.get("input_notes"), ""),
    }
    for field in (
        "building_footprint_length_ft",
        "building_footprint_width_ft",
        "footprint_area_sqft",
        "building_perimeter_ft",
        "wall_height_ft",
        "ceiling_included",
        "roof_underside_included",
        "outside_walls_included",
        "ceiling_area_sqft",
        "roof_center_height_ft",
        "ridge_height_ft",
        "roof_rise_ft",
        "roof_half_span_ft",
        "roof_rafter_length_ft",
        "roof_underside_area_sqft",
        "pitched_roof_underside_area_sqft",
        "roof_underside_area_formula",
        "roof_underside_source_text",
        "gross_wall_area_sqft",
        "gross_insulation_area_sqft",
        "opening_area_known_sqft",
        "opening_area_missing",
        "net_insulation_area_sqft",
        "openings",
        "foam_type",
        "foam_thickness_inches",
        "thickness_inches",
        "requested_timing",
        "building_installation_timing",
        "customer_name",
        "phone",
        "address",
        "missing_questions",
        "insulation_surface_areas",
        "insulation_deductions",
        "insulation_r_value_targets",
        "area_calculation_explanation",
    ):
        if field in parsed:
            scope[field] = parsed.get(field)
    if _is_insulation_scope(scope):
        scope["division"] = "Insulation"
        scope["template_type"] = "insulation"
        scope["project_type"] = first_nonblank(scope.get("project_type"), "spray foam insulation")
    return scope


def _plan_included_package(recommendation: Any, package: str) -> bool:
    package_text = _normalized(package)
    for row in _records(_rec_value(recommendation, "material_plan", [])):
        text = _normalized(" ".join(str(row.get(key) or "") for key in ("category", "package", "item", "notes")))
        if package_text in text and row.get("included_in_total") is not False:
            return True
        if package == "coating" and "coating" in text and row.get("included_in_total") is not False:
            return True
    return False


def _package_suggestion_status(recommendation: Any, package: str, scope: dict[str, Any] | None = None) -> str:
    package_text = _normalized(package)
    notes = _scope_note_text(recommendation, scope)
    note_text = _normalized(notes)
    if scope is not None and _is_insulation_scope(scope):
        if package == "foam":
            return "yes"
        if package == "thermal_barrier_coating":
            return "review"
        if package in {"membrane", "primer", "caulk_sealant"}:
            return "review" if _has_positive_note_signal(note_text, ["thermal barrier", "dc315", "ignition barrier", "seal", "caulk", "membrane", "primer"]) else "no"
        if package in {"lift", "delivery_fee", "generator", "space_heater", "freight", "abaa_audit", "drum_disposal", "misc_materials", "thinner"}:
            return "no"
    for row in _records(_rec_value(recommendation, "material_plan", [])):
        text = _normalized(" ".join(str(row.get(key) or "") for key in ("category", "package", "item", "notes")))
        if package_text in text or (package == "coating" and "coating" in text):
            if row.get("included_in_total") is False or row.get("needs_review") is True or row.get("review_required") is True:
                return "review"
            return "yes"
    if package == "coating" and first_nonblank((_rec_value(recommendation, "parsed_fields", {}) or {}).get("coating_type")):
        return "yes"
    if package == "primer" and _has_positive_note_signal(note_text, ["primer", "prime", "priming", "rust", "oxidation", "adhesion"]):
        return "review"
    if package == "seam_treatment" and _has_positive_note_signal(note_text, ["open seam", "open seams", "seam repair", "failed seam", "separate", "separating"]):
        return "review"
    if package == "fastener_treatment" and _has_positive_note_signal(note_text, ["fastener", "fasteners", "screw", "screws", "exposed fastener"]):
        return "review"
    if package == "caulk_detail" and _has_positive_note_signal(note_text, ["curb", "penetration", "pipe boot", "pitch pocket", "detail", "caulk", "sealant"]):
        return "review"
    return "no"


def _plan_included_labor(recommendation: Any, package: str) -> bool:
    for row in _records(_rec_value(recommendation, "labor_plan", [])):
        task = str(row.get("task") or row.get("labor_package") or "")
        if task == package and row.get("included_in_total") is not False:
            return True
    return False


def _labor_suggestion_status(recommendation: Any, package: str, scope: dict[str, Any] | None = None) -> str:
    notes = _scope_note_text(recommendation, scope)
    note_text = _normalized(notes)
    if scope is not None and _is_insulation_scope(scope):
        if package in {"labor_foam", "labor_set_up", "labor_clean_up", "labor_loading"}:
            return "yes"
        if package == "labor_traveling":
            return "review"
        if package == "labor_dc_315":
            return "review"
        if package in {"labor_mask", "labor_prime", "labor_membrane", "labor_misc", "meals_lodging"}:
            return "no"
    if scope is not None and package in BASELINE_COATING_LABOR and _is_coating_scope(scope, notes):
        return "yes"
    if package == "labor_prime":
        return "review" if _has_positive_note_signal(note_text, ["primer", "prime", "priming", "rust", "oxidation", "adhesion"]) else "no"
    if package == "labor_seam_sealer":
        return "review" if _has_positive_note_signal(note_text, ["open seam", "open seams", "seam repair", "failed seam", "separate", "separating"]) else "no"
    if package == "labor_details":
        return "review" if _has_positive_note_signal(note_text, ["curb", "penetration", "pipe boot", "pitch pocket", "detail"]) else "no"
    if package == "labor_caulk":
        return "review" if _has_positive_note_signal(note_text, ["caulk", "sealant", "detail"]) else "no"
    for row in _records(_rec_value(recommendation, "labor_plan", [])):
        task = str(row.get("task") or row.get("labor_package") or "")
        if task == package:
            if row.get("included_in_total") is False or row.get("needs_review") is True or row.get("review_required") is True:
                return "review"
            return "yes"
    return "no"


def _suggestion_reason(package: str, scope: dict[str, Any], status: str) -> str:
    condition = _normalized(scope.get("roof_condition"))
    penetrations = _normalized(scope.get("penetrations_complexity"))
    if _is_insulation_scope(scope):
        if package == "foam":
            return "Shown for estimator review because the notes request spray foam insulation."
        if package == "thermal_barrier_coating":
            return "Shown for estimator review because thermal/ignition barrier requirements must be confirmed."
        if package.startswith("labor_"):
            return "Shown because this is a common insulation labor template row."
        return "Shown but unchecked; available for insulation estimator adjustment."
    if package == "coating" and status == "yes":
        return "Filled in because the notes describe a coating/restoration scope."
    if package == "primer":
        if status in {"yes", "review"}:
            return "Filled in for estimator review because the notes indicate substrate or condition concerns."
        return "Shown but unchecked because notes do not mention primer, adhesion, rust, bleed-through, or manufacturer primer requirements."
    if package == "seam_treatment":
        if status in {"yes", "review"}:
            return "Filled in because the notes mention seam or detail work."
        return "Shown but unchecked because notes do not mention open seams, failed seams, seam repair, or leaks."
    if package == "fastener_treatment":
        if status in {"yes", "review"}:
            return "Filled in because the notes mention fasteners or exposed-fastener metal roof details."
        return "Shown but unchecked because notes do not mention exposed fasteners or fastener repairs."
    if package == "caulk_detail":
        if status in {"yes", "review"} or "high" in penetrations:
            return "Filled in because the notes indicate detail or penetration work."
        return "Shown but unchecked because notes do not indicate heavy details or penetration repairs."
    if package.startswith("labor_") and status in {"yes", "review"}:
        return "Filled in because this labor package appears in the historical company default set for this scope."
    if package.startswith("labor_prime"):
        return "Shown but unchecked because primer is not currently included."
    if condition in {"excellent", "good"} and package in {"labor_seam_sealer", "labor_details"}:
        return "Shown but unchecked because the described condition is clean/light and does not call for heavy detail labor."
    return "Shown but unchecked; available for estimator adjustment."


def _relationship_score(row: pd.Series, scope: dict[str, Any], package: str) -> float:
    score = safe_number(row.get("evidence_count") or row.get("job_count"), 0)
    if _normalized(row.get("package")) == _normalized(package):
        score += 1000
    if _normalized(row.get("division")) == "roofing":
        score += 100
    if _normalized(row.get("template_type")) == "roofing":
        score += 60
    substrate = _normalized(scope.get("roof_type_substrate"))
    if substrate and substrate in _normalized(row.get("substrate")):
        score += 40
    coating_type = _normalized(scope.get("coating_type"))
    if coating_type and coating_type in _normalized(row.get("coating_type")):
        score += 30
    warranty = optional_number(scope.get("warranty_years"))
    row_warranty = optional_number(row.get("warranty_years"))
    if warranty is not None and row_warranty is not None and int(warranty) == int(row_warranty):
        score += 20
    return score


def best_relationship_row(frame: pd.DataFrame, package: str, scope: dict[str, Any]) -> dict[str, Any] | None:
    if frame.empty or "package" not in frame.columns:
        return None
    rows = frame[frame["package"].astype(str).str.lower().eq(str(package).lower())].copy()
    if rows.empty:
        return None
    rows["_workbench_score"] = rows.apply(lambda row: _relationship_score(row, scope, package), axis=1)
    for column in ("evidence_count", "job_count"):
        if column not in rows.columns:
            rows[column] = 0
    rows = rows.sort_values(["_workbench_score", "evidence_count", "job_count"], ascending=False, na_position="last")
    return rows.iloc[0].drop(labels=["_workbench_score"], errors="ignore").to_dict()


def _material_distribution_from_relationships(
    data: Any,
    package: str,
    default_unit: str,
    reasons: dict[str, int],
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ratios = _frame(data, "relationship_material_qty_ratios")
    if ratios.empty:
        return {}
    package_rows = ratios[_package_match_series(ratios, package)].copy()
    if package_rows.empty:
        return {}
    eligible, scoped_reasons = _scope_filter_diagnostics(package_rows, filters)
    for reason, count in scoped_reasons.items():
        _add_reason(reasons, f"relationship_{reason}", count)

    def accepted_count(candidate_rows: pd.DataFrame) -> int:
        values = _numeric_series(candidate_rows, "median_qty_per_sqft")
        accepted_rows = candidate_rows[values.notna() & (values > 0)].copy()
        return _evidence_count_from_rows(accepted_rows)

    eligible, filter_summary = _filter_rows_with_relaxation(eligible, filters, accepted_count)
    median_values = _numeric_series(eligible, "median_qty_per_sqft")
    accepted = eligible[median_values.notna() & (median_values > 0)].copy()
    cost_values = _numeric_series(eligible, "median_cost_per_sqft")
    cost_rows = eligible[cost_values.notna() & (cost_values > 0)].copy()
    _add_reason(reasons, "relationship_missing_qty_per_sqft", len(eligible) - len(accepted))
    if accepted.empty:
        return _with_distribution_metadata(
            {
            "median": 0.0,
            "p25": 0.0,
            "p75": 0.0,
            "median_cost_per_sqft": _positive_percentile(cost_rows.get("median_cost_per_sqft", pd.Series(dtype=float)), 0.5),
            "historical_cost_evidence_count": _evidence_count_from_rows(cost_rows),
            "source": "relationship_material_qty_ratios_full_corpus",
            "historical_jobs_found": _evidence_count_from_rows(package_rows),
            "rows_accepted": 0,
            "rows_rejected": len(package_rows),
            "rejection_reasons": _format_reasons(reasons),
            },
            filter_summary,
        )
    evidence_count = _evidence_count_from_rows(accepted)
    unit = first_nonblank(next((value for value in accepted.get("unit", pd.Series(dtype=object)).dropna().astype(str) if value.strip()), ""), default_unit)
    return _with_distribution_metadata(
        {
            "median": _positive_percentile(accepted["median_qty_per_sqft"], 0.5),
            "p25": _positive_percentile(accepted.get("p25_qty_per_sqft", accepted["median_qty_per_sqft"]), 0.5) or _positive_percentile(accepted["median_qty_per_sqft"], 0.25),
            "p75": _positive_percentile(accepted.get("p75_qty_per_sqft", accepted["median_qty_per_sqft"]), 0.5) or _positive_percentile(accepted["median_qty_per_sqft"], 0.75),
            "median_cost_per_sqft": _positive_percentile(cost_rows.get("median_cost_per_sqft", pd.Series(dtype=float)), 0.5),
            "historical_cost_evidence_count": _evidence_count_from_rows(cost_rows),
            "evidence_count": evidence_count,
            "historical_jobs_found": _evidence_count_from_rows(package_rows),
            "rows_accepted": len(accepted),
            "rows_rejected": len(package_rows) - len(accepted),
            "rejection_reasons": _format_reasons(reasons),
            "unit": unit,
            "confidence": _confidence(evidence_count),
            "source": "relationship_material_qty_ratios_full_corpus",
        },
        filter_summary,
    )


def material_sizing_distribution(
    data: Any,
    package: str,
    default_unit: str,
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    summary = _frame(data, "job_package_summary")
    reasons: dict[str, int] = {}
    history_diag = _bucket_history_diagnostics(data, package, filters)
    template_model = _insulation_foam_template_model_distribution(data, package, filters)

    def attach_template_model(distribution: dict[str, Any]) -> dict[str, Any]:
        if template_model:
            distribution.update(template_model)
            if safe_number(distribution.get("evidence_count"), 0) <= 0:
                distribution["evidence_count"] = int(safe_number(template_model.get("foam_template_model_evidence_count"), 0))
                distribution["confidence"] = _confidence(distribution["evidence_count"])
            distribution["unit"] = "estimated_units"
        return distribution

    if summary.empty:
        fallback = _material_distribution_from_relationships(data, package, default_unit, reasons, filters)
        if fallback:
            fallback.update(history_diag)
            return attach_template_model(fallback)
        return _with_distribution_metadata(
            attach_template_model({
            "median": 0.0,
            "p25": 0.0,
            "p75": 0.0,
            "evidence_count": 0,
            "historical_jobs_found": 0,
            "rows_accepted": 0,
            "rows_rejected": 0,
            "rejection_reasons": "job_package_summary_empty",
            "unit": default_unit,
            "confidence": "none",
            "source": "no_sufficient_evidence",
            **history_diag,
            }),
        )
    package_rows = summary[_package_match_series(summary, package)].copy()
    if package_rows.empty:
        fallback = _material_distribution_from_relationships(data, package, default_unit, reasons, filters)
        if fallback:
            fallback.setdefault("median", 0.0)
            fallback.setdefault("p25", 0.0)
            fallback.setdefault("p75", 0.0)
            fallback.setdefault("evidence_count", 0)
            fallback.setdefault("unit", default_unit)
            fallback.setdefault("confidence", _confidence(fallback.get("evidence_count", 0)))
            fallback.update(history_diag)
            return attach_template_model(fallback)
        return attach_template_model({
            "median": 0.0,
            "p25": 0.0,
            "p75": 0.0,
            "evidence_count": 0,
            "historical_jobs_found": 0,
            "rows_accepted": 0,
            "rows_rejected": 0,
            "rejection_reasons": "no_package_rows_found",
            "unit": default_unit,
            "confidence": "none",
            "source": "no_sufficient_evidence",
            **history_diag,
        })

    eligible, scoped_reasons = _scope_filter_diagnostics(package_rows, filters)
    reasons.update(scoped_reasons)

    def quantity_evidence_count(candidate_rows: pd.DataFrame) -> int:
        area = _numeric_series(candidate_rows, "area_sqft")
        total_quantity = _numeric_series(candidate_rows, "total_quantity")
        qty_per_sqft = _numeric_series(candidate_rows, "qty_per_sqft")
        computed_qty_per_sqft = total_quantity / area
        candidate_qty = qty_per_sqft.where(qty_per_sqft.notna() & (qty_per_sqft > 0), computed_qty_per_sqft)
        if "has_physical_quantity" in candidate_rows.columns:
            physical_mask = candidate_rows["has_physical_quantity"].map(_truthy)
        elif "physical_quantity_valid" in candidate_rows.columns:
            physical_mask = candidate_rows["physical_quantity_valid"].map(_truthy)
        else:
            physical_mask = (candidate_qty > 0) | (total_quantity > 0)
        bad_units = _text_series(candidate_rows, "unit").map(_normalized).isin({"mixed", "allowance", "usd", "$", "dollar", "dollars"})
        accepted_rows = candidate_rows[physical_mask & ~bad_units & candidate_qty.notna() & (candidate_qty > 0)].copy()
        return _job_count(accepted_rows)

    eligible, filter_summary = _filter_rows_with_relaxation(eligible, filters, quantity_evidence_count)
    area = _numeric_series(eligible, "area_sqft")
    total_quantity = _numeric_series(eligible, "total_quantity")
    qty_per_sqft = _numeric_series(eligible, "qty_per_sqft")
    computed_qty_per_sqft = total_quantity / area
    eligible["_workbench_qty_per_sqft"] = qty_per_sqft.where(qty_per_sqft.notna() & (qty_per_sqft > 0), computed_qty_per_sqft)
    cost_per_sqft = _numeric_series(eligible, "cost_per_sqft")
    if "has_physical_quantity" in eligible.columns:
        physical_mask = eligible["has_physical_quantity"].map(_truthy)
    elif "physical_quantity_valid" in eligible.columns:
        physical_mask = eligible["physical_quantity_valid"].map(_truthy)
    else:
        physical_mask = (eligible["_workbench_qty_per_sqft"] > 0) | (total_quantity > 0)
    bad_units = _text_series(eligible, "unit").map(_normalized).isin({"mixed", "allowance", "usd", "$", "dollar", "dollars"})
    missing_sqft = area.isna() | (area <= 0)
    missing_quantity = ~physical_mask | ((total_quantity.isna() | (total_quantity <= 0)) & (eligible["_workbench_qty_per_sqft"].isna() | (eligible["_workbench_qty_per_sqft"] <= 0)))
    missing_qty_per_sqft = eligible["_workbench_qty_per_sqft"].isna() | (eligible["_workbench_qty_per_sqft"] <= 0)
    _add_reason(reasons, "mixed_or_allowance_unit", int(bad_units.sum()))
    _add_reason(reasons, "missing_sqft", int(missing_sqft.sum()))
    _add_reason(reasons, "missing_physical_quantity", int(missing_quantity.sum()))
    _add_reason(reasons, "missing_qty_per_sqft", int(missing_qty_per_sqft.sum()))
    accepted = eligible[physical_mask & ~bad_units & ~missing_qty_per_sqft].copy()
    cost_rows = eligible[cost_per_sqft.notna() & (cost_per_sqft > 0)].copy()
    rejected_missing_area = int(missing_sqft.sum())
    rejected_missing_quantity = int(missing_quantity.sum())
    rejected_missing_cost = int(len(eligible) - len(cost_rows))
    rejected_filter_mismatch = int(sum(scoped_reasons.values())) if scoped_reasons else 0
    if accepted.empty:
        fallback = _material_distribution_from_relationships(data, package, default_unit, reasons, filters)
        if fallback and (safe_number(fallback.get("median"), 0) > 0 or safe_number(fallback.get("median_cost_per_sqft"), 0) > 0):
            fallback.update(
                {
                    **history_diag,
                    "accepted_qty_per_sqft_rows": 0,
                    "rejected_missing_area": rejected_missing_area,
                    "rejected_missing_quantity": rejected_missing_quantity,
                    "rejected_missing_cost": rejected_missing_cost,
                    "rejected_filter_mismatch": rejected_filter_mismatch,
                }
            )
            return attach_template_model(fallback)
        historical_jobs = _job_count(package_rows)
        return _with_distribution_metadata(
            attach_template_model({
                "median": 0.0,
                "p25": 0.0,
                "p75": 0.0,
                "median_cost_per_sqft": _positive_percentile(cost_rows.get("cost_per_sqft", pd.Series(dtype=float)), 0.5),
                "historical_cost_evidence_count": _job_count(cost_rows),
                "evidence_count": 0,
                "historical_jobs_found": historical_jobs,
                "rows_accepted": 0,
                "rows_rejected": len(package_rows),
                "rejection_reasons": _format_reasons(reasons),
                "unit": default_unit,
                "confidence": "none",
                "source": "no_sufficient_evidence",
                **history_diag,
                "accepted_qty_per_sqft_rows": 0,
                "rejected_missing_area": rejected_missing_area,
                "rejected_missing_quantity": rejected_missing_quantity,
                "rejected_missing_cost": rejected_missing_cost,
                "rejected_filter_mismatch": rejected_filter_mismatch,
            }),
            filter_summary,
        )
    evidence_count = _job_count(accepted)
    unit = first_nonblank(next((value for value in accepted.get("unit", pd.Series(dtype=object)).dropna().astype(str) if value.strip() and _normalized(value) != "mixed"), ""), default_unit)
    return _with_distribution_metadata(
        attach_template_model({
            "median": _positive_percentile(accepted["_workbench_qty_per_sqft"], 0.5),
            "p25": _positive_percentile(accepted["_workbench_qty_per_sqft"], 0.25),
            "p75": _positive_percentile(accepted["_workbench_qty_per_sqft"], 0.75),
            "median_cost_per_sqft": _positive_percentile(cost_rows.get("cost_per_sqft", pd.Series(dtype=float)), 0.5),
            "historical_cost_evidence_count": _job_count(cost_rows),
            "evidence_count": evidence_count,
            "historical_jobs_found": _job_count(package_rows),
            "rows_accepted": len(accepted),
            "rows_rejected": len(package_rows) - len(accepted),
            "rejection_reasons": _format_reasons(reasons),
            "unit": unit,
            "confidence": _confidence(evidence_count),
            "source": "job_package_summary_filtered",
            **history_diag,
            "accepted_qty_per_sqft_rows": len(accepted),
            "rejected_missing_area": rejected_missing_area,
            "rejected_missing_quantity": rejected_missing_quantity,
            "rejected_missing_cost": rejected_missing_cost,
            "rejected_filter_mismatch": rejected_filter_mismatch,
        }),
        filter_summary,
    )


def _labor_distribution_from_relationships(
    data: Any,
    package: str,
    reasons: dict[str, int],
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rates = _frame(data, "relationship_labor_rates")
    if rates.empty:
        return {}
    package_rows = rates[_package_match_series(rates, package)].copy()
    if package_rows.empty:
        return {}
    eligible, scoped_reasons = _scope_filter_diagnostics(package_rows, filters)
    for reason, count in scoped_reasons.items():
        _add_reason(reasons, f"relationship_{reason}", count)

    def accepted_count(candidate_rows: pd.DataFrame) -> int:
        values = _numeric_series(candidate_rows, "median_hours_per_1000_sqft")
        accepted_rows = candidate_rows[values.notna() & (values > 0)].copy()
        return _evidence_count_from_rows(accepted_rows)

    eligible, filter_summary = _filter_rows_with_relaxation(eligible, filters, accepted_count)
    median_values = _numeric_series(eligible, "median_hours_per_1000_sqft")
    accepted = eligible[median_values.notna() & (median_values > 0)].copy()
    _add_reason(reasons, "relationship_missing_hours_per_1000_sqft", len(eligible) - len(accepted))
    if accepted.empty:
        return _with_distribution_metadata(
            {
            "source": "relationship_labor_rates_full_corpus",
            "historical_jobs_found": _evidence_count_from_rows(package_rows),
            "rows_accepted": 0,
            "rows_rejected": len(package_rows),
            "rejection_reasons": _format_reasons(reasons),
            },
            filter_summary,
        )
    evidence_count = _evidence_count_from_rows(accepted)
    return _with_distribution_metadata(
        {
            "median": _positive_percentile(accepted["median_hours_per_1000_sqft"], 0.5),
            "p25": _positive_percentile(accepted.get("p25_hours_per_1000_sqft", accepted["median_hours_per_1000_sqft"]), 0.5) or _positive_percentile(accepted["median_hours_per_1000_sqft"], 0.25),
            "p75": _positive_percentile(accepted.get("p75_hours_per_1000_sqft", accepted["median_hours_per_1000_sqft"]), 0.5) or _positive_percentile(accepted["median_hours_per_1000_sqft"], 0.75),
            "evidence_count": evidence_count,
            "historical_jobs_found": _evidence_count_from_rows(package_rows),
            "rows_accepted": len(accepted),
            "rows_rejected": len(package_rows) - len(accepted),
            "rejection_reasons": _format_reasons(reasons),
            "median_crew_size": safe_number(accepted.get("median_crew_size", pd.Series([4])).median(), 4),
            "confidence": _confidence(evidence_count),
            "source": "relationship_labor_rates_full_corpus",
        },
        filter_summary,
    )


def labor_sizing_distribution(data: Any, package: str, filters: dict[str, Any] | None = None) -> dict[str, Any]:
    summary = _frame(data, "job_package_summary")
    reasons: dict[str, int] = {}
    history_diag = _bucket_history_diagnostics(data, package, filters)
    if summary.empty:
        fallback = _labor_distribution_from_relationships(data, package, reasons, filters)
        if fallback:
            fallback.update(history_diag)
            return fallback
        return {
            "median": 0.0,
            "p25": 0.0,
            "p75": 0.0,
            "evidence_count": 0,
            "historical_jobs_found": 0,
            "rows_accepted": 0,
            "rows_rejected": 0,
            "rejection_reasons": "job_package_summary_empty",
            "median_crew_size": 4,
            "median_days": 0.0,
            "median_daily_rate": 0.0,
            "median_hourly_rate": 0.0,
            "formula_mode": "",
            "confidence": "none",
            "source": "no_sufficient_evidence",
            **history_diag,
        }
    package_rows = summary[_package_match_series(summary, package)].copy()
    if package_rows.empty:
        fallback = _labor_distribution_from_relationships(data, package, reasons, filters)
        if fallback:
            fallback.setdefault("median", 0.0)
            fallback.setdefault("p25", 0.0)
            fallback.setdefault("p75", 0.0)
            fallback.setdefault("evidence_count", 0)
            fallback.setdefault("median_crew_size", 4)
            fallback.setdefault("median_days", 0.0)
            fallback.setdefault("median_daily_rate", 0.0)
            fallback.setdefault("median_hourly_rate", 0.0)
            fallback.setdefault("formula_mode", "")
            fallback.setdefault("confidence", _confidence(fallback.get("evidence_count", 0)))
            fallback.update(history_diag)
            return fallback
        return {
            "median": 0.0,
            "p25": 0.0,
            "p75": 0.0,
            "evidence_count": 0,
            "historical_jobs_found": 0,
            "rows_accepted": 0,
            "rows_rejected": 0,
            "rejection_reasons": "no_package_rows_found",
            "median_crew_size": 4,
            "median_days": 0.0,
            "median_daily_rate": 0.0,
            "median_hourly_rate": 0.0,
            "formula_mode": "",
            "confidence": "none",
            "source": "no_sufficient_evidence",
            **history_diag,
        }
    eligible, scoped_reasons = _scope_filter_diagnostics(package_rows, filters)
    reasons.update(scoped_reasons)

    def labor_evidence_count(candidate_rows: pd.DataFrame) -> int:
        area = _numeric_series(candidate_rows, "area_sqft")
        total_hours = _numeric_series(candidate_rows, "total_hours")
        hours_per_sqft = _numeric_series(candidate_rows, "hours_per_sqft")
        computed_hours_per_sqft = total_hours / area
        hours_per_1000 = hours_per_sqft.where(hours_per_sqft.notna() & (hours_per_sqft > 0), computed_hours_per_sqft) * 1000
        accepted_rows = candidate_rows[hours_per_1000.notna() & (hours_per_1000 > 0)].copy()
        return _job_count(accepted_rows)

    eligible, filter_summary = _filter_rows_with_relaxation(eligible, filters, labor_evidence_count)
    area = _numeric_series(eligible, "area_sqft")
    total_hours = _numeric_series(eligible, "total_hours")
    hours_per_sqft = _numeric_series(eligible, "hours_per_sqft")
    computed_hours_per_sqft = total_hours / area
    eligible["_workbench_hours_per_1000"] = hours_per_sqft.where(hours_per_sqft.notna() & (hours_per_sqft > 0), computed_hours_per_sqft) * 1000
    missing_sqft = area.isna() | (area <= 0)
    missing_hours = (total_hours.isna() | (total_hours <= 0)) & (hours_per_sqft.isna() | (hours_per_sqft <= 0))
    missing_hours_rate = eligible["_workbench_hours_per_1000"].isna() | (eligible["_workbench_hours_per_1000"] <= 0)
    _add_reason(reasons, "missing_sqft", int(missing_sqft.sum()))
    _add_reason(reasons, "missing_hours", int(missing_hours.sum()))
    _add_reason(reasons, "missing_hours_per_1000_sqft", int(missing_hours_rate.sum()))
    accepted = eligible[~missing_hours_rate].copy()
    rejected_filter_mismatch = int(sum(scoped_reasons.values())) if scoped_reasons else 0
    if accepted.empty:
        fallback = _labor_distribution_from_relationships(data, package, reasons, filters)
        if fallback and safe_number(fallback.get("median"), 0) > 0:
            fallback.update(
                {
                    **history_diag,
                    "accepted_qty_per_sqft_rows": 0,
                    "rejected_missing_area": int(missing_sqft.sum()),
                    "rejected_missing_quantity": int(missing_hours.sum()),
                    "rejected_missing_cost": 0,
                    "rejected_filter_mismatch": rejected_filter_mismatch,
                }
            )
            return fallback
        return _with_distribution_metadata(
            {
                "median": 0.0,
                "p25": 0.0,
                "p75": 0.0,
                "evidence_count": 0,
                "historical_jobs_found": _job_count(package_rows),
                "rows_accepted": 0,
                "rows_rejected": len(package_rows),
                "rejection_reasons": _format_reasons(reasons),
                "median_crew_size": 4,
                "median_days": 0.0,
                "median_daily_rate": 0.0,
                "median_hourly_rate": 0.0,
                "formula_mode": "",
                "confidence": "none",
                "source": "no_sufficient_evidence",
                **history_diag,
                "accepted_qty_per_sqft_rows": 0,
                "rejected_missing_area": int(missing_sqft.sum()),
                "rejected_missing_quantity": int(missing_hours.sum()),
                "rejected_missing_cost": 0,
                "rejected_filter_mismatch": rejected_filter_mismatch,
            },
            filter_summary,
        )
    evidence_count = _job_count(accepted)
    crew = _numeric_series(accepted, "crew_size")
    crew = crew[crew.notna() & (crew > 0)]
    days = _numeric_series(accepted, "days")
    days = days[days.notna() & (days > 0)]
    daily_rate = _numeric_series(accepted, "daily_rate")
    daily_rate = daily_rate[daily_rate.notna() & (daily_rate > 0)]
    hourly_rate = _numeric_series(accepted, "hourly_rate")
    if hourly_rate.isna().all():
        hourly_rate = _numeric_series(accepted, "blended_rate")
    if hourly_rate.isna().all() and "total_cost" in accepted.columns:
        hourly_rate = _numeric_series(accepted, "total_cost") / total_hours
    hourly_rate = hourly_rate[hourly_rate.notna() & (hourly_rate > 0)]
    formula_mode = _mode_text(accepted.get("formula_mode", pd.Series(dtype=object)).dropna().astype(str).tolist()) if "formula_mode" in accepted.columns else ""
    return _with_distribution_metadata(
        {
            "median": _positive_percentile(accepted["_workbench_hours_per_1000"], 0.5),
            "p25": _positive_percentile(accepted["_workbench_hours_per_1000"], 0.25),
            "p75": _positive_percentile(accepted["_workbench_hours_per_1000"], 0.75),
            "evidence_count": evidence_count,
            "historical_jobs_found": _job_count(package_rows),
            "rows_accepted": len(accepted),
            "rows_rejected": len(package_rows) - len(accepted),
            "rejection_reasons": _format_reasons(reasons),
            "median_crew_size": float(crew.median()) if not crew.empty else 4,
            "median_days": float(days.median()) if not days.empty else 0.0,
            "median_daily_rate": float(daily_rate.median()) if not daily_rate.empty else 0.0,
            "median_hourly_rate": float(hourly_rate.median()) if not hourly_rate.empty else 0.0,
            "formula_mode": formula_mode,
            "confidence": _confidence(evidence_count),
            "source": "job_package_summary_full_corpus",
            **history_diag,
            "accepted_qty_per_sqft_rows": len(accepted),
            "rejected_missing_area": int(missing_sqft.sum()),
            "rejected_missing_quantity": int(missing_hours.sum()),
            "rejected_missing_cost": 0,
            "rejected_filter_mismatch": rejected_filter_mismatch,
        },
        filter_summary,
    )


def adder_sizing_distribution(data: Any, adder: str, area: float = 0.0, filters: dict[str, Any] | None = None) -> dict[str, Any]:
    summary = _frame(data, "job_package_summary")
    if summary.empty:
        return {
            "historical_usage_rate": 0.0,
            "median_cost_when_used": 0.0,
            "median_cost_per_sqft": 0.0,
            "editable_default": 0.0,
            "evidence_count": 0,
            "confidence": "none",
            "source": "no_sufficient_evidence",
            "rejection_reasons": "job_package_summary_empty",
        }
    package_rows = summary[_package_match_series(summary, adder)].copy()
    if package_rows.empty:
        return {
            "historical_usage_rate": 0.0,
            "median_cost_when_used": 0.0,
            "median_cost_per_sqft": 0.0,
            "editable_default": 0.0,
            "evidence_count": 0,
            "confidence": "none",
            "source": "no_sufficient_evidence",
            "rejection_reasons": "no_package_rows_found",
        }
    eligible, reasons = _scope_filter_diagnostics(package_rows, filters)

    def adder_evidence_count(candidate_rows: pd.DataFrame) -> int:
        total_cost = _numeric_series(candidate_rows, "total_cost")
        return _job_count(candidate_rows[total_cost.notna() & (total_cost > 0)])

    eligible, filter_summary = _filter_rows_with_relaxation(eligible, filters, adder_evidence_count)
    total_cost = _numeric_series(eligible, "total_cost")
    cost_per_sqft = _numeric_series(eligible, "cost_per_sqft")
    cost_rows = eligible[total_cost.notna() & (total_cost > 0)].copy()
    psf_rows = eligible[cost_per_sqft.notna() & (cost_per_sqft > 0)].copy()
    _add_reason(reasons, "missing_total_cost", len(eligible) - len(cost_rows))
    median_cost = _positive_percentile(cost_rows.get("total_cost", pd.Series(dtype=float)), 0.5)
    median_psf = _positive_percentile(psf_rows.get("cost_per_sqft", pd.Series(dtype=float)), 0.5)
    editable_default = median_cost if median_cost > 0 else median_psf * area if area and median_psf > 0 else 0.0
    denominator_rows = summary.copy()
    target_division = _normalized((filters or {}).get("division")) or "roofing"
    if "division" in denominator_rows.columns:
        division_rows = denominator_rows["division"].map(_normalized).eq(target_division)
        if division_rows.any():
            denominator_rows = denominator_rows[division_rows].copy()
    denominator = _job_count(denominator_rows)
    evidence_count = _job_count(cost_rows)
    usage_rate = round(min(_job_count(eligible) / denominator, 1.0), 4) if denominator else 0.0
    return _with_distribution_metadata(
        {
            "historical_usage_rate": usage_rate,
            "median_cost_when_used": median_cost,
            "median_cost_per_sqft": median_psf,
            "editable_default": editable_default,
            "evidence_count": evidence_count,
            "historical_jobs_found": _job_count(package_rows),
            "rows_accepted": len(cost_rows),
            "rows_rejected": len(package_rows) - len(cost_rows),
            "confidence": _confidence(evidence_count),
            "source": "job_package_summary_full_corpus" if evidence_count else "no_sufficient_evidence",
            "rejection_reasons": _format_reasons(reasons),
            "median": median_cost,
            "p25": _positive_percentile(cost_rows.get("total_cost", pd.Series(dtype=float)), 0.25),
            "p75": _positive_percentile(cost_rows.get("total_cost", pd.Series(dtype=float)), 0.75),
        },
        filter_summary,
    )


def _is_reliable_adder_default(sizing: dict[str, Any]) -> bool:
    evidence_count = int(safe_number(sizing.get("evidence_count"), 0))
    median_cost = safe_number(sizing.get("median_cost_when_used"), 0.0)
    median_psf = safe_number(sizing.get("median_cost_per_sqft"), 0.0)
    if evidence_count < ADDER_MIN_RELIABLE_EVIDENCE:
        return False
    if median_cost > MAX_ADDER_DEFAULT_COST:
        return False
    if median_psf > MAX_ADDER_DEFAULT_COST_PER_SQFT:
        return False
    return True


def _price_for_package(pricing: pd.DataFrame, package_spec: dict[str, Any], scope: dict[str, Any]) -> tuple[float, str]:
    if pricing.empty:
        return 0.0, ""
    keywords = list(package_spec.get("keywords") or [])
    if package_spec["package"] == "coating" and scope.get("coating_type"):
        keywords.insert(0, str(scope.get("coating_type")))
    preferred = "price_per_gallon" if package_spec["package"] == "coating" else "unit_price"
    price = find_current_price(pricing, keywords, preferred)
    if not price:
        return 0.0, ""
    for column in (preferred, "price_per_unit", "unit_price", "price_per_sqft", "price_per_gallon"):
        number = optional_number(price.get(column))
        if number is not None and number > 0:
            label = first_nonblank(price.get("product_name"), price.get("description"), price.get("pricing_item_id"), "pricing_catalog")
            return number, str(label)
    return 0.0, ""


def _current_pricing_rows(pricing: pd.DataFrame) -> pd.DataFrame:
    if pricing.empty:
        return pricing
    rows = pricing.copy()
    if "is_current" in rows.columns:
        rows = rows[rows["is_current"].map(_truthy)].copy()
    if "status" in rows.columns:
        active = rows["status"].fillna("").astype(str).str.lower().isin({"", "active", "current"})
        rows = rows[active].copy()
    if "needs_review" in rows.columns:
        rows = rows[~rows["needs_review"].map(_truthy)].copy()
    return rows


def _pricing_options_for_package(pricing: pd.DataFrame, package_spec: dict[str, Any], scope: dict[str, Any]) -> list[dict[str, Any]]:
    current = _current_pricing_rows(pricing)
    if current.empty:
        return []
    package = str(package_spec.get("package") or "")
    preferred = "price_per_gallon" if package == "coating" else "unit_price"
    keywords = [str(keyword) for keyword in package_spec.get("keywords") or [] if str(keyword or "").strip()]
    if package == "coating" and scope.get("coating_type"):
        keywords.insert(0, str(scope.get("coating_type")))
    aliases = list(_package_aliases(package))
    haystack = current.apply(
        lambda row: _normalized(" ".join(str(row.get(column) or "") for column in ("product_name", "description", "category", "price_basis", "unit_of_measure"))),
        axis=1,
    )
    search_terms = {_normalized(term) for term in [*keywords, *aliases] if _normalized(term)}
    mask = pd.Series([False] * len(current), index=current.index)
    for term in search_terms:
        mask = mask | haystack.str.contains(re.escape(term), na=False)
    candidates = current[mask].copy()
    if candidates.empty and package == "coating":
        candidates = current[haystack.str.contains("coating|silicone|acrylic", regex=True, na=False)].copy()
    options: list[dict[str, Any]] = []
    seen: set[str] = set()
    for _, row in candidates.iterrows():
        item_name = first_nonblank(row.get("product_name"), row.get("description"), row.get("pricing_item_id"), "")
        if not item_name:
            continue
        key = _normalized(item_name)
        if key in seen:
            continue
        seen.add(key)
        unit_price = _price_value_from_row(row, preferred)
        options.append(
            {
                "item_name": str(item_name),
                "unit": _unit_from_row(row, str(package_spec.get("default_unit") or "unit")),
                "unit_price": unit_price,
                "pricing_item_id": row.get("pricing_item_id"),
                "source": "current_pricing",
            }
        )
    options.sort(key=lambda option: (0 if option.get("unit_price") else 1, safe_number(option.get("unit_price"), 0), option.get("item_name") or ""))
    return options


def _contains_any_text(text: str, terms: list[str]) -> bool:
    normalized = _normalized(text)
    return any(term and term in normalized for term in terms)


def _number_token_value(value: str | None) -> float | None:
    if not value:
        return None
    text = _normalized(value).replace(",", "")
    number = optional_number(text)
    if number is not None:
        return number
    if text in NUMBER_WORDS:
        return NUMBER_WORDS[text]
    parts = [part for part in re.split(r"[\s-]+", text) if part]
    if not parts:
        return None
    total = 0.0
    for part in parts:
        if part not in NUMBER_WORDS:
            return None
        total += NUMBER_WORDS[part]
    return total or None


def _scope_note_text(recommendation: Any | None, scope: dict[str, Any] | None = None) -> str:
    scope = scope or {}
    parsed = _parsed_fields(recommendation) if recommendation is not None else {}
    return str(
        first_nonblank(
            scope.get("notes"),
            scope.get("raw_notes"),
            parsed.get("notes"),
            parsed.get("raw_notes"),
            parsed.get("field_notes"),
            parsed.get("input_notes"),
            "",
        )
        or ""
    )


def _is_coating_scope(scope: dict[str, Any], notes: str = "") -> bool:
    project_type = _normalized(scope.get("project_type"))
    coating_type = _normalized(scope.get("coating_type"))
    text = _normalized(notes)
    return bool(
        coating_type
        or "coating" in project_type
        or "restoration" in project_type
        or "coating" in text
        or "restoration" in text
        or "restore" in text
    )


def _partial_primer_basis_sqft(notes: str, area: float) -> float:
    if not notes or area <= 0:
        return 0.0
    text = _normalized(notes)
    if not re.search(r"\b(primer|prime|priming)\b", text):
        return 0.0

    number_word_pattern = (
        r"(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|"
        r"fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|"
        r"eighty|ninety)(?:[-\s](?:one|two|three|four|five|six|seven|eight|nine))?"
    )
    numeric_or_word = rf"(?:\d+(?:\.\d+)?|{number_word_pattern})"
    for match in re.finditer(rf"(?:approximately|about|around|roughly)?\s*(?P<value>{numeric_or_word})\s*(?:%|percent)\b", text):
        window = text[max(0, match.start() - 120) : min(len(text), match.end() + 120)]
        if not re.search(r"\b(primer|prime|priming)\b", window):
            continue
        percent = _number_token_value(match.group("value"))
        if percent is not None and 0 < percent <= 100:
            return round(area * percent / 100, 2)

    percent_patterns = [
        rf"(?:approximately|about|around|roughly)?\s*(?P<value>{numeric_or_word})\s*(?:%|percent)\b.{0,100}\b(?:primer|prime|priming)\b",
        rf"\b(?:primer|prime|priming)\b.{0,100}(?:approximately|about|around|roughly)?\s*(?P<value>{numeric_or_word})\s*(?:%|percent)\b",
    ]
    for pattern in percent_patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        percent = _number_token_value(match.group("value"))
        if percent is not None and 0 < percent <= 100:
            return round(area * percent / 100, 2)

    sqft_patterns = [
        r"\b(?:primer|prime|priming)\b.{0,60}(?P<value>\d[\d,]*(?:\.\d+)?)\s*(?:sq\s*ft|sqft|square feet)\b",
        r"(?P<value>\d[\d,]*(?:\.\d+)?)\s*(?:sq\s*ft|sqft|square feet)\b.{0,60}\b(?:primer|prime|priming)\b",
    ]
    for pattern in sqft_patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        sqft = _number_token_value(match.group("value"))
        if sqft is not None and sqft > 0:
            return round(min(sqft, area), 2)
    return 0.0


def _has_positive_note_signal(notes: str, terms: list[str]) -> bool:
    text = _normalized(notes)
    return any(term in text for term in terms)


def _is_forbidden_coating_option(option: dict[str, Any]) -> bool:
    text = _normalized(" ".join(str(option.get(key) or "") for key in ("item_name", "unit", "price_basis", "category")))
    return _contains_any_text(f" {text} ", COATING_FORBIDDEN_SIGNALS)


def _is_valid_coating_option(option: dict[str, Any]) -> bool:
    text = _normalized(" ".join(str(option.get(key) or "") for key in ("item_name", "unit", "price_basis", "category")))
    if _is_forbidden_coating_option(option):
        return False
    return _contains_any_text(text, COATING_REQUIRED_POSITIVE_SIGNALS) and _contains_any_text(text, COATING_UNIT_SIGNALS)


def _is_selectable_package_item(package: str, option: dict[str, Any], scope: dict[str, Any]) -> bool:
    text = _normalized(" ".join(str(option.get(key) or "") for key in ("item_name", "unit", "price_basis", "category")))
    if package == "coating":
        return _is_valid_coating_option(option)
    if package == "thermal_barrier_coating":
        return _contains_any_text(text, ["dc315", "dc 315", "thermal barrier", "ignition barrier"]) and not _contains_any_text(
            text, ["primer", "foam primer", "roof coating", "silicone", "sealant", "caulk"]
        )
    if package == "drum_disposal":
        return _contains_any_text(text, ["drum disposal", "disposal", "waste", "environmental"]) and not _contains_any_text(
            text, ["silicone", "roof coating", "coating", "primer", "foam"]
        )
    return True


def _package_item_fit_details(package: str, option: dict[str, Any], scope: dict[str, Any]) -> tuple[float, list[str]]:
    """Score whether an item belongs in a template bucket.

    This keeps broad keywords like "silicone" from letting flashing-grade sealants win the
    main coating row while still allowing those products on seam/detail buckets.
    """
    name = _normalized(option.get("item_name"))
    unit = _normalized(option.get("unit"))
    combined = f"{name} {unit}"
    score = 0.0
    reasons: list[str] = []
    coating_type = _normalized(scope.get("coating_type"))
    if coating_type and coating_type in combined:
        score += 25
        reasons.append(f"matches coating type {coating_type}")

    if package == "coating":
        if _contains_any_text(combined, COATING_REQUIRED_POSITIVE_SIGNALS):
            score += 160
            reasons.append("roof coating product signal")
        if _contains_any_text(combined, COATING_UNIT_SIGNALS):
            score += 120
            reasons.append("coating unit/package signal")
        if _contains_any_text(combined, COATING_FORBIDDEN_SIGNALS + ["detail", "seam"]):
            score -= 5000
            reasons.append("rejected as coating: sealant/detail/fastener signal")
        if _contains_any_text(combined, ["11 oz", "10 oz", "20 oz", "oz", "tube", "sausage", "cartridge"]):
            score -= 3000
            reasons.append("rejected as coating: small cartridge/tube unit")
    elif package == "primer":
        if _contains_any_text(combined, ["primer", "prime", "rust inhibitive", "epoxy primer", "acrylic primer"]):
            score += 250
            reasons.append("primer product signal")
        if _contains_any_text(combined, ["sealant", "caulk", "tube", "sausage", "fabric", "granule"]):
            score -= 500
            reasons.append("rejected as primer: conflicting sealant/fabric/granule signal")
    elif package == "seam_treatment":
        if _contains_any_text(combined, ["seam", "sealant", "flashing grade", "fabric", "caulk"]):
            score += 180
            reasons.append("seam/detail product signal")
        if _contains_any_text(combined, ["roof coating", "primer", "granule"]):
            score -= 250
            reasons.append("less suitable for seam treatment")
    elif package == "caulk_detail":
        if _contains_any_text(combined, ["caulk", "sealant", "flashing grade", "detail", "tube", "sausage"]):
            score += 220
            reasons.append("caulk/detail product signal")
        if _contains_any_text(combined, ["roof coating", "primer", "granule"]):
            score -= 250
            reasons.append("less suitable for caulk/detail")
    elif package == "fastener_treatment":
        if _contains_any_text(combined, ["fastener", "screw", "washer", "plate"]):
            score += 220
            reasons.append("fastener-specific product signal")
        if _contains_any_text(combined, ["sealant", "caulk"]):
            score -= 50
            reasons.append("sealant fallback only; no fastener-specific signal")
        if _contains_any_text(combined, ["roof coating", "primer", "granule"]):
            score -= 250
            reasons.append("less suitable for fastener treatment")
    elif package == "fabric":
        if _contains_any_text(combined, ["fabric", "roll", "seam fabric"]):
            score += 250
            reasons.append("fabric product signal")
        if _contains_any_text(combined, ["roof coating", "primer", "granule"]):
            score -= 250
            reasons.append("less suitable for fabric")
    elif package == "granules":
        if _contains_any_text(combined, ["granule", "granules", "ceramic granules", "broadcast", "bag"]):
            score += 250
            reasons.append("granules product signal")
        if _contains_any_text(combined, ["roof coating", "primer", "sealant", "caulk"]):
            score -= 250
            reasons.append("less suitable for granules")
    elif package in {"board_stock", "plates", "edge_metal", "gutter_downspouts"}:
        for term in _package_aliases(package):
            if term and term in combined:
                score += 120
                reasons.append(f"matches {package} signal")
        if _contains_any_text(combined, ["roof coating", "primer", "sealant", "caulk", "granule"]):
            score -= 200
            reasons.append(f"less suitable for {package}")
    elif package == "foam":
        if _contains_any_text(combined, ["spray foam", "closed cell", "closed-cell", "open cell", "open-cell", "spf", "foam insulation", "2.0 lb", "2 lb"]):
            score += 240
            reasons.append("spray foam insulation product signal")
        elif _contains_any_text(combined, ["foam"]):
            score += 80
            reasons.append("foam product signal")
        if _is_insulation_scope(scope) and _contains_any_text(combined, ["roof repair", "repair foam", "roof kit", "roofing foam", "roof foam", "roofing"]):
            score -= 900
            reasons.append("less suitable for wall/ceiling insulation than insulation foam")
        if _contains_any_text(combined, ["roof coating", "silicone", "primer", "sealant", "caulk", "tube"]):
            score -= 300
            reasons.append("less suitable for foam insulation")
    elif package == "thermal_barrier_coating":
        if _contains_any_text(combined, ["dc315", "dc 315", "thermal barrier", "ignition barrier"]):
            score += 260
            reasons.append("thermal/ignition barrier product signal")
        if _contains_any_text(combined, ["primer", "foam primer", "a4121"]):
            score -= 1000
            reasons.append("rejected as thermal barrier: primer product")
        if _contains_any_text(combined, ["roof coating", "silicone", "sealant", "caulk", "foam"]):
            score -= 350
            reasons.append("less suitable for thermal barrier")
    elif package == "drum_disposal":
        if _contains_any_text(combined, ["drum disposal", "disposal", "waste", "environmental"]):
            score += 250
            reasons.append("drum disposal service signal")
        if _contains_any_text(combined, ["silicone", "roof coating", "coating", "primer", "foam"]):
            score -= 1000
            reasons.append("rejected as drum disposal: material product")
    if not reasons:
        reasons.append("weak item/package match")
    return score, reasons


def _package_item_fit_score(package: str, option: dict[str, Any], scope: dict[str, Any]) -> float:
    return _package_item_fit_details(package, option, scope)[0]


def _historical_item_options(
    data: Any,
    package: str,
    filters: dict[str, Any] | None,
    default_unit: str,
) -> list[dict[str, Any]]:
    summary = _frame(data, "job_package_summary")
    if summary.empty:
        return []
    package_rows = summary[_package_match_series(summary, package)].copy()
    if package_rows.empty:
        return []
    eligible, _ = _scope_filter_diagnostics(package_rows, filters)

    def accepted_count(candidate_rows: pd.DataFrame) -> int:
        named = candidate_rows[candidate_rows.apply(lambda row: bool(_item_name_from_row(row)), axis=1)].copy()
        return _job_count(named)

    eligible, _ = _filter_rows_with_relaxation(eligible, filters, accepted_count)
    if eligible.empty:
        return []
    eligible["_workbench_item_name"] = eligible.apply(_item_name_from_row, axis=1)
    eligible = eligible[eligible["_workbench_item_name"].astype(str).str.strip().ne("")].copy()
    if eligible.empty:
        return []
    area = _numeric_series(eligible, "area_sqft")
    total_quantity = _numeric_series(eligible, "total_quantity")
    qty_per_sqft = _numeric_series(eligible, "qty_per_sqft")
    eligible["_workbench_qty_per_sqft"] = qty_per_sqft.where(qty_per_sqft.notna() & (qty_per_sqft > 0), total_quantity / area)
    cost_per_sqft = _numeric_series(eligible, "cost_per_sqft")
    options: list[dict[str, Any]] = []
    for item_name, group in eligible.groupby("_workbench_item_name", dropna=False):
        quantity_values = _numeric_series(group, "_workbench_qty_per_sqft")
        cost_values = cost_per_sqft.loc[group.index] if not cost_per_sqft.empty else pd.Series(dtype=float)
        unit = first_nonblank(next((value for value in group.get("unit", pd.Series(dtype=object)).dropna().astype(str) if value.strip()), ""), default_unit)
        evidence_count = _job_count(group)
        options.append(
            {
                "item_name": str(item_name),
                "unit": unit,
                "median_qty_per_sqft": _positive_percentile(quantity_values, 0.5),
                "median_cost_per_sqft": _positive_percentile(cost_values, 0.5),
                "evidence_count": evidence_count,
                "source": "historical_most_common_item",
            }
        )
    options.sort(key=lambda option: (safe_number(option.get("evidence_count"), 0), safe_number(option.get("median_qty_per_sqft"), 0)), reverse=True)
    return options


def _select_material_item(
    package: str,
    pricing_options: list[dict[str, Any]],
    historical_options: list[dict[str, Any]],
    scope: dict[str, Any],
    fallback_label: str,
    default_unit: str,
) -> dict[str, Any]:
    historical_by_name = {_normalized(option.get("item_name")): option for option in historical_options}
    note_terms = _normalized(" ".join(str(scope.get(key) or "") for key in ("coating_type", "project_type", "roof_type_substrate")))
    if pricing_options:
        scored = []
        for option in pricing_options:
            name = _normalized(option.get("item_name"))
            score, reasons = _package_item_fit_details(package, option, scope)
            if name in historical_by_name:
                score += 1000 + safe_number(historical_by_name[name].get("evidence_count"), 0)
                reasons.append("used historically for this package")
            if note_terms and any(term and term in name for term in note_terms.split()):
                score += 10
                reasons.append("matches parsed scope wording")
            scored.append((score, option, reasons))
        scored.sort(key=lambda item: (item[0], -safe_number(item[1].get("unit_price"), 0)), reverse=True)
        selectable = [item for item in scored if _is_selectable_package_item(package, item[1], scope)]
        strict_item_packages = {"coating", "thermal_barrier_coating", "drum_disposal"}
        if package in strict_item_packages and not selectable:
            bad_reasons = [
                {
                    "item_name": option.get("item_name"),
                    "score": round(float(score), 2),
                    "reason": "; ".join(reasons),
                }
                for score, option, reasons in scored[:6]
            ]
            if historical_options:
                historical_scored = []
                for option in historical_options:
                    score, reasons = _package_item_fit_details(package, option, scope)
                    historical_scored.append((score, option, reasons))
                historical_scored.sort(key=lambda item: (item[0], safe_number(item[1].get("evidence_count"), 0)), reverse=True)
                historical_selectable = [item for item in historical_scored if _is_selectable_package_item(package, item[1], scope)]
                if historical_selectable:
                    selected = dict(historical_selectable[0][1])
                    selected["unit_price"] = 0.0
                    selected["item_source"] = "historical_most_common_item" if safe_number(selected.get("median_qty_per_sqft"), 0) > 0 else "historical_cost_default"
                    selected["item_median_qty_per_sqft"] = selected.get("median_qty_per_sqft", 0.0)
                    selected["item_median_cost_per_sqft"] = selected.get("median_cost_per_sqft", 0.0)
                    selected["item_evidence_count"] = selected.get("evidence_count", 0)
                    selected["selected_item_reason"] = f"Selected from historical {package.replace('_', ' ')} usage because no suitable current pricing item matched."
                    selected["selected_item_score"] = round(float(historical_selectable[0][0]), 2)
                    selected["top_rejected_item_reasons"] = bad_reasons
                    return selected
            return {
                "item_name": "Manual roof coating item" if package == "coating" else fallback_label,
                "unit": default_unit,
                "unit_price": 0.0,
                "item_source": "manual",
                "item_median_qty_per_sqft": 0.0,
                "item_median_cost_per_sqft": 0.0,
                "item_evidence_count": 0,
                "selected_item_reason": (
                    "No suitable roof coating pricing item matched; sealant/tube candidates were rejected for the main coating row."
                    if package == "coating"
                    else f"No suitable {package.replace('_', ' ')} pricing item matched; conflicting product candidates were rejected."
                ),
                "selected_item_score": 0.0,
                "top_rejected_item_reasons": bad_reasons,
            }
        selected_tuple = selectable[0] if selectable else scored[0]
        selected = dict(selected_tuple[1])
        selected["item_source"] = "current_pricing_plus_historical_usage" if _normalized(selected.get("item_name")) in historical_by_name else "current_pricing"
        selected["selected_item_reason"] = "; ".join(selected_tuple[2])
        selected["selected_item_score"] = round(float(selected_tuple[0]), 2)
        selected["top_rejected_item_reasons"] = [
            {
                "item_name": option.get("item_name"),
                "score": round(float(score), 2),
                "reason": "; ".join(reasons),
            }
            for score, option, reasons in scored
            if option is not selected_tuple[1]
        ]
        selected["top_rejected_item_reasons"] = selected["top_rejected_item_reasons"][:5]
        historical = historical_by_name.get(_normalized(selected.get("item_name")), {})
        selected["item_median_qty_per_sqft"] = historical.get("median_qty_per_sqft", 0.0)
        selected["item_median_cost_per_sqft"] = historical.get("median_cost_per_sqft", 0.0)
        selected["item_evidence_count"] = historical.get("evidence_count", 0)
        return selected
    if historical_options:
        historical_scored = []
        for option in historical_options:
            score, reasons = _package_item_fit_details(package, option, scope)
            historical_scored.append((score, option, reasons))
        historical_scored.sort(key=lambda item: (item[0], safe_number(item[1].get("evidence_count"), 0)), reverse=True)
        selectable = [item for item in historical_scored if _is_selectable_package_item(package, item[1], scope)]
        if package in {"coating", "thermal_barrier_coating", "drum_disposal"} and not selectable:
            return {
                "item_name": "Manual roof coating item" if package == "coating" else fallback_label,
                "unit": default_unit,
                "unit_price": 0.0,
                "item_source": "manual",
                "item_median_qty_per_sqft": 0.0,
                "item_median_cost_per_sqft": 0.0,
                "item_evidence_count": 0,
                "selected_item_reason": (
                    "No suitable historical roof coating item matched; manual item review required."
                    if package == "coating"
                    else f"No suitable historical {package.replace('_', ' ')} item matched; manual item review required."
                ),
                "selected_item_score": 0.0,
                "top_rejected_item_reasons": [
                    {"item_name": option.get("item_name"), "score": round(float(score), 2), "reason": "; ".join(reasons)}
                    for score, option, reasons in historical_scored[:5]
                ],
            }
        selected_tuple = selectable[0] if selectable else historical_scored[0]
        selected = dict(selected_tuple[1])
        selected["unit_price"] = 0.0
        selected["item_source"] = "historical_most_common_item" if safe_number(selected.get("median_qty_per_sqft"), 0) > 0 else "historical_cost_default"
        selected["item_median_qty_per_sqft"] = selected.get("median_qty_per_sqft", 0.0)
        selected["item_median_cost_per_sqft"] = selected.get("median_cost_per_sqft", 0.0)
        selected["item_evidence_count"] = selected.get("evidence_count", 0)
        selected["selected_item_reason"] = "Selected from historical package usage because no current pricing item matched."
        selected["selected_item_score"] = round(float(selected_tuple[0]), 2)
        selected["top_rejected_item_reasons"] = []
        return selected
    return {
        "item_name": fallback_label,
        "unit": default_unit,
        "unit_price": 0.0,
        "item_source": "manual",
        "item_median_qty_per_sqft": 0.0,
        "item_median_cost_per_sqft": 0.0,
        "item_evidence_count": 0,
        "selected_item_reason": "No current pricing or historical item matched; manual item review required.",
        "selected_item_score": 0.0,
        "top_rejected_item_reasons": [],
    }


def _item_options_payload(pricing_options: list[dict[str, Any]], historical_options: list[dict[str, Any]], selected: dict[str, Any]) -> str:
    options: dict[str, dict[str, Any]] = {}
    for option in [*historical_options, *pricing_options, selected]:
        name = str(option.get("item_name") or "").strip()
        if not name:
            continue
        existing = options.get(name, {})
        merged = {**existing, **option}
        options[name] = {
            "item_name": name,
            "unit": merged.get("unit"),
            "unit_price": safe_number(merged.get("unit_price"), 0.0),
            "item_source": merged.get("item_source") or merged.get("source") or "manual",
            "item_median_qty_per_sqft": safe_number(merged.get("item_median_qty_per_sqft") or merged.get("median_qty_per_sqft"), 0.0),
            "item_median_cost_per_sqft": safe_number(merged.get("item_median_cost_per_sqft") or merged.get("median_cost_per_sqft"), 0.0),
            "item_evidence_count": int(safe_number(merged.get("item_evidence_count") or merged.get("evidence_count"), 0)),
        }
    return json.dumps(list(options.values()), sort_keys=True, default=str)


def _pricing_option_for_item(row: dict[str, Any]) -> dict[str, Any] | None:
    item_name = _normalized(row.get("item_name"))
    if not item_name:
        return None
    try:
        options = json.loads(row.get("item_options_json") or "[]")
    except (TypeError, ValueError):
        options = []
    for option in options:
        if _normalized(option.get("item_name")) == item_name:
            return option
    return None


def _confidence(evidence_count: Any) -> str:
    count = safe_number(evidence_count, 0)
    if count >= 10:
        return "high"
    if count >= 5:
        return "medium"
    if count > 0:
        return "low"
    return "none"


def _historical_usage_rate(data: Any, package: str, scope: dict[str, Any], evidence_count: int) -> float:
    summary = _frame(data, "job_package_summary")
    if summary.empty or "job_id" not in summary.columns or "package" not in summary.columns:
        return 0.0
    rows = summary.copy()
    if "division" in rows.columns:
        target_division = _normalized(scope.get("division")) or ("insulation" if _is_insulation_scope(scope) else "roofing")
        division_rows = rows["division"].map(_normalized).eq(target_division)
        if division_rows.any():
            rows = rows[division_rows].copy()
    substrate = _normalized(first_nonblank(scope.get("roof_type_substrate"), scope.get("building_type"), scope.get("substrate")))
    if substrate and "substrate" in rows.columns:
        scoped = rows[rows["substrate"].astype(str).str.lower().str.contains(substrate, na=False)]
        if not scoped.empty:
            rows = scoped
    denominator = rows["job_id"].dropna().astype(str).nunique()
    if denominator <= 0:
        return 0.0
    package_jobs = rows[_package_match_series(rows, package)]["job_id"].dropna().astype(str).nunique()
    if package_jobs <= 0 and evidence_count > 0:
        package_jobs = evidence_count
    return round(min(package_jobs / denominator, 1.0), 4)


def _material_explanation(
    *,
    package: str,
    sizing: dict[str, Any],
    evidence_count: int,
    qty_per_sqft: float,
    status: str,
    scope: dict[str, Any],
    unit_price: float = 0.0,
    historical_cost_per_sqft: float = 0.0,
) -> str:
    reason = _suggestion_reason(package, scope, status)
    history_label = _history_label(scope)
    historical_jobs = int(safe_number(sizing.get("historical_jobs_found"), 0))
    total_bucket_rows = int(safe_number(sizing.get("total_insulation_rows_for_bucket"), 0))
    distinct_files = int(safe_number(sizing.get("distinct_insulation_files_for_bucket"), 0))
    clean_rows = int(safe_number(sizing.get("accepted_qty_per_sqft_rows"), 0))
    cost_evidence = int(safe_number(sizing.get("historical_cost_evidence_count"), 0))
    accepted = int(safe_number(sizing.get("rows_accepted"), 0))
    rejected = int(safe_number(sizing.get("rows_rejected"), 0))
    diagnostics = f" Sizing pool accepted {accepted} rows and rejected {rejected}."
    rejection_reasons = str(sizing.get("rejection_reasons") or "")
    if rejection_reasons:
        diagnostics += f" Rejections: {rejection_reasons}."
    if evidence_count > 0 and qty_per_sqft > 0:
        if _is_insulation_scope(scope) and (total_bucket_rows > clean_rows or distinct_files > evidence_count):
            history_total = distinct_files or total_bucket_rows
            unit_label = "estimate files" if distinct_files else "rows"
            text = (
                f"{package.replace('_', ' ').title()} appears in {history_total:,} historical {history_label.lower()} {unit_label}. "
                f"{clean_rows or accepted:,} had clean quantity-per-sqft evidence. Default is based on those rows. "
                f"Historical cost fallback uses {cost_evidence:,} jobs. Median when used: {qty_per_sqft:g} per sqft."
                f"{diagnostics} {reason}"
            )
        else:
            text = (
                f"Used in {evidence_count} historical {history_label} jobs. Median when used: {qty_per_sqft:g} per sqft."
                f"{diagnostics} {reason}"
            )
        if status != "yes":
            text += " Shown unchecked. Historical default is prefilled so estimator can include it if needed."
        if unit_price <= 0 and historical_cost_per_sqft > 0:
            text += " Current price not found; using historical cost default when included."
        elif unit_price <= 0:
            text += " Historical quantity exists but current price is missing."
        return text
    if historical_jobs > 0:
        text = (
            f"Found {historical_jobs} historical {history_label}/package jobs, but accepted 0 for physical quantity sizing; "
            f"left quantity at 0 for estimator review.{diagnostics} {reason}"
        )
        if historical_cost_per_sqft > 0:
            text += " Historical usage exists, but physical quantity could not be normalized; using historical cost/sqft when included."
        return text
    if evidence_count > 0:
        return f"Used in {evidence_count} historical {history_label} jobs, but no reliable historical quantity was found; left quantity at 0 for estimator review.{diagnostics} {reason}"
    if historical_cost_per_sqft > 0:
        return f"No historical quantity evidence found; using historical cost/sqft when included.{diagnostics} {reason}"
    return f"No historical quantity or cost evidence found.{diagnostics} {reason}"


def _labor_explanation(
    *,
    package: str,
    sizing: dict[str, Any],
    evidence_count: int,
    hours_per_1000: float,
    status: str,
    scope: dict[str, Any],
) -> str:
    reason = _suggestion_reason(package, scope, status)
    history_label = _history_label(scope)
    historical_jobs = int(safe_number(sizing.get("historical_jobs_found"), 0))
    accepted = int(safe_number(sizing.get("rows_accepted"), 0))
    rejected = int(safe_number(sizing.get("rows_rejected"), 0))
    diagnostics = f" Sizing pool accepted {accepted} rows and rejected {rejected}."
    rejection_reasons = str(sizing.get("rejection_reasons") or "")
    if rejection_reasons:
        diagnostics += f" Rejections: {rejection_reasons}."
    if evidence_count > 0 and hours_per_1000 > 0:
        text = (
            f"Used in {evidence_count} historical {history_label} jobs. Median when used: {hours_per_1000:g} hours per 1,000 sqft."
            f"{diagnostics} {reason}"
        )
        if status != "yes":
            text += " Shown unchecked. Historical default is prefilled so estimator can include it if needed."
        return text
    if historical_jobs > 0:
        return (
            f"Found {historical_jobs} historical {history_label}/package jobs, but accepted 0 for labor sizing; "
            f"left at 0 for estimator review.{diagnostics} {reason}"
        )
    if evidence_count > 0:
        return f"Used in {evidence_count} historical {history_label} jobs, but no reliable labor rate was found; left at 0 for estimator review.{diagnostics} {reason}"
    return f"No historical {history_label} labor evidence found; left at 0 for estimator review.{diagnostics} {reason}"


def _short_material_note(
    *,
    package: str,
    evidence_count: int,
    qty_per_sqft: float,
    status: str,
    unit_price: float,
    historical_cost_per_sqft: float,
    sizing: dict[str, Any],
    scope: dict[str, Any],
) -> str:
    notes: list[str] = []
    history_label = _history_label(scope).lower()
    total_bucket_rows = int(safe_number(sizing.get("total_insulation_rows_for_bucket"), 0))
    distinct_files = int(safe_number(sizing.get("distinct_insulation_files_for_bucket"), 0))
    clean_rows = int(safe_number(sizing.get("accepted_qty_per_sqft_rows"), 0))
    cost_evidence = int(safe_number(sizing.get("historical_cost_evidence_count"), 0))
    if evidence_count > 0 and qty_per_sqft > 0:
        if _is_insulation_scope(scope) and (total_bucket_rows > clean_rows or distinct_files > evidence_count):
            history_total = distinct_files or total_bucket_rows
            unit_label = "estimate files" if distinct_files else "rows"
            package_label = package.replace("_", " ").title()
            notes.append(
                f"{package_label} appears in {history_total:,} {history_label} {unit_label}. "
                f"{clean_rows or evidence_count:,} had clean quantity-per-sqft evidence. Default is based on those rows. "
                f"Cost fallback uses {cost_evidence:,} jobs. Median: {qty_per_sqft:.4g}/sqft."
            )
        else:
            notes.append(f"Historical default from {evidence_count} {history_label} jobs. Median when used: {qty_per_sqft:.4g}/sqft.")
    elif historical_cost_per_sqft > 0:
        notes.append("No normalized quantity found; using historical cost default if included.")
    else:
        notes.append("No reliable historical quantity or cost found.")
    notes.append(_suggestion_reason(package, scope, status))
    if status != "yes":
        notes.append("Shown unchecked. Historical default is prefilled if needed.")
    if unit_price > 0:
        notes.append("Current price found in pricing catalog.")
    elif historical_cost_per_sqft > 0:
        notes.append("No current price found; using historical cost default.")
    if sizing.get("variability_warning"):
        notes.append("Wide historical range; estimator should review.")
    return " ".join(part for part in notes if part)


def _short_labor_note(
    *,
    package: str,
    evidence_count: int,
    hours_per_1000: float,
    status: str,
    sizing: dict[str, Any],
    scope: dict[str, Any],
) -> str:
    notes: list[str] = []
    history_label = _history_label(scope).lower()
    if evidence_count > 0 and hours_per_1000 > 0:
        notes.append(f"Historical default from {evidence_count} {history_label} jobs. Median when used: {hours_per_1000:.4g} hrs/1,000 sqft.")
    else:
        notes.append("No reliable historical labor default found.")
    notes.append(_suggestion_reason(package, scope, status))
    if status != "yes":
        notes.append("Shown unchecked. Historical default is prefilled if needed.")
    if sizing.get("variability_warning"):
        notes.append("Wide historical range; estimator should review.")
    return " ".join(part for part in notes if part)


def material_workbench_rows(
    recommendation: Any,
    data: Any,
    scope: dict[str, Any],
    historical_filters: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    area = _estimate_area(scope)
    notes = _scope_note_text(recommendation, scope)
    pricing = _frame(data, "pricing_catalog")
    if pricing.empty:
        pricing = _frame(data, "pricing")
    decisions = _decision_recommendation_lookup(data, historical_filters)
    rows: list[dict[str, Any]] = []
    for spec in _material_specs_for_scope(scope):
        package = spec["package"]
        decision_id = _material_decision_id(package, scope)
        decision_fields = [
            "resolved_item_name",
            "thickness_inches",
            "yield_or_coverage",
            "foam_density_lb",
            "gal_per_100_sqft",
            "gal_per_sqft",
            "wet_mils_estimate",
            "waste_factor_pct",
        ]
        decision_meta = _decision_meta(decisions, decision_id, decision_fields)
        default_unit = str(spec.get("default_unit") or "unit")
        sizing = material_sizing_distribution(data, package, str(spec.get("default_unit") or "unit"), historical_filters)
        pricing_options = _pricing_options_for_package(pricing, spec, scope)
        historical_options = _historical_item_options(data, package, historical_filters, default_unit)
        selected_item = _select_material_item(package, pricing_options, historical_options, scope, str(spec.get("label") or package), default_unit)
        item_qty_per_sqft = safe_number(selected_item.get("item_median_qty_per_sqft"), 0.0)
        item_evidence_count = int(safe_number(selected_item.get("item_evidence_count"), 0))
        min_evidence = int(safe_number(sizing.get("minimum_evidence_count"), DEFAULT_MIN_EVIDENCE_COUNT))
        qty_per_sqft = item_qty_per_sqft if item_qty_per_sqft > 0 and item_evidence_count >= min_evidence else safe_number(sizing.get("median"), 0.0)
        historical_cost_per_sqft = safe_number(sizing.get("median_cost_per_sqft"), 0.0)
        foam_quantity_model = first_nonblank(sizing.get("foam_quantity_model"))
        foam_units_per_sqft_per_inch = safe_number(sizing.get("median_units_per_sqft_per_inch"), 0.0)
        foam_sets_per_sqft_per_inch = safe_number(sizing.get("median_sets_per_sqft_per_inch"), 0.0)
        foam_cost_per_sqft_per_inch = safe_number(sizing.get("median_cost_per_sqft_per_inch"), 0.0)
        foam_thickness_inches = safe_number(
            first_nonblank(
                scope.get("foam_thickness_inches"),
                scope.get("thickness_inches"),
                _decision_value(decisions, decision_id, "thickness_inches"),
                sizing.get("median_foam_thickness_inches"),
            ),
            0.0,
        )
        foam_yield_factor = safe_number(
            first_nonblank(
                scope.get("foam_yield_factor"),
                _decision_value(decisions, decision_id, "yield_or_coverage"),
                sizing.get("median_foam_yield"),
            ),
            0.0,
        )
        decision_gal_per_100 = safe_number(_decision_value(decisions, decision_id, "gal_per_100_sqft"), 0.0)
        if decision_gal_per_100 > 0 and package in {"coating", "thermal_barrier_coating"}:
            qty_per_sqft = decision_gal_per_100 / 100
        if package == "foam" and _is_insulation_scope(scope) and foam_units_per_sqft_per_inch <= 0 and foam_yield_factor > 0:
            foam_units_per_sqft_per_inch = 1000 / foam_yield_factor
            foam_sets_per_sqft_per_inch = foam_units_per_sqft_per_inch / 1000
        if package == "foam" and _is_insulation_scope(scope) and foam_units_per_sqft_per_inch > 0 and foam_thickness_inches > 0:
            qty_per_sqft = foam_units_per_sqft_per_inch * foam_thickness_inches
            historical_cost_per_sqft = historical_cost_per_sqft or (foam_cost_per_sqft_per_inch * foam_thickness_inches)
        if historical_cost_per_sqft <= 0:
            historical_cost_per_sqft = safe_number(selected_item.get("item_median_cost_per_sqft"), 0.0)
        historical_cost_evidence_count = int(safe_number(sizing.get("historical_cost_evidence_count"), 0))
        evidence_count = int(safe_number(sizing.get("evidence_count"), 0))
        if package == "foam" and _is_insulation_scope(scope):
            evidence_count = max(evidence_count, int(safe_number(sizing.get("foam_template_model_evidence_count"), 0)))
        unit_price = safe_number(selected_item.get("unit_price"), 0.0)
        if package == "foam" and _is_insulation_scope(scope) and unit_price <= 0:
            unit_price = safe_number(sizing.get("median_foam_unit_price"), 0.0)
        price_source = str(selected_item.get("item_name") or "")
        status = _package_suggestion_status(recommendation, package, scope)
        include = status == "yes"
        if package == "coating" and scope.get("coating_type"):
            status = "yes"
            include = True
        editable_qty_per_sqft = qty_per_sqft
        scope_partial = scope.get("partial_scope") if isinstance(scope.get("partial_scope"), dict) else {}
        partial_basis_sqft = 0.0
        if package == "primer":
            partial_basis_sqft = safe_number(scope_partial.get("primer_basis_sqft"), 0.0) or _partial_primer_basis_sqft(notes, area)
        if include:
            editable_basis_sqft = partial_basis_sqft if partial_basis_sqft > 0 else area
        elif package == "coating":
            editable_basis_sqft = area
        elif partial_basis_sqft > 0:
            editable_basis_sqft = partial_basis_sqft
        else:
            editable_basis_sqft = 0.0
        calculated_quantity = editable_qty_per_sqft * editable_basis_sqft if include and editable_basis_sqft else 0.0
        foam_estimated_units = calculated_quantity if package == "foam" and _is_insulation_scope(scope) else 0.0
        foam_estimated_sets = foam_estimated_units / 1000 if foam_estimated_units else 0.0
        if include and unit_price > 0:
            estimated_cost = calculated_quantity * unit_price
            selected_price_source = "current_pricing"
        elif include and historical_cost_per_sqft > 0 and editable_basis_sqft:
            estimated_cost = historical_cost_per_sqft * editable_basis_sqft
            selected_price_source = "historical_cost_default"
        else:
            estimated_cost = 0.0
            selected_price_source = "current_pricing_missing" if historical_cost_per_sqft <= 0 and unit_price <= 0 else "not_included"
        needs_review = bool(unit_price <= 0 and historical_cost_per_sqft > 0)
        item_source = str(selected_item.get("item_source") or "manual")
        item_name = str(
            first_nonblank(
                selected_item.get("item_name"),
                sizing.get("default_foam_product") if package == "foam" and _is_insulation_scope(scope) else "",
                _decision_value(decisions, decision_id, "resolved_item_name"),
                spec["label"],
            )
        )
        product_context = _product_context(data, item_name=item_name, decision_id=decision_id, package=package)
        decision_output = {
            "selected_option": _decision_value(decisions, decision_id, "resolved_item_name", item_name),
            "thickness_inches": foam_thickness_inches if package == "foam" else None,
            "yield_or_coverage": foam_yield_factor if package == "foam" else None,
            "gal_per_100_sqft": decision_gal_per_100 if package in {"coating", "thermal_barrier_coating"} else None,
            "wet_mils_estimate": _decision_value(decisions, decision_id, "wet_mils_estimate"),
            "waste_factor_pct": _decision_value(decisions, decision_id, "waste_factor_pct"),
        }
        explanation = _material_explanation(
            package=package,
            sizing=sizing,
            evidence_count=evidence_count,
            qty_per_sqft=qty_per_sqft,
            status=status,
            scope=scope,
            unit_price=unit_price,
            historical_cost_per_sqft=historical_cost_per_sqft,
        )
        if item_source == "current_pricing_plus_historical_usage":
            explanation += f" Default item selected from current pricing and historical usage: {item_name}."
        elif item_source == "current_pricing":
            explanation += f" Default item selected from current pricing: {item_name}."
        elif item_source.startswith("historical"):
            explanation += f" Default item selected from historical usage/cost evidence: {item_name}."
        else:
            explanation += " Item can be entered manually if the estimator wants a different product."
        short_note = _short_material_note(
            package=package,
            evidence_count=evidence_count,
            qty_per_sqft=qty_per_sqft,
            status=status,
            scope=scope,
            unit_price=unit_price,
            historical_cost_per_sqft=historical_cost_per_sqft,
            sizing=sizing,
        )
        if product_context:
            context_note_parts = []
            if product_context.get("recommended_use"):
                context_note_parts.append(f"Product guidance: {product_context.get('recommended_use')}")
            if product_context.get("coverage"):
                context_note_parts.append(f"Coverage: {product_context.get('coverage')}")
            if product_context.get("warnings"):
                context_note_parts.append("Manufacturer warning available.")
            if context_note_parts:
                short_note = f"{short_note} {' '.join(context_note_parts)}"
            if product_context.get("important_limitations"):
                explanation += f" Manufacturer limitations: {product_context.get('important_limitations')}."
        historical_recommendation = _material_decision_recommendation_summary(
            decision_output=decision_output,
            item_name=item_name,
            evidence_count=int(decision_meta.get("decision_evidence_count") or evidence_count),
            confidence=str(decision_meta.get("decision_confidence") or sizing.get("confidence") or _confidence(evidence_count)),
            package=package,
            unit=str(selected_item.get("unit") or sizing.get("unit") or spec.get("default_unit") or ""),
        )
        editable_value_summary = _value_summary(
            {
                "item": item_name,
                "basis_sqft": round(editable_basis_sqft, 2),
                "qty_per_sqft": round(editable_qty_per_sqft, 6),
                "thickness_inches": round(foam_thickness_inches, 4) if package == "foam" and _is_insulation_scope(scope) and foam_thickness_inches else None,
                "yield": round(foam_yield_factor, 4) if package == "foam" and _is_insulation_scope(scope) and foam_yield_factor else None,
                "gal_per_100_sqft": round(decision_gal_per_100, 4) if package in {"coating", "thermal_barrier_coating"} and decision_gal_per_100 else None,
            }
        )
        calculated_output_summary = _value_summary(
            {
                "quantity": round(calculated_quantity, 2),
                "sets": round(foam_estimated_sets, 4) if package == "foam" and _is_insulation_scope(scope) and foam_estimated_sets else None,
                "cost": round(estimated_cost, 2),
            }
        )
        product_guidance = _product_guidance_summary(product_context)
        rows.append(
            {
                "include": bool(include),
                "package": spec["label"],
                "package_key": package,
                "template_bucket": package,
                "workbook_row": str(spec.get("workbook_row") or ""),
                **decision_meta,
                "recommended_decision_value": first_nonblank(
                    decision_output.get("selected_option"),
                    decision_output.get("thickness_inches"),
                    decision_output.get("gal_per_100_sqft"),
                    "",
                ),
                "editable_decision_value": first_nonblank(
                    decision_output.get("selected_option"),
                    decision_output.get("thickness_inches"),
                    decision_output.get("gal_per_100_sqft"),
                    "",
                ),
                "decision_values": decision_output,
                "workbook_rows_controlled": str(spec.get("workbook_row") or ""),
                "row_traceability": f"Estimate rows {spec.get('workbook_row') or ''}",
                "calculated_output": round(estimated_cost, 2),
                "estimator_decision": f"{spec['label']} ({package})",
                "historical_recommendation": historical_recommendation,
                "editable_value": editable_value_summary,
                "calculated_output_summary": calculated_output_summary,
                "evidence_summary": f"{decision_meta.get('decision_evidence_count') or evidence_count} decision rows; {decision_meta.get('decision_source_jobs_count') or evidence_count} jobs",
                "product_guidance": product_guidance,
                "product_warning_summary": _value_summary(product_context.get("warnings") or product_context.get("important_limitations") or ""),
                "product_source_evidence": _value_summary(product_context.get("source_documents") or product_context.get("source_evidence") or []),
                "item_name": item_name,
                "product_id": product_context.get("product_id") or "",
                "product_manufacturer": product_context.get("manufacturer") or "",
                "product_knowledge_product_name": product_context.get("product_name") or "",
                "product_knowledge_product_family": product_context.get("product_family") or "",
                "product_knowledge_category": product_context.get("category") or "",
                "product_recommended_use": product_context.get("recommended_use") or "",
                "product_manufacturer_guidance": product_context.get("manufacturer_guidance") or "",
                "product_coverage": product_context.get("coverage") or "",
                "product_limitations": product_context.get("important_limitations") or "",
                "product_warnings": product_context.get("warnings") or [],
                "product_source_documents": product_context.get("source_documents") or [],
                "product_source_evidence_rows": product_context.get("source_evidence") or [],
                "product_linked_decision_nodes": product_context.get("linked_decision_nodes") or [],
                "product_context_confidence": product_context.get("confidence") or "",
                "product_match_score": product_context.get("match_score") or 0.0,
                "product_r_value_per_inch": product_context.get("r_value_per_inch") or 0.0,
                "product_r_value_per_inch_source": product_context.get("r_value_per_inch_source") or "",
                "product_aged_r_value_per_inch": product_context.get("aged_r_value_per_inch") or 0.0,
                "product_aged_r_value_per_inch_source": product_context.get("aged_r_value_per_inch_source") or "",
                "product_initial_r_value_per_inch": product_context.get("initial_r_value_per_inch") or 0.0,
                "product_initial_r_value_per_inch_source": product_context.get("initial_r_value_per_inch_source") or "",
                "current_item": item_name,
                "historical_item": item_name if item_source.startswith("historical") else first_nonblank(selected_item.get("historical_item"), ""),
                "selected_item_reason": selected_item.get("selected_item_reason") or "",
                "selected_item_score": selected_item.get("selected_item_score") or 0.0,
                "top_rejected_item_reasons": selected_item.get("top_rejected_item_reasons") or [],
                "item_source": item_source,
                "item_options": " | ".join(option.get("item_name") for option in [*pricing_options, *historical_options] if option.get("item_name")),
                "item_options_json": _item_options_payload(pricing_options, historical_options, selected_item),
                "suggested_by_notes_rules": status,
                "historical_usage_rate": _historical_usage_rate(data, package, scope, evidence_count),
                "historical_qty_per_basis_sqft": round(qty_per_sqft, 6),
                "historical_qty_per_sqft": round(qty_per_sqft, 6),
                "historical_median": round(qty_per_sqft, 6),
                "quantity_model": foam_quantity_model if package == "foam" and foam_quantity_model else "qty_per_sqft",
                "decision_model": "workbook_formula_inputs" if package == "foam" and _is_insulation_scope(scope) else "historical_rate_default",
                "decision_fields": (
                    "foam_product,foam_density_lb,editable_basis_sqft,thickness_inches,yield_factor,current_unit_price"
                    if package == "foam" and _is_insulation_scope(scope)
                    else "item_name,editable_basis_sqft,editable_qty_per_sqft,current_unit_price"
                ),
                "calculated_output_fields": (
                    "estimated_units,estimated_sets,estimated_cost"
                    if package == "foam" and _is_insulation_scope(scope)
                    else "calculated_quantity,estimated_cost"
                ),
                "foam_product": item_name if package == "foam" and _is_insulation_scope(scope) else "",
                "foam_density_lb": safe_number(sizing.get("default_foam_density_lb"), 0.0),
                "median_sets_per_sqft_per_inch": round(foam_sets_per_sqft_per_inch, 8),
                "median_units_per_sqft_per_inch": round(foam_units_per_sqft_per_inch, 6),
                "foam_thickness_inches": round(foam_thickness_inches, 4) if foam_thickness_inches else 0.0,
                "thickness_inches": round(foam_thickness_inches, 4) if package == "foam" and _is_insulation_scope(scope) else 0.0,
                "yield_factor": round(foam_yield_factor, 4) if package == "foam" and _is_insulation_scope(scope) else 0.0,
                "median_foam_yield": round(safe_number(sizing.get("median_foam_yield"), 0.0), 4),
                "item_level_qty_per_sqft": round(item_qty_per_sqft, 6),
                "item_level_evidence_count": item_evidence_count,
                "editable_basis_sqft": round(editable_basis_sqft, 2),
                "default_basis_sqft": round(editable_basis_sqft, 2),
                "p25_qty_per_sqft": round(safe_number(sizing.get("p25"), 0.0), 6),
                "p75_qty_per_sqft": round(safe_number(sizing.get("p75"), 0.0), 6),
                "editable_qty_per_sqft": round(editable_qty_per_sqft, 6),
                "editable_default": round(editable_qty_per_sqft, 6),
                "calculated_quantity": round(calculated_quantity, 2),
                "estimated_units": round(foam_estimated_units, 2) if package == "foam" and _is_insulation_scope(scope) else round(calculated_quantity, 2),
                "estimated_sets": round(foam_estimated_sets, 4) if package == "foam" and _is_insulation_scope(scope) else 0.0,
                "unit": sizing.get("unit") if package == "foam" and foam_quantity_model else selected_item.get("unit") or sizing.get("unit") or spec.get("default_unit"),
                "current_unit_price": round(unit_price, 4) if unit_price else 0.0,
                "current_price": round(unit_price, 4) if unit_price else 0.0,
                "historical_cost_per_sqft": round(historical_cost_per_sqft, 4),
                "historical_cost_default": round(historical_cost_per_sqft, 4),
                "estimated_cost": round(estimated_cost, 2),
                "evidence_count": evidence_count,
                "historical_cost_evidence_count": historical_cost_evidence_count,
                "historical_jobs_found": int(safe_number(sizing.get("historical_jobs_found"), 0)),
                "rows_accepted": int(safe_number(sizing.get("rows_accepted"), 0)),
                "rows_rejected": int(safe_number(sizing.get("rows_rejected"), 0)),
                "total_insulation_rows_for_bucket": int(safe_number(sizing.get("total_insulation_rows_for_bucket"), 0)),
                "distinct_insulation_files_for_bucket": int(safe_number(sizing.get("distinct_insulation_files_for_bucket"), 0)),
                "rows_with_quantity": int(safe_number(sizing.get("rows_with_quantity"), 0)),
                "rows_with_cost": int(safe_number(sizing.get("rows_with_cost"), 0)),
                "rows_with_area": int(safe_number(sizing.get("rows_with_area"), 0)),
                "accepted_qty_per_sqft_rows": int(safe_number(sizing.get("accepted_qty_per_sqft_rows"), 0)),
                "rejected_missing_area": int(safe_number(sizing.get("rejected_missing_area"), 0)),
                "rejected_missing_quantity": int(safe_number(sizing.get("rejected_missing_quantity"), 0)),
                "rejected_missing_cost": int(safe_number(sizing.get("rejected_missing_cost"), 0)),
                "rejected_filter_mismatch": int(safe_number(sizing.get("rejected_filter_mismatch"), 0)),
                "rejection_reasons": sizing.get("rejection_reasons") or "",
                "range_width": round(safe_number(sizing.get("range_width"), 0.0), 6),
                "relative_range_width": round(safe_number(sizing.get("relative_range_width"), 0.0), 4),
                "variability_warning": sizing.get("variability_warning") or "",
                "filters_applied": sizing.get("filters_applied") or "",
                "filters_relaxed": sizing.get("filters_relaxed") or "",
                "minimum_evidence_count": int(safe_number(sizing.get("minimum_evidence_count"), DEFAULT_MIN_EVIDENCE_COUNT)),
                "filter_hash": sizing.get("filter_hash") or historical_filter_hash(historical_filters),
                "manual_override": False,
                "reset_to_historical_default": False,
                "confidence": sizing.get("confidence") or _confidence(evidence_count),
                "source": sizing.get("source") or "no_sufficient_evidence",
                "pricing_source": price_source or selected_price_source,
                "price_source": selected_price_source,
                "needs_review": needs_review,
                "notes": short_note,
                "explanation": explanation,
            }
        )
    return rows


def labor_workbench_rows(
    recommendation: Any,
    data: Any,
    scope: dict[str, Any],
    hourly_rate: float = DEFAULT_HOURLY_RATE,
    historical_filters: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    area = _estimate_area(scope)
    decisions = _decision_recommendation_lookup(data, historical_filters)
    rows: list[dict[str, Any]] = []
    for spec in _labor_specs_for_scope(scope):
        package = spec["package"]
        decision_id = _labor_decision_id(package, scope)
        decision_fields = ["days", "crew_size", "crew_selector_code", "daily_rate", "hourly_rate", "formula_mode"]
        decision_meta = _decision_meta(decisions, decision_id, decision_fields)
        sizing = labor_sizing_distribution(data, package, historical_filters)
        hours_per_1000 = safe_number(sizing.get("median"), 0.0)
        evidence_count = int(safe_number(sizing.get("evidence_count"), 0))
        status = _labor_suggestion_status(recommendation, package, scope)
        include = status == "yes"
        editable_hours_per_1000 = hours_per_1000
        calculated_hours = editable_hours_per_1000 * area / 1000 if include and area else 0.0
        crew_size = int(safe_number(_decision_value(decisions, decision_id, "crew_size", sizing.get("median_crew_size")), 4) or 4)
        hours_per_day = 10.0
        default_days = safe_number(_decision_value(decisions, decision_id, "days", sizing.get("median_days")), 0.0)
        if default_days <= 0 and calculated_hours > 0 and crew_size > 0:
            default_days = calculated_hours / (crew_size * hours_per_day)
        hourly_rate = safe_number(_decision_value(decisions, decision_id, "hourly_rate", sizing.get("median_hourly_rate")), 0.0) or hourly_rate
        daily_rate = safe_number(_decision_value(decisions, decision_id, "daily_rate", sizing.get("median_daily_rate")), 0.0)
        if daily_rate <= 0 and crew_size > 0:
            daily_rate = hourly_rate * crew_size * hours_per_day
        formula_mode = str(_decision_value(decisions, decision_id, "formula_mode", sizing.get("formula_mode")) or ("mixed_formula" if package.startswith("labor_") else "hours_based"))
        labor_decision_value = {
            "days": round(default_days, 4),
            "crew_size": crew_size,
            "daily_rate": round(daily_rate, 4),
            "hourly_rate": round(hourly_rate, 4),
            "formula_mode": formula_mode,
        }
        labor_calculated_value = {
            "days": round(default_days, 4),
            "crew_size": crew_size,
            "total_hours": round(calculated_hours, 2),
            "daily_rate": round(daily_rate, 4),
            "hourly_rate": round(hourly_rate, 4),
            "formula_mode": formula_mode,
        }
        explanation = _labor_explanation(
            package=package,
            sizing=sizing,
            evidence_count=evidence_count,
            hours_per_1000=hours_per_1000,
            status=status,
            scope=scope,
        )
        rows.append(
            {
                "include": bool(include),
                "labor_package": spec["label"],
                "package_key": package,
                "template_bucket": package,
                "workbook_row": str(spec.get("workbook_row") or ""),
                **decision_meta,
                "recommended_decision_value": labor_decision_value,
                "editable_decision_value": labor_decision_value,
                "decision_values": labor_calculated_value,
                "workbook_rows_controlled": str(spec.get("workbook_row") or ""),
                "row_traceability": f"Estimate row {spec.get('workbook_row') or ''}",
                "calculated_output": round(calculated_hours * hourly_rate, 2),
                "estimator_decision": f"{spec['label']} ({package})",
                "historical_recommendation": _labor_decision_recommendation_summary(
                    labor_decision_value,
                    int(decision_meta.get("decision_evidence_count") or evidence_count),
                    str(decision_meta.get("decision_confidence") or sizing.get("confidence") or _confidence(evidence_count)),
                    package,
                ),
                "editable_value": _value_summary(labor_decision_value),
                "calculated_output_summary": _value_summary(
                    {
                        "hours": round(calculated_hours, 2),
                        "cost": round(calculated_hours * hourly_rate, 2),
                        "formula_mode": formula_mode,
                    }
                ),
                "evidence_summary": f"{decision_meta.get('decision_evidence_count') or evidence_count} decision rows; {decision_meta.get('decision_source_jobs_count') or evidence_count} jobs",
                "product_guidance": "",
                "product_warning_summary": "",
                "product_source_evidence": "",
                "suggested_by_notes_rules": status,
                "historical_hours_per_1000_sqft": round(hours_per_1000, 4),
                "historical_median": round(hours_per_1000, 4),
                "days": round(default_days, 4),
                "editable_days": round(default_days, 4),
                "crew_people_selection": crew_size,
                "daily_rate": round(daily_rate, 4),
                "total_hours": round(calculated_hours, 2),
                "hourly_rate": round(hourly_rate, 4),
                "formula_mode": formula_mode,
                "p25_hours_per_1000_sqft": round(safe_number(sizing.get("p25"), 0.0), 4),
                "p75_hours_per_1000_sqft": round(safe_number(sizing.get("p75"), 0.0), 4),
                "editable_hours_per_1000_sqft": round(editable_hours_per_1000, 4),
                "editable_default": round(editable_hours_per_1000, 4),
                "calculated_hours": round(calculated_hours, 2),
                "crew_size": crew_size,
                "labor_rate": hourly_rate,
                "estimated_cost": round(calculated_hours * hourly_rate, 2),
                "evidence_count": evidence_count,
                "historical_jobs_found": int(safe_number(sizing.get("historical_jobs_found"), 0)),
                "rows_accepted": int(safe_number(sizing.get("rows_accepted"), 0)),
                "rows_rejected": int(safe_number(sizing.get("rows_rejected"), 0)),
                "total_insulation_rows_for_bucket": int(safe_number(sizing.get("total_insulation_rows_for_bucket"), 0)),
                "distinct_insulation_files_for_bucket": int(safe_number(sizing.get("distinct_insulation_files_for_bucket"), 0)),
                "rows_with_quantity": int(safe_number(sizing.get("rows_with_quantity"), 0)),
                "rows_with_cost": int(safe_number(sizing.get("rows_with_cost"), 0)),
                "rows_with_area": int(safe_number(sizing.get("rows_with_area"), 0)),
                "accepted_qty_per_sqft_rows": int(safe_number(sizing.get("accepted_qty_per_sqft_rows"), 0)),
                "rejected_missing_area": int(safe_number(sizing.get("rejected_missing_area"), 0)),
                "rejected_missing_quantity": int(safe_number(sizing.get("rejected_missing_quantity"), 0)),
                "rejected_missing_cost": int(safe_number(sizing.get("rejected_missing_cost"), 0)),
                "rejected_filter_mismatch": int(safe_number(sizing.get("rejected_filter_mismatch"), 0)),
                "rejection_reasons": sizing.get("rejection_reasons") or "",
                "range_width": round(safe_number(sizing.get("range_width"), 0.0), 4),
                "relative_range_width": round(safe_number(sizing.get("relative_range_width"), 0.0), 4),
                "variability_warning": sizing.get("variability_warning") or "",
                "filters_applied": sizing.get("filters_applied") or "",
                "filters_relaxed": sizing.get("filters_relaxed") or "",
                "minimum_evidence_count": int(safe_number(sizing.get("minimum_evidence_count"), DEFAULT_MIN_EVIDENCE_COUNT)),
                "filter_hash": sizing.get("filter_hash") or historical_filter_hash(historical_filters),
                "manual_override": False,
                "reset_to_historical_default": False,
                "confidence": sizing.get("confidence") or _confidence(evidence_count),
                "source": sizing.get("source") or "no_sufficient_evidence",
                "notes": _short_labor_note(
                    package=package,
                    sizing=sizing,
                    evidence_count=evidence_count,
                    hours_per_1000=hours_per_1000,
                    status=status,
                    scope=scope,
                ),
                "explanation": explanation,
            }
        )
    return rows


def adder_workbench_rows(
    recommendation: Any,
    data: Any = None,
    scope: dict[str, Any] | None = None,
    historical_filters: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    scope = scope or {}
    area = _estimate_area(scope)
    travel = _rec_value(recommendation, "travel_plan", {}) or {}
    travel_cost = safe_number(travel.get("travel_vehicle_cost"), 0.0) + safe_number(travel.get("travel_labor_cost"), 0.0)
    rows = []
    history_label = _history_label(scope)
    for spec in ADDER_ROWS:
        is_travel = spec["adder"] == "travel"
        sizing = adder_sizing_distribution(data, spec["adder"], area, historical_filters)
        reliable_default = _is_reliable_adder_default(sizing)
        raw_historical_default = safe_number(sizing.get("editable_default"), 0.0)
        historical_default = raw_historical_default if reliable_default else 0.0
        editable_value = travel_cost if is_travel and travel_cost > 0 else historical_default
        include = bool(is_travel and travel_cost > 0)
        estimated_cost = editable_value if include else 0.0
        notes = first_nonblank(travel.get("travel_notes"), "") if is_travel else ""
        if not notes and historical_default > 0:
            notes = (
                f"Shown unchecked. Historical default is prefilled so estimator can include it if needed. "
                f"Median when used: ${historical_default:,.2f} from {int(safe_number(sizing.get('evidence_count'), 0))} historical {history_label} jobs."
            )
        elif not notes and raw_historical_default > 0 and not reliable_default:
            notes = "Insufficient reliable history; estimator review required."
        rows.append(
            {
                "include": include,
                "adder": spec["label"],
                "adder_key": spec["adder"],
                "template_bucket": spec["adder"],
                "workbook_row": str(spec.get("workbook_row") or ""),
                "historical_usage_rate": safe_number(sizing.get("historical_usage_rate"), 0.0),
                "median_cost_when_used": round(safe_number(sizing.get("median_cost_when_used"), 0.0), 2),
                "median_cost_per_sqft": round(safe_number(sizing.get("median_cost_per_sqft"), 0.0), 4),
                "historical_median": round(safe_number(sizing.get("median_cost_when_used"), 0.0), 2),
                "historical_default_value": round(historical_default, 2),
                "editable_value": round(editable_value, 2),
                "editable_default": round(editable_value, 2),
                "estimated_cost": round(estimated_cost, 2),
                "evidence_count": int(safe_number(sizing.get("evidence_count"), 0)),
                "range_width": round(safe_number(sizing.get("range_width"), 0.0), 2),
                "relative_range_width": round(safe_number(sizing.get("relative_range_width"), 0.0), 4),
                "variability_warning": sizing.get("variability_warning") or "",
                "filters_applied": sizing.get("filters_applied") or "",
                "filters_relaxed": sizing.get("filters_relaxed") or "",
                "minimum_evidence_count": int(safe_number(sizing.get("minimum_evidence_count"), DEFAULT_MIN_EVIDENCE_COUNT)),
                "filter_hash": sizing.get("filter_hash") or historical_filter_hash(historical_filters),
                "manual_override": False,
                "reset_to_historical_default": False,
                "confidence": "review" if is_travel and travel_cost > 0 else (sizing.get("confidence") if reliable_default else ("low" if int(safe_number(sizing.get("evidence_count"), 0)) else "none")),
                "source": "travel_plan" if is_travel and travel_cost > 0 else sizing.get("source") or "manual",
                "needs_review": bool(editable_value > 0 or not reliable_default),
                "notes": notes,
            }
        )
    return rows


def build_estimating_workbench(
    recommendation: Any,
    data: Any = None,
    scope_override: dict[str, Any] | None = None,
    historical_filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    scope = {**_scope_from_recommendation(recommendation), **(scope_override or {})}
    filters = {**historical_filters_from_scope(scope), **(historical_filters or {})}
    estimate_id = first_nonblank((_rec_value(recommendation, "parsed_fields", {}) or {}).get("run_id"), f"estimate-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}")
    review_flags = list(_rec_value(recommendation, "review_flags", []) or [])
    if _is_insulation_scope(scope):
        placeholder_warning = "Insulation workbench: verify foam type, thickness/R-value, opening deductions, and thermal barrier requirements before quoting."
        if placeholder_warning not in review_flags:
            review_flags.append(placeholder_warning)
    materials = material_workbench_rows(recommendation, data, scope, filters)
    foam_template_decisions = (
        _build_insulation_foam_template_decisions(scope=scope, foam_row=_foam_material_row(materials), data=data)
        if _is_insulation_scope(scope)
        else []
    )
    roofing_foam_template_decisions = (
        _build_roofing_foam_template_decisions(scope=scope, data=data)
        if not _is_insulation_scope(scope)
        else []
    )
    if roofing_foam_template_decisions:
        _apply_roofing_foam_template_decisions_to_materials(
            {"materials": materials, "roofing_foam_template_decisions": roofing_foam_template_decisions}
        )
    roofing_coating_template_decisions = (
        _build_roofing_coating_template_decisions(scope=scope, coating_row=_coating_material_row(materials), data=data)
        if not _is_insulation_scope(scope)
        else []
    )
    roofing_primer_template_decisions = (
        _build_roofing_primer_template_decisions(scope=scope, primer_row=_primer_material_row(materials), data=data)
        if not _is_insulation_scope(scope)
        else []
    )
    roofing_detail_template_decisions = (
        _build_roofing_detail_template_decisions(
            scope=scope,
            caulk_row=_caulk_detail_material_row(materials),
            fabric_row=_fabric_material_row(materials),
            data=data,
        )
        if not _is_insulation_scope(scope)
        else []
    )
    roofing_detail_quantity_template_decisions = (
        _build_roofing_detail_quantity_template_decisions(
            scope=scope,
            materials=materials,
        )
        if not _is_insulation_scope(scope)
        else []
    )
    roofing_board_fastener_template_decisions = (
        _build_roofing_board_fastener_template_decisions(
            scope=scope,
            board_row=_board_stock_material_row(materials),
            fastener_row=_fastener_material_row(materials),
            plates_row=_plates_material_row(materials),
            data=data,
        )
        if not _is_insulation_scope(scope)
        else []
    )
    roofing_granules_template_decisions = (
        _build_roofing_granules_template_decisions(
            scope=scope,
            granules_row=_granules_material_row(materials),
            data=data,
        )
        if not _is_insulation_scope(scope)
        else []
    )
    labor_rows = labor_workbench_rows(recommendation, data, scope, historical_filters=filters)
    adder_rows = adder_workbench_rows(recommendation, data, scope, filters)
    insulation_detail_material_template_decisions = []
    insulation_thermal_barrier_template_decisions = []
    insulation_support_material_template_decisions = []
    insulation_equipment_logistics_template_decisions = []
    insulation_compliance_template_decisions = []
    insulation_labor_template_decisions = []
    insulation_pricing_template_decisions = []
    if _is_insulation_scope(scope):
        insulation_detail_material_template_decisions = _build_insulation_decision_rows(
            section="insulation_detail_material_template_decisions",
            specs=INSULATION_DETAIL_DECISION_SPECS,
            scope=scope,
            materials=materials,
            adders=adder_rows,
            data=data,
        )
        insulation_thermal_barrier_template_decisions = _build_insulation_decision_rows(
            section="insulation_thermal_barrier_template_decisions",
            specs=INSULATION_THERMAL_DECISION_SPECS,
            scope=scope,
            materials=materials,
            adders=adder_rows,
            data=data,
        )
        insulation_dependencies = _insulation_dependency_totals(
            {
                "insulation_foam_template_decisions": foam_template_decisions,
            },
            rows={
                "insulation_detail_material_template_decisions": insulation_detail_material_template_decisions,
                "insulation_thermal_barrier_template_decisions": insulation_thermal_barrier_template_decisions,
            },
        )
        insulation_support_material_template_decisions = _build_insulation_decision_rows(
            section="insulation_support_material_template_decisions",
            specs=INSULATION_SUPPORT_DECISION_SPECS,
            scope=scope,
            materials=materials,
            adders=adder_rows,
            data=data,
            dependencies=insulation_dependencies,
        )
        insulation_dependencies = _insulation_dependency_totals(
            {
                "insulation_foam_template_decisions": foam_template_decisions,
            },
            rows={
                "insulation_detail_material_template_decisions": insulation_detail_material_template_decisions,
                "insulation_thermal_barrier_template_decisions": insulation_thermal_barrier_template_decisions,
                "insulation_support_material_template_decisions": insulation_support_material_template_decisions,
            },
        )
        insulation_equipment_logistics_template_decisions = _build_insulation_decision_rows(
            section="insulation_equipment_logistics_template_decisions",
            specs=INSULATION_EQUIPMENT_LOGISTICS_DECISION_SPECS,
            scope=scope,
            materials=materials,
            adders=adder_rows,
            data=data,
            dependencies=insulation_dependencies,
        )
        insulation_compliance_template_decisions = _build_insulation_decision_rows(
            section="insulation_compliance_template_decisions",
            specs=INSULATION_COMPLIANCE_DECISION_SPECS,
            scope=scope,
            materials=materials,
            adders=adder_rows,
            data=data,
            dependencies=insulation_dependencies,
        )
        insulation_labor_template_decisions = _build_insulation_labor_template_decisions(
            scope=scope,
            labor_rows=labor_rows,
        )
        insulation_dependencies = _insulation_dependency_totals(
            {
                "insulation_foam_template_decisions": foam_template_decisions,
            },
            rows={
                "insulation_detail_material_template_decisions": insulation_detail_material_template_decisions,
                "insulation_thermal_barrier_template_decisions": insulation_thermal_barrier_template_decisions,
                "insulation_support_material_template_decisions": insulation_support_material_template_decisions,
                "insulation_equipment_logistics_template_decisions": insulation_equipment_logistics_template_decisions,
                "insulation_compliance_template_decisions": insulation_compliance_template_decisions,
                "insulation_labor_template_decisions": insulation_labor_template_decisions,
            },
        )
        insulation_pricing_template_decisions = _build_insulation_decision_rows(
            section="insulation_pricing_template_decisions",
            specs=INSULATION_PRICING_DECISION_SPECS,
            scope=scope,
            materials=materials,
            adders=adder_rows,
            data=data,
            dependencies=insulation_dependencies,
        )
    roofing_equipment_template_decisions = (
        _build_roofing_equipment_template_decisions(
            scope=scope,
            adders=adder_rows,
        )
        if not _is_insulation_scope(scope)
        else []
    )
    roofing_travel_freight_template_decisions = (
        _build_roofing_travel_freight_template_decisions(
            scope=scope,
            adders=adder_rows,
        )
        if not _is_insulation_scope(scope)
        else []
    )
    roofing_accessory_template_decisions = (
        _build_roofing_accessory_template_decisions(
            scope=scope,
            materials=materials,
            coating_decisions=roofing_coating_template_decisions,
        )
        if not _is_insulation_scope(scope)
        else []
    )
    roofing_labor_template_decisions = (
        _build_roofing_labor_template_decisions(
            scope=scope,
            labor_rows=labor_rows,
        )
        if not _is_insulation_scope(scope)
        else []
    )
    surface_rows = _build_insulation_surface_rows_for_workbench(
        scope,
        notes=_scope_note_text(recommendation, scope),
        foam_row=_foam_material_row(materials),
    )
    ai_context = _ai_scope_debug_context(recommendation)
    area_trace = (
        build_area_calculation_trace(
            scope,
            ai_scope=ai_context.get("ai_parsed_scope"),
            deterministic_scope=ai_context.get("deterministic_scope"),
            merge_decisions=ai_context.get("merge_decisions"),
        )
        if _is_insulation_scope(scope)
        else []
    )
    area_explanation = build_area_calculation_explanation(scope, trace_rows=area_trace) if _is_insulation_scope(scope) else ""
    workbench = {
        "estimate_id": estimate_id,
        "scope": scope,
        "historical_filters": filters,
        "historical_filter_hash": historical_filter_hash(filters),
        "area_calculation_trace": area_trace,
        "area_calculation_explanation": area_explanation,
        "insulation_surfaces": surface_rows,
        "insulation_foam_template_decisions": foam_template_decisions,
        "insulation_detail_material_template_decisions": insulation_detail_material_template_decisions,
        "insulation_thermal_barrier_template_decisions": insulation_thermal_barrier_template_decisions,
        "insulation_support_material_template_decisions": insulation_support_material_template_decisions,
        "insulation_equipment_logistics_template_decisions": insulation_equipment_logistics_template_decisions,
        "insulation_compliance_template_decisions": insulation_compliance_template_decisions,
        "insulation_labor_template_decisions": insulation_labor_template_decisions,
        "insulation_pricing_template_decisions": insulation_pricing_template_decisions,
        "roofing_foam_template_decisions": roofing_foam_template_decisions,
        "roofing_coating_template_decisions": roofing_coating_template_decisions,
        "roofing_primer_template_decisions": roofing_primer_template_decisions,
        "roofing_detail_template_decisions": roofing_detail_template_decisions,
        "roofing_detail_quantity_template_decisions": roofing_detail_quantity_template_decisions,
        "roofing_board_fastener_template_decisions": roofing_board_fastener_template_decisions,
        "roofing_granules_template_decisions": roofing_granules_template_decisions,
        "roofing_equipment_template_decisions": roofing_equipment_template_decisions,
        "roofing_travel_freight_template_decisions": roofing_travel_freight_template_decisions,
        "roofing_accessory_template_decisions": roofing_accessory_template_decisions,
        "roofing_labor_template_decisions": roofing_labor_template_decisions,
        "insulation_performance_specs": [],
        "insulation_deductions": build_insulation_deductions(scope) if _is_insulation_scope(scope) else [],
        "insulation_r_value_targets": parse_r_value_targets(_scope_note_text(recommendation, scope)) if _is_insulation_scope(scope) else [],
        "materials": materials,
        "labor": labor_rows,
        "adders": adder_rows,
        "similar_jobs": _records(_rec_value(recommendation, "similar_examples", [])),
        "review_flags": review_flags,
        "suggested_rules": [
            {
                "rule": "Suggested rules are collected for future approval dashboards.",
                "status": "placeholder",
                "applied_automatically": False,
            }
        ],
    }
    return recalculate_workbench_tables(workbench)


def _records_from_editor(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, pd.DataFrame):
        return value.to_dict(orient="records")
    return _records(value)


def recalculate_workbench_tables(workbench: dict[str, Any], hourly_rate: float = DEFAULT_HOURLY_RATE) -> dict[str, Any]:
    updated = deepcopy(workbench)
    scope = updated.setdefault("scope", {})
    area = _estimate_area(scope)
    if _is_insulation_scope(scope):
        updated["insulation_surfaces"] = _build_insulation_surface_rows_for_workbench(
            scope,
            notes=str(first_nonblank(scope.get("notes"), scope.get("raw_input_notes"), "")),
            foam_row=_foam_material_row(updated.get("materials")),
            existing_rows=updated.get("insulation_surfaces") or None,
        )
        updated["insulation_deductions"] = build_insulation_deductions(scope)
        if not updated.get("insulation_r_value_targets"):
            updated["insulation_r_value_targets"] = parse_r_value_targets(str(first_nonblank(scope.get("notes"), scope.get("raw_input_notes"), "")))
        updated["insulation_foam_template_decisions"] = _build_insulation_foam_template_decisions(
            scope=scope,
            foam_row=_foam_material_row(updated.get("materials")),
            existing_rows=updated.get("insulation_foam_template_decisions") or None,
        )
        _apply_foam_template_decision_to_materials(updated)
    for row in updated.get("materials") or []:
        if row.get("reset_to_historical_default"):
            row["editable_qty_per_sqft"] = row.get("historical_qty_per_sqft", 0.0)
            row["editable_basis_sqft"] = row.get("default_basis_sqft", row.get("editable_basis_sqft", 0.0))
            row["reset_to_historical_default"] = False
        matched_item = _pricing_option_for_item(row)
        if matched_item:
            row["unit"] = matched_item.get("unit") or row.get("unit")
            row["current_unit_price"] = round(safe_number(matched_item.get("unit_price"), 0.0), 4)
            row["item_source"] = matched_item.get("item_source") or row.get("item_source") or "manual"
        row["current_item"] = first_nonblank(row.get("item_name"), row.get("current_item"), row.get("package"))
        include = bool(row.get("include"))
        qty_per_sqft = safe_number(row.get("editable_qty_per_sqft"), 0.0)
        historical_qty = safe_number(row.get("historical_qty_per_sqft"), 0.0)
        basis_sqft = safe_number(row.get("editable_basis_sqft"), 0.0)
        if include and basis_sqft <= 0 and row.get("package_key") != "primer":
            basis_sqft = area
            row["editable_basis_sqft"] = round(basis_sqft, 2)
        default_basis_sqft = safe_number(row.get("default_basis_sqft"), 0.0)
        row["manual_override"] = abs(qty_per_sqft - historical_qty) > 1e-9 or abs(basis_sqft - default_basis_sqft) > 1e-9
        unit_price = safe_number(row.get("current_unit_price"), 0.0)
        if unit_price <= 0:
            unit_price = safe_number(row.get("current_price"), 0.0)
            row["current_unit_price"] = round(unit_price, 4) if unit_price else 0.0
        row["current_price"] = round(unit_price, 4) if unit_price else 0.0
        historical_cost_per_sqft = safe_number(row.get("historical_cost_per_sqft"), 0.0)
        row["historical_median"] = round(historical_qty, 6)
        row["editable_default"] = round(qty_per_sqft, 6)
        package_key = str(row.get("package_key") or row.get("template_bucket") or "").lower()
        existing_decisions = row.get("decision_values") if isinstance(row.get("decision_values"), dict) else {}
        editable_decisions = decision_dict(row.get("editable_decision_value"))
        formula_inputs = {**existing_decisions, **editable_decisions}
        formula_result: dict[str, Any] | None = None
        if package_key == "foam" and _is_insulation_scope(scope):
            thickness = positive_number(
                row.get("thickness_inches"),
                row.get("foam_thickness_inches"),
                editable_decisions.get("thickness_inches"),
                existing_decisions.get("thickness_inches"),
                default=0.0,
            )
            yield_factor = positive_number(
                row.get("yield_factor"),
                row.get("median_foam_yield"),
                row.get("yield_or_coverage"),
                editable_decisions.get("yield_or_coverage"),
                editable_decisions.get("yield_factor"),
                existing_decisions.get("yield_or_coverage"),
                existing_decisions.get("yield_factor"),
                default=0.0,
            )
            formula_result = calculate_insulation_foam(
                area_sqft=basis_sqft,
                thickness_inches=thickness,
                yield_or_coverage=yield_factor,
                unit_price=unit_price,
                units_per_sqft_per_inch=row.get("median_units_per_sqft_per_inch"),
                cost_per_sqft=historical_cost_per_sqft,
                include=include,
            )
            surface_rows = _records(updated.get("insulation_surfaces"))
            has_surface_decisions = any(
                str(surface.get("surface_type") or "").lower() != "general" or safe_number(surface.get("target_r_value"), 0.0) > 0
                for surface in surface_rows
            )
            if has_surface_decisions and not row.get("_foam_template_basis_override"):
                surface_aggregate = aggregate_surface_foam_outputs(
                    surface_rows,
                    yield_or_coverage=yield_factor,
                    unit_price=unit_price,
                    units_per_sqft_per_inch=row.get("median_units_per_sqft_per_inch"),
                    cost_per_sqft=historical_cost_per_sqft,
                    include=include,
                )
                if safe_number(surface_aggregate.get("area_sqft"), 0.0) > 0:
                    formula_result = {
                        **formula_result,
                        **surface_aggregate,
                        "thickness_inches": surface_aggregate.get("weighted_thickness_inches"),
                        "yield_or_coverage": yield_factor,
                        "formula_source": "insulation_surface_decisions",
                    }
                    basis_sqft = safe_number(surface_aggregate.get("area_sqft"), basis_sqft)
                    row["editable_basis_sqft"] = round(basis_sqft, 2)
                    row["default_basis_sqft"] = round(basis_sqft, 2)
                    row["surface_formula_outputs"] = surface_aggregate.get("surface_outputs") or []
                    row["surface_weighted_thickness_inches"] = surface_aggregate.get("weighted_thickness_inches")
            if include and safe_number(formula_result.get("estimated_units"), 0.0) <= 0 and qty_per_sqft > 0 and basis_sqft > 0:
                fallback_units = qty_per_sqft * basis_sqft
                if unit_price > 0:
                    fallback_cost = fallback_units * unit_price
                    fallback_cost_source = "current_pricing"
                elif historical_cost_per_sqft > 0:
                    fallback_cost = historical_cost_per_sqft * basis_sqft
                    fallback_cost_source = "historical_cost_default"
                else:
                    fallback_cost = 0.0
                    fallback_cost_source = "current_pricing_missing"
                formula_result = {
                    **formula_result,
                    "formula_model": "historical_qty_per_sqft_fallback",
                    "formula_source": "historical_qty_per_sqft",
                    "area_sqft": round(basis_sqft, 4),
                    "estimated_units": round(fallback_units, 6),
                    "estimated_sets": round(fallback_units / 1000.0, 6),
                    "estimated_cost": round(fallback_cost, 2),
                    "cost_source": fallback_cost_source,
                }
            row["quantity_model"] = formula_result["formula_model"]
            row["formula_model"] = formula_result["formula_model"]
            row["formula_source"] = formula_result["formula_source"]
            row["thickness_inches"] = formula_result["thickness_inches"]
            row["foam_thickness_inches"] = formula_result["thickness_inches"]
            row["yield_factor"] = formula_result["yield_or_coverage"]
            row["estimated_units"] = round(safe_number(formula_result.get("estimated_units"), 0.0), 2)
            row["estimated_sets"] = round(safe_number(formula_result.get("estimated_sets"), 0.0), 6)
            row["calculated_quantity"] = row["estimated_units"]
            row["estimated_cost"] = formula_result["estimated_cost"]
            row["price_source"] = formula_result["cost_source"]
            row["unit"] = "estimated_units"
        elif package_key in {"coating", "thermal_barrier_coating"}:
            gal_per_100 = positive_number(
                qty_per_sqft * 100 if qty_per_sqft else None,
                row.get("gal_per_100_sqft"),
                editable_decisions.get("gal_per_100_sqft"),
                existing_decisions.get("gal_per_100_sqft"),
                default=0.0,
            )
            waste_pct = safe_number(
                first_nonblank(
                    formula_inputs.get("waste_factor_pct"),
                    row.get("waste_factor_pct"),
                    row.get("margin_pct"),
                ),
                0.0,
            )
            calculator = calculate_insulation_thermal_barrier if package_key == "thermal_barrier_coating" else calculate_roofing_coating
            formula_result = calculator(
                area_sqft=basis_sqft,
                gal_per_100_sqft=gal_per_100,
                unit_price=unit_price,
                waste_factor_pct=waste_pct,
                cost_per_sqft=historical_cost_per_sqft,
                include=include,
            )
            row["quantity_model"] = formula_result["formula_model"]
            row["formula_model"] = formula_result["formula_model"]
            row["formula_source"] = formula_result["formula_source"]
            row["gal_per_100_sqft"] = formula_result["gal_per_100_sqft"]
            row["gal_per_sqft"] = formula_result["gal_per_sqft"]
            row["wet_mils_estimate"] = formula_result["wet_mils_estimate"]
            row["waste_factor_pct"] = formula_result["waste_factor_pct"]
            row["estimated_gallons"] = round(safe_number(formula_result.get("estimated_gallons"), 0.0), 2)
            row["calculated_quantity"] = row["estimated_gallons"]
            row["estimated_cost"] = formula_result["estimated_cost"]
            row["price_source"] = formula_result["cost_source"]
            row["editable_qty_per_sqft"] = round(safe_number(formula_result.get("gal_per_sqft"), qty_per_sqft), 8)
            row["editable_default"] = row["editable_qty_per_sqft"]
        else:
            quantity = qty_per_sqft * basis_sqft if include and basis_sqft else 0.0
            row["calculated_quantity"] = round(quantity, 2)
            if include and unit_price > 0:
                row["estimated_cost"] = round(quantity * unit_price, 2)
                row["price_source"] = "current_pricing"
            elif include and historical_cost_per_sqft > 0 and basis_sqft:
                row["estimated_cost"] = round(historical_cost_per_sqft * basis_sqft, 2)
                row["price_source"] = "historical_cost_default"
                row["needs_review"] = True
            else:
                row["estimated_cost"] = 0.0
                row["price_source"] = "not_included" if not include else "current_pricing_missing"
        if include and row.get("price_source") in {"historical_cost_default", "historical_cost_per_sqft_per_inch"}:
            row["needs_review"] = True
        row["calculated_output"] = row["estimated_cost"]
        row["decision_values"] = {
            **(row.get("decision_values") if isinstance(row.get("decision_values"), dict) else {}),
            "selected_option": row.get("item_name") or row.get("current_item"),
            "basis_sqft": round(basis_sqft, 2),
            "qty_per_sqft": round(safe_number(row.get("editable_qty_per_sqft"), qty_per_sqft), 6),
            "calculated_quantity": row["calculated_quantity"],
            "estimated_cost": row["estimated_cost"],
        }
        if formula_result:
            row["decision_values"].update(
                {
                    key: value
                    for key, value in formula_result.items()
                    if key
                    in {
                        "formula_model",
                        "formula_source",
                        "thickness_inches",
                        "yield_or_coverage",
                        "estimated_units",
                        "estimated_sets",
                        "gal_per_100_sqft",
                        "gal_per_sqft",
                        "estimated_gallons",
                        "wet_mils_estimate",
                        "waste_factor_pct",
                        "cost_source",
                    }
                }
            )
        row["editable_decision_value"] = first_nonblank(row.get("item_name"), row.get("current_item"), row.get("editable_decision_value"))
        row["editable_value"] = _value_summary(
            {
                "item": row.get("item_name") or row.get("current_item"),
                "basis_sqft": round(basis_sqft, 2),
                "qty_per_sqft": round(safe_number(row.get("editable_qty_per_sqft"), qty_per_sqft), 6),
                "thickness_inches": row.get("thickness_inches") if package_key == "foam" else None,
                "yield": row.get("yield_factor") if package_key == "foam" else None,
                "gal_per_100_sqft": row.get("gal_per_100_sqft") if package_key in {"coating", "thermal_barrier_coating"} else None,
            }
        )
        row["calculated_output_summary"] = _value_summary(
            {
                "quantity": row["calculated_quantity"],
                "sets": row.get("estimated_sets") if package_key == "foam" else None,
                "gallons": row.get("estimated_gallons") if package_key in {"coating", "thermal_barrier_coating"} else None,
                "cost": row["estimated_cost"],
            }
        )
        row["workbook_cell_write_preview"] = cell_preview_for_material(row)
    if not _is_insulation_scope(scope):
        if "roofing_foam_template_decisions" in updated:
            updated["roofing_foam_template_decisions"] = _build_roofing_foam_template_decisions(
                scope=scope,
                existing_rows=updated.get("roofing_foam_template_decisions") or None,
            )
            _apply_roofing_foam_template_decisions_to_materials(updated)
        updated["roofing_coating_template_decisions"] = _build_roofing_coating_template_decisions(
            scope=scope,
            coating_row=_coating_material_row(updated.get("materials")),
            existing_rows=updated.get("roofing_coating_template_decisions") or None,
        )
        _apply_roofing_coating_template_decisions_to_materials(updated)
        if "roofing_primer_template_decisions" in updated:
            updated["roofing_primer_template_decisions"] = _build_roofing_primer_template_decisions(
                scope=scope,
                primer_row=_primer_material_row(updated.get("materials")),
                existing_rows=updated.get("roofing_primer_template_decisions") or None,
            )
            _apply_roofing_primer_template_decisions_to_materials(updated)
        if "roofing_detail_template_decisions" in updated:
            updated["roofing_detail_template_decisions"] = _build_roofing_detail_template_decisions(
                scope=scope,
                caulk_row=_caulk_detail_material_row(updated.get("materials")),
                fabric_row=_fabric_material_row(updated.get("materials")),
                existing_rows=updated.get("roofing_detail_template_decisions") or None,
            )
            _apply_roofing_detail_template_decisions_to_materials(updated)
        if "roofing_detail_quantity_template_decisions" in updated:
            updated["roofing_detail_quantity_template_decisions"] = _build_roofing_detail_quantity_template_decisions(
                scope=scope,
                materials=updated.get("materials") or [],
                existing_rows=updated.get("roofing_detail_quantity_template_decisions") or None,
            )
            _apply_roofing_detail_quantity_template_decisions_to_materials(updated)
        if "roofing_board_fastener_template_decisions" in updated:
            updated["roofing_board_fastener_template_decisions"] = _build_roofing_board_fastener_template_decisions(
                scope=scope,
                board_row=_board_stock_material_row(updated.get("materials")),
                fastener_row=_fastener_material_row(updated.get("materials")),
                plates_row=_plates_material_row(updated.get("materials")),
                existing_rows=updated.get("roofing_board_fastener_template_decisions") or None,
            )
            _apply_roofing_board_fastener_template_decisions_to_materials(updated)
        if "roofing_granules_template_decisions" in updated:
            updated["roofing_granules_template_decisions"] = _build_roofing_granules_template_decisions(
                scope=scope,
                granules_row=_granules_material_row(updated.get("materials")),
                existing_rows=updated.get("roofing_granules_template_decisions") or None,
            )
            _apply_roofing_granules_template_decisions_to_materials(updated)
        if "roofing_equipment_template_decisions" in updated:
            updated["roofing_equipment_template_decisions"] = _build_roofing_equipment_template_decisions(
                scope=scope,
                adders=updated.get("adders") or [],
                existing_rows=updated.get("roofing_equipment_template_decisions") or None,
            )
            _apply_roofing_equipment_template_decisions_to_adders(updated)
        if "roofing_travel_freight_template_decisions" in updated:
            updated["roofing_travel_freight_template_decisions"] = _build_roofing_travel_freight_template_decisions(
                scope=scope,
                adders=updated.get("adders") or [],
                existing_rows=updated.get("roofing_travel_freight_template_decisions") or None,
            )
            _apply_roofing_travel_freight_template_decisions_to_adders(updated)
        if "roofing_accessory_template_decisions" in updated:
            updated["roofing_accessory_template_decisions"] = _build_roofing_accessory_template_decisions(
                scope=scope,
                materials=updated.get("materials") or [],
                coating_decisions=updated.get("roofing_coating_template_decisions") or [],
                existing_rows=updated.get("roofing_accessory_template_decisions") or None,
            )
            _apply_roofing_accessory_template_decisions_to_materials(updated)
        if "roofing_labor_template_decisions" in updated:
            updated["roofing_labor_template_decisions"] = _build_roofing_labor_template_decisions(
                scope=scope,
                labor_rows=updated.get("labor") or [],
                existing_rows=updated.get("roofing_labor_template_decisions") or None,
            )
            _apply_roofing_labor_template_decisions_to_labor(updated)
    if _is_insulation_scope(scope):
        if not updated.get("area_calculation_trace"):
            updated["area_calculation_trace"] = build_area_calculation_trace(scope)
        updated["area_calculation_explanation"] = build_area_calculation_explanation(
            scope,
            trace_rows=updated.get("area_calculation_trace") or [],
        )
        updated["insulation_foam_template_decisions"] = _build_insulation_foam_template_decisions(
            scope=scope,
            foam_row=_foam_material_row(updated.get("materials")),
            existing_rows=updated.get("insulation_foam_template_decisions") or None,
        )
        updated["insulation_detail_material_template_decisions"] = _build_insulation_decision_rows(
            section="insulation_detail_material_template_decisions",
            specs=INSULATION_DETAIL_DECISION_SPECS,
            scope=scope,
            materials=updated.get("materials") or [],
            adders=updated.get("adders") or [],
            existing_rows=updated.get("insulation_detail_material_template_decisions") or None,
        )
        updated["insulation_thermal_barrier_template_decisions"] = _build_insulation_decision_rows(
            section="insulation_thermal_barrier_template_decisions",
            specs=INSULATION_THERMAL_DECISION_SPECS,
            scope=scope,
            materials=updated.get("materials") or [],
            adders=updated.get("adders") or [],
            existing_rows=updated.get("insulation_thermal_barrier_template_decisions") or None,
        )
        insulation_dependencies = _insulation_dependency_totals(
            updated,
            rows={
                "insulation_detail_material_template_decisions": updated.get("insulation_detail_material_template_decisions") or [],
                "insulation_thermal_barrier_template_decisions": updated.get("insulation_thermal_barrier_template_decisions") or [],
            },
        )
        updated["insulation_support_material_template_decisions"] = _build_insulation_decision_rows(
            section="insulation_support_material_template_decisions",
            specs=INSULATION_SUPPORT_DECISION_SPECS,
            scope=scope,
            materials=updated.get("materials") or [],
            adders=updated.get("adders") or [],
            existing_rows=updated.get("insulation_support_material_template_decisions") or None,
            dependencies=insulation_dependencies,
        )
        insulation_dependencies = _insulation_dependency_totals(
            updated,
            rows={
                "insulation_detail_material_template_decisions": updated.get("insulation_detail_material_template_decisions") or [],
                "insulation_thermal_barrier_template_decisions": updated.get("insulation_thermal_barrier_template_decisions") or [],
                "insulation_support_material_template_decisions": updated.get("insulation_support_material_template_decisions") or [],
            },
        )
        updated["insulation_equipment_logistics_template_decisions"] = _build_insulation_decision_rows(
            section="insulation_equipment_logistics_template_decisions",
            specs=INSULATION_EQUIPMENT_LOGISTICS_DECISION_SPECS,
            scope=scope,
            materials=updated.get("materials") or [],
            adders=updated.get("adders") or [],
            existing_rows=updated.get("insulation_equipment_logistics_template_decisions") or None,
            dependencies=insulation_dependencies,
        )
        updated["insulation_compliance_template_decisions"] = _build_insulation_decision_rows(
            section="insulation_compliance_template_decisions",
            specs=INSULATION_COMPLIANCE_DECISION_SPECS,
            scope=scope,
            materials=updated.get("materials") or [],
            adders=updated.get("adders") or [],
            existing_rows=updated.get("insulation_compliance_template_decisions") or None,
            dependencies=insulation_dependencies,
        )
        updated["insulation_labor_template_decisions"] = _build_insulation_labor_template_decisions(
            scope=scope,
            labor_rows=updated.get("labor") or [],
            existing_rows=updated.get("insulation_labor_template_decisions") or None,
        )
        insulation_dependencies = _insulation_dependency_totals(
            updated,
            rows={
                "insulation_detail_material_template_decisions": updated.get("insulation_detail_material_template_decisions") or [],
                "insulation_thermal_barrier_template_decisions": updated.get("insulation_thermal_barrier_template_decisions") or [],
                "insulation_support_material_template_decisions": updated.get("insulation_support_material_template_decisions") or [],
                "insulation_equipment_logistics_template_decisions": updated.get("insulation_equipment_logistics_template_decisions") or [],
                "insulation_compliance_template_decisions": updated.get("insulation_compliance_template_decisions") or [],
                "insulation_labor_template_decisions": updated.get("insulation_labor_template_decisions") or [],
            },
        )
        updated["insulation_pricing_template_decisions"] = _build_insulation_decision_rows(
            section="insulation_pricing_template_decisions",
            specs=INSULATION_PRICING_DECISION_SPECS,
            scope=scope,
            materials=updated.get("materials") or [],
            adders=updated.get("adders") or [],
            existing_rows=updated.get("insulation_pricing_template_decisions") or None,
            dependencies=insulation_dependencies,
        )
        updated["insulation_performance_specs"] = build_insulation_performance_specs(
            scope=scope,
            surface_rows=_records(updated.get("insulation_surfaces")),
            foam_row=_foam_material_row(updated.get("materials")),
        )
    for row in updated.get("labor") or []:
        if row.get("reset_to_historical_default"):
            row["editable_hours_per_1000_sqft"] = row.get("historical_hours_per_1000_sqft", 0.0)
            row["reset_to_historical_default"] = False
        include = bool(row.get("include"))
        hours_per_1000 = safe_number(row.get("editable_hours_per_1000_sqft"), 0.0)
        historical_hours = safe_number(row.get("historical_hours_per_1000_sqft"), 0.0)
        row["manual_override"] = abs(hours_per_1000 - historical_hours) > 1e-9
        existing_decisions = row.get("decision_values") if isinstance(row.get("decision_values"), dict) else {}
        editable_decisions = decision_dict(row.get("editable_decision_value"))
        formula_inputs = {**existing_decisions, **editable_decisions}
        labor_rate = safe_number(
            first_nonblank(
                formula_inputs.get("hourly_rate"),
                row.get("hourly_rate"),
                row.get("labor_rate"),
                hourly_rate,
            ),
            hourly_rate,
        )
        days = safe_number(first_nonblank(formula_inputs.get("days"), row.get("editable_days"), row.get("days")), 0.0)
        crew_size = safe_number(first_nonblank(formula_inputs.get("crew_size"), row.get("crew_size")), 0.0)
        daily_rate = safe_number(first_nonblank(formula_inputs.get("daily_rate"), row.get("daily_rate")), 0.0)
        explicit_total_hours = safe_number(
            first_nonblank(editable_decisions.get("total_hours"), row.get("editable_total_hours")),
            0.0,
        )
        formula_mode = str(first_nonblank(formula_inputs.get("formula_mode"), row.get("formula_mode"), "mixed_formula"))
        labor_formula = calculate_mixed_labor(
            days=days,
            crew_size=crew_size,
            total_hours=explicit_total_hours,
            hours_per_1000_sqft=hours_per_1000,
            area_sqft=area,
            daily_rate=daily_rate,
            hourly_rate=labor_rate,
            formula_mode=formula_mode,
            include=include,
        )
        hours = safe_number(labor_formula.get("total_hours"), 0.0)
        row["historical_median"] = round(historical_hours, 4)
        row["editable_default"] = round(hours_per_1000, 4)
        row["calculated_hours"] = round(hours, 2)
        row["estimated_cost"] = round(safe_number(labor_formula.get("estimated_cost"), 0.0), 2)
        row["total_hours"] = row["calculated_hours"]
        row["calculated_output"] = row["estimated_cost"]
        row["days"] = round(safe_number(labor_formula.get("days"), days), 4)
        row["editable_days"] = row["days"]
        row["crew_size"] = int(safe_number(labor_formula.get("crew_size"), crew_size) or 0)
        row["crew_people_selection"] = row["crew_size"]
        row["daily_rate"] = round(safe_number(labor_formula.get("daily_rate"), daily_rate), 4)
        row["hourly_rate"] = round(safe_number(labor_formula.get("hourly_rate"), labor_rate), 4)
        row["labor_rate"] = row["hourly_rate"]
        row["formula_mode"] = str(labor_formula.get("formula_mode") or formula_mode)
        row["formula_model"] = str(labor_formula.get("formula_model") or "labor_cost_from_days_crew_rate")
        row["formula_source"] = str(labor_formula.get("formula_source") or "")
        row["decision_values"] = {
            **(row.get("decision_values") if isinstance(row.get("decision_values"), dict) else {}),
            "days": row["days"],
            "crew_size": row["crew_size"],
            "total_hours": row["calculated_hours"],
            "daily_rate": row["daily_rate"],
            "hourly_rate": row["hourly_rate"],
            "formula_mode": row.get("formula_mode") or "",
            "formula_model": row.get("formula_model") or "",
            "formula_source": row.get("formula_source") or "",
            "estimated_cost": row["estimated_cost"],
        }
        row["editable_decision_value"] = {
            "days": row["days"],
            "crew_size": row["crew_size"],
            "daily_rate": row["daily_rate"],
            "hourly_rate": row["hourly_rate"],
            "formula_mode": row.get("formula_mode") or "",
        }
        row["editable_value"] = _value_summary(row["editable_decision_value"])
        row["calculated_output_summary"] = _value_summary(
            {
                "hours": row["calculated_hours"],
                "cost": row["estimated_cost"],
                "formula_mode": row.get("formula_mode") or "",
                "formula_source": row.get("formula_source") or "",
            }
        )
        row["workbook_cell_write_preview"] = cell_preview_for_labor(row)
    for row in updated.get("adders") or []:
        if row.get("reset_to_historical_default"):
            row["editable_value"] = row.get("historical_default_value", row.get("median_cost_when_used", 0.0))
            row["reset_to_historical_default"] = False
        historical_default = safe_number(row.get("historical_default_value"), 0.0)
        editable_value = safe_number(row.get("editable_value"), 0.0)
        row["historical_median"] = round(safe_number(row.get("median_cost_when_used"), historical_default), 2)
        row["editable_default"] = round(editable_value, 2)
        row["manual_override"] = abs(editable_value - historical_default) > 1e-9
        row["estimated_cost"] = round(safe_number(row.get("editable_value"), 0.0), 2) if row.get("include") else 0.0
    return updated


def _material_row_is_edited(row: dict[str, Any]) -> bool:
    return (
        bool(row.get("manual_override"))
        or abs(safe_number(row.get("editable_qty_per_sqft"), 0.0) - safe_number(row.get("historical_qty_per_sqft"), 0.0)) > 1e-9
        or abs(safe_number(row.get("editable_basis_sqft"), 0.0) - safe_number(row.get("default_basis_sqft"), 0.0)) > 1e-9
    )


def _labor_row_is_edited(row: dict[str, Any]) -> bool:
    return bool(row.get("manual_override")) or abs(safe_number(row.get("editable_hours_per_1000_sqft"), 0.0) - safe_number(row.get("historical_hours_per_1000_sqft"), 0.0)) > 1e-9


def _adder_row_is_edited(row: dict[str, Any]) -> bool:
    return bool(row.get("manual_override")) or abs(safe_number(row.get("editable_value"), 0.0) - safe_number(row.get("historical_default_value"), 0.0)) > 1e-9


def apply_historical_filter_update(previous_workbench: dict[str, Any] | None, filtered_workbench: dict[str, Any]) -> dict[str, Any]:
    """Merge a new filtered default pool with prior estimator edits.

    Filter changes should refresh historical medians for untouched rows, but they should not erase an
    estimator's edited quantity, labor rate, include checkbox, or adder amount.
    """
    if not previous_workbench:
        return filtered_workbench
    updated = deepcopy(filtered_workbench)

    previous_materials = {row.get("package_key"): row for row in previous_workbench.get("materials") or []}
    for row in updated.get("materials") or []:
        previous = previous_materials.get(row.get("package_key"))
        if not previous:
            continue
        row["include"] = previous.get("include", row.get("include"))
        row["current_unit_price"] = previous.get("current_unit_price", row.get("current_unit_price"))
        row["item_name"] = previous.get("item_name", row.get("item_name"))
        row["unit"] = previous.get("unit", row.get("unit"))
        if previous.get("reset_to_historical_default"):
            row["editable_qty_per_sqft"] = row.get("historical_qty_per_sqft", 0.0)
            row["editable_basis_sqft"] = row.get("default_basis_sqft", row.get("editable_basis_sqft", 0.0))
        elif _material_row_is_edited(previous):
            row["editable_qty_per_sqft"] = previous.get("editable_qty_per_sqft", row.get("editable_qty_per_sqft"))
            row["editable_basis_sqft"] = previous.get("editable_basis_sqft", row.get("editable_basis_sqft"))
            row["manual_override"] = True

    previous_labor = {row.get("package_key"): row for row in previous_workbench.get("labor") or []}
    for row in updated.get("labor") or []:
        previous = previous_labor.get(row.get("package_key"))
        if not previous:
            continue
        row["include"] = previous.get("include", row.get("include"))
        row["crew_size"] = previous.get("crew_size", row.get("crew_size"))
        row["labor_rate"] = previous.get("labor_rate", row.get("labor_rate"))
        if previous.get("reset_to_historical_default"):
            row["editable_hours_per_1000_sqft"] = row.get("historical_hours_per_1000_sqft", 0.0)
        elif _labor_row_is_edited(previous):
            row["editable_hours_per_1000_sqft"] = previous.get("editable_hours_per_1000_sqft", row.get("editable_hours_per_1000_sqft"))
            row["manual_override"] = True

    previous_adders = {row.get("adder_key"): row for row in previous_workbench.get("adders") or []}
    for row in updated.get("adders") or []:
        previous = previous_adders.get(row.get("adder_key"))
        if not previous:
            continue
        row["include"] = previous.get("include", row.get("include"))
        if previous.get("reset_to_historical_default"):
            row["editable_value"] = row.get("historical_default_value", row.get("editable_value"))
        elif _adder_row_is_edited(previous):
            row["editable_value"] = previous.get("editable_value", row.get("editable_value"))
            row["manual_override"] = True

    previous_surfaces = {row.get("surface_type"): row for row in previous_workbench.get("insulation_surfaces") or []}
    for row in updated.get("insulation_surfaces") or []:
        previous = previous_surfaces.get(row.get("surface_type"))
        if not previous:
            continue
        row["include"] = previous.get("include", row.get("include"))
        for field in ("target_r_value", "edited_thickness_inches", "net_area_sqft", "deduction_area_sqft"):
            if previous.get(field) not in (None, ""):
                row[field] = previous.get(field)
        if previous.get("edited_thickness_inches") != previous.get("rounded_thickness_inches"):
            row["manual_override"] = True

    return recalculate_workbench_tables(updated)


def manual_material_workbench_row(scope: dict[str, Any] | None = None, *, item_name: str = "Manual custom item") -> dict[str, Any]:
    scope = scope or {}
    return {
        "include": False,
        "package": "Manual",
        "package_key": "manual",
        "template_bucket": "manual",
        "workbook_row": "",
        "item_name": item_name,
        "current_item": item_name,
        "historical_item": "",
        "item_source": "manual",
        "item_options": item_name,
        "item_options_json": _item_options_payload([], [], {"item_name": item_name, "unit": "unit", "unit_price": 0, "item_source": "manual"}),
        "suggested_by_notes_rules": "review",
        "historical_usage_rate": 0.0,
        "historical_qty_per_basis_sqft": 0.0,
        "historical_qty_per_sqft": 0.0,
        "historical_median": 0.0,
        "item_level_qty_per_sqft": 0.0,
        "item_level_evidence_count": 0,
        "editable_basis_sqft": 0.0,
        "default_basis_sqft": 0.0,
        "p25_qty_per_sqft": 0.0,
        "p75_qty_per_sqft": 0.0,
        "editable_qty_per_sqft": 0.0,
        "editable_default": 0.0,
        "calculated_quantity": 0.0,
        "unit": "unit",
        "current_unit_price": 0.0,
        "current_price": 0.0,
        "historical_cost_per_sqft": 0.0,
        "historical_cost_default": 0.0,
        "estimated_cost": 0.0,
        "evidence_count": 0,
        "historical_cost_evidence_count": 0,
        "historical_jobs_found": 0,
        "rows_accepted": 0,
        "rows_rejected": 0,
        "rejection_reasons": "",
        "range_width": 0.0,
        "relative_range_width": 0.0,
        "variability_warning": "",
        "filters_applied": "",
        "filters_relaxed": "",
        "minimum_evidence_count": DEFAULT_MIN_EVIDENCE_COUNT,
        "filter_hash": "",
        "manual_override": False,
        "reset_to_historical_default": False,
        "confidence": "manual",
        "source": "manual",
        "pricing_source": "manual",
        "price_source": "manual",
        "needs_review": True,
        "notes": "Manual material line. Enter item, basis, quantity rate, unit, and unit price.",
        "explanation": "Manual material line. Enter item, basis, quantity rate, unit, and unit price.",
    }


def workbench_to_draft_workbook_inputs(workbench: dict[str, Any]) -> dict[str, Any]:
    workbench = recalculate_workbench_tables(workbench)
    scope = workbench.get("scope") or {}
    material_rows = []
    roofing_foam_decision_rows = [
        row for row in workbench.get("roofing_foam_template_decisions") or [] if isinstance(row, dict) and row.get("include")
    ]
    roofing_coating_decision_rows = [
        row for row in workbench.get("roofing_coating_template_decisions") or [] if isinstance(row, dict) and row.get("include")
    ]
    roofing_primer_decision_rows = [
        row for row in workbench.get("roofing_primer_template_decisions") or [] if isinstance(row, dict) and row.get("include")
    ]
    roofing_detail_decision_rows = [
        row for row in workbench.get("roofing_detail_template_decisions") or [] if isinstance(row, dict) and row.get("include")
    ]
    roofing_detail_quantity_decision_rows = [
        row
        for row in workbench.get("roofing_detail_quantity_template_decisions") or []
        if isinstance(row, dict) and row.get("include")
    ]
    roofing_board_fastener_decision_rows = [
        row
        for row in workbench.get("roofing_board_fastener_template_decisions") or []
        if isinstance(row, dict) and row.get("include")
    ]
    roofing_granules_decision_rows = [
        row
        for row in workbench.get("roofing_granules_template_decisions") or []
        if isinstance(row, dict) and row.get("include")
    ]
    roofing_equipment_decision_rows = [
        row
        for row in workbench.get("roofing_equipment_template_decisions") or []
        if isinstance(row, dict) and row.get("include")
    ]
    roofing_travel_freight_decision_rows = [
        row
        for row in workbench.get("roofing_travel_freight_template_decisions") or []
        if isinstance(row, dict) and row.get("include")
    ]
    roofing_accessory_decision_rows = [
        row
        for row in workbench.get("roofing_accessory_template_decisions") or []
        if isinstance(row, dict) and row.get("include")
    ]
    insulation_foam_decision_rows = [
        row
        for row in workbench.get("insulation_foam_template_decisions") or []
        if isinstance(row, dict) and row.get("include")
    ]
    insulation_material_decision_rows = [
        row
        for section in (
            "insulation_detail_material_template_decisions",
            "insulation_thermal_barrier_template_decisions",
            "insulation_support_material_template_decisions",
            "insulation_equipment_logistics_template_decisions",
            "insulation_compliance_template_decisions",
            "insulation_pricing_template_decisions",
        )
        for row in workbench.get(section) or []
        if isinstance(row, dict) and row.get("include")
    ]
    insulation_labor_decision_rows = [
        row
        for row in workbench.get("insulation_labor_template_decisions") or []
        if isinstance(row, dict) and row.get("include")
    ]
    for row in insulation_foam_decision_rows:
        material_rows.append(
            {
                "decision_id": row.get("decision_id"),
                "template_bucket": "foam",
                "workbook_row": row.get("workbook_row"),
                "row_traceability": f"Estimate row {row.get('workbook_row')}",
                "item": first_nonblank(row.get("selected_pricing_candidate"), row.get("resolved_template_option"), "Insulation foam"),
                "category": "foam",
                "quantity": safe_number(row.get("estimated_units"), 0.0),
                "basis_sqft": safe_number(row.get("basis_sqft"), 0.0),
                "area_sqft": safe_number(row.get("basis_sqft"), 0.0),
                "thickness_inches": safe_number(row.get("thickness_inches"), 0.0),
                "yield_factor": safe_number(row.get("yield_or_coverage"), 0.0),
                "yield_or_coverage": safe_number(row.get("yield_or_coverage"), 0.0),
                "estimated_units": safe_number(row.get("estimated_units"), 0.0),
                "estimated_sets": safe_number(row.get("estimated_sets"), 0.0),
                "selector_code": row.get("editable_selector_code") or row.get("selector_code"),
                "unit": "estimated_units",
                "unit_price": safe_number(row.get("unit_price"), 0.0),
                "estimated_cost": safe_number(row.get("estimated_cost"), 0.0),
                "formula_model": row.get("formula_model"),
                "formula_source": row.get("formula_source"),
                "calculated_output_summary": row.get("calculated_output_summary"),
                "surface_formula_outputs": row.get("surface_formula_outputs") or [],
                "workbook_cell_write_preview": row.get("workbook_cell_write_preview") or [],
                "notes": (
                    f"Insulation foam template decision; selector={row.get('editable_selector_code') or row.get('selector_code')}; "
                    f"template_option={row.get('resolved_template_option')}; evidence_count={row.get('historical_selector_evidence_count')}"
                ),
            }
        )
    for row in insulation_material_decision_rows:
        bucket = str(row.get("template_bucket") or "").lower()
        material_rows.append(
            {
                "decision_id": row.get("decision_id"),
                "template_bucket": bucket,
                "workbook_row": row.get("workbook_row"),
                "row_traceability": f"Estimate row {row.get('workbook_row')}",
                "item": first_nonblank(row.get("selected_pricing_candidate"), row.get("resolved_template_option"), row.get("template_line"), bucket),
                "category": bucket,
                "quantity": safe_number(
                    first_nonblank(row.get("estimated_gallons"), row.get("estimated_units"), row.get("estimated_drums"), row.get("quantity")),
                    0.0,
                ),
                "basis_sqft": safe_number(row.get("basis_sqft"), 0.0),
                "area_sqft": safe_number(row.get("basis_sqft"), 0.0),
                "linear_ft": safe_number(row.get("linear_ft"), 0.0),
                "estimated_units": safe_number(first_nonblank(row.get("estimated_units"), row.get("estimated_drums")), 0.0),
                "estimated_gallons": safe_number(row.get("estimated_gallons"), 0.0),
                "selector_code": row.get("editable_selector_code") or row.get("selector_code"),
                "unit": "gal" if bucket == "thermal_barrier_coating" else "unit",
                "unit_price": safe_number(row.get("unit_price"), 0.0),
                "gal_per_100_sqft": safe_number(row.get("gal_per_100_sqft"), 0.0),
                "waste_factor_pct": safe_number(row.get("waste_factor_pct"), 0.0),
                "feet_per_unit": safe_number(row.get("feet_per_unit"), 0.0),
                "period": safe_number(row.get("period"), 0.0),
                "days": safe_number(row.get("days"), 0.0),
                "margin_pct": safe_number(row.get("margin_pct"), 0.0),
                "trip_count": safe_number(row.get("trip_count"), 0.0),
                "round_trip_miles": safe_number(row.get("round_trip_miles"), 0.0),
                "amount": safe_number(row.get("amount"), 0.0),
                "estimated_cost": safe_number(row.get("estimated_cost"), 0.0),
                "formula_model": row.get("formula_model"),
                "formula_source": row.get("formula_source"),
                "calculated_output_summary": row.get("calculated_output_summary"),
                "workbook_cell_write_preview": row.get("workbook_cell_write_preview") or [],
                "notes": f"Insulation template decision; template_option={row.get('resolved_template_option')}; section={row.get('section')}.",
            }
        )
    for row in roofing_foam_decision_rows:
        material_rows.append(
            {
                "decision_id": row.get("decision_id"),
                "template_bucket": "roofing_foam",
                "workbook_row": row.get("workbook_row"),
                "row_traceability": f"Estimate row {row.get('workbook_row')}",
                "item": first_nonblank(row.get("selected_pricing_candidate"), row.get("resolved_template_option"), "Roofing SPF foam"),
                "category": "roofing_foam",
                "quantity": safe_number(row.get("estimated_units"), 0.0),
                "basis_sqft": safe_number(row.get("basis_sqft"), 0.0),
                "area_sqft": safe_number(row.get("basis_sqft"), 0.0),
                "thickness_inches": safe_number(row.get("thickness_inches"), 0.0),
                "yield_factor": safe_number(row.get("yield_or_coverage"), 0.0),
                "yield_or_coverage": safe_number(row.get("yield_or_coverage"), 0.0),
                "estimated_units": safe_number(row.get("estimated_units"), 0.0),
                "estimated_sets": safe_number(row.get("estimated_sets"), 0.0),
                "selector_code": row.get("editable_selector_code") or row.get("selector_code"),
                "unit": "estimated_units",
                "unit_price": safe_number(row.get("unit_price"), 0.0),
                "estimated_cost": safe_number(row.get("estimated_cost"), 0.0),
                "formula_model": row.get("formula_model"),
                "formula_source": row.get("formula_source"),
                "calculated_output_summary": row.get("calculated_output_summary"),
                "workbook_cell_write_preview": row.get("workbook_cell_write_preview") or [],
                "notes": (
                    f"Roofing SPF foam template decision; selector={row.get('editable_selector_code') or row.get('selector_code')}; "
                    f"template_option={row.get('resolved_template_option')}; evidence_count={row.get('historical_selector_evidence_count')}"
                ),
            }
        )
    for row in roofing_coating_decision_rows:
        material_rows.append(
            {
                "decision_id": row.get("decision_id"),
                "template_bucket": "coating",
                "workbook_row": row.get("workbook_row"),
                "row_traceability": f"Estimate row {row.get('workbook_row')}",
                "item": first_nonblank(row.get("selected_pricing_candidate"), row.get("resolved_template_option"), "Roof coating"),
                "category": "coating",
                "quantity": safe_number(row.get("estimated_gallons"), 0.0),
                "basis_sqft": safe_number(row.get("basis_sqft"), 0.0),
                "area_sqft": safe_number(row.get("basis_sqft"), 0.0),
                "gal_per_100_sqft": safe_number(row.get("gal_per_100_sqft"), 0.0),
                "gal_per_sqft": safe_number(row.get("gal_per_sqft"), 0.0),
                "waste_factor_pct": safe_number(row.get("waste_factor_pct"), 0.0),
                "wet_mils_estimate": safe_number(row.get("wet_mils_estimate"), 0.0),
                "estimated_gallons": safe_number(row.get("estimated_gallons"), 0.0),
                "selector_code": row.get("editable_selector_code") or row.get("selector_code"),
                "unit": "gal",
                "unit_price": safe_number(row.get("unit_price"), 0.0),
                "estimated_cost": safe_number(row.get("estimated_cost"), 0.0),
                "formula_model": row.get("formula_model"),
                "formula_source": row.get("formula_source"),
                "calculated_output_summary": row.get("calculated_output_summary"),
                "workbook_cell_write_preview": row.get("workbook_cell_write_preview") or [],
                "notes": (
                    f"Roof coating template decision; selector={row.get('editable_selector_code') or row.get('selector_code')}; "
                    f"template_option={row.get('resolved_template_option')}; evidence_count={row.get('historical_selector_evidence_count')}"
                ),
            }
        )
    for row in roofing_primer_decision_rows:
        material_rows.append(
            {
                "decision_id": row.get("decision_id"),
                "template_bucket": "primer",
                "workbook_row": row.get("workbook_row"),
                "row_traceability": f"Estimate row {row.get('workbook_row')}",
                "item": first_nonblank(row.get("selected_pricing_candidate"), row.get("resolved_template_option"), "Primer"),
                "category": "primer",
                "quantity": safe_number(row.get("estimated_units"), 0.0),
                "basis_sqft": safe_number(row.get("basis_sqft"), 0.0),
                "area_sqft": safe_number(row.get("basis_sqft"), 0.0),
                "coverage_sqft_per_unit": safe_number(row.get("coverage_sqft_per_unit"), ROOFING_PRIMER_DEFAULT_COVERAGE_SQFT_PER_UNIT),
                "estimated_units": safe_number(row.get("estimated_units"), 0.0),
                "selector_code": row.get("editable_selector_code") or row.get("selector_code"),
                "unit": "unit",
                "unit_price": safe_number(row.get("unit_price"), 0.0),
                "estimated_cost": safe_number(row.get("estimated_cost"), 0.0),
                "formula_model": row.get("formula_model"),
                "formula_source": row.get("formula_source"),
                "calculated_output_summary": row.get("calculated_output_summary"),
                "workbook_cell_write_preview": row.get("workbook_cell_write_preview") or [],
                "notes": (
                    f"Roofing primer template decision; selector={row.get('editable_selector_code') or row.get('selector_code')}; "
                    f"template_option={row.get('resolved_template_option')}; evidence_count={row.get('historical_selector_evidence_count')}"
                ),
            }
        )
    for row in roofing_detail_decision_rows:
        template_bucket = str(row.get("template_bucket") or "")
        is_fabric = template_bucket == "fabric"
        material_rows.append(
            {
                "decision_id": row.get("decision_id"),
                "template_bucket": template_bucket,
                "workbook_row": row.get("workbook_row"),
                "row_traceability": f"Estimate row {row.get('workbook_row')}",
                "item": first_nonblank(row.get("selected_pricing_candidate"), row.get("resolved_template_option"), "Fabric" if is_fabric else "Caulk / sealant"),
                "category": "fabric" if is_fabric else "caulk_detail",
                "quantity": safe_number(row.get("linear_ft") if is_fabric else row.get("estimated_units") or row.get("units"), 0.0),
                "linear_ft": safe_number(row.get("linear_ft"), 0.0),
                "estimated_units": safe_number(row.get("estimated_units") or row.get("units"), 0.0),
                "selector_code": row.get("editable_selector_code") or row.get("selector_code"),
                "unit": "lf" if is_fabric else "unit",
                "unit_price": safe_number(row.get("unit_price"), 0.0),
                "estimated_cost": safe_number(row.get("estimated_cost"), 0.0),
                "formula_model": row.get("formula_model"),
                "formula_source": row.get("formula_source"),
                "calculated_output_summary": row.get("calculated_output_summary"),
                "workbook_cell_write_preview": row.get("workbook_cell_write_preview") or [],
                "notes": (
                    f"Roofing {'fabric' if is_fabric else 'caulk/sealant'} template decision; "
                    f"selector={row.get('editable_selector_code') or row.get('selector_code')}; "
                    f"template_option={row.get('resolved_template_option')}; evidence_count={row.get('historical_selector_evidence_count')}"
                ),
            }
        )
    for row in roofing_detail_quantity_decision_rows:
        bucket = str(row.get("template_bucket") or "").lower()
        material_rows.append(
            {
                "decision_id": row.get("decision_id"),
                "template_bucket": bucket,
                "workbook_row": row.get("workbook_row"),
                "row_traceability": f"Estimate row {row.get('workbook_row')}",
                "item": first_nonblank(row.get("resolved_template_option"), row.get("template_bucket")),
                "category": bucket,
                "quantity": safe_number(row.get("linear_ft") or row.get("units") or row.get("estimated_units"), 0.0),
                "linear_ft": safe_number(row.get("linear_ft"), 0.0),
                "estimated_units": safe_number(row.get("estimated_units") or row.get("units"), 0.0),
                "amount": safe_number(row.get("amount"), 0.0),
                "unit": "lf" if safe_number(row.get("linear_ft"), 0.0) > 0 else "unit",
                "estimated_cost": safe_number(row.get("estimated_cost"), 0.0),
                "formula_model": row.get("formula_model"),
                "formula_source": row.get("formula_source"),
                "calculated_output_summary": row.get("calculated_output_summary"),
                "workbook_cell_write_preview": row.get("workbook_cell_write_preview") or [],
                "notes": f"Roofing detail quantity template decision; template_option={row.get('resolved_template_option')}.",
            }
        )
    for row in roofing_board_fastener_decision_rows:
        template_bucket = str(row.get("template_bucket") or "")
        is_board = template_bucket == "board_stock"
        is_plate = template_bucket == "plates"
        material_rows.append(
            {
                "decision_id": row.get("decision_id"),
                "template_bucket": template_bucket,
                "workbook_row": row.get("workbook_row"),
                "row_traceability": f"Estimate row {row.get('workbook_row')}",
                "item": first_nonblank(
                    row.get("selected_pricing_candidate"),
                    row.get("resolved_template_option"),
                    "Board stock" if is_board else ("Plates" if is_plate else "Fasteners"),
                ),
                "category": template_bucket,
                "quantity": safe_number(row.get("estimated_squares") if is_board else row.get("estimated_units"), 0.0),
                "basis_sqft": safe_number(row.get("basis_sqft"), 0.0),
                "area_sqft": safe_number(row.get("basis_sqft"), 0.0),
                "board_area_sqft": safe_number(row.get("board_area_sqft"), 0.0),
                "thickness_inches": safe_number(row.get("thickness_inches"), 0.0),
                "estimated_squares": safe_number(row.get("estimated_squares"), 0.0),
                "estimated_units": safe_number(row.get("estimated_units"), 0.0),
                "selector_code": row.get("editable_selector_code") or row.get("selector_code"),
                "unit": "square" if is_board else "m",
                "unit_price": safe_number(row.get("price_per_square") if is_board else row.get("unit_price_per_thousand") or row.get("unit_price"), 0.0),
                "price_per_square": safe_number(row.get("price_per_square"), 0.0),
                "unit_price_per_thousand": safe_number(row.get("unit_price_per_thousand") or row.get("unit_price"), 0.0),
                "estimated_cost": safe_number(row.get("estimated_cost"), 0.0),
                "formula_model": row.get("formula_model"),
                "formula_source": row.get("formula_source"),
                "calculated_output_summary": row.get("calculated_output_summary"),
                "workbook_cell_write_preview": row.get("workbook_cell_write_preview") or [],
                "notes": (
                    f"Roofing board/fastener template decision; template_option={row.get('resolved_template_option')}; "
                    f"evidence_count={row.get('historical_selector_evidence_count')}"
                ),
            }
        )
    for row in roofing_granules_decision_rows:
        material_rows.append(
            {
                "decision_id": row.get("decision_id"),
                "template_bucket": "granules",
                "workbook_row": row.get("workbook_row"),
                "row_traceability": f"Estimate row {row.get('workbook_row')}",
                "item": first_nonblank(row.get("selected_pricing_candidate"), row.get("resolved_template_option"), "Granules"),
                "category": "granules",
                "quantity": safe_number(row.get("estimated_units"), 0.0),
                "basis_sqft": safe_number(row.get("basis_sqft"), 0.0),
                "area_sqft": safe_number(row.get("basis_sqft"), 0.0),
                "coverage_lbs_per_100_sqft": safe_number(row.get("coverage_lbs_per_100_sqft"), ROOFING_GRANULES_DEFAULT_COVERAGE_LBS_PER_100_SQFT),
                "bag_weight_lbs": safe_number(row.get("bag_weight_lbs"), ROOFING_GRANULES_DEFAULT_BAG_WEIGHT_LBS),
                "estimated_units": safe_number(row.get("estimated_units"), 0.0),
                "selector_code": row.get("editable_selector_code") or row.get("selector_code"),
                "unit": "bag",
                "unit_price": safe_number(row.get("unit_price"), 0.0),
                "estimated_cost": safe_number(row.get("estimated_cost"), 0.0),
                "formula_model": row.get("formula_model"),
                "formula_source": row.get("formula_source"),
                "calculated_output_summary": row.get("calculated_output_summary"),
                "workbook_cell_write_preview": row.get("workbook_cell_write_preview") or [],
                "notes": (
                    f"Roofing granules template decision; selector={row.get('editable_selector_code') or row.get('selector_code')}; "
                    f"template_option={row.get('resolved_template_option')}; evidence_count={row.get('historical_selector_evidence_count')}"
                ),
            }
        )
    for row in roofing_equipment_decision_rows:
        bucket = str(row.get("template_bucket") or "").lower()
        payload = {
            "decision_id": row.get("decision_id"),
            "template_bucket": bucket,
            "workbook_row": row.get("workbook_row"),
            "row_traceability": f"Estimate row {row.get('workbook_row')}",
            "item": first_nonblank(row.get("selected_pricing_candidate"), row.get("resolved_template_option"), row.get("template_bucket")),
            "category": bucket,
            "quantity": safe_number(row.get("estimated_units") or row.get("calculated_quantity"), 0.0),
            "estimated_units": safe_number(row.get("estimated_units") or row.get("calculated_quantity"), 0.0),
            "basis_sqft": safe_number(row.get("basis_sqft"), 0.0),
            "area_sqft": safe_number(row.get("basis_sqft"), 0.0),
            "thickness_inches": safe_number(row.get("thickness_inches"), 0.0),
            "selector_code": row.get("editable_selector_code") or row.get("selector_code"),
            "size": row.get("size"),
            "period": safe_number(row.get("period"), 0.0),
            "days": safe_number(row.get("days"), 0.0),
            "unit": "unit",
            "unit_price": safe_number(row.get("unit_price"), 0.0),
            "margin_pct": safe_number(row.get("margin_pct"), 0.0),
            "estimated_cost": safe_number(row.get("estimated_cost"), 0.0),
            "formula_model": row.get("formula_model"),
            "formula_source": row.get("formula_source"),
            "calculated_output_summary": row.get("calculated_output_summary"),
            "workbook_cell_write_preview": row.get("workbook_cell_write_preview") or [],
            "notes": (
                f"Roofing equipment template decision; selector={row.get('editable_selector_code') or row.get('selector_code')}; "
                f"template_option={row.get('resolved_template_option')}; evidence_count={row.get('historical_selector_evidence_count')}"
            ),
        }
        material_rows.append(payload)
    for row in roofing_travel_freight_decision_rows:
        bucket = str(row.get("template_bucket") or "").lower()
        payload = {
            "decision_id": row.get("decision_id"),
            "template_bucket": bucket,
            "workbook_row": row.get("workbook_row"),
            "row_traceability": f"Estimate row {row.get('workbook_row')}",
            "item": first_nonblank(row.get("resolved_template_option"), row.get("template_bucket")),
            "category": bucket,
            "quantity": safe_number(row.get("estimated_units") or row.get("units"), 0.0),
            "estimated_units": safe_number(row.get("estimated_units") or row.get("units"), 0.0),
            "amount": safe_number(row.get("amount"), 0.0),
            "trip_count": safe_number(row.get("trip_count"), 0.0),
            "round_trip_miles": safe_number(row.get("round_trip_miles"), 0.0),
            "unit": "trip" if bucket in {"sales_trips", "truck_expense"} else "unit",
            "unit_price": safe_number(row.get("unit_price"), 0.0),
            "estimated_cost": safe_number(row.get("estimated_cost"), 0.0),
            "formula_model": row.get("formula_model"),
            "formula_source": row.get("formula_source"),
            "calculated_output_summary": row.get("calculated_output_summary"),
            "workbook_cell_write_preview": row.get("workbook_cell_write_preview") or [],
            "notes": f"Roofing travel/freight template decision; template_option={row.get('resolved_template_option')}.",
        }
        material_rows.append(payload)
    for row in roofing_accessory_decision_rows:
        bucket = str(row.get("template_bucket") or "").lower()
        payload = {
            "decision_id": row.get("decision_id"),
            "template_bucket": bucket,
            "workbook_row": row.get("workbook_row"),
            "row_traceability": f"Estimate row {row.get('workbook_row')}",
            "item": first_nonblank(row.get("resolved_template_option"), row.get("template_bucket")),
            "category": bucket,
            "quantity": safe_number(row.get("estimated_units") or row.get("units") or row.get("linear_ft"), 0.0),
            "estimated_units": safe_number(row.get("estimated_units") or row.get("units"), 0.0),
            "linear_ft": safe_number(row.get("linear_ft"), 0.0),
            "amount": safe_number(row.get("amount"), 0.0),
            "total_coating_gallons": safe_number(row.get("total_coating_gallons"), 0.0),
            "selector_code": row.get("editable_selector_code") or row.get("selector_code"),
            "unit": "lf" if safe_number(row.get("linear_ft"), 0.0) > 0 else "unit",
            "unit_price": safe_number(row.get("unit_price"), 0.0),
            "estimated_cost": safe_number(row.get("estimated_cost"), 0.0),
            "formula_model": row.get("formula_model"),
            "formula_source": row.get("formula_source"),
            "calculated_output_summary": row.get("calculated_output_summary"),
            "workbook_cell_write_preview": row.get("workbook_cell_write_preview") or [],
            "notes": f"Roofing accessory/support template decision; template_option={row.get('resolved_template_option')}.",
        }
        material_rows.append(payload)
    for row in workbench.get("materials") or []:
        if not row.get("include"):
            continue
        if _is_insulation_scope(scope) and (insulation_foam_decision_rows or insulation_material_decision_rows):
            continue
        if roofing_foam_decision_rows and str(row.get("package_key") or row.get("template_bucket") or "").lower() in {"roofing_foam", "foam"}:
            continue
        if roofing_coating_decision_rows and str(row.get("package_key") or row.get("template_bucket") or "").lower() == "coating":
            continue
        if roofing_primer_decision_rows and str(row.get("package_key") or row.get("template_bucket") or "").lower() == "primer":
            continue
        if roofing_detail_decision_rows and str(row.get("package_key") or row.get("template_bucket") or "").lower() in {"caulk_detail", "caulk_sealant", "fabric"}:
            continue
        if roofing_detail_quantity_decision_rows and str(row.get("package_key") or row.get("template_bucket") or "").lower() in {
            str(decision.get("template_bucket") or "").lower() for decision in roofing_detail_quantity_decision_rows
        }:
            continue
        if roofing_board_fastener_decision_rows and str(row.get("package_key") or row.get("template_bucket") or "").lower() in {
            "board_stock",
            "fastener_treatment",
            "fasteners",
            "plates",
        }:
            continue
        if roofing_granules_decision_rows and str(row.get("package_key") or row.get("template_bucket") or "").lower() == "granules":
            continue
        if roofing_accessory_decision_rows and str(row.get("package_key") or row.get("template_bucket") or "").lower() in {
            str(decision.get("template_bucket") or "").lower() for decision in roofing_accessory_decision_rows
        }:
            continue
        material_rows.append(
            {
                "decision_id": row.get("decision_id"),
                "template_bucket": row.get("template_bucket") or row.get("package_key"),
                "workbook_row": row.get("workbook_row"),
                "row_traceability": row.get("row_traceability"),
                "item": first_nonblank(row.get("item_name"), row.get("package")),
                "category": row.get("package_key"),
                "quantity": safe_number(row.get("calculated_quantity"), 0.0),
                "basis_sqft": safe_number(row.get("editable_basis_sqft"), 0.0),
                "area_sqft": safe_number(row.get("editable_basis_sqft"), 0.0),
                "thickness_inches": safe_number(row.get("thickness_inches"), 0.0),
                "yield_factor": safe_number(row.get("yield_factor"), 0.0),
                "yield_or_coverage": safe_number(row.get("yield_factor"), 0.0),
                "gal_per_100_sqft": safe_number(row.get("gal_per_100_sqft"), 0.0),
                "gal_per_sqft": safe_number(row.get("gal_per_sqft"), 0.0),
                "waste_factor_pct": safe_number(row.get("waste_factor_pct"), 0.0),
                "wet_mils_estimate": safe_number(row.get("wet_mils_estimate"), 0.0),
                "estimated_units": safe_number(row.get("estimated_units"), 0.0),
                "estimated_sets": safe_number(row.get("estimated_sets"), 0.0),
                "estimated_gallons": safe_number(row.get("estimated_gallons"), 0.0),
                "selector_code": row.get("selector_code"),
                "unit": row.get("unit"),
                "unit_price": safe_number(row.get("current_unit_price"), 0.0),
                "estimated_cost": safe_number(row.get("estimated_cost"), 0.0),
                "formula_model": row.get("formula_model"),
                "formula_source": row.get("formula_source"),
                "calculated_output_summary": row.get("calculated_output_summary"),
                "surface_formula_outputs": row.get("surface_formula_outputs") or [],
                "surface_weighted_thickness_inches": safe_number(row.get("surface_weighted_thickness_inches"), 0.0),
                "workbook_cell_write_preview": row.get("workbook_cell_write_preview") or [],
                "notes": (
                    f"Workbench edited value; item_source={row.get('item_source')}; "
                    f"source={row.get('source')}; evidence_count={row.get('evidence_count')}; "
                    f"basis_sqft={row.get('editable_basis_sqft')}"
                ),
            }
        )
    labor_rows = []
    if _is_insulation_scope(scope) and insulation_labor_decision_rows:
        for row in insulation_labor_decision_rows:
            crew_size = max(1, int(safe_number(row.get("crew_size"), 1)))
            hours = safe_number(row.get("calculated_hours") or row.get("total_hours"), 0.0)
            labor_rows.append(
                {
                    "decision_id": row.get("decision_id"),
                    "template_bucket": row.get("template_bucket") or row.get("package_key"),
                    "workbook_row": row.get("workbook_row"),
                    "row_traceability": row.get("row_traceability"),
                    "task": row.get("template_bucket") or row.get("package_key"),
                    "crew_size": crew_size,
                    "total_hours": hours,
                    "adjusted_days": safe_number(row.get("days"), 0.0),
                    "base_days": safe_number(row.get("days"), 0.0),
                    "daily_rate": safe_number(row.get("daily_rate"), 0.0),
                    "hourly_rate": safe_number(row.get("hourly_rate"), safe_number(row.get("labor_rate"), 0.0)),
                    "formula_mode": row.get("formula_mode"),
                    "formula_model": row.get("formula_model"),
                    "formula_source": row.get("formula_source"),
                    "estimated_cost": safe_number(row.get("estimated_cost"), 0.0),
                    "calculated_output_summary": row.get("calculated_output_summary"),
                    "workbook_cell_write_preview": row.get("workbook_cell_write_preview") or [],
                    "notes": f"Insulation labor template decision; evidence_count={row.get('decision_evidence_count')}",
                }
            )
    for row in workbench.get("labor") or []:
        if not row.get("include"):
            continue
        if _is_insulation_scope(scope) and insulation_labor_decision_rows:
            continue
        crew_size = max(1, int(safe_number(row.get("crew_size"), 1)))
        hours = safe_number(row.get("calculated_hours"), 0.0)
        decision_based_labor = str(row.get("formula_model") or "") == "labor_cost_from_days_crew_rate"
        base_days = safe_number(row.get("days"), 0.0)
        labor_rows.append(
            {
                "decision_id": row.get("decision_id"),
                "template_bucket": row.get("template_bucket") or row.get("package_key"),
                "workbook_row": row.get("workbook_row"),
                "row_traceability": row.get("row_traceability"),
                "task": row.get("package_key"),
                "crew_size": crew_size,
                "total_hours": hours,
                "adjusted_days": round(base_days, 3)
                if decision_based_labor and row.get("days_was_explicit")
                else (round(hours / (crew_size * 8), 3) if crew_size else 0),
                "base_days": base_days,
                "daily_rate": safe_number(row.get("daily_rate"), 0.0),
                "hourly_rate": safe_number(row.get("hourly_rate"), safe_number(row.get("labor_rate"), 0.0)),
                "formula_mode": row.get("formula_mode"),
                "formula_model": row.get("formula_model"),
                "formula_source": row.get("formula_source"),
                "estimated_cost": safe_number(row.get("estimated_cost"), 0.0),
                "calculated_output_summary": row.get("calculated_output_summary"),
                "workbook_cell_write_preview": row.get("workbook_cell_write_preview") or [],
                "notes": f"Workbench edited value; source={row.get('source')}; evidence_count={row.get('evidence_count')}",
            }
        )
    covered_equipment_adders = {
        str(row.get("template_bucket") or "").lower()
        for row in roofing_equipment_decision_rows
        if str(row.get("template_bucket") or "").lower() in {"dumpster", "lift", "generator"}
    }
    covered_travel_freight_adders = {
        str(row.get("template_bucket") or "").lower()
        for row in roofing_travel_freight_decision_rows
        if str(row.get("template_bucket") or "").lower() in {"delivery_fee", "freight", "sales_trips", "truck_expense"}
    }
    if "sales_trips" in covered_travel_freight_adders:
        covered_travel_freight_adders.add("inspection")
    if "truck_expense" in covered_travel_freight_adders:
        covered_travel_freight_adders.add("travel")
    adders = [
        row
        for row in workbench.get("adders") or []
        if row.get("include")
        and not (_is_insulation_scope(scope) and insulation_material_decision_rows)
        and str(row.get("adder_key") or row.get("template_bucket") or "").lower()
        not in (covered_equipment_adders | covered_travel_freight_adders)
    ]
    travel_rows = []
    adders_review_rows = []
    for row in adders:
        payload = {
            "item": row.get("adder"),
            "category": row.get("adder_key"),
            "estimated_cost": safe_number(row.get("estimated_cost"), 0.0),
            "notes": row.get("notes"),
        }
        if row.get("adder_key") == "travel":
            travel_rows.append({"travel_vehicle_cost": payload["estimated_cost"], "travel_notes": payload.get("notes")})
        else:
            adders_review_rows.append(payload)
    return {
        "template_type": "insulation" if _is_insulation_scope(scope) else "roofing",
        "header": {
            "C2_job_name": first_nonblank(scope.get("job_name"), "Estimating Assistant Draft"),
            "C3_job_type": scope.get("project_type"),
            "C4_site_address": first_nonblank(scope.get("site_address"), scope.get("address")),
            "C5_city_state_zip": scope.get("city_state_zip"),
            "C12_estimated_sqft": _estimate_area(scope),
            "gross_area_sqft": safe_number(scope.get("gross_sqft"), 0.0),
            "deduction_area_sqft": safe_number(scope.get("deduction_sqft"), 0.0),
            "net_area_sqft": _estimate_area(scope),
            "dimension_notes": [],
        },
        "material_rows": material_rows,
        "labor_rows": labor_rows,
        "travel_rows": travel_rows,
        "adders_review_rows": adders_review_rows,
    }


ROOFING_MATERIAL_TOTAL_DECISION_SECTIONS = (
    "roofing_foam_template_decisions",
    "roofing_coating_template_decisions",
    "roofing_primer_template_decisions",
    "roofing_detail_template_decisions",
    "roofing_detail_quantity_template_decisions",
    "roofing_board_fastener_template_decisions",
    "roofing_granules_template_decisions",
    "roofing_accessory_template_decisions",
)

ROOFING_ADDER_TOTAL_DECISION_SECTIONS = (
    "roofing_equipment_template_decisions",
    "roofing_travel_freight_template_decisions",
)

ROOFING_LABOR_TOTAL_DECISION_SECTIONS = ("roofing_labor_template_decisions",)

INSULATION_MATERIAL_TOTAL_DECISION_SECTIONS = (
    "insulation_performance_specs",
    "insulation_foam_template_decisions",
    "insulation_detail_material_template_decisions",
    "insulation_thermal_barrier_template_decisions",
    "insulation_support_material_template_decisions",
    "insulation_compliance_template_decisions",
    "insulation_pricing_template_decisions",
)

INSULATION_ADDER_TOTAL_DECISION_SECTIONS = ("insulation_equipment_logistics_template_decisions",)

INSULATION_LABOR_TOTAL_DECISION_SECTIONS = ("insulation_labor_template_decisions",)


def _decision_total_rows(workbench: dict[str, Any], section_names: Iterable[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for section_name in section_names:
        for row in workbench.get(section_name) or []:
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _included_cost_total(rows: Iterable[dict[str, Any]]) -> float:
    return sum(safe_number(row.get("estimated_cost"), 0.0) for row in rows if row.get("include"))


def _coverage_key_values(row: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for key in ("template_bucket", "package_key", "category", "adder_key", "labor_package", "task"):
        value = str(row.get(key) or "").strip().lower()
        if value:
            values.add(value)
    return values


def _coverage_row_values(row: dict[str, Any]) -> set[str]:
    value = str(row.get("workbook_row") or "").strip().lower()
    if not value:
        return set()
    return {part.strip() for part in re.split(r"[,/]|\\band\\b", value) if part.strip()}


def _coverage_from_decision_rows(rows: Iterable[dict[str, Any]], *, extra_keys: Iterable[str] | None = None) -> tuple[set[str], set[str]]:
    keys = {str(value).strip().lower() for value in (extra_keys or []) if str(value).strip()}
    workbook_rows: set[str] = set()
    for row in rows:
        keys.update(_coverage_key_values(row))
        workbook_rows.update(_coverage_row_values(row))
    return keys, workbook_rows


def _flat_row_is_covered(row: dict[str, Any], covered_keys: set[str], covered_workbook_rows: set[str]) -> bool:
    if _coverage_key_values(row) & covered_keys:
        return True
    if _coverage_row_values(row) & covered_workbook_rows:
        return True
    return False


def _flat_fallback_total(rows: Iterable[dict[str, Any]], covered_keys: set[str], covered_workbook_rows: set[str]) -> float:
    return sum(
        safe_number(row.get("estimated_cost"), 0.0)
        for row in rows
        if row.get("include") and not _flat_row_is_covered(row, covered_keys, covered_workbook_rows)
    )


def _insulation_material_total_rows(workbench: dict[str, Any]) -> tuple[list[dict[str, Any]], set[str], set[str]]:
    performance_rows = [row for row in workbench.get("insulation_performance_specs") or [] if isinstance(row, dict)]
    if any(row.get("include") and safe_number(row.get("estimated_cost"), 0.0) > 0 for row in performance_rows):
        decision_rows = performance_rows + _decision_total_rows(
            workbench,
            tuple(section for section in INSULATION_MATERIAL_TOTAL_DECISION_SECTIONS if section != "insulation_performance_specs"),
        )
        covered_keys, covered_rows = _coverage_from_decision_rows(decision_rows, extra_keys={"foam"})
        return decision_rows, covered_keys, covered_rows
    decision_rows = _decision_total_rows(workbench, tuple(section for section in INSULATION_MATERIAL_TOTAL_DECISION_SECTIONS if section != "insulation_performance_specs"))
    covered_keys, covered_rows = _coverage_from_decision_rows(decision_rows, extra_keys={"foam"} if decision_rows else set())
    return decision_rows, covered_keys, covered_rows


def summarize_workbench_totals(workbench: dict[str, Any]) -> dict[str, float]:
    workbench = recalculate_workbench_tables(workbench)
    if _is_insulation_scope(workbench.get("scope") or {}):
        material_decision_rows, material_covered_keys, material_covered_workbook_rows = _insulation_material_total_rows(workbench)
        labor_decision_rows = _decision_total_rows(workbench, INSULATION_LABOR_TOTAL_DECISION_SECTIONS)
        labor_covered_keys, labor_covered_workbook_rows = _coverage_from_decision_rows(labor_decision_rows)
        adder_decision_rows = _decision_total_rows(workbench, INSULATION_ADDER_TOTAL_DECISION_SECTIONS)
        adder_covered_keys, adder_covered_workbook_rows = _coverage_from_decision_rows(adder_decision_rows)
    else:
        material_decision_rows = _decision_total_rows(workbench, ROOFING_MATERIAL_TOTAL_DECISION_SECTIONS)
        material_covered_keys, material_covered_workbook_rows = _coverage_from_decision_rows(material_decision_rows)
        labor_decision_rows = _decision_total_rows(workbench, ROOFING_LABOR_TOTAL_DECISION_SECTIONS)
        labor_covered_keys, labor_covered_workbook_rows = _coverage_from_decision_rows(labor_decision_rows)
        adder_decision_rows = _decision_total_rows(workbench, ROOFING_ADDER_TOTAL_DECISION_SECTIONS)
        adder_covered_keys, adder_covered_workbook_rows = _coverage_from_decision_rows(adder_decision_rows)

    material_total = _included_cost_total(material_decision_rows) + _flat_fallback_total(
        workbench.get("materials") or [],
        material_covered_keys,
        material_covered_workbook_rows,
    )
    labor_total = _included_cost_total(labor_decision_rows) + _flat_fallback_total(
        workbench.get("labor") or [],
        labor_covered_keys,
        labor_covered_workbook_rows,
    )
    adder_total = _included_cost_total(adder_decision_rows) + _flat_fallback_total(
        workbench.get("adders") or [],
        adder_covered_keys,
        adder_covered_workbook_rows,
    )
    return {
        "material_total": round(material_total, 2),
        "labor_total": round(labor_total, 2),
        "adder_total": round(adder_total, 2),
        "draft_total": round(material_total + labor_total + adder_total, 2),
    }


def build_edit_history_rows(
    original_workbench: dict[str, Any],
    edited_workbench: dict[str, Any],
    *,
    estimator: str = "",
    reason_map: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    reason_map = reason_map or {}
    timestamp = datetime.now(UTC).isoformat()
    estimate_id = first_nonblank(edited_workbench.get("estimate_id"), original_workbench.get("estimate_id"), "")
    rows: list[dict[str, Any]] = []

    def add_row(section: str, field: str, default: Any, final: Any, threshold: float | None = None, *, require_when_changed: bool = False) -> None:
        default_number = optional_number(default)
        final_number = optional_number(final)
        difference = None
        percent_difference = None
        reason_required = False
        if default_number is not None and final_number is not None:
            difference = final_number - default_number
            if require_when_changed and default != final:
                reason_required = True
            if abs(default_number) > 0:
                percent_difference = difference / default_number
                if threshold is not None and abs(percent_difference) > threshold:
                    reason_required = True
        elif default != final:
            difference = str(final)
            reason_required = require_when_changed
        rows.append(
            {
                "estimate_id": estimate_id,
                "timestamp": timestamp,
                "estimator": estimator,
                "section": section,
                "field": field,
                "field_name": field,
                "package_or_labor_task": section.split(".", 1)[1] if "." in section else "",
                "historical_default": default,
                "suggested_value": default,
                "final_value": final,
                "difference": difference,
                "percent_difference": percent_difference,
                "difference_pct": percent_difference,
                "reason_required": reason_required,
                "reason": reason_map.get(f"{section}.{field}", ""),
            }
        )

    for key, default in (original_workbench.get("scope") or {}).items():
        add_row("scope", key, default, (edited_workbench.get("scope") or {}).get(key))
    original_materials = {row.get("package_key"): row for row in original_workbench.get("materials") or []}
    for row in edited_workbench.get("materials") or []:
        package = row.get("package_key")
        original = original_materials.get(package, {})
        add_row(f"materials.{package}", "include", original.get("include"), row.get("include"), require_when_changed=True)
        add_row(f"materials.{package}", "editable_qty_per_sqft", original.get("editable_qty_per_sqft"), row.get("editable_qty_per_sqft"), 0.5)
    original_labor = {row.get("package_key"): row for row in original_workbench.get("labor") or []}
    for row in edited_workbench.get("labor") or []:
        package = row.get("package_key")
        original = original_labor.get(package, {})
        add_row(f"labor.{package}", "include", original.get("include"), row.get("include"), require_when_changed=True)
        add_row(f"labor.{package}", "editable_hours_per_1000_sqft", original.get("editable_hours_per_1000_sqft"), row.get("editable_hours_per_1000_sqft"), 0.3)
    original_adders = {row.get("adder_key"): row for row in original_workbench.get("adders") or []}
    for row in edited_workbench.get("adders") or []:
        adder = row.get("adder_key")
        original = original_adders.get(adder, {})
        add_row(f"adders.{adder}", "include", original.get("include"), row.get("include"), require_when_changed=True)
        add_row(f"adders.{adder}", "editable_value", original.get("editable_value"), row.get("editable_value"), 0.5)
    return rows


def append_edit_history(rows: list[dict[str, Any]], output_dir: Path | str = "output/estimator_feedback") -> Path:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "estimator_edit_history.csv"
    columns = [
        "estimate_id",
        "timestamp",
        "estimator",
        "section",
        "field",
        "field_name",
        "package_or_labor_task",
        "historical_default",
        "suggested_value",
        "final_value",
        "difference",
        "percent_difference",
        "difference_pct",
        "reason_required",
        "reason",
    ]
    write_header = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        if write_header:
            writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column) for column in columns})
    return path
