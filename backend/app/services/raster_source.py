"""Source selection and Copernicus GLO-30 reading for the /raster endpoint.

``choose_raster_source`` decides between the national DEM and the pan-European
Copernicus GLO-30 product; ``read_copernicus_grid`` builds a grid for a bbox
with exactly the requested ``cols`` x ``rows`` so the geometry matches the
national branch.
"""
import logging
from typing import Optional

import numpy as np

from app.dem_sources import DEMSource, coverage_for_resolution, get_source
from app.services.heightmap import covering_copernicus_paths
from app.services.point_query import _COPERNICUS_S3_ENV, resolve_source
from app.services.source_registry import _parse_resolution

try:
    import rasterio
    from rasterio.env import Env
    from rasterio.merge import merge as rio_merge
    from rasterio.warp import Resampling
except ImportError:  # pragma: no cover - container-only dependency
    rasterio = None

logger = logging.getLogger(__name__)

# A national source finer than or equal to this is preferred over Copernicus.
COPERNICUS_NATIVE_M = 30.0

# Attribution notice for Copernicus DEM data that has been adapted or modified
# (the grid is resampled to the requested resolution). Verbatim from the
# Copernicus Data Space Ecosystem product page for COP-DEM:
# https://dataspace.copernicus.eu/explore-data/data-collections/copernicus-contributing-missions/collections-description/COP-DEM
COPERNICUS_ATTRIBUTION = (
    "produced using Copernicus WorldDEM-30 © DLR e.V. 2010-2014 and "
    "© Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS "
    "by the European Union and ESA; all rights reserved"
)

# Bounded reads: a z14 tile must answer inside consumers' 15 s budget.
_READ_ENV = {**_COPERNICUS_S3_ENV, "GDAL_HTTP_TIMEOUT": "10", "GDAL_HTTP_MAX_RETRY": "1"}

FALLBACK_REASONS = ("national_coarser", "national_error", "no_national", "national_no_coverage")


def choose_raster_source(
    lat: float, lon: float, resolution_m: float,
) -> tuple[Optional[DEMSource], Optional[str]]:
    """Pick the DEM source for the bbox centre.

    Returns ``(source, fallback_reason)``. ``fallback_reason`` is None when the
    national source is used, ``national_coarser`` when it is coarser than
    Copernicus, ``no_national`` when no national source covers the point.
    ``(None, None)`` means nothing covers the point.
    """
    eu = get_source("EU")
    if eu is not None:
        west, south, east, north = eu.bbox
        if not (west <= lon <= east and south <= lat <= north):
            eu = None

    national = resolve_source(lat, lon)
    if national is None:
        return (eu, "no_national") if eu else (None, None)

    if coverage_for_resolution(national.country_code, resolution_m) is not None:
        return national, None
    native = _parse_resolution(national.resolution)
    if native is not None and native <= COPERNICUS_NATIVE_M:
        return national, None
    return (eu, "national_coarser") if eu else (national, None)


def _is_missing_tile(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(t in msg for t in ("does not exist", "no such file", "404", "nosuchkey"))


def read_copernicus_grid(
    bbox: tuple[float, float, float, float], cols: int, rows: int,
) -> np.ndarray:
    """Copernicus GLO-30 grid (rows, cols) float32, row 0 = north edge.

    No-data (declared nodata, and 1-degree cells missing from the bucket, i.e.
    open sea) is NaN. Raises on transport errors other than a missing tile.
    """
    if rasterio is None:
        raise RuntimeError("rasterio not available")
    west, south, east, north = bbox
    res_x = (east - west) / cols
    res_y = (north - south) / rows
    res_m = res_y * 111320.0
    resampling = Resampling.nearest if res_m <= COPERNICUS_NATIVE_M else Resampling.average

    datasets = []
    with Env(**_READ_ENV):
        try:
            for path in covering_copernicus_paths(bbox):
                try:
                    datasets.append(rasterio.open(path))
                except Exception as exc:
                    if _is_missing_tile(exc):
                        continue
                    raise
            if not datasets:
                return np.full((rows, cols), np.nan, dtype=np.float32)
            merged, _ = rio_merge(
                datasets, bounds=bbox, res=(res_x, res_y),
                resampling=resampling, nodata=float("nan"), dtype="float32",
            )
        finally:
            for ds in datasets:
                try:
                    ds.close()
                except Exception:
                    pass

    grid = merged[0]
    if grid.shape != (rows, cols):
        # rio_merge derives the size from bounds/res with its own rounding.
        grid = _fit(grid, rows, cols)
    return grid


def _fit(grid: np.ndarray, rows: int, cols: int) -> np.ndarray:
    """Crop or NaN-pad by at most a rounding row/column to (rows, cols)."""
    if abs(grid.shape[0] - rows) > 1 or abs(grid.shape[1] - cols) > 1:
        raise ValueError(f"unexpected merged shape {grid.shape} for ({rows}, {cols})")
    out = np.full((rows, cols), np.nan, dtype=grid.dtype)
    r, c = min(rows, grid.shape[0]), min(cols, grid.shape[1])
    out[:r, :c] = grid[:r, :c]
    return out
