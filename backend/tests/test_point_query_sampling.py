"""Unit tests for point-query sampling helpers in app.services.point_query.

These exercise the REAL raster reading path with in-memory GeoTIFF fixtures
(no rasterio.open mocking) — the brief's verification requirement #1.
"""

import io

import numpy as np
import pytest

pytest.importorskip("rasterio", reason="rasterio not installed (non-container env)")

import rasterio
from rasterio.transform import from_origin

from app.services import point_query


# ---------------------------------------------------------------------------
# Fixtures: build real GeoTIFF bytes in memory
# ---------------------------------------------------------------------------

def _tiff_bytes(values, transform, crs="EPSG:4326", nodata=None, dtype="int16"):
    height, width = values.shape
    buf = io.BytesIO()
    with rasterio.open(
        buf, "w", driver="GTiff", height=height, width=width, count=1,
        dtype=dtype, crs=crs, transform=transform, nodata=nodata,
    ) as dst:
        dst.write(values, 1)
    return buf.getvalue()


def test_copernicus_tile_path_nw_hemisphere():
    assert point_query.copernicus_tile_path(42.63, -2.07) == (
        "/vsis3/copernicus-dem-30m/Copernicus_DSM_COG_10_N42_00_W003_00_DEM/"
        "Copernicus_DSM_COG_10_N42_00_W003_00_DEM.tif"
    )


def test_copernicus_tile_path_sw_hemisphere():
    assert point_query.copernicus_tile_path(-0.5, -0.5) == (
        "/vsis3/copernicus-dem-30m/Copernicus_DSM_COG_10_S01_00_W001_00_DEM/"
        "Copernicus_DSM_COG_10_S01_00_W001_00_DEM.tif"
    )


def test_copernicus_tile_path_ne_hemisphere():
    assert point_query.copernicus_tile_path(51.5, 0.1) == (
        "/vsis3/copernicus-dem-30m/Copernicus_DSM_COG_10_N51_00_E000_00_DEM/"
        "Copernicus_DSM_COG_10_N51_00_E000_00_DEM.tif"
    )


def test_sample_tiff_point_reads_point_pixel_not_corner():
    """The value returned is the pixel CONTAINING the point, not arr[0, 0]."""
    arr = np.full((10, 10), 500, dtype="int16")
    arr[4, 4] = 1234  # point (0.045, -0.045) maps to row 4, col 4
    transform = from_origin(0.0, 0.0, 0.01, 0.01)  # west=0, north=0, 0.01 deg px
    content = _tiff_bytes(arr, transform)

    value = point_query.sample_tiff_point(content, lat=-0.045, lon=0.045)
    assert value == 1234.0


def test_sample_tiff_point_rejects_degenerate_transform():
    """Mimics the IGN 1x1 bug: pixel size of thousands of degrees -> None."""
    arr = np.array([[0]], dtype="int16")
    transform = from_origin(0.0, 10.0, 5616.89, 6065.17)  # garbage georeferencing
    content = _tiff_bytes(arr, transform)

    assert point_query.sample_tiff_point(content, lat=42.63, lon=-2.07) is None


def test_sample_tiff_point_rejects_point_outside_raster():
    arr = np.full((3, 3), 500, dtype="int16")
    transform = from_origin(0.0, 0.0, 0.01, 0.01)
    content = _tiff_bytes(arr, transform)

    # lon 5.0 is far outside a 0.03 deg-wide raster
    assert point_query.sample_tiff_point(content, lat=-0.01, lon=5.0) is None


def test_sample_tiff_point_rejects_nodata():
    arr = np.array([[0, 0], [0, -9999]], dtype="int16")
    transform = from_origin(0.0, 0.0, 0.01, 0.01)
    content = _tiff_bytes(arr, transform, nodata=-9999)

    # point maps to (row 1, col 1) == nodata
    assert point_query.sample_tiff_point(content, lat=-0.015, lon=0.015) is None


def test_sample_tiff_point_rejects_implausible_value():
    arr = np.array([[0, 0], [0, 30000]], dtype="int16")
    transform = from_origin(0.0, 0.0, 0.01, 0.01)
    content = _tiff_bytes(arr, transform)

    assert point_query.sample_tiff_point(content, lat=-0.015, lon=0.015) is None


def test_sample_copernicus_point_samples_from_vsis3(monkeypatch):
    class FakeDS:
        nodata = None

        def __init__(self):
            self.opened_path = None

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def sample(self, points):
            return [(572.83,)]

    fake = FakeDS()

    def fake_open(path, *a, **kw):
        fake.opened_path = path
        return fake

    monkeypatch.setattr(point_query.rasterio, "open", fake_open)

    value = point_query.sample_copernicus_point(42.63, -2.07)
    assert value == 572.83
    assert "copernicus-dem-30m" in fake.opened_path


def test_sample_copernicus_point_raises_on_nodata(monkeypatch):
    class FakeDS:
        nodata = -32768

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def sample(self, points):
            return [(-32768,)]

    monkeypatch.setattr(point_query.rasterio, "open", lambda path, *a, **kw: FakeDS())

    with pytest.raises(ValueError):
        point_query.sample_copernicus_point(42.63, -2.07)


def test_build_wcs_url_requests_3x3_grid():
    from app.dem_sources import get_source

    dem = get_source("ES")
    url = point_query.build_wcs_url(dem, 42.63, -2.07)
    assert "WIDTH=3" in url
    assert "HEIGHT=3" in url


# ---------------------------------------------------------------------------
# Source plan selection
# ---------------------------------------------------------------------------

from app.dem_sources import DEMSource  # noqa: E402

_ES = DEMSource("ES", "España", "https://es/wcs", "WCS", "GEOTIFFINT16",
                "25m", (-18.2, 27.6, 4.4, 43.8), layer_name="Elevacion4258_25")
_EU = DEMSource("EU", "Pan-European", "/vsis3/copernicus-dem-30m", "DOWNLOAD",
                "GeoTIFF", "30m", (-32.0, 27.0, 45.0, 72.0), fallback=True)


def _patch_sources(monkeypatch, national):
    monkeypatch.setattr(point_query, "resolve_source", lambda lat, lon: national)
    monkeypatch.setattr(
        point_query, "get_source",
        lambda code: {"EU": _EU, "ES": _ES}.get(code),
    )


def test_plan_auto_prefers_copernicus(monkeypatch):
    _patch_sources(monkeypatch, national=_ES)
    plan = point_query.point_source_plan("auto", 42.63, -2.07)
    assert [s.country_code for s in plan] == ["EU", "ES"]


def test_plan_national_prefers_national(monkeypatch):
    _patch_sources(monkeypatch, national=_ES)
    plan = point_query.point_source_plan("national", 42.63, -2.07)
    assert [s.country_code for s in plan] == ["ES", "EU"]


def test_plan_cnig_forces_spain(monkeypatch):
    _patch_sources(monkeypatch, national=_ES)
    plan = point_query.point_source_plan("cnig", 42.63, -2.07)
    assert [s.country_code for s in plan] == ["ES"]


def test_plan_copernicus_forces_eu(monkeypatch):
    _patch_sources(monkeypatch, national=None)
    plan = point_query.point_source_plan("copernicus", 42.63, -2.07)
    assert [s.country_code for s in plan] == ["EU"]


def test_plan_auto_without_national_uses_copernicus(monkeypatch):
    _patch_sources(monkeypatch, national=None)
    plan = point_query.point_source_plan("auto", 42.63, -2.07)
    assert [s.country_code for s in plan] == ["EU"]


def test_plan_unknown_source_raises(monkeypatch):
    _patch_sources(monkeypatch, national=None)
    with pytest.raises(ValueError):
        point_query.point_source_plan("wms", 42.63, -2.07)


def test_plan_auto_outside_eu_bbox_is_empty(monkeypatch):
    _patch_sources(monkeypatch, national=None)
    plan = point_query.point_source_plan("auto", 0.0, 0.0)  # Gulf of Guinea
    assert plan == []
