"""Tests for the /point endpoint — contract, caching, source plan, sampling.

Auth is disabled via tests/conftest.py (reader gate covered by test_reader_auth.py).
"""

import io

import numpy as np
import pytest

pytest.importorskip("rasterio", reason="rasterio not installed (non-container env)")

from rasterio.transform import from_origin  # noqa: E402

import rasterio  # noqa: E402
from unittest.mock import AsyncMock, patch, MagicMock  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.dem_sources import DEMSource  # noqa: E402

client = TestClient(app)

ES = DEMSource(
    country_code="ES", country_name="España",
    service_url="https://servicios.idee.es/wcs-inspire/mdt",
    service_type="WCS", format="GEOTIFFINT16", resolution="25m",
    bbox=(-18.2, 27.6, 4.4, 43.8), layer_name="Elevacion4258_25",
)
EU = DEMSource(
    country_code="EU", country_name="Pan-European (Copernicus DEM 30m)",
    service_url="/vsis3/copernicus-dem-30m", service_type="DOWNLOAD",
    format="GeoTIFF", resolution="30m", bbox=(-32.0, 27.0, 45.0, 72.0),
    fallback=True,
)


def _tiff_bytes(values, transform, nodata=None, dtype="int16"):
    height, width = values.shape
    buf = io.BytesIO()
    with rasterio.open(
        buf, "w", driver="GTiff", height=height, width=width, count=1,
        dtype=dtype, crs="EPSG:4326", transform=transform, nodata=nodata,
    ) as dst:
        dst.write(values, 1)
    return buf.getvalue()


def _redis_miss():
    inst = AsyncMock()
    inst.get = AsyncMock(return_value=None)
    return inst


class TestPointContract:

    @patch("app.api.elevation._get_redis", new_callable=AsyncMock)
    @patch("app.api.elevation.point_source_plan", create=True)
    def test_no_coverage_returns_null_not_zero(self, mock_plan, mock_redis):
        mock_redis.return_value = None
        mock_plan.return_value = []
        resp = client.get("/api/elevation/point?lat=0.0&lon=0.0")
        assert resp.status_code == 200
        data = resp.json()
        assert data["elevation_m"] is None
        assert data["status"] == "unavailable"
        assert data["source"] is None
        assert data["error"]["code"] == "no_dem_coverage"

    @patch("app.api.elevation._get_redis", new_callable=AsyncMock)
    @patch("app.api.elevation._wcs_point_sample", new_callable=AsyncMock, create=True)
    @patch("app.api.elevation.point_source_plan", create=True)
    def test_success_returns_ok_status(self, mock_plan, mock_sample, mock_redis):
        mock_redis.return_value = None
        mock_plan.return_value = [ES]
        mock_sample.return_value = 574.0
        resp = client.get("/api/elevation/point?lat=42.6&lon=-2.0&source=national")
        assert resp.status_code == 200
        data = resp.json()
        assert data["elevation_m"] == 574.0
        assert data["status"] == "ok"
        assert data["error"] is None
        assert data["source"]["id"] == "builtin:ES"

    @patch("app.api.elevation._get_redis", new_callable=AsyncMock)
    @patch("app.api.elevation._wcs_point_sample", new_callable=AsyncMock, create=True)
    @patch("app.api.elevation.point_source_plan", create=True)
    def test_all_sources_fail_returns_null_not_zero(self, mock_plan, mock_sample, mock_redis):
        """A source that cannot answer must produce null + status, never 0.0."""
        mock_redis.return_value = None
        mock_plan.return_value = [ES]
        mock_sample.return_value = None  # source failed to yield a value
        resp = client.get("/api/elevation/point?lat=42.6&lon=-2.0&source=cnig")
        assert resp.status_code == 200
        data = resp.json()
        assert data["elevation_m"] is None
        assert data["status"] == "unavailable"
        assert data["error"]["code"] == "source_unavailable"

    @patch("app.api.elevation._get_redis", new_callable=AsyncMock)
    @patch("app.api.elevation._wcs_point_sample", new_callable=AsyncMock, create=True)
    @patch("app.api.elevation.sample_copernicus_point", create=True)
    @patch("app.api.elevation.point_source_plan", create=True)
    def test_auto_prefers_copernicus(self, mock_plan, mock_cop, mock_wcs, mock_redis):
        mock_redis.return_value = None
        mock_plan.return_value = [EU, ES]
        mock_cop.return_value = 572.83
        resp = client.get("/api/elevation/point?lat=42.6&lon=-2.0&source=auto")
        assert resp.status_code == 200
        data = resp.json()
        assert data["elevation_m"] == 572.83
        assert data["source"]["id"] == "builtin:EU"
        mock_wcs.assert_not_called()


class TestPointCacheKey:

    @patch("app.api.elevation._get_redis", new_callable=AsyncMock)
    @patch("app.api.elevation._wcs_point_sample", new_callable=AsyncMock, create=True)
    @patch("app.api.elevation.point_source_plan", create=True)
    def test_cache_key_distinguishes_source_and_purpose(self, mock_plan, mock_sample, mock_redis):
        inst = _redis_miss()
        mock_redis.return_value = inst
        mock_plan.return_value = [ES]
        mock_sample.return_value = 574.0

        client.get("/api/elevation/point?lat=42.6&lon=-2.0&source=auto&purpose=weather")
        client.get("/api/elevation/point?lat=42.6&lon=-2.0&source=copernicus&purpose=precision")

        keys = [c.args[0] for c in inst.set.call_args_list]
        assert len(keys) == 2
        assert keys[0] != keys[1]
        assert "auto" in keys[0] and "copernicus" in keys[1]

    @patch("app.api.elevation._get_redis", new_callable=AsyncMock)
    def test_cache_hit_returns_cached_value(self, mock_redis):
        inst = AsyncMock()
        inst.get = AsyncMock(return_value='{"lat": 42.817, "lon": -1.642, '
                                          '"elevation_m": 445.0, "status": "ok", '
                                          '"source": {"id": "builtin:ES"}}')
        mock_redis.return_value = inst

        resp = client.get("/api/elevation/point?lat=42.817&lon=-1.642")
        assert resp.status_code == 200
        assert resp.json()["elevation_m"] == 445.0


class TestPointRealPixelPath:

    @patch("app.api.elevation._get_redis", new_callable=AsyncMock)
    @patch("app.api.elevation.httpx.AsyncClient.get")
    def test_endpoint_reads_point_pixel_not_corner(self, mock_get, mock_redis):
        """Real GeoTIFF through the endpoint: the value is the point's pixel."""
        mock_redis.return_value = None
        arr = np.full((10, 10), 500, dtype="int16")
        arr[4, 4] = 1234  # query point (0.045, -0.045) lands on row 4, col 4
        content = _tiff_bytes(arr, from_origin(0.0, 0.0, 0.01, 0.01))

        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.content = content
        mock_get.return_value = resp

        r = client.get("/api/elevation/point?lat=-0.045&lon=0.045&source=cnig")
        assert r.status_code == 200
        data = r.json()
        assert data["elevation_m"] == 1234.0
        assert data["status"] == "ok"


class TestPointParams:

    @patch("app.api.elevation._get_redis", new_callable=AsyncMock)
    @patch("app.api.elevation.point_source_plan", create=True)
    def test_purpose_param_accepted(self, mock_plan, mock_redis):
        mock_redis.return_value = None
        mock_plan.return_value = []
        for purpose in ["auto", "precision", "routing", "weather", "visualization"]:
            resp = client.get(f"/api/elevation/point?lat=42.0&lon=-1.0&purpose={purpose}")
            assert resp.status_code == 200

    def test_invalid_purpose_rejected(self):
        resp = client.get("/api/elevation/point?lat=42.0&lon=-1.0&purpose=invalid")
        assert resp.status_code == 422

    @patch("app.api.elevation._get_redis", new_callable=AsyncMock)
    @patch("app.api.elevation.point_source_plan", create=True)
    def test_source_param_accepted(self, mock_plan, mock_redis):
        mock_redis.return_value = None
        mock_plan.return_value = []
        for source in ["auto", "cnig", "copernicus", "national"]:
            resp = client.get(f"/api/elevation/point?lat=42.0&lon=-1.0&source={source}")
            assert resp.status_code == 200

    def test_invalid_source_rejected(self):
        resp = client.get("/api/elevation/point?lat=42.0&lon=-1.0&source=invalid")
        assert resp.status_code == 400

    def test_missing_coords_returns_422(self):
        assert client.get("/api/elevation/point").status_code == 422
        assert client.get("/api/elevation/point?lat=42.0").status_code == 422
        assert client.get("/api/elevation/point?lon=-1.0").status_code == 422


class TestPointRefresh:

    @patch("app.api.elevation._get_redis", new_callable=AsyncMock)
    @patch("app.api.elevation.point_source_plan", create=True)
    def test_refresh_bypasses_cache(self, mock_plan, mock_redis):
        inst = AsyncMock()
        inst.get = AsyncMock(return_value='{"elevation_m": 999}')
        mock_redis.return_value = inst
        mock_plan.return_value = []

        resp = client.get("/api/elevation/point?lat=42.817&lon=-1.642&refresh=true")
        assert resp.status_code == 200
        inst.get.assert_not_called()  # refresh skipped the cache read

    @patch("app.api.elevation._get_redis", new_callable=AsyncMock)
    @patch("app.api.elevation.point_source_plan", create=True)
    def test_redis_unavailable_still_works(self, mock_plan, mock_redis):
        mock_redis.return_value = None  # Redis down
        mock_plan.return_value = []
        resp = client.get("/api/elevation/point?lat=42.817&lon=-1.642")
        assert resp.status_code == 200
        assert resp.json()["elevation_m"] is None
