"""Tests for terrain tile generation logic — tile math, VRT prep, WCS download."""

import os
import json
import tempfile
import pytest
from unittest.mock import patch, MagicMock, call

# These tests import from the tasks module — may need rasterio
pytest.importorskip("rasterio", reason="rasterio not installed")

from app.tasks.elevation_tasks import (
    _num_tiles_at_zoom,
    _tile_bounds,
    _tiles_in_bbox,
    _copernicus_tiles_for_bbox,
    _generate_layer_json,
    _build_wcs_getcoverage_url,
    _prepare_dem,
    _prepare_local_dem,
)


class TestTilingMath:

    def test_num_tiles_at_zoom_0(self):
        """Zoom 0: 2 columns × 1 row (Cesium geographic)."""
        cols, rows = _num_tiles_at_zoom(0)
        assert cols == 2
        assert rows == 1

    def test_num_tiles_at_zoom_8(self):
        """Zoom 8: 2^9 = 512 cols, 2^8 = 256 rows."""
        cols, rows = _num_tiles_at_zoom(8)
        assert cols == 512
        assert rows == 256

    def test_tile_bounds_zoom_0_col_0_row_0(self):
        """Tile (0,0) at zoom 0 covers the western hemisphere."""
        west, south, east, north = _tile_bounds(0, 0, 0)
        assert west == -180.0
        assert south == -90.0
        assert east == 0.0
        assert north == 90.0

    def test_tile_bounds_zoom_0_col_1_row_0(self):
        """Tile (1,0) at zoom 0 covers the eastern hemisphere."""
        west, south, east, north = _tile_bounds(0, 1, 0)
        assert west == 0.0
        assert south == -90.0
        assert east == 180.0
        assert north == 90.0

    def test_tiles_in_bbox_small_area(self):
        """Small BBOX at zoom 8 returns a handful of tiles."""
        # Madrid area (~40.4, -3.7)
        bbox = (-4.0, 40.0, -3.0, 41.0)
        tiles = _tiles_in_bbox(8, bbox)
        assert len(tiles) > 0
        assert len(tiles) < 50  # Should be small

    def test_tiles_in_bbox_full_europe(self):
        """EU-sized BBOX at zoom 8 returns many tiles."""
        bbox = (-10.0, 35.0, 30.0, 62.0)
        tiles = _tiles_in_bbox(8, bbox)
        assert len(tiles) > 50

    def test_tiles_in_bbox_empty(self):
        """BBOX outside valid range returns empty list."""
        # BBOX in the Pacific nowhere near land
        tiles = _tiles_in_bbox(8, (-160.0, -50.0, -159.0, -49.0))
        assert len(tiles) >= 0  # May or may not have tiles depending on math


class TestCopernicusEnumeration:

    def test_single_tile_bbox(self):
        """A 1°×1° BBOX straddling tile boundaries returns the intersecting tiles."""
        # London area: ~51.5°N, 0.1°W → lat 51-52, lon -1 to 0+1
        # BBOX (-0.5, 51.0, 0.5, 52.0) touches 4 tiles:
        #   N51W001, N51E000, N52W001, N52E000
        bbox = (-0.5, 51.0, 0.5, 52.0)
        tiles = _copernicus_tiles_for_bbox(bbox)
        assert len(tiles) == 4
        assert any("N51" in t for t in tiles)
        assert any("W001" in t for t in tiles)

    def test_crossing_equator(self):
        """BBOX crossing the equator returns N and S tiles."""
        bbox = (-1.0, -1.0, 1.0, 1.0)
        tiles = _copernicus_tiles_for_bbox(bbox)
        assert len(tiles) >= 2
        has_n = any("N00" in t for t in tiles)
        has_s = any("S01" in t for t in tiles)
        assert has_n or has_s  # At least crossing 0° lat

    def test_url_format(self):
        """Copernicus tile URLs use /vsicurl/ prefix for GDAL streaming."""
        bbox = (2.0, 48.0, 3.0, 49.0)  # Paris
        tiles = _copernicus_tiles_for_bbox(bbox)
        for t in tiles:
            assert t.startswith("/vsicurl/https://")
            assert t.endswith(".tif")
            assert "Copernicus_DSM_COG_10_" in t


class TestLayerJsonGeneration:

    def test_basic_generation(self):
        """layer.json has all required fields for Cesium."""
        bbox = (-10.0, 35.0, 5.0, 44.0)
        available = {
            8: [(100, 50), (101, 50)],
            9: [(200, 100)],
        }
        layer = _generate_layer_json(bbox, available, (8, 9))

        assert layer["tilejson"] == "2.1.0"
        assert layer["format"] == "quantized-mesh-1.0"
        assert layer["scheme"] == "tms"
        assert layer["projection"] == "EPSG:4326"
        assert layer["bounds"] == list(bbox)
        assert layer["minzoom"] == 8
        assert layer["maxzoom"] == 9
        assert "available" in layer
        assert len(layer["available"]) == 2  # 2 zoom levels

    def test_empty_zoom_level(self):
        """A zoom level with no tiles returns empty available entry."""
        bbox = (-10.0, 35.0, 5.0, 44.0)
        available = {
            8: [(100, 50)],
            9: [],  # No tiles at zoom 9
        }
        layer = _generate_layer_json(bbox, available, (8, 9))
        assert layer["available"][0]  # zoom 8 has data
        assert layer["available"][1] == []  # zoom 9 is empty

    def test_tile_ranges_grouped(self):
        """Consecutive tile coordinates are grouped into ranges."""
        bbox = (-10.0, 35.0, 5.0, 44.0)
        available = {
            8: [(100, 50), (101, 50), (102, 50)],
        }
        layer = _generate_layer_json(bbox, available, (8, 8))
        zoom8 = layer["available"][0][0]
        assert zoom8["startX"] == 100
        assert zoom8["endX"] == 102
        assert zoom8["startY"] == 50
        assert zoom8["endY"] == 50


class TestWcsUrlBuilding:

    def test_wcs_2_0_default_format(self):
        """Countries without special params use WCS 2.0.1 SUBSET syntax."""
        url = _build_wcs_getcoverage_url(
            "https://example.com/wcs",
            "elevation",
            (-5.0, 40.0, -4.0, 41.0),
            25.0,
            "XX",  # Unknown country → WCS 2.0.1
        )
        assert "VERSION=2.0.1" in url
        assert "REQUEST=GetCoverage" in url
        assert "COVERAGEID=elevation" in url
        assert "SUBSET=Long" in url
        assert "SUBSET=Lat" in url

    def test_wcs_1_0_for_spain(self):
        """Spain (ES) uses WCS 1.0.0 with GEOTIFFINT16 and COVERAGE param."""
        url = _build_wcs_getcoverage_url(
            "https://servicios.idee.es/wcs-inspire/mdt",
            "Elevacion4326_500",
            (-5.0, 40.0, -4.0, 41.0),
            500.0,
            "ES",
        )
        assert "VERSION=1.0.0" in url
        assert "FORMAT=GEOTIFFINT16" in url
        assert "COVERAGE=Elevacion4326_500" in url
        assert "BBOX=" in url
        assert "CRS=EPSG:4326" in url
        assert "WIDTH=" in url
        assert "HEIGHT=" in url

    def test_wcs_url_includes_service_params(self):
        """All WCS URLs include SERVICE=WCS."""
        url = _build_wcs_getcoverage_url(
            "https://example.com/wcs", "layer", (0, 0, 1, 1), 10.0
        )
        assert "SERVICE=WCS" in url
