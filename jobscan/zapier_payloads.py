from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_records(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".json":
        return json.loads(path.read_text(encoding="utf-8"))
    raise ValueError("Use the JSON job index output as the Zapier payload source.")


def build_digest(records: list[dict[str, Any]], limit: int = 10) -> str:
    rows = records[:limit]
    lines = ["# Roofing Ops Digest", ""]
    lines.append(f"Jobs indexed: {len(records)}")
    if not rows:
        lines.append("No job folders found.")
        return "\n".join(lines)

    lines.append("")
    for r in rows:
        amount = r.get("final_price") or r.get("invoice_amount") or "unknown"
        if isinstance(amount, (int, float)):
            amount = f"${amount:,.2f}"
        lines.append(f"- **{r.get('job_name') or r.get('folder_name')}** — {r.get('status')} — {amount}")
        bits = []
        if r.get("site_address"):
            bits.append(str(r["site_address"]))
        if r.get("invoice_number"):
            bits.append(f"Invoice {r['invoice_number']}")
        if r.get("photo_count") is not None:
            bits.append(f"{r['photo_count']} photos")
        if bits:
            lines.append(f"  - {' | '.join(bits)}")
        if r.get("warnings"):
            lines.append(f"  - Warning: {r['warnings']}")
    return "\n".join(lines)


def build_job_event_payload(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return compact records intended for a Zapier webhook or Teams message action."""
    payloads = []
    for r in records:
        payloads.append({
            "job_id": r.get("job_id"),
            "status": r.get("status"),
            "customer": r.get("customer"),
            "job_name": r.get("job_name"),
            "address": ", ".join(x for x in [r.get("site_address"), r.get("city"), r.get("state"), r.get("zip_code")] if x),
            "final_price": r.get("final_price"),
            "invoice_number": r.get("invoice_number"),
            "invoice_amount": r.get("invoice_amount"),
            "has_signed_contract": r.get("has_signed_contract"),
            "has_invoice": r.get("has_invoice"),
            "photo_count": r.get("photo_count"),
            "warnings": r.get("warnings"),
        })
    return payloads


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Zapier-friendly payloads from a job index JSON file.")
    parser.add_argument("json_index", type=Path)
    parser.add_argument("--digest", type=Path, default=Path("output/teams_digest.md"))
    parser.add_argument("--payload", type=Path, default=Path("output/zapier_payload.json"))
    args = parser.parse_args()

    records = load_records(args.json_index)
    args.digest.parent.mkdir(parents=True, exist_ok=True)
    args.payload.parent.mkdir(parents=True, exist_ok=True)
    args.digest.write_text(build_digest(records), encoding="utf-8")
    args.payload.write_text(json.dumps(build_job_event_payload(records), indent=2), encoding="utf-8")
    print(f"Digest: {args.digest}")
    print(f"Zapier payload: {args.payload}")


if __name__ == "__main__":
    main()
