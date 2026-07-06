#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jobscan.estimator.data_loader import load_estimator_data
from jobscan.products.template_product_mapping import (
    collect_product_mapping_audit,
    proposed_product_aliases,
    proposed_template_product_links,
    write_product_mapping_audit,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit template/pricing/history product names against product knowledge.")
    parser.add_argument("--out-dir", default="output/product_mapping_audit", help="Directory for audit CSV outputs.")
    parser.add_argument("--database-url", default=None, help="Optional database URL. Defaults to environment configuration.")
    parser.add_argument("--prefer-database", action="store_true", help="Require database-backed estimator data.")
    parser.add_argument("--min-score", type=float, default=0.55, help="Minimum product match score for mapped rows.")
    args = parser.parse_args()

    data = load_estimator_data(database_url=args.database_url, prefer_database=args.prefer_database)
    paths = write_product_mapping_audit(data, Path(args.out_dir), min_score=args.min_score)
    audit = collect_product_mapping_audit(data, min_score=args.min_score)
    aliases = proposed_product_aliases(data, audit)
    links = proposed_template_product_links(audit)
    total = len(audit)
    matched = int(audit["mapping_status"].eq("matched").sum()) if total and "mapping_status" in audit else 0
    print(f"Product mapping audit rows: {total}")
    print(f"Matched rows: {matched}")
    print(f"Unmapped rows: {total - matched}")
    print(f"Alias candidates: {len(aliases)}")
    print(f"Template link candidates: {len(links)}")
    for label, path in paths.items():
        print(f"{label}: {path}")
    if data.warnings:
        print("Warnings:")
        for warning in data.warnings[:10]:
            print(f"- {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
