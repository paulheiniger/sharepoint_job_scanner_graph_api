from __future__ import annotations

import argparse
from pathlib import Path

from .product_catalog import export_product_catalog_xlsx, load_product_catalog_json


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export generic product knowledge JSON to a review workbook.")
    parser.add_argument("--catalog", default="output/product_catalog.json", help="Input product catalog JSON.")
    parser.add_argument("--out", required=True, help="Output XLSX path.")
    args = parser.parse_args(argv)
    knowledge = load_product_catalog_json(args.catalog)
    path = export_product_catalog_xlsx(knowledge, Path(args.out))
    print(f"Wrote product catalog workbook: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
