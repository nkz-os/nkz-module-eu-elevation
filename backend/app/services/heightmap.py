"""
On-demand terrarium heightmap tiles from Copernicus GLO-30.

Serves WebMercator tiles (z/x/y) as terrarium-encoded PNGs generated on the
fly via windowed reads over the public Copernicus DEM S3 COGs, with a
write-through MinIO cache: only tiles actually requested by users are stored
(bounded growth, replaces the ~300 GB bulk pre-ingestion pipeline).

Grid convention: 256x256 samples UNIFORM in lat/lon across the tile bbox.
This is self-consistent with the module frontend decoder (custom heightmap
provider samples the grid linearly), so no mercator-y correction is needed.

Zoom window: [MIN_ZOOM, MAX_ZOOM]. Below MIN_ZOOM the number of covering
1x1-degree COGs explodes (globe view); the frontend delegates those levels
to a global open-data fallback instead.
"""

import io
import logging
import math
import os
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

TILE_SIZE = 256
MIN_ZOOM = 6
MAX_ZOOM = 16
# z=6 tile spans ~5.6 deg -> at most ~7x7=49 COGs; cap defensively.
MAX_COGS_PER_TILE = 80

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:  # pragma: no cover - Pillow is in requirements
    HAS_PIL = False

try:
    import rasterio
    from rasterio.env import Env
    from rasterio.merge import merge as rio_merge
    from rasterio.warp import Resampling
    HAS_RASTERIO = True
except ImportError:  # pragma: no cover - container-only dependency
    HAS_RASTERIO = False

from app.services.point_query import _COPERNICUS_S3_ENV


# ---------------------------------------------------------------------------
# Tile math (slippy / WebMercator)
# ---------------------------------------------------------------------------

def tile_bbox(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """Geographic bounds (west, south, east, north) of a WebMercator tile."""
    n = 2 ** z
    west = x / n * 360.0 - 180.0
    east = (x + 1) / n * 360.0 - 180.0
    south = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
    north = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return west, south, east, north


def _copernicus_path_for_cell(lat_i: int, lon_i: int) -> str:
    ns = "N" if lat_i >= 0 else "S"
    ew = "E" if lon_i >= 0 else "W"
    tile = (
        f"Copernicus_DSM_COG_10_{ns}{abs(lat_i):02d}_00"
        f"_{ew}{abs(lon_i):03d}_00_DEM"
    )
    return f"/vsis3/copernicus-dem-30m/{tile}/{tile}.tif"


def covering_copernicus_paths(
    bbox: tuple[float, float, float, float],
) -> list[str]:
    """Copernicus GLO-30 1x1-deg COG paths covering the bbox (deduplicated)."""
    west, south, east, north = bbox
    paths = []
    for lat_i in range(math.floor(south), math.ceil(north)):
        for lon_i in range(math.floor(west), math.ceil(east)):
            paths.append(_copernicus_path_for_cell(lat_i, lon_i))
    return paths


# ---------------------------------------------------------------------------
# Terrarium encoding
# ---------------------------------------------------------------------------

def encode_terrarium_png(elev: np.ndarray) -> bytes:
    """Encode an elevation grid (metres) as a terrarium RGB PNG.

    Inverse: elevation = R * 256 + G + B / 256 - 32768 (error <= 1/512 m).
    """
    if not HAS_PIL:
        raise RuntimeError("Pillow not available")
    e = np.nan_to_num(np.asarray(elev, dtype=np.float64), nan=0.0, posinf=0.0, neginf=0.0)
    e = np.clip(e, -32768.0, 32767.996) + 32768.0
    r = np.minimum(e // 256.0, 255.0).astype(np.uint8)
    g = np.clip(np.floor(e - r.astype(np.float64) * 256.0), 0, 255).astype(np.uint8)
    b = np.clip(np.round((e - np.floor(e)) * 256.0), 0, 255).astype(np.uint8)
    img = Image.fromarray(np.dstack([r, g, b]), mode="RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG", compress_level=6)
    return buf.getvalue()


def decode_terrarium_png(png_bytes: bytes) -> np.ndarray:
    """Decode terrarium PNG back to metres (test helper / inverse check)."""
    if not HAS_PIL:
        raise RuntimeError("Pillow not available")
    arr = np.asarray(Image.open(io.BytesIO(png_bytes)).convert("RGB"), dtype=np.float64)
    return arr[:, :, 0] * 256.0 + arr[:, :, 1] + arr[:, :, 2] / 256.0 - 32768.0


# ---------------------------------------------------------------------------
# Generation (windowed read over remote COGs)
# ---------------------------------------------------------------------------

def generate_heightmap_png(z: int, x: int, y: int) -> Optional[bytes]:
    """Generate a terrarium PNG for the tile, or None when unfeasible."""
    if not HAS_RASTERIO:
        return None

    bbox = tile_bbox(z, x, y)
    paths = covering_copernicus_paths(bbox)
    if not paths or len(paths) > MAX_COGS_PER_TILE:
        return None

    datasets = []
    with Env(**_COPERNICUS_S3_ENV):
        try:
            for p in paths:
                try:
                    datasets.append(rasterio.open(p))
                except Exception:
                    continue  # missing COG cell (open ocean / edge) — skip
            if not datasets:
                return None
            west, south, east, north = bbox
            res_x = (east - west) / TILE_SIZE
            res_y = (north - south) / TILE_SIZE
            merged, _ = rio_merge(
                datasets,
                bounds=bbox,
                res=(res_x, res_y),
                resampling=Resampling.average,
                nodata=0.0,
            )
        finally:
            for ds in datasets:
                try:
                    ds.close()
                except Exception:
                    pass

    # merge returns (bands, rows, cols); row 0 = north, which matches both
    # the terrarium PNG layout and Cesium's custom heightmap row order. Areas
    # with no COG coverage arrive as nodata (0.0 = sea level).
    return encode_terrarium_png(merged[0])


# ---------------------------------------------------------------------------
# MinIO write-through cache
# ---------------------------------------------------------------------------

_CACHE_PREFIX = "terrain/ondemand"


def cache_key(z: int, x: int, y: int) -> str:
    return f"{_CACHE_PREFIX}/{z}/{x}/{y}.png"


def get_cached_tile(z: int, x: int, y: int) -> Optional[bytes]:
    """Fetch a cached tile from MinIO. Returns None on miss (never raises)."""
    try:
        from app.services.s3_client import get_s3_client
        s3 = get_s3_client()
        bucket = os.getenv("MINIO_BUCKET", "terrain-tilesets")
        resp = s3.get_object(Bucket=bucket, Key=cache_key(z, x, y))
        return resp["Body"].read()
    except Exception:
        return None


def store_tile(z: int, x: int, y: int, png_bytes: bytes) -> None:
    """Write a tile to the MinIO cache (best-effort, never raises)."""
    try:
        from app.services.s3_client import get_s3_client
        s3 = get_s3_client()
        bucket = os.getenv("MINIO_BUCKET", "terrain-tilesets")
        s3.put_object(
            Bucket=bucket,
            Key=cache_key(z, x, y),
            Body=png_bytes,
            ContentType="image/png",
        )
    except Exception as exc:  # cache failure must not break serving
        logger.warning("heightmap cache write failed for %s/%s/%s: %s", z, x, y, exc)
