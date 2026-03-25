"""
SOTA ETL Pipeline for EU Elevation Processing.

Converts DEM data (WCS, GeoTIFF) into Cesium Quantized Mesh terrain tiles
following the TMS Geographic tiling scheme (EPSG:4326).

Pipeline:
1. Download/prepare DEM data via GDAL (VRT mosaic, reprojection)
2. Calculate tile grid for each zoom level intersecting the BBOX
3. For each tile: extract raster window → decimate mesh → encode quantized mesh → gzip
4. Upload tiles to MinIO via S3 API
5. Generate layer.json with available tile ranges
"""

import os
import io
import gzip
import json
import math
import subprocess
from typing import Optional

import numpy as np
from loguru import logger
from celery import shared_task

# Graceful degradation: C++ encoders may not be available outside Docker
try:
    import rasterio
    from rasterio.windows import from_bounds
    from rasterio.warp import transform_bounds
    import quantized_mesh_encoder
    from pydelatin import Delatin
    HAS_ENCODERS = True
except ImportError as e:
    HAS_ENCODERS = False
    logger.warning(f"C++ encoders not found ({e}). Must run inside Docker worker.")

# MinIO/S3 client — lazy init
try:
    from minio import Minio
    HAS_MINIO = True
except ImportError:
    HAS_MINIO = False
    logger.warning("minio package not available — S3 upload disabled.")

# Temporary working directory (ephemeral, cleaned after job)
WORK_DIR = os.getenv("TERRAIN_WORK_DIR", "/tmp/terrain_work")
os.makedirs(WORK_DIR, exist_ok=True)

# MinIO configuration from env
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "terrain-tilesets")
MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"


# =============================================================================
# Cesium Geographic TMS Tiling Math
# =============================================================================
# Cesium uses a Geographic (EPSG:4326) tiling scheme:
#   - Zoom 0: 2 columns × 1 row (each tile covers 180° × 180°)
#   - Zoom n: 2^(n+1) columns × 2^n rows
#   - Tile (0,0) is at the southwest corner (-180, -90)
# =============================================================================

def _num_tiles_at_zoom(zoom: int) -> tuple[int, int]:
    """Return (num_cols, num_rows) for a given zoom level in Cesium Geographic TMS."""
    return (2 ** (zoom + 1), 2 ** zoom)


def _tile_bounds(zoom: int, col: int, row: int) -> tuple[float, float, float, float]:
    """Get geographic bounds (west, south, east, north) for a tile."""
    num_cols, num_rows = _num_tiles_at_zoom(zoom)
    tile_width = 360.0 / num_cols
    tile_height = 180.0 / num_rows

    west = -180.0 + col * tile_width
    south = -90.0 + row * tile_height
    east = west + tile_width
    north = south + tile_height

    return (west, south, east, north)


def _tiles_in_bbox(zoom: int, bbox: tuple[float, float, float, float]) -> list[tuple[int, int]]:
    """
    Return list of (col, row) tiles at given zoom that intersect the BBOX.
    BBOX is (west, south, east, north) in EPSG:4326.
    """
    num_cols, num_rows = _num_tiles_at_zoom(zoom)
    tile_width = 360.0 / num_cols
    tile_height = 180.0 / num_rows

    min_col = max(0, int(math.floor((bbox[0] + 180.0) / tile_width)))
    max_col = min(num_cols - 1, int(math.floor((bbox[2] + 180.0) / tile_width)))
    min_row = max(0, int(math.floor((bbox[1] + 90.0) / tile_height)))
    max_row = min(num_rows - 1, int(math.floor((bbox[3] + 90.0) / tile_height)))

    tiles = []
    for col in range(min_col, max_col + 1):
        for row in range(min_row, max_row + 1):
            tiles.append((col, row))

    return tiles


# =============================================================================
# GDAL Helpers
# =============================================================================

def _run_gdal(cmd: list[str]) -> None:
    """Execute a GDAL command, raising RuntimeError on failure."""
    logger.debug(f"GDAL: {' '.join(cmd)}")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        logger.error(f"GDAL stderr: {proc.stderr}")
        raise RuntimeError(f"GDAL command failed ({proc.returncode}): {' '.join(cmd[:3])}...")


def _prepare_dem(
    source_urls: list[str],
    bbox: tuple[float, float, float, float],
    work_dir: str
) -> str:
    """
    Prepare a EPSG:4326 VRT from source DEM files/URLs, clipped to BBOX.
    Returns path to the reprojected VRT.
    """
    vrt_raw = os.path.join(work_dir, "mosaic_raw.vrt")
    vrt_4326 = os.path.join(work_dir, "mosaic_epsg4326.vrt")

    # Step 1: Build VRT mosaic restricted to BBOX
    vrt_cmd = [
        "gdalbuildvrt",
        "-te", str(bbox[0]), str(bbox[1]), str(bbox[2]), str(bbox[3]),
        vrt_raw
    ] + source_urls
    _run_gdal(vrt_cmd)

    # Step 2: Reproject to EPSG:4326 (required by Cesium)
    warp_cmd = [
        "gdalwarp",
        "-t_srs", "EPSG:4326",
        "-of", "VRT",
        "--config", "GDAL_CACHEMAX", "2048",
        "-multi",
        vrt_raw,
        vrt_4326
    ]
    _run_gdal(warp_cmd)

    return vrt_4326


def _prepare_local_dem(
    file_path: str,
    bbox: Optional[tuple[float, float, float, float]],
    work_dir: str
) -> str:
    """
    Prepare local DEM file for processing, reprojecting to EPSG:4326.
    Returns path to the reprojected VRT.
    """
    vrt_4326 = os.path.join(work_dir, "local_epsg4326.vrt")

    warp_cmd = [
        "gdalwarp",
        "-t_srs", "EPSG:4326",
        "-of", "VRT",
        "--config", "GDAL_CACHEMAX", "2048",
        "-multi"
    ]
    if bbox:
        warp_cmd.extend(["-te", str(bbox[0]), str(bbox[1]), str(bbox[2]), str(bbox[3])])
    warp_cmd.extend([file_path, vrt_4326])

    _run_gdal(warp_cmd)
    return vrt_4326


# =============================================================================
# MinIO S3 Upload
# =============================================================================

def _get_minio_client() -> "Minio":
    """Create MinIO client from environment configuration."""
    if not HAS_MINIO:
        raise RuntimeError("minio package not installed")
    if not MINIO_ACCESS_KEY or not MINIO_SECRET_KEY:
        raise RuntimeError("MINIO_ACCESS_KEY and MINIO_SECRET_KEY must be set")

    return Minio(
        MINIO_ENDPOINT,
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        secure=MINIO_SECURE
    )


def _ensure_bucket(client: "Minio", bucket: str) -> None:
    """Ensure the target bucket exists."""
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
        logger.info(f"Created MinIO bucket: {bucket}")


def _upload_bytes(client: "Minio", bucket: str, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
    """Upload bytes to MinIO."""
    client.put_object(
        bucket,
        key,
        io.BytesIO(data),
        length=len(data),
        content_type=content_type
    )


# =============================================================================
# Terrain Tile Processing
# =============================================================================

def _process_tile(
    ds,
    zoom: int,
    col: int,
    row: int,
    max_error: float = 0.5
) -> Optional[bytes]:
    """
    Extract elevation data for a specific tile, decimate and encode to Quantized Mesh.
    Returns gzipped .terrain bytes or None if tile has no valid data.
    """
    tile_bounds = _tile_bounds(zoom, col, row)

    try:
        # Calculate the raster window for this tile's geographic bounds
        window = from_bounds(
            tile_bounds[0], tile_bounds[1],
            tile_bounds[2], tile_bounds[3],
            ds.transform
        )

        # Clamp window to dataset bounds
        window = window.intersection(rasterio.windows.Window(0, 0, ds.width, ds.height))

        if window.width < 2 or window.height < 2:
            return None

        # Read elevation data
        elevation_data = ds.read(
            1,
            window=window,
            out_shape=(min(int(window.height), 256), min(int(window.width), 256))
        )

        # Check for valid data (skip tiles that are all nodata)
        if ds.nodata is not None:
            valid_mask = elevation_data != ds.nodata
            if not valid_mask.any():
                return None
            # Replace nodata with 0 for mesh generation
            elevation_data = np.where(valid_mask, elevation_data, 0)

        # Handle NaN values
        if np.isnan(elevation_data).any():
            elevation_data = np.nan_to_num(elevation_data, nan=0.0)

        # Ensure float32 for pydelatin
        elevation_data = elevation_data.astype(np.float32)

        # Skip completely flat tiles (all same value)
        if elevation_data.max() == elevation_data.min():
            # Still generate a valid flat tile
            pass

        # Mesh decimation with pydelatin
        tin = Delatin(elevation_data, max_error=max_error)
        vertices = tin.vertices
        triangles = tin.triangles

        if len(vertices) < 3 or len(triangles) < 1:
            return None

        # Encode to Quantized Mesh
        qm_bytes = quantized_mesh_encoder.encode(
            vertices,
            triangles,
            bounds=tile_bounds
        )

        # Gzip compress
        gz_buffer = io.BytesIO()
        with gzip.GzipFile(fileobj=gz_buffer, mode="wb") as gz:
            gz.write(qm_bytes)

        return gz_buffer.getvalue()

    except Exception as e:
        logger.warning(f"Failed to process tile z={zoom} x={col} y={row}: {e}")
        return None


def _generate_layer_json(
    bounds: tuple[float, float, float, float],
    available_tiles: dict[int, list[tuple[int, int]]],
    zoom_range: tuple[int, int]
) -> dict:
    """Generate a proper Cesium layer.json with available tile ranges."""
    available = []
    for z in range(zoom_range[0], zoom_range[1] + 1):
        tiles = available_tiles.get(z, [])
        if not tiles:
            available.append([])
            continue

        # Group into contiguous ranges
        cols = sorted(set(t[0] for t in tiles))
        rows = sorted(set(t[1] for t in tiles))

        available.append([{
            "startX": min(cols),
            "startY": min(rows),
            "endX": max(cols),
            "endY": max(rows)
        }])

    return {
        "tilejson": "2.1.0",
        "name": "Nekazari EU Elevation",
        "description": "High-resolution elevation terrain for EU/UK regions",
        "version": "1.0.0",
        "format": "quantized-mesh-1.0",
        "scheme": "tms",
        "tiles": ["{z}/{x}/{y}.terrain"],
        "projection": "EPSG:4326",
        "bounds": list(bounds),
        "minzoom": zoom_range[0],
        "maxzoom": zoom_range[1],
        "available": available,
        "extensions": ["octvertexnormals"]
    }


# =============================================================================
# Celery Tasks
# =============================================================================

@shared_task(bind=True, name="app.tasks.elevation_tasks.process_dem_to_quantized_mesh")
def process_dem_to_quantized_mesh(
    self,
    country_code: str,
    source_urls: list[str],
    bbox: tuple[float, float, float, float],
    zoom_min: int = 8,
    zoom_max: int = 14,
    max_error: float = 0.5,
    _is_fallback: bool = False,
    _original_error: str = ""
):
    """
    SOTA ETL Pipeline for EU Elevation Processing (Selective BBOX Ingestion).

    1. Build VRT mosaic restricted to BBOX
    2. Reproject to EPSG:4326
    3. For each zoom level, calculate intersecting tiles
    4. For each tile: extract → decimate → encode → gzip
    5. Upload to MinIO via S3 API
    6. Generate and upload layer.json

    If the primary source fails, automatically falls back to the
    pan-European Copernicus GLO-30 (30m) and warns the user.
    """
    if not HAS_ENCODERS:
        raise RuntimeError("C++ encoders (rasterio, pydelatin, quantized-mesh-encoder) not available. "
                           "This task must run inside the Docker worker image.")

    source_label = "FALLBACK Copernicus GLO-30 (30m)" if _is_fallback else f"primary source"
    logger.info(f"[{country_code}] Starting pipeline ({source_label}) BBOX: {bbox}, zoom {zoom_min}-{zoom_max}")

    if _is_fallback:
        self.update_state(state='PROCESSING', meta={
            'progress': 2,
            'message': f'⚠️ Primary source failed: {_original_error}. '
                       f'Falling back to Copernicus GLO-30 (30m, lower resolution)...',
            'fallback_used': True,
            'fallback_reason': _original_error
        })
    else:
        self.update_state(state='PROCESSING', meta={'progress': 2, 'message': 'Preparing DEM data...'})

    # Create isolated work directory for this job
    job_dir = os.path.join(WORK_DIR, f"{country_code}_{self.request.id}")
    os.makedirs(job_dir, exist_ok=True)

    try:
        # Phase 1: Prepare DEM (VRT + reprojection)
        self.update_state(state='PROCESSING', meta={
            'progress': 5,
            'message': 'Building VRT mosaic and reprojecting to EPSG:4326...',
            **({"fallback_used": True, "fallback_reason": _original_error} if _is_fallback else {})
        })

        try:
            vrt_path = _prepare_dem(source_urls, bbox, job_dir)
        except Exception as vrt_error:
            # === AUTOMATIC FALLBACK ===
            # If this is already a fallback attempt, don't recurse — fail hard
            if _is_fallback:
                raise

            # Try the pan-European fallback source
            from app.dem_sources import get_source
            fallback_src = get_source("EU")

            if not fallback_src:
                logger.error(f"[{country_code}] Primary source failed and no EU fallback configured")
                raise

            original_error_msg = str(vrt_error)
            logger.warning(
                f"[{country_code}] Primary source failed ({original_error_msg}). "
                f"Falling back to {fallback_src.country_name} ({fallback_src.resolution})"
            )

            # Cleanup failed job dir
            import shutil
            shutil.rmtree(job_dir, ignore_errors=True)

            # Re-invoke self with fallback source
            return process_dem_to_quantized_mesh(
                self,
                country_code=country_code,
                source_urls=[fallback_src.service_url],
                bbox=bbox,
                zoom_min=zoom_min,
                zoom_max=min(zoom_max, 12),  # Cap zoom for 30m resolution
                max_error=max_error,
                _is_fallback=True,
                _original_error=original_error_msg
            )

        # Phase 2: Initialize MinIO client
        self.update_state(state='PROCESSING', meta={'progress': 10, 'message': 'Connecting to object storage...'})
        minio_client = _get_minio_client()
        _ensure_bucket(minio_client, MINIO_BUCKET)
        base_key = f"terrain/{country_code}"

        # Phase 3: Calculate total tiles for progress tracking
        total_tiles = 0
        zoom_tiles: dict[int, list[tuple[int, int]]] = {}
        for z in range(zoom_min, zoom_max + 1):
            tiles = _tiles_in_bbox(z, bbox)
            zoom_tiles[z] = tiles
            total_tiles += len(tiles)

        logger.info(f"[{country_code}] Total tiles to process: {total_tiles} across zoom {zoom_min}-{zoom_max}")

        if total_tiles == 0:
            raise ValueError(f"No tiles found for BBOX {bbox} in zoom range {zoom_min}-{zoom_max}")

        # Phase 4: Process tiles
        processed = 0
        failed = 0
        available_tiles: dict[int, list[tuple[int, int]]] = {}

        with rasterio.open(vrt_path) as ds:
            for z in range(zoom_min, zoom_max + 1):
                tiles = zoom_tiles[z]
                available_tiles[z] = []

                for col, row in tiles:
                    progress_pct = 10 + int((processed / total_tiles) * 80)
                    self.update_state(state='PROCESSING', meta={
                        'progress': progress_pct,
                        'message': f'Processing tile z={z} x={col} y={row} ({processed + 1}/{total_tiles})',
                        **({"fallback_used": True, "fallback_reason": _original_error} if _is_fallback else {})
                    })

                    tile_data = _process_tile(ds, z, col, row, max_error=max_error)

                    if tile_data:
                        object_key = f"{base_key}/{z}/{col}/{row}.terrain"
                        _upload_bytes(
                            minio_client,
                            MINIO_BUCKET,
                            object_key,
                            tile_data,
                            content_type="application/vnd.quantized-mesh"
                        )
                        available_tiles[z].append((col, row))
                    else:
                        failed += 1

                    processed += 1

        # Phase 5: Generate and upload layer.json
        self.update_state(state='PROCESSING', meta={'progress': 95, 'message': 'Generating layer.json metadata...'})
        layer_json = _generate_layer_json(bbox, available_tiles, (zoom_min, zoom_max))
        layer_json_bytes = json.dumps(layer_json, indent=2).encode("utf-8")

        _upload_bytes(
            minio_client,
            MINIO_BUCKET,
            f"{base_key}/layer.json",
            layer_json_bytes,
            content_type="application/json"
        )

        # Phase 6: Cleanup work directory
        import shutil
        shutil.rmtree(job_dir, ignore_errors=True)

        total_success = processed - failed
        result = {
            "status": "success",
            "country": country_code,
            "tiles_processed": total_success,
            "tiles_failed": failed,
            "zoom_range": f"{zoom_min}-{zoom_max}",
            "storage_path": f"s3://{MINIO_BUCKET}/{base_key}/"
        }

        # Add fallback warning to result so frontend can display it
        if _is_fallback:
            result["fallback_used"] = True
            result["fallback_reason"] = _original_error
            result["fallback_resolution"] = "30m"
            result["warning"] = (
                f"⚠️ The primary DEM source for {country_code} was unavailable "
                f"({_original_error}). Terrain was generated using "
                f"Copernicus GLO-30 (30m resolution). You can retry with "
                f"the primary source later for higher quality."
            )
            logger.warning(f"[{country_code}] Completed with FALLBACK: {result['warning']}")
        else:
            logger.info(f"[{country_code}] Pipeline complete: {total_success} tiles uploaded, {failed} skipped")

        self.update_state(state='SUCCESS', meta={
            'progress': 100,
            'message': 'Pipeline completed successfully.' + (
                f' (⚠️ Using fallback source: Copernicus GLO-30, 30m)' if _is_fallback else ''
            ),
            **({"fallback_used": True, "fallback_reason": _original_error} if _is_fallback else {})
        })
        return result

    except Exception as e:
        import shutil
        shutil.rmtree(job_dir, ignore_errors=True)

        self.update_state(state='FAILED', meta={'progress': 0, 'message': f'Critical error: {str(e)}'})
        logger.error(f"[{country_code}] Pipeline failed: {str(e)}")
        raise self.retry(exc=e, countdown=60, max_retries=2)


@shared_task(bind=True, name="app.tasks.elevation_tasks.process_local_dem_to_quantized_mesh")
def process_local_dem_to_quantized_mesh(
    self,
    country_code: str,
    file_path: str,
    bbox: Optional[tuple[float, float, float, float]] = None,
    zoom_min: int = 8,
    zoom_max: int = 14,
    max_error: float = 0.5
):
    """
    SOTA ETL Pipeline for local DEM file upload.

    Same quality as remote pipeline but starts from a local file instead of URLs.
    """
    if not HAS_ENCODERS:
        raise RuntimeError("C++ encoders not available. Must run inside Docker worker.")

    logger.info(f"[{country_code}] Starting local DEM pipeline from: {file_path}")
    self.update_state(state='PROCESSING', meta={'progress': 5, 'message': 'Preparing local DEM...'})

    job_dir = os.path.join(WORK_DIR, f"{country_code}_local_{self.request.id}")
    os.makedirs(job_dir, exist_ok=True)

    try:
        # Reproject local file
        self.update_state(state='PROCESSING', meta={'progress': 10, 'message': 'Reprojecting local DEM to EPSG:4326...'})
        vrt_path = _prepare_local_dem(file_path, bbox, job_dir)

        # If no BBOX provided, extract from dataset
        if not bbox:
            with rasterio.open(vrt_path) as ds:
                bbox = (ds.bounds.left, ds.bounds.bottom, ds.bounds.right, ds.bounds.top)
            logger.info(f"[{country_code}] Extracted BBOX from dataset: {bbox}")

        # Initialize MinIO
        minio_client = _get_minio_client()
        _ensure_bucket(minio_client, MINIO_BUCKET)
        base_key = f"terrain/{country_code}"

        # Calculate tiles
        total_tiles = 0
        zoom_tiles: dict[int, list[tuple[int, int]]] = {}
        for z in range(zoom_min, zoom_max + 1):
            tiles = _tiles_in_bbox(z, bbox)
            zoom_tiles[z] = tiles
            total_tiles += len(tiles)

        if total_tiles == 0:
            raise ValueError(f"No tiles found for BBOX {bbox}")

        # Process tiles
        processed = 0
        failed = 0
        available_tiles: dict[int, list[tuple[int, int]]] = {}

        with rasterio.open(vrt_path) as ds:
            for z in range(zoom_min, zoom_max + 1):
                tiles = zoom_tiles[z]
                available_tiles[z] = []

                for col, row in tiles:
                    progress_pct = 10 + int((processed / total_tiles) * 80)
                    self.update_state(state='PROCESSING', meta={
                        'progress': progress_pct,
                        'message': f'Processing tile z={z} x={col} y={row} ({processed + 1}/{total_tiles})'
                    })

                    tile_data = _process_tile(ds, z, col, row, max_error=max_error)
                    if tile_data:
                        object_key = f"{base_key}/{z}/{col}/{row}.terrain"
                        _upload_bytes(minio_client, MINIO_BUCKET, object_key, tile_data,
                                      content_type="application/vnd.quantized-mesh")
                        available_tiles[z].append((col, row))
                    else:
                        failed += 1
                    processed += 1

        # Generate layer.json
        self.update_state(state='PROCESSING', meta={'progress': 95, 'message': 'Generating metadata...'})
        layer_json = _generate_layer_json(bbox, available_tiles, (zoom_min, zoom_max))
        _upload_bytes(
            minio_client, MINIO_BUCKET,
            f"{base_key}/layer.json",
            json.dumps(layer_json, indent=2).encode("utf-8"),
            content_type="application/json"
        )

        # Cleanup
        import shutil
        shutil.rmtree(job_dir, ignore_errors=True)
        try:
            os.remove(file_path)
        except OSError:
            pass

        total_success = processed - failed
        result = {
            "status": "success",
            "country": country_code,
            "tiles_processed": total_success,
            "tiles_failed": failed,
            "zoom_range": f"{zoom_min}-{zoom_max}",
            "storage_path": f"s3://{MINIO_BUCKET}/{base_key}/"
        }

        logger.info(f"[{country_code}] Local pipeline complete: {total_success} tiles, {failed} skipped")
        self.update_state(state='SUCCESS', meta={'progress': 100, 'message': 'Local pipeline completed.'})
        return result

    except Exception as e:
        import shutil
        shutil.rmtree(job_dir, ignore_errors=True)
        self.update_state(state='FAILED', meta={'progress': 0, 'message': f'Error: {str(e)}'})
        logger.error(f"[{country_code}] Local pipeline failed: {str(e)}")
        raise self.retry(exc=e, countdown=10, max_retries=1)
