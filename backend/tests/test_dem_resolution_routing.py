"""Tests that /raster selects the IGN coverage by requested resolution."""
from unittest.mock import patch, AsyncMock
import io

import rasterio
import numpy as np
from fastapi.testclient import TestClient

from app.main import app


def _fake_geotiff_bytes(elev=440.0, cols=4, rows=4):
    arr = np.full((rows, cols), elev, dtype="int16")
    buf = io.BytesIO()
    with rasterio.open(
        buf, "w", driver="GTiff", height=rows, width=cols, count=1,
        dtype="int16", crs="EPSG:4258",
        transform=rasterio.transform.from_origin(-1.65, 42.83, 0.0002, 0.0002),
    ) as dst:
        dst.write(arr, 1)
    return buf.getvalue()


def _captured_coverage():
    """Return a context that records the layer_name actually queried."""
    box = {}

    async def fake_query(source, bbox, width, height):
        box["coverage"] = source.get("layer_name")
        return _fake_geotiff_bytes()

    return box, fake_query


def test_raster_5m_uses_es_5m_coverage():
    box, fake = _captured_coverage()
    with patch("app.api.elevation._wcs_bbox_query", new=AsyncMock(side_effect=fake)):
        client = TestClient(app)
        resp = client.get(
            "/api/elevation/raster",
            params={"min_lon": -1.645, "min_lat": 42.812, "max_lon": -1.635,
                    "max_lat": 42.822, "resolution_m": 5},
        )
    assert resp.status_code == 200, resp.text
    assert box["coverage"] == "Elevacion4258_5", box


def test_raster_25m_uses_es_25m_coverage():
    box, fake = _captured_coverage()
    with patch("app.api.elevation._wcs_bbox_query", new=AsyncMock(side_effect=fake)):
        client = TestClient(app)
        resp = client.get(
            "/api/elevation/raster",
            params={"min_lon": -1.645, "min_lat": 42.812, "max_lon": -1.635,
                    "max_lat": 42.822, "resolution_m": 25},
        )
    assert resp.status_code == 200, resp.text
    assert box["coverage"] == "Elevacion4258_25", box
