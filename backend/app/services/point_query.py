"""Point elevation query — resolves best DEM source by bbox and samples elevation via WCS."""
import io
import logging
import math

import rasterio
from rasterio.env import Env

from app.dem_sources import DEM_SOURCES, DEMSource, get_source

logger = logging.getLogger(__name__)

# Known-good WCS 1.0.0 parameters per country
WCS_PARAMS: dict[str, dict] = {
    # ── WCS 1.0.0 endpoints ──────────────────────────
    "ES": {
        "VERSION": "1.0.0",
        "FORMAT": "GEOTIFFINT16",
        "CRS": "EPSG:4326",
        "COVERAGE_PARAM": "COVERAGE",
        "BBOX_ORDER": "lonlat",  # lon,lat in BBOX
    },
    "NL": {
        "VERSION": "1.0.0",
        "FORMAT": "GEOTIFFINT16",
        "CRS": "EPSG:4326",
        "COVERAGE_PARAM": "COVERAGE",
    },
    "PL": {
        "VERSION": "1.0.0",
        "FORMAT": "GEOTIFFINT16",
        "CRS": "EPSG:4326",
        "COVERAGE_PARAM": "COVERAGE",
    },
    # ── WCS 2.0.1 endpoints (explicit; defaults also work) ──
    "DE": {"VERSION": "2.0.1"},
    "AT": {"VERSION": "2.0.1"},
    "CZ": {"VERSION": "2.0.1"},
    "GB": {"VERSION": "2.0.1"},
    "DK": {"VERSION": "2.0.1"},
    "FI": {"VERSION": "2.0.1"},
    "NO": {"VERSION": "2.0.1"},
    "IT": {"VERSION": "2.0.1"},
}


def resolve_source(lat: float, lon: float) -> DEMSource | None:
    """Find the first DEM source whose bbox contains the query point.

    Only primary (non-fallback) national WCS endpoints are considered.
    Sources marked fallback=True (pan-European Copernicus, known outages
    like PT since 2026-06-05) are skipped — they are not WCS-queryable.
    """
    for src in DEM_SOURCES:
        if src.fallback:
            continue
        west, south, east, north = src.bbox
        if west <= lon <= east and south <= lat <= north:
            return src
    return None


# Grid size for WCS point queries. Some servers (e.g. IGN ES) return a
# degenerate 1-pixel GeoTIFF (pixel size in thousands of degrees, value 0)
# for WIDTH=1&HEIGHT=1 requests — always ask for a 3x3 grid and sample the
# centre pixel instead.
_POINT_GRID = 3


def _point_half_span_deg(source: DEMSource) -> float:
    try:
        res_m = float(source.resolution.replace("m", ""))
    except (ValueError, AttributeError):
        res_m = 30.0
    return (_POINT_GRID * res_m / 111320.0) / 2.0


def build_wcs_url(source: DEMSource, lat: float, lon: float) -> str:
    """Build a WCS GetCoverage URL for a small grid centred on the point.

    Uses WCS 1.0.0 for known-compatible sources (ES), WCS 2.0.1 otherwise.
    Returns the full URL string.
    """
    params = WCS_PARAMS.get(source.country_code, {})
    version = params.get("VERSION", "2.0.1")

    if version == "1.0.0":
        return _build_wcs_1_0(source, lat, lon, params)
    else:
        return _build_wcs_2_0(source, lat, lon, params)


def _build_wcs_1_0(source: DEMSource, lat: float, lon: float, params: dict) -> str:
    """WCS 1.0.0 GetCoverage: simple BBOX + CRS + FORMAT."""
    fmt = params.get("FORMAT", source.format)
    crs = params.get("CRS", "EPSG:4326")
    coverage_param = params.get("COVERAGE_PARAM", "COVERAGE")
    coverage = source.layer_name or "elevation"
    half = _point_half_span_deg(source)
    bbox = f"{lon - half},{lat - half},{lon + half},{lat + half}"

    url = (
        f"{source.service_url}?"
        f"SERVICE=WCS&"
        f"VERSION=1.0.0&"
        f"REQUEST=GetCoverage&"
        f"{coverage_param}={coverage}&"
        f"FORMAT={fmt}&"
        f"BBOX={bbox}&"
        f"CRS={crs}&"
        f"WIDTH={_POINT_GRID}&"
        f"HEIGHT={_POINT_GRID}"
    )
    return url


def _build_wcs_2_0(source: DEMSource, lat: float, lon: float, params: dict) -> str:
    """WCS 2.0.1 GetCoverage: SUBSET syntax."""
    half = _point_half_span_deg(source)

    coverage = source.layer_name or "elevation"
    url = (
        f"{source.service_url}?"
        f"SERVICE=WCS&"
        f"VERSION=2.0.1&"
        f"REQUEST=GetCoverage&"
        f"COVERAGEID={coverage}&"
        f"FORMAT=image/tiff&"
        f"SUBSET=Long({lon - half},{lon + half})&"
        f"SUBSET=Lat({lat - half},{lat + half})"
    )
    return url


# ---------------------------------------------------------------------------
# Point sampling helpers
# ---------------------------------------------------------------------------

# Plausible elevation bounds (m) — rejects nodata-collapsed zeros and garbage.
_ELEV_MIN_M, _ELEV_MAX_M = -500.0, 9000.0

# Copernicus GLO-30 lives in the public AWS bucket copernicus-dem-30m. The
# backend image sets AWS_S3_ENDPOINT=minio:9000 / AWS_HTTPS=NO for the module's
# own storage, so we must override the endpoint here (GDAL reads these as config
# options from Env() and prefers them over the process environment).
_COPERNICUS_S3_ENV = {
    "AWS_NO_SIGN_REQUEST": "YES",
    "AWS_S3_ENDPOINT": "s3.eu-central-1.amazonaws.com",
    "AWS_HTTPS": "YES",
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
}


def sample_tiff_point(content: bytes, lat: float, lon: float) -> float | None:
    """Sample elevation at (lat, lon) from an in-memory GeoTIFF.

    Returns None ("no usable value") when the raster is degenerate, the point
    falls outside it, the sampled cell is nodata, or the value is implausible.
    Never returns a fabricated 0.0.
    """
    try:
        with rasterio.open(io.BytesIO(content)) as ds:
            t = ds.transform
            if t[0] == 0 or t[4] == 0 or abs(t[0]) >= 1.0 or abs(t[4]) >= 1.0:
                return None  # degenerate georeferencing (server bug)
            row, col = ds.index(lon, lat)
            if not (0 <= row < ds.height and 0 <= col < ds.width):
                return None
            value = list(ds.sample([(lon, lat)]))[0][0]
            if ds.nodata is not None and value == ds.nodata:
                return None
            elevation = float(value)
            if not (_ELEV_MIN_M <= elevation <= _ELEV_MAX_M):
                return None
            return elevation
    except Exception:
        return None


def copernicus_tile_path(lat: float, lon: float) -> str:
    """Path of the 1°x1° Copernicus GLO-30 COG tile containing (lat, lon)."""
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    tile = (
        f"Copernicus_DSM_COG_10_{ns}{abs(math.floor(lat)):02d}_00"
        f"_{ew}{abs(math.floor(lon)):03d}_00_DEM"
    )
    return f"/vsis3/copernicus-dem-30m/{tile}/{tile}.tif"


def sample_copernicus_point(lat: float, lon: float) -> float:
    """Sample Copernicus GLO-30 COG directly from S3. Raises on failure."""
    path = copernicus_tile_path(lat, lon)
    with Env(**_COPERNICUS_S3_ENV):
        with rasterio.open(path) as ds:
            value = list(ds.sample([(lon, lat)]))[0][0]
            if ds.nodata is not None and value == ds.nodata:
                raise ValueError(f"Copernicus nodata at ({lat}, {lon})")
            elevation = float(value)
            if not (_ELEV_MIN_M <= elevation <= _ELEV_MAX_M):
                raise ValueError(
                    f"Copernicus implausible elevation {elevation} at ({lat}, {lon})"
                )
            return elevation


def _bbox_contains(source: DEMSource, lat: float, lon: float) -> bool:
    west, south, east, north = source.bbox
    return west <= lon <= east and south <= lat <= north


def point_source_plan(source: str, lat: float, lon: float) -> list[DEMSource]:
    """Ordered DEM sources to try for /point, per the source selector.

    - auto:      Copernicus S3 first (robust single integration), then the
                 national WCS for the point as an extra net.
    - national:  national WCS first (higher resolution), Copernicus fallback.
    - cnig:      force the Spanish IGN WCS only (no silent cross-fallback).
    - copernicus: force Copernicus S3 only.

    Raises ValueError for an unknown selector.
    """
    eu = get_source("EU")
    national = resolve_source(lat, lon)

    if source == "auto":
        plan = [s for s in (eu, national) if s is not None]
    elif source == "national":
        plan = [s for s in (national, eu) if s is not None]
    elif source == "copernicus":
        plan = [eu] if eu else []
    elif source == "cnig":
        es = get_source("ES")
        plan = [es] if es else []
    else:
        raise ValueError(f"Unknown source: {source}")

    # Explicitly-forced country codes skip bbox gating; the rest must cover the point.
    if source in ("auto", "national", "copernicus"):
        plan = [s for s in plan if _bbox_contains(s, lat, lon)]
    return plan
