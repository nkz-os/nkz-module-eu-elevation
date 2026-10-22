"""Integration tests for elevation API with purpose parameter.

Requires full environment (rasterio, gdal, httpx).
Run inside the Docker container: docker compose exec backend pytest tests/test_elevation_api.py
"""
import pytest

pytest.importorskip("rasterio", reason="rasterio not installed (non-container env)")
pytest.importorskip("app.main", reason="app.main import failed (missing deps)")

from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


class TestPointEndpoint:

    @patch("app.api.elevation._get_redis", new_callable=AsyncMock)
    @patch("rasterio.open")
    @patch("httpx.AsyncClient.get")
    @patch("app.api.elevation.resolve_source")
    def test_point_with_purpose_param_accepted(self, mock_resolve, mock_get, mock_rasterio_open, mock_redis):
        """Point endpoint accepts the 'purpose' query parameter."""
        mock_redis.return_value = None
        # Must return a valid DEMSource (not None) or endpoint returns 404
        from app.dem_sources import DEMSource
        mock_resolve.return_value = DEMSource(
            country_code="ES", country_name="España",
            service_url="https://test.example.com/wcs",
            service_type="WCS", format="GEOTIFFINT16",
            resolution="500m", bbox=(-18.2, 27.6, 4.4, 43.8),
            layer_name="Elevacion4326_500",
        )
        # Mock HTTP response
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.content = b'fake_tiff_bytes'
        mock_get.return_value = mock_resp
        # Mock rasterio: return synthetic elevation value
        mock_dataset = MagicMock()
        mock_dataset.read.return_value = MagicMock()
        mock_dataset.__getitem__.return_value = 642.5  # elevation_m in meters
        mock_dataset.__enter__.return_value = mock_dataset
        mock_rasterio_open.return_value = mock_dataset

        resp = client.get("/api/elevation/point?lat=42.817&lon=-1.642&purpose=auto")
        assert resp.status_code == 200
        data = resp.json()
        assert "elevation_m" in data

    @patch("app.api.elevation._get_redis", new_callable=AsyncMock)
    def test_point_without_purpose_defaults_to_auto(self, mock_redis):
        """Purpose defaults to 'auto' when not provided."""
        mock_redis.return_value = None
        resp = client.get("/api/elevation/point?lat=42.817&lon=-1.642")
        assert resp.status_code == 200

    def test_point_invalid_purpose_rejected(self):
        """Invalid purpose values return 422 (validation error)."""
        resp = client.get("/api/elevation/point?lat=42.0&lon=-1.0&purpose=unknown")
        assert resp.status_code in (400, 422)

    @patch("app.api.elevation._get_redis", new_callable=AsyncMock)
    def test_point_response_includes_source_object(self, mock_redis):
        """Response contains a 'source' object (not a string) with metadata."""
        mock_redis.return_value = None
        resp = client.get("/api/elevation/point?lat=42.817&lon=-1.642&purpose=auto")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data.get("source"), dict)
        assert "id" in data["source"]


class TestRasterEndpoint:

    def test_raster_purpose_param_accepted(self):
        """Raster endpoint accepts the 'purpose' query parameter."""
        resp = client.get(
            "/api/elevation/raster?min_lon=-1.65&min_lat=42.81"
            "&max_lon=-1.63&max_lat=42.83&purpose=routing"
        )
        assert resp.status_code in (200, 404, 502)

    def test_raster_missing_bbox_returns_422(self):
        """Missing bbox params return validation error."""
        resp = client.get("/api/elevation/raster?purpose=auto")
        assert resp.status_code == 422
