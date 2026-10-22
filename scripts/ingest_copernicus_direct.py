#!/usr/bin/env python3
"""
Direct Copernicus GLO-30 ingestion — no Celery, no catalog round-trip.

Enumerates Copernicus DEM COG tiles from AWS S3, builds a VRT mosaic,
processes Cesium Quantized Mesh tiles at zooms 8–12, and uploads to MinIO.

Designed to run inside the elevation-worker pod:
  python3 /app/scripts/ingest_copernicus_direct.py

Processing is sequential (one tile at a time) to keep memory low.
Estimated time for full EU (~3000 tiles): 6–12 hours at 2 tiles/minute.
"""

import os
import sys
import io
import gzip
import json
import math
import time
import shutil
import tempfile
import argparse
import subprocess
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import from_bounds
import quantized_mesh_encoder
from pydelatin import Delatin
import boto3
from botocore.exceptions import ClientError as BotocoreClientError

# ── Configuration ─────────────────────────────────────────────────
S3_BUCKET = "copernicus-dem-30m"
S3_ENDPOINT = "s3.eu-central-1.amazonaws.com"
S3_PREFIX = f"/vsis3/{S3_BUCKET}"

MINIO_ENDPOINT = os.environ.get("MINIO_ENDPOINT", "minio-service:9000")
MINIO_ACCESS_KEY = os.environ.get("MINIO_ACCESS_KEY", "")
MINIO_SECRET_KEY = os.environ.get("MINIO_SECRET_KEY", "")
MINIO_BUCKET = os.environ.get("MINIO_BUCKET", "terrain-tilesets")

# GDAL S3 access for unauthenticated public bucket
# MUST overwrite (not setdefault) — worker env has AWS_S3_ENDPOINT=minio:9000 globally
os.environ["AWS_NO_SIGN_REQUEST"] = "YES"
os.environ["AWS_S3_ENDPOINT"] = S3_ENDPOINT
os.environ["GDAL_DISABLE_READDIR_ON_OPEN"] = "EMPTY_DIR"
os.environ["CPL_VSIL_CURL_ALLOWED_EXTENSIONS"] = ".tif"
os.environ["GDAL_CACHEMAX"] = "1024"

# Default pan-European BBOX
DEFAULT_BBOX = (-32.0, 27.0, 45.0, 72.0)
DEFAULT_ZOOM_MIN = 8
DEFAULT_ZOOM_MAX = 12  # Capped: 30m DEM doesn't benefit from higher zooms
DEFAULT_MAX_ERROR = 0.5


# ── Tile enumeration ──────────────────────────────────────────────

def tile_s3_path(lat: int, lon: int) -> str:
    """Build /vsis3/ path for a Copernicus DEM 1°×1° COG tile."""
    lat_dir = "N" if lat >= 0 else "S"
    lon_dir = "E" if lon >= 0 else "W"
    tile_name = (
        f"Copernicus_DSM_COG_10_{lat_dir}{abs(lat):02d}_00_"
        f"{lon_dir}{abs(lon):03d}_00_DEM"
    )
    return f"{S3_PREFIX}/{tile_name}/{tile_name}.tif"


def enumerate_tiles(bbox: tuple[float, ...]) -> list[str]:
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


# ── Cesium TMS tile math ──────────────────────────────────────────

def _num_tiles_at_zoom(zoom: int) -> tuple[int, int]:
    return (2 ** (zoom + 1), 2 ** zoom)


def _tiles_in_bbox(zoom: int, bbox: tuple[float, ...]) -> list[tuple[int, int]]:
    num_cols, num_rows = _num_tiles_at_zoom(zoom)
    tw = 360.0 / num_cols
    th = 180.0 / num_rows
    min_col = max(0, int(math.floor((bbox[0] + 180.0) / tw)))
    max_col = min(num_cols - 1, int(math.floor((bbox[2] + 180.0) / tw)))
    min_row = max(0, int(math.floor((bbox[1] + 90.0) / th)))
    max_row = min(num_rows - 1, int(math.floor((bbox[3] + 90.0) / th)))
    return [(c, r) for c in range(min_col, max_col + 1) for r in range(min_row, max_row + 1)]


def _tile_bounds(zoom: int, col: int, row: int) -> tuple[float, ...]:
    ncols, nrows = _num_tiles_at_zoom(zoom)
    tw = 360.0 / ncols
    th = 180.0 / nrows
    return (-180.0 + col * tw, -90.0 + row * th, -180.0 + (col + 1) * tw, -90.0 + (row + 1) * th)


# ── GDAL helpers ──────────────────────────────────────────────────

def _run_gdal(cmd: list[str], label: str = "") -> None:
    """Execute a GDAL command; raise on failure."""
    print(f"  [{label}] gdal {' '.join(cmd[:4])}...", flush=True)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"  GDAL ERROR: {proc.stderr[:500]}", flush=True)
        raise RuntimeError(f"GDAL command failed: {' '.join(cmd[:3])}")


def build_vrt(tiles: list[str], bbox: tuple[float, ...], work_dir: str) -> str:
    """Build a EPSG:4326 VRT from Copernicus S3 tiles covering the BBOX."""
    # Write tile list for gdalbuildvrt -input_file_list
    tile_list_path = os.path.join(work_dir, "tiles.txt")
    with open(tile_list_path, "w") as f:
        for t in tiles:
            f.write(t + "\n")

    vrt_path = os.path.join(work_dir, "mosaic.vrt")

    cmd = [
        "gdalbuildvrt",
        "-te", str(bbox[0]), str(bbox[1]), str(bbox[2]), str(bbox[3]),
        "-input_file_list", tile_list_path,
        "-overwrite",
        vrt_path,
    ]
    _run_gdal(cmd, "buildvrt")

    # Copernicus COG data is already EPSG:4326, but run gdalwarp to ensure consistency
    warped_path = os.path.join(work_dir, "mosaic_4326.vrt")
    cmd2 = [
        "gdalwarp",
        "-t_srs", "EPSG:4326",
        "-of", "VRT",
        "--config", "GDAL_CACHEMAX", "1024",
        "-multi",
        "-overwrite",
        vrt_path,
        warped_path,
    ]
    _run_gdal(cmd2, "warp")

    return warped_path


# ── Tile processing ───────────────────────────────────────────────

def process_tile(ds, zoom: int, col: int, row: int, max_error: float = 0.5):
    """Extract + decimate + encode one tile. Returns gzipped bytes or None."""
    bounds = _tile_bounds(zoom, col, row)
    try:
        window = from_bounds(bounds[0], bounds[1], bounds[2], bounds[3], ds.transform)
        window = window.intersection(
            rasterio.windows.Window(0, 0, ds.width, ds.height)
        )
        if window.width < 2 or window.height < 2:
            return None

        data = ds.read(1, window=window, out_shape=(min(int(window.height), 256), min(int(window.width), 256)))

        if ds.nodata is not None:
            data = np.where(data != ds.nodata, data, 0)
        data = np.nan_to_num(data, nan=0.0).astype(np.float32)

        if data.max() == data.min():
            return None  # Skip completely flat tiles (ocean, large plains)

        tin = Delatin(data, max_error=max_error)
        if len(tin.vertices) < 3 or len(tin.triangles) < 1:
            return None

        qm_buffer = io.BytesIO()
        quantized_mesh_encoder.encode(qm_buffer, tin.vertices, tin.triangles, bounds=bounds)
        qm = qm_buffer.getvalue()

        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
            gz.write(qm)
        return buf.getvalue()
    except Exception:
        return None


# ── S3 / MinIO ─────────────────────────────────────────────────────

_s3 = None


def get_s3():
    global _s3
    if _s3 is not None:
        return _s3
    _s3 = boto3.client(
        "s3",
        endpoint_url=f"http://{MINIO_ENDPOINT}",
        aws_access_key_id=MINIO_ACCESS_KEY,
        aws_secret_access_key=MINIO_SECRET_KEY,
        config=boto3.session.Config(signature_version="s3v4"),
        region_name="us-east-1",
    )
    return _s3


def ensure_bucket(s3):
    try:
        s3.head_bucket(Bucket=MINIO_BUCKET)
    except BotocoreClientError as e:
        if e.response.get("Error", {}).get("Code") in ("404", "NoSuchBucket"):
            s3.create_bucket(Bucket=MINIO_BUCKET)


# ── Main ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Direct Copernicus GLO-30 → Quantized Mesh ingestion")
    parser.add_argument("--bbox", type=str, default=None,
                        help="BBOX as 'west,south,east,north' (default: pan-European)")
    parser.add_argument("--min-zoom", type=int, default=DEFAULT_ZOOM_MIN)
    parser.add_argument("--max-zoom", type=int, default=DEFAULT_ZOOM_MAX)
    parser.add_argument("--max-error", type=float, default=DEFAULT_MAX_ERROR)
    parser.add_argument("--output-prefix", type=str, default="EU",
                        help="Storage prefix in MinIO (default: EU)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Enumerate tiles and estimate without processing")
    parser.add_argument("--resume-from", type=int, default=0,
                        help="Resume from tile index N (0-based)")
    args = parser.parse_args()

    bbox = DEFAULT_BBOX
    if args.bbox:
        parts = [float(x) for x in args.bbox.split(",")]
        if len(parts) != 4:
            print("ERROR: --bbox must be 'west,south,east,north'", file=sys.stderr)
            sys.exit(1)
        bbox = tuple(parts)

    tiles = enumerate_tiles(bbox)
    print(f"Copernicus GLO-30 Direct Ingestion")
    print(f"  BBOX: {bbox}")
    print(f"  S3 tiles covering BBOX: {len(tiles)}")
    print(f"  Zoom range: {args.min_zoom}–{args.max_zoom}")
    print(f"  Max error: {args.max_error}")
    print(f"  Output: terrain/{args.output_prefix}/")
    print()

    if args.dry_run:
        print("Sample tiles:")
        for t in tiles[:15]:
            print(f"  {t}")
        print("  ...")
        return

    # Phase 1: Build VRT from S3 tiles
    work_dir = tempfile.mkdtemp(prefix="copernicus_", dir="/tmp")
    print(f"Work dir: {work_dir}")
    print("Phase 1: Building VRT mosaic from S3 tiles...")
    try:
        vrt_path = build_vrt(tiles, bbox, work_dir)
    except RuntimeError:
        print("FATAL: VRT build failed. Check S3 connectivity and GDAL version.")
        sys.exit(1)

    # Phase 2: Enumerate Cesium tiles across all zooms
    all_cesium_tiles: dict[int, list[tuple[int, int]]] = {}
    total = 0
    for z in range(args.min_zoom, args.max_zoom + 1):
        ct = _tiles_in_bbox(z, bbox)
        all_cesium_tiles[z] = ct
        total += len(ct)
    print(f"Phase 2: Cesium tiles to generate: {total} across {args.min_zoom}-{args.max_zoom}")

    # Phase 3: Process tiles and upload
    print("Phase 3: Processing tiles...")
    s3 = get_s3()
    ensure_bucket(s3)
    base_key = f"terrain/{args.output_prefix}"

    available: dict[int, list[tuple[int, int]]] = {}
    processed = 0
    uploaded = 0
    skipped = 0
    t_start = time.time()

    with rasterio.open(vrt_path) as ds:
        for z in range(args.min_zoom, args.max_zoom + 1):
            available[z] = []
            cesium_tiles = all_cesium_tiles[z]
            z_start = time.time()

            for ci, (col, row) in enumerate(cesium_tiles):
                processed += 1
                if processed <= args.resume_from:
                    continue

                tile_data = process_tile(ds, z, col, row, max_error=args.max_error)
                if tile_data:
                    key = f"{base_key}/{z}/{col}/{row}.terrain"
                    s3.put_object(Bucket=MINIO_BUCKET, Key=key, Body=tile_data,
                                  ContentType="application/vnd.quantized-mesh")
                    available[z].append((col, row))
                    uploaded += 1
                else:
                    skipped += 1

                if processed % 50 == 0 or processed == total:
                    elapsed = time.time() - t_start
                    rate = processed / elapsed if elapsed > 0 else 0
                    eta = (total - processed) / rate if rate > 0 else 0
                    print(f"  [{processed}/{total}] z={z} tile {ci+1}/{len(cesium_tiles)} | "
                          f"{uploaded} uploaded, {skipped} skipped | "
                          f"{rate:.1f} tiles/s | ETA {eta/60:.0f}m",
                          flush=True)

            z_elapsed = time.time() - z_start
            print(f"  Zoom {z} done: {len(available[z])} tiles in {z_elapsed:.0f}s", flush=True)

    # Phase 4: Generate layer.json
    print("Phase 4: Generating layer.json...")
    available_formatted = []
    for z in range(args.min_zoom, args.max_zoom + 1):
        tiles_z = available.get(z, [])
        if not tiles_z:
            available_formatted.append([])
            continue
        cols = sorted(set(t[0] for t in tiles_z))
        rows = sorted(set(t[1] for t in tiles_z))
        available_formatted.append([{
            "startX": min(cols), "startY": min(rows),
            "endX": max(cols), "endY": max(rows)
        }])

    layer_json = {
        "tilejson": "2.1.0",
        "name": f"Nekazari Copernicus GLO-30 — {args.output_prefix}",
        "description": "Free 30m European terrain from Copernicus DEM — no API key needed",
        "version": "1.0.0",
        "format": "quantized-mesh-1.0",
        "scheme": "tms",
        "tiles": ["{z}/{x}/{y}.terrain"],
        "projection": "EPSG:4326",
        "bounds": list(bbox),
        "minzoom": args.min_zoom,
        "maxzoom": args.max_zoom,
        "available": available_formatted,
        "extensions": ["octvertexnormals"]
    }
    layer_bytes = json.dumps(layer_json, indent=2).encode("utf-8")
    s3.put_object(Bucket=MINIO_BUCKET, Key=f"{base_key}/layer.json", Body=layer_bytes,
                  ContentType="application/json")
    print(f"  layer.json → s3://{MINIO_BUCKET}/{base_key}/layer.json")

    # Cleanup
    total_elapsed = time.time() - t_start
    shutil.rmtree(work_dir, ignore_errors=True)

    print()
    print(f"DONE in {total_elapsed/60:.0f}m: {uploaded} tiles uploaded, {skipped} skipped")
    print(f"Terrain URL for Cesium: /api/elevation/terrain/{args.output_prefix}/layer.json")


if __name__ == "__main__":
    main()
