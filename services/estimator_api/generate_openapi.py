from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from urllib.parse import urlsplit

from .server import app


OUTPUT_PATH = Path(__file__).with_name("openapi.json")


def build_action_openapi(
    server_url: str = "https://estimator-api.example.com",
) -> dict:
    normalized_server_url = _validated_server_url(server_url)
    specification = copy.deepcopy(app.openapi())
    specification["servers"] = [
        {
            "url": normalized_server_url,
            "description": "Replace with the deployed estimator API host.",
        }
    ]
    specification["paths"].pop("/health", None)
    specification["paths"].pop("/v1/estimating/workbooks/{artifact_id}", None)
    request_schema = specification["components"]["schemas"].get(
        "EstimateContextRequest",
        {},
    )
    request_schema.get("properties", {}).pop("include_source_metadata", None)
    response_schema = specification["components"]["schemas"].get(
        "EstimateContextResponse",
        {},
    )
    response_schema.get("properties", {}).pop("source_metadata", None)
    _restrict_roof_measure_to_visual_trace(specification)
    for path_item in specification["paths"].values():
        for method, operation in path_item.items():
            if method.lower() in {"get", "post"}:
                operation["x-openai-isConsequential"] = (
                    operation.get("operationId")
                    in {
                        "generateEstimateWorkbook",
                        "generateEstimateWorkbookOptions",
                    }
                )
    return specification


def _restrict_roof_measure_to_visual_trace(specification: dict) -> None:
    """Expose only Assistant-drawn roof geometry in the Custom GPT contract."""
    schemas = specification["components"]["schemas"]
    source = schemas["RoofMeasureCalculationRequest"]
    properties = source["properties"]
    schemas["RoofMeasureAssistantCalculationRequest"] = {
        "additionalProperties": False,
        "description": (
            "Measure roof polygons visually traced by the Assistant on the "
            "full-size context image."
        ),
        "properties": {
            "context_id": copy.deepcopy(properties["context_id"]),
            "normalized_sections": copy.deepcopy(properties["normalized_sections"]),
            "pitch_rise_per_12": copy.deepcopy(properties["pitch_rise_per_12"]),
        },
        "required": ["context_id", "normalized_sections"],
        "title": "RoofMeasureAssistantCalculationRequest",
        "type": "object",
    }
    calculation = specification["paths"]["/v1/roof-measure/calculate"]["post"]
    calculation["requestBody"]["content"]["application/json"]["schema"] = {
        "$ref": "#/components/schemas/RoofMeasureAssistantCalculationRequest"
    }
    calculation["summary"] = "Measure an Assistant-traced roof boundary"
    calculation["description"] = (
        "Validates and measures normalized roof polygons visually traced on the "
        "attached context image. Returns a tight review overlay. The API calls no "
        "AI model and requires estimator verification."
    )
    specification["paths"].pop("/v1/roof-measure/segment", None)
    schemas.pop("RoofMeasureCalculationRequest", None)


def _validated_server_url(value: str) -> str:
    normalized = str(value or "").strip().rstrip("/")
    parsed = urlsplit(normalized)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("Action server URL must be a public HTTPS URL.")
    if parsed.path or parsed.query or parsed.fragment:
        raise ValueError("Action server URL must not include a path, query, or fragment.")
    return normalized


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate the Custom GPT/Copilot action OpenAPI contract.",
    )
    parser.add_argument(
        "--server-url",
        default="https://estimator-api.example.com",
        help="Public HTTPS origin used by the action.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=OUTPUT_PATH,
        help="OpenAPI JSON output path.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.output.write_text(
        json.dumps(
            build_action_openapi(args.server_url),
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
