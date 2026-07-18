#!/usr/bin/env python3
"""Import downloaded building-footprint GeoJSONL into operational PostgreSQL.

The importer is idempotent by source feature ID, commits bounded batches, and
stores a database checkpoint after each committed batch for safe resume.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Iterator

import psycopg2
from psycopg2.extras import execute_values

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from jobscan.env import load_project_env


DEFAULT_INPUT = REPO_ROOT / "output/reference/kentucky_ornl_building_footprints.geojsonl"
SCHEMA_PATH = REPO_ROOT / "db/add_building_footprint_tables.sql"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="GeoJSONL source file.")
    parser.add_argument("--source", default="ky_ornl", help="Stable source identifier for idempotent upserts.")
    parser.add_argument("--state", default="KY", help="Two-letter source state code.")
    parser.add_argument("--database-url", default="", help="PostgreSQL URL; defaults to configured environment.")
    parser.add_argument("--batch-size", type=int, default=1000, help="Rows per transaction.")
    parser.add_argument("--progress-every", type=int, default=10000, help="Print progress every N committed rows.")
    parser.add_argument("--limit", type=int, default=0, help="Stop after considering this many source lines; 0 imports all.")
    parser.add_argument("--resume", action="store_true", help="Resume from the committed database checkpoint for this source file.")
    parser.add_argument("--dry-run", action="store_true", help="Parse and validate without database writes.")
    return parser.parse_args()


def database_url(explicit: str) -> str:
    load_project_env()
    value = explicit or os.getenv("DATABASE_URL") or os.getenv("NEON_DATABASE_URL") or os.getenv("NEON_PSQL_URL") or ""
    value = value.strip().replace("postgresql+psycopg2://", "postgresql://", 1)
    if not value:
        raise RuntimeError("Set --database-url, DATABASE_URL, NEON_DATABASE_URL, or NEON_PSQL_URL.")
    return value


def ensure_schema(connection: psycopg2.extensions.connection) -> None:
    with connection.cursor() as cursor:
        cursor.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
    connection.commit()


def checkpoint(connection: psycopg2.extensions.connection, *, source: str, source_file: str) -> tuple[int, int, int]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT last_line_number, imported_records, skipped_records
            FROM building_footprint_import_state
            WHERE source = %s AND source_file = %s
            """,
            (source, source_file),
        )
        row = cursor.fetchone()
    return (int(row[0]), int(row[1]), int(row[2])) if row else (0, 0, 0)


def save_checkpoint(
    cursor: psycopg2.extensions.cursor,
    *,
    source: str,
    source_file: str,
    last_line_number: int,
    imported_records: int,
    skipped_records: int,
) -> None:
    cursor.execute(
        """
        INSERT INTO building_footprint_import_state (
            source, source_file, last_line_number, imported_records, skipped_records, updated_at
        ) VALUES (%s, %s, %s, %s, %s, NOW())
        ON CONFLICT (source, source_file) DO UPDATE SET
            last_line_number = EXCLUDED.last_line_number,
            imported_records = EXCLUDED.imported_records,
            skipped_records = EXCLUDED.skipped_records,
            updated_at = NOW()
        """,
        (source, source_file, last_line_number, imported_records, skipped_records),
    )


def coordinates_from_geometry(geometry: dict[str, Any]) -> Iterator[tuple[float, float]]:
    geometry_type = str(geometry.get("type") or "")
    coordinates = geometry.get("coordinates")
    if geometry_type == "Polygon":
        polygons = [coordinates]
    elif geometry_type == "MultiPolygon":
        polygons = coordinates
    else:
        return
    if not isinstance(polygons, list):
        return
    for polygon in polygons:
        if not isinstance(polygon, list):
            continue
        for ring in polygon:
            if not isinstance(ring, list):
                continue
            for point in ring:
                if not isinstance(point, (list, tuple)) or len(point) < 2:
                    continue
                try:
                    yield float(point[0]), float(point[1])
                except (TypeError, ValueError):
                    continue


def import_record(feature: dict[str, Any], *, source: str, state_code: str, source_file: str) -> tuple[Any, ...] | None:
    geometry = feature.get("geometry")
    if not isinstance(geometry, dict) or geometry.get("type") not in {"Polygon", "MultiPolygon"}:
        return None
    coordinates = list(coordinates_from_geometry(geometry))
    if len(coordinates) < 3:
        return None
    properties = feature.get("properties") if isinstance(feature.get("properties"), dict) else {}
    source_id = feature.get("id") or properties.get("OBJECTID") or properties.get("objectid")
    if source_id is None:
        source_id = hashlib.sha256(json.dumps(geometry, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    longitudes, latitudes = zip(*coordinates)
    return (
        source,
        str(source_id),
        state_code,
        json.dumps(geometry, separators=(",", ":")),
        str(geometry["type"]),
        min(longitudes),
        min(latitudes),
        max(longitudes),
        max(latitudes),
        json.dumps(properties, separators=(",", ":")),
        source_file,
    )


UPSERT_SQL = """
INSERT INTO building_footprints (
    source, source_feature_id, state_code, geometry_geojson, geometry_type,
    min_longitude, min_latitude, max_longitude, max_latitude, source_properties, source_file
) VALUES %s
ON CONFLICT (source, source_feature_id) DO UPDATE SET
    state_code = EXCLUDED.state_code,
    geometry_geojson = EXCLUDED.geometry_geojson,
    geometry_type = EXCLUDED.geometry_type,
    min_longitude = EXCLUDED.min_longitude,
    min_latitude = EXCLUDED.min_latitude,
    max_longitude = EXCLUDED.max_longitude,
    max_latitude = EXCLUDED.max_latitude,
    source_properties = EXCLUDED.source_properties,
    source_file = EXCLUDED.source_file,
    imported_at = NOW()
"""


def flush_batch(
    connection: psycopg2.extensions.connection,
    rows: list[tuple[Any, ...]],
    *,
    source: str,
    source_file: str,
    line_number: int,
    imported_records: int,
    skipped_records: int,
) -> None:
    with connection.cursor() as cursor:
        execute_values(cursor, UPSERT_SQL, rows, page_size=len(rows))
        save_checkpoint(
            cursor,
            source=source,
            source_file=source_file,
            last_line_number=line_number,
            imported_records=imported_records,
            skipped_records=skipped_records,
        )
    connection.commit()


def main() -> int:
    args = parse_args()
    input_path = args.input.resolve()
    if not input_path.is_file():
        raise RuntimeError(f"Input file does not exist: {input_path}")
    state_code = args.state.strip().upper()
    if len(state_code) != 2:
        raise RuntimeError("--state must be a two-letter code.")
    source_file = str(input_path)
    batch_size = max(1, min(int(args.batch_size), 5000))

    if args.dry_run:
        valid = skipped = 0
        with input_path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if args.limit and line_number > args.limit:
                    break
                try:
                    record = import_record(json.loads(line), source=args.source, state_code=state_code, source_file=source_file)
                except json.JSONDecodeError:
                    record = None
                if record is None:
                    skipped += 1
                else:
                    valid += 1
        print(f"dry_run valid={valid} skipped={skipped} input={input_path}")
        return 0

    with psycopg2.connect(database_url(args.database_url)) as connection:
        ensure_schema(connection)
        start_line, imported_records, skipped_records = checkpoint(connection, source=args.source, source_file=source_file)
        if start_line and not args.resume:
            raise RuntimeError("Existing import checkpoint found. Use --resume or choose a distinct --source.")
        if not args.resume:
            start_line = imported_records = skipped_records = 0
        print(f"starting_line={start_line} imported={imported_records} skipped={skipped_records} batch_size={batch_size}")
        batch: list[tuple[Any, ...]] = []
        line_number = start_line
        with input_path.open("r", encoding="utf-8") as source:
            for current_line, line in enumerate(source, start=1):
                if current_line <= start_line:
                    continue
                if args.limit and current_line > args.limit:
                    break
                line_number = current_line
                try:
                    record = import_record(json.loads(line), source=args.source, state_code=state_code, source_file=source_file)
                except json.JSONDecodeError:
                    record = None
                if record is None:
                    skipped_records += 1
                else:
                    batch.append(record)
                    imported_records += 1
                if len(batch) >= batch_size:
                    flush_batch(
                        connection,
                        batch,
                        source=args.source,
                        source_file=source_file,
                        line_number=line_number,
                        imported_records=imported_records,
                        skipped_records=skipped_records,
                    )
                    batch.clear()
                    if imported_records % max(1, args.progress_every) < batch_size:
                        print(f"line={line_number} imported={imported_records} skipped={skipped_records}")
            if batch:
                flush_batch(
                    connection,
                    batch,
                    source=args.source,
                    source_file=source_file,
                    line_number=line_number,
                    imported_records=imported_records,
                    skipped_records=skipped_records,
                )
        print(f"complete line={line_number} imported={imported_records} skipped={skipped_records}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
