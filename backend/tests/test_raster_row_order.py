"""/raster declares its row order: row 0 is the north edge of the bbox."""
import asyncio
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import numpy as np
import rasterio
from rasterio.transform import from_bounds

from app.api import elevation


def _north_up_tiff(rows: np.ndarray, bbox: tuple) -> bytes:
    h, w = rows.shape
    buf = io.BytesIO()
    with rasterio.open(
        buf, "w", driver="GTiff", height=h, width=w, count=1, dtype="int16",
        crs="EPSG:4326", transform=from_bounds(*bbox, w, h),
    ) as dst:
        dst.write(rows.astype("int16"), 1)
    return buf.getvalue()


def test_raster_declares_north_first_rows():
    bbox = (-1.0, 42.0, -0.99, 42.01)
    grid = np.array([[400] * 4, [300] * 4, [200] * 4, [100] * 4])
    dem = SimpleNamespace(
        service_url="https://example.invalid/wcs", layer_name="dem",
        format="GEOTIFFINT16", country_code="ZZ", country_name="Testland", resolution="25m",
    )
    with patch.object(elevation, "resolve_source", return_value=dem), \
         patch.object(elevation, "_wcs_bbox_query", AsyncMock(return_value=_north_up_tiff(grid, bbox))):
        resp = asyncio.run(elevation.get_elevation_raster(
            min_lon=bbox[0], min_lat=bbox[1], max_lon=bbox[2], max_lat=bbox[3],
            resolution_m=250, purpose=elevation.PurposeEnum.auto, _tenant_id="acme",
        ))

    assert resp["row_order"] == "north_first"
    assert resp["north_lat"] == 42.01
    assert resp["origin_lat"] == 42.0
    assert resp["elevations"][0] == [400] * 4
