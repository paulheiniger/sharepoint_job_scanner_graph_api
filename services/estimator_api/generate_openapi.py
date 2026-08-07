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
    for schema_name in (
        "EstimateWorkbookValidationRequest",
        "BidScopePrepareAttachmentContextRequest",
    ):
        request_schema = specification["components"]["schemas"].get(schema_name, {})
        file_refs = request_schema.get("properties", {}).get("openaiFileIdRefs")
        if isinstance(file_refs, dict):
            # ChatGPT injects runtime file-reference objects, but GPT Actions only
            # enables the attachment handoff when this parameter is declared as an
            # array of strings in the imported OpenAPI contract.
            file_refs["items"] = {"type": "string"}
    for path_item in specification["paths"].values():
        for method, operation in path_item.items():
            if method.lower() in {"get", "post"}:
                operation["x-openai-isConsequential"] = (
                    operation.get("operationId")
                    in {
                        "generateEstimateWorkbook",
                        "generateEstimateWorkbookOptions",
                        "validateEstimateWorkbook",
                    }
                )
    return specification


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
