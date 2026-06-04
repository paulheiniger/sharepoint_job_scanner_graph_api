from __future__ import annotations

import argparse
from pathlib import Path

from .scan import scan_root, write_csv, write_excel, write_json


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan exported SharePoint job folders and build a dashboard-ready job index.")
    parser.add_argument("root", type=Path, help="Root folder containing exported SharePoint job folders")
    parser.add_argument("--out", type=Path, default=Path("output/job_index.csv"), help="CSV output path")
    parser.add_argument("--json", type=Path, default=None, help="Optional JSON output path")
    parser.add_argument("--xlsx", type=Path, default=None, help="Optional Excel output path")
    args = parser.parse_args()

    records = scan_root(args.root)
    write_csv(records, args.out)
    if args.json:
        write_json(records, args.json)
    if args.xlsx:
        write_excel(records, args.xlsx)

    print(f"Scanned {len(records)} job folder(s)")
    print(f"CSV: {args.out}")
    if args.json:
        print(f"JSON: {args.json}")
    if args.xlsx:
        print(f"Excel: {args.xlsx}")


if __name__ == "__main__":
    main()
