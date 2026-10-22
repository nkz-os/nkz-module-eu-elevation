#!/usr/bin/env python3
"""
Bootstrap script: Pre-ingest Copernicus GLO-30 DEM for pan-European coverage.

Generates Cesium Quantized Mesh terrain tiles and uploads them to MinIO so the
built-in "europe_copernicus" provider works for all tenants without API keys.

Usage:
  # From within the elevation-worker container:
  python3 /app/scripts/ingest_copernicus_eu.py

  # Or with custom BBOX / zoom range:
  python3 /app/scripts/ingest_copernicus_eu.py --bbox "-10,35,30,62" --min-zoom 7 --max-zoom 12

Requirements:
  - elevation-worker pod running with GDAL 3.x + rasterio + pydelatin + quantized-mesh-encoder
  - MinIO bucket 'terrain-tilesets' must exist
  - Redis must be reachable for Celery task queue

The script enumerates Copernicus DEM 1°×1° COG tiles from the public AWS S3 bucket,
splits them into manageable batches, and submits Celery tasks for processing.
Each task processes one tile and uploads the resulting quantized mesh to MinIO.

Estimated time for full EU coverage (3465 tiles): 4–12 hours depending on resources.
"""

import argparse
import math
import os
import sys
import time

# Ensure the app package is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Copernicus DEM tile grid: 1°×1° COG files
# Naming: Copernicus_DSM_COG_10_N{lat_abs_2dp}_00_E{lon_abs_3dp}_00_DEM/
#         Copernicus_DSM_COG_10_N{lat_abs_2dp}_00_E{lon_abs_3dp}_00_DEM.tif
S3_PREFIX = "/vsis3/copernicus-dem-30m"

# EU/UK bounding box (EPSG:4326)
DEFAULT_BBOX = (-32.0, 27.0, 45.0, 72.0)

# Copernicus DEM COG tile naming helper
def tile_s3_path(lat: int, lon: int) -> str:
    """Build the /vsis3/ path for a Copernicus DEM COG tile."""
    lat_abs = abs(lat)
    lon_abs = abs(lon)
    lat_dir = "N" if lat >= 0 else "S"
    lon_dir = "E" if lon >= 0 else "W"
    tile_name = f"Copernicus_DSM_COG_10_{lat_dir}{lat_abs:02d}_00_{lon_dir}{lon_abs:03d}_00_DEM"
    return f"{S3_PREFIX}/{tile_name}/{tile_name}.tif"


def enumerate_tiles(bbox: tuple[float, float, float, float]) -> list[str]:
    """Return all Copernicus tile paths covering the given BBOX."""
    west, south, east, north = bbox
    lat_start = int(math.floor(south))
    lat_end = int(math.floor(north))
    lon_start = int(math.floor(west))
    lon_end = int(math.floor(east))

    tiles = []
    for lat in range(lat_start, lat_end + 1):
        for lon in range(lon_start, lon_end + 1):
            tiles.append(tile_s3_path(lat, lon))
    return tiles


def submit_batch(tiles: list[str], bbox: tuple, zoom_range: tuple[int, int],
                 max_error: float, batch_size: int = 50):
    """Submit Celery tasks for a batch of tiles."""
    os.environ.setdefault("AWS_NO_SIGN_REQUEST", "YES")
    os.environ.setdefault("AWS_S3_ENDPOINT", "s3.eu-central-1.amazonaws.com")
    os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
    os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif")

    from app.tasks.elevation_tasks import process_dem_to_quantized_mesh

    total = len(tiles)
    for i in range(0, total, batch_size):
        batch = tiles[i : i + batch_size]
        print(f"\nBatch {i // batch_size + 1}/{(total + batch_size - 1) // batch_size}: "
              f"{len(batch)} tiles ({i + 1}–{min(i + batch_size, total)} of {total})")

        result = process_dem_to_quantized_mesh.delay(
            "EU",
            batch,
            bbox,
            zoom_range[0],
            zoom_range[1],
            max_error,
        )
        print(f"  Task ID: {result.id}")
        time.sleep(0.5)  # Brief pause between batches to avoid overwhelming Redis


def main():
    parser = argparse.ArgumentParser(
        description="Bootstrap Copernicus GLO-30 ingestion for pan-European terrain"
    )
    parser.add_argument("--bbox", type=str, default=None,
                        help="BBOX as 'west,south,east,north' (default: EU+UK)")
    parser.add_argument("--min-zoom", type=int, default=8,
                        help="Minimum zoom level (default: 8)")
    parser.add_argument("--max-zoom", type=int, default=12,
                        help="Maximum zoom level (default: 12, capped for 30m resolution)")
    parser.add_argument("--max-error", type=float, default=0.5,
                        help="pydelatin max error for mesh decimation (default: 0.5)")
    parser.add_argument("--batch-size", type=int, default=50,
                        help="Tiles per Celery task (default: 50)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Just enumerate tiles without submitting tasks")
    args = parser.parse_args()

    bbox = DEFAULT_BBOX
    if args.bbox:
        parts = [float(x) for x in args.bbox.split(",")]
        if len(parts) != 4:
            print("ERROR: --bbox must be 'west,south,east,north'", file=sys.stderr)
            sys.exit(1)
        bbox = tuple(parts)

    print(f"Copernicus GLO-30 EU Ingestion Bootstrap")
    print(f"  BBOX: {bbox}")
    print(f"  Zoom range: {args.min_zoom}–{args.max_zoom}")
    print(f"  Max error: {args.max_error}")
    print(f"  Batch size: {args.batch_size}")
    print()

    tiles = enumerate_tiles(bbox)
    print(f"Total tiles covering BBOX: {len(tiles)}")

    if args.dry_run:
        print("  (dry run — no tasks submitted)")
        print("  Sample tiles:")
        for t in tiles[:10]:
            print(f"    {t}")
        return

    confirm = input(f"\nSubmit {len(tiles)} tiles in batches of {args.batch_size}? [y/N] ")
    if confirm.lower() != "y":
        print("Aborted.")
        return

    submit_batch(tiles, bbox, (args.min_zoom, args.max_zoom), args.max_error,
                 batch_size=args.batch_size)
    print(f"\nDone. {len(tiles)} tiles submitted to Celery queue.")
    print("Monitor progress via: /api/elevation/ws/status/<job_id>")
    print("Once all tiles are processed, the europe_copernicus provider will serve them automatically.")


if __name__ == "__main__":
    main()
