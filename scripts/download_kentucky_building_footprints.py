#!/usr/bin/env python3
"""Download Kentucky's public ORNL building footprints as a resumable GeoJSON sequence.

The official ArcGIS layer is public but can be slow. This downloader pages it
in bounded requests, writes each feature once, and checkpoints after every
successful page so it can resume without re-fetching prior pages.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import requests


LAYER_URL = (
    "https://kygisserver.ky.gov/arcgis/rest/services/"
    "WGS84WM_Services/Ky_ORNL_Building_Footprints_WGS84WM/MapServer/0"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("output/reference/kentucky_ornl_building_footprints.geojsonl"),
        help="GeoJSON sequence output path.",
    )
    parser.add_argument("--page-size", type=int, default=1000, help="Features requested per ArcGIS page (maximum 1000).")
    parser.add_argument("--limit", type=int, default=0, help="Stop after this many features; 0 downloads the whole layer.")
    parser.add_argument("--resume", action="store_true", help="Resume from the checkpoint beside --out.")
    parser.add_argument("--finalize", type=Path, help="Write an RFC 7946 FeatureCollection from the GeoJSON sequence after download.")
    parser.add_argument("--timeout", type=float, default=45, help="Per-request timeout in seconds.")
    parser.add_argument("--dry-run", action="store_true", help="Print the source and requested plan without downloading.")
    return parser.parse_args()


def request_json(session: requests.Session, url: str, *, params: dict[str, Any], timeout: float) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            response = session.get(url, params=params, timeout=timeout)
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, dict) and payload.get("error"):
                raise RuntimeError(str(payload["error"]))
            if not isinstance(payload, dict):
                raise RuntimeError("ArcGIS response was not a JSON object.")
            return payload
        except (requests.RequestException, RuntimeError, ValueError) as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(2**attempt)
    raise RuntimeError(f"ArcGIS request failed after 3 attempts: {last_error}")


def checkpoint_path(output_path: Path) -> Path:
    return output_path.with_suffix(output_path.suffix + ".state.json")


def load_offset(output_path: Path, *, resume: bool) -> int:
    state_path = checkpoint_path(output_path)
    if not resume:
        return 0
    if not output_path.exists() or not state_path.exists():
        raise RuntimeError(f"Cannot resume: expected {output_path} and {state_path}.")
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    return max(0, int(payload.get("next_offset") or 0))


def save_offset(output_path: Path, *, next_offset: int) -> None:
    checkpoint_path(output_path).write_text(
        json.dumps({"source": LAYER_URL, "next_offset": next_offset}, indent=2) + "\n",
        encoding="utf-8",
    )


def finalize_geojson(sequence_path: Path, output_path: Path) -> None:
    with sequence_path.open("r", encoding="utf-8") as source, output_path.open("w", encoding="utf-8") as target:
        target.write('{"type":"FeatureCollection","features":[\n')
        first = True
        for line in source:
            feature = line.strip()
            if not feature:
                continue
            if not first:
                target.write(",\n")
            target.write(feature)
            first = False
        target.write("\n]}\n")


def main() -> int:
    args = parse_args()
    page_size = max(1, min(int(args.page_size), 1000))
    if args.dry_run:
        print(f"source={LAYER_URL}")
        print(f"output={args.out}")
        print(f"page_size={page_size}")
        print(f"resume={args.resume}")
        print(f"limit={args.limit}")
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    offset = load_offset(args.out, resume=args.resume)
    mode = "a" if args.resume else "w"
    session = requests.Session()
    session.headers["User-Agent"] = "SprayTecBuildingFootprintCache/1.0"
    metadata = request_json(session, LAYER_URL, params={"f": "json"}, timeout=args.timeout)
    max_record_count = int(metadata.get("maxRecordCount") or page_size)
    page_size = min(page_size, max_record_count)
    count_payload = request_json(
        session,
        f"{LAYER_URL}/query",
        params={"f": "json", "where": "1=1", "returnCountOnly": "true"},
        timeout=args.timeout,
    )
    available = int(count_payload.get("count") or 0)
    target = min(available, args.limit) if args.limit > 0 else available
    print(f"available={available} target={target} starting_offset={offset} page_size={page_size}")

    written = offset
    with args.out.open(mode, encoding="utf-8") as output:
        while written < target:
            records = min(page_size, target - written)
            page = request_json(
                session,
                f"{LAYER_URL}/query",
                params={
                    "f": "geojson",
                    "where": "1=1",
                    "outFields": "OBJECTID",
                    "returnGeometry": "true",
                    "resultOffset": written,
                    "resultRecordCount": records,
                    "orderByFields": "OBJECTID ASC",
                },
                timeout=args.timeout,
            )
            features = page.get("features") or []
            if not features:
                break
            for feature in features:
                output.write(json.dumps(feature, separators=(",", ":")) + "\n")
            output.flush()
            written += len(features)
            save_offset(args.out, next_offset=written)
            print(f"written={written}/{target}")
            if len(features) < records:
                break

    if args.finalize:
        finalize_geojson(args.out, args.finalize)
        print(f"finalized={args.finalize}")
    print(f"complete={written} output={args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
