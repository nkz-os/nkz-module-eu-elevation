"""/raster: Copernicus fallback, source selection and explicit no-data mode.

The WCS is mocked; Copernicus 1-degree COGs are replaced by small local
GeoTIFFs (a missing cell is a path that does not exist, as in the bucket).
"""
import asyncio
import io
import math
import time
from unittest.mock import AsyncMock, patch

import httpx
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_bounds

from app.api import elevation
from app.services import raster_source
from app.services.raster_source import choose_raster_source, read_copernicus_grid

PX = 120  # source pixels per degree in the synthetic cells


def _ramp(lat_i, lon_i):
    """Smooth ramp: value depends only on position, identical across cells."""
    lons = lon_i + (np.arange(PX) + 0.5) / PX
    lats = lat_i + 1 - (np.arange(PX) + 0.5) / PX
    return (1000.0 + 100.0 * (lats[:, None] - 40.0) + 10.0 * (lons[None, :] + 10.0)).astype("float32")


def _write_cell(dirpath, lat_i, lon_i):
    path = dirpath / f"{lat_i}_{lon_i}.tif"
    with rasterio.open(
        path, "w", driver="GTiff", height=PX, width=PX, count=1, dtype="float32",
        crs="EPSG:4326",
        transform=from_bounds(lon_i, lat_i, lon_i + 1, lat_i + 1, PX, PX),
    ) as dst:
        dst.write(_ramp(lat_i, lon_i), 1)
    return path


@pytest.fixture
def cells(tmp_path):
    """Install fake Copernicus cells; returns a function declaring which exist."""
    present = set()

    def paths_for(bbox):
        west, south, east, north = bbox
        out = []
        for lat_i in range(math.floor(south), math.ceil(north)):
            for lon_i in range(math.floor(west), math.ceil(east)):
                out.append(str(tmp_path / (f"{lat_i}_{lon_i}.tif" if (lat_i, lon_i) in present
                                           else "missing.tif")))
        return out

    def declare(*cell_list):
        for lat_i, lon_i in cell_list:
            _write_cell(tmp_path, lat_i, lon_i)
            present.add((lat_i, lon_i))

    with patch.object(raster_source, "covering_copernicus_paths", paths_for):
        yield declare


def _tiff(arr, bbox, nodata=None, dtype="int16"):
    h, w = arr.shape
    buf = io.BytesIO()
    with rasterio.open(
        buf, "w", driver="GTiff", height=h, width=w, count=1, dtype=dtype,
        crs="EPSG:4326", transform=from_bounds(*bbox, w, h), nodata=nodata,
    ) as dst:
        dst.write(arr.astype(dtype), 1)
    return buf.getvalue()


def _wcs_returning(value=300, nodata=None, cell_value=None):
    async def fake(source, bbox, width, height):
        arr = np.full((height, width), value)
        if cell_value is not None:
            arr[0, 0] = cell_value
        return _tiff(arr, bbox, nodata=nodata)
    return AsyncMock(side_effect=fake)


def _call(bbox, resolution_m, nodata="zero"):
    return asyncio.run(elevation.get_elevation_raster(
        min_lon=bbox[0], min_lat=bbox[1], max_lon=bbox[2], max_lat=bbox[3],
        resolution_m=resolution_m, purpose=elevation.PurposeEnum.auto,
        nodata=elevation.NodataEnum(nodata), _tenant_id="acme",
    ))


ES_BBOX = (-1.645, 42.812, -1.635, 42.822)
# Same width/height in degrees, placed in Germany.
DE_BBOX = (11.355, 48.312, 11.365, 48.322)
GEOMETRY_KEYS = ("cols", "rows", "pixel_size_deg", "row_order")


# --- Review focus 1: DE gets Copernicus with the same grid geometry -------------

def test_de_uses_copernicus_same_geometry_as_es(cells):
    cells((48, 11))
    wcs = _wcs_returning(300)
    with patch.object(elevation, "_wcs_bbox_query", wcs):
        es = _call(ES_BBOX, 10)
        de = _call(DE_BBOX, 10)
    assert es["source"]["fallback_reason"] is None
    assert de["source"]["fallback_reason"] == "national_coarser"
    assert de["source"]["id"] == "builtin:EU"
    assert de["source"]["category"] == "copernicus_s3"
    assert "Copernicus" in de["source"]["attribution"]
    assert wcs.await_count == 1  # only ES hit the WCS
    for k in GEOMETRY_KEYS:
        assert de[k] == pytest.approx(es[k]), k
    assert len(de["elevations"]) == len(es["elevations"]) == de["rows"]
    assert all(len(r) == de["cols"] for r in de["elevations"])
    assert de["north_lat"] - de["origin_lat"] == pytest.approx(es["north_lat"] - es["origin_lat"])
    assert de["nodata_count"] == 0
    # north-first: ramp rises to the north, so row 0 is the highest
    assert de["elevations"][0][0] > de["elevations"][-1][0]


def test_copernicus_grid_exact_shape_without_fitting(cells):
    cells((50, 9), (50, 10), (51, 9), (51, 10))
    with patch.object(raster_source, "_fit", side_effect=AssertionError("shape drifted")):
        for res in (10, 25, 30, 90, 250):
            for bbox in (
                (9.355, 50.312, 9.365, 50.322),
                (9.9, 50.9, 10.1, 51.1),
                (9.0, 50.0, 10.0, 51.0),
                (9.123, 50.456, 9.789, 50.987),
            ):
                px = res / 111320.0
                cols = max(2, int(round((bbox[2] - bbox[0]) / px)))
                rows = max(2, int(round((bbox[3] - bbox[1]) / px)))
                if cols * rows > 4_000_000:
                    continue
                g = read_copernicus_grid(bbox, cols, rows)
                assert g.shape == (rows, cols), (res, bbox)


# --- Review focus 2: national failure falls back, no 502 ---------------------

@pytest.mark.parametrize("exc", [
    httpx.ReadTimeout("t"),
    httpx.HTTPStatusError("503", request=httpx.Request("GET", "http://x.invalid"),
                          response=httpx.Response(503)),
    ValueError("parse"),
])
def test_national_error_falls_back_to_copernicus(cells, exc):
    cells((42, -2), (42, -1))
    with patch.object(elevation, "_wcs_bbox_query", AsyncMock(side_effect=exc)):
        r = _call((-1.1, 42.4, -1.09, 42.41), 10)
    assert r["source"]["id"] == "builtin:EU"
    assert r["source"]["fallback_reason"] == "national_error"
    assert r["nodata_count"] == 0


def test_national_and_copernicus_both_fail_is_502(cells):
    with patch.object(elevation, "_wcs_bbox_query", AsyncMock(side_effect=httpx.ReadTimeout("t"))), \
         patch.object(raster_source, "read_copernicus_grid", side_effect=RuntimeError("boom")):
        with pytest.raises(elevation.HTTPException) as ei:
            _call(ES_BBOX, 10)
    assert ei.value.status_code == 502


def test_nothing_covers_centre_is_404():
    with pytest.raises(elevation.HTTPException) as ei:
        _call((0.0, 10.0, 0.01, 10.01), 10)
    assert ei.value.status_code == 404


# --- Source selection ---------------------------------------------------------

def test_choose_source():
    es, reason = choose_raster_source(42.8, -1.6, 25)
    assert (es.country_code, reason) == ("ES", None)
    es5, reason = choose_raster_source(42.8, -1.6, 5)
    assert (es5.country_code, reason) == ("ES", None)
    de, reason = choose_raster_source(48.3, 11.3, 10)
    assert (de.country_code, reason) == ("EU", "national_coarser")
    fr, reason = choose_raster_source(47.0, 2.0, 10)  # FR is WMS: not raster-capable
    assert (fr.country_code, reason) == ("EU", "national_unsupported")
    shadowed, reason = choose_raster_source(50.3, 9.36, 10)  # western DE, inside FR's bbox
    assert (shadowed.country_code, reason) == ("EU", "national_unsupported")
    sea, reason = choose_raster_source(40.0, -20.0, 10)
    assert (sea.country_code, reason) == ("EU", "no_national")
    assert choose_raster_source(10.0, 0.0, 10) == (None, None)


# --- Review focus 3: no-data ----------------------------------------------------

def test_half_sea_nodata_null_and_zero(cells):
    cells((52, 8))  # 52..53 exists, 53..54 missing (sea)
    bbox = (8.4, 52.99, 8.41, 53.01)
    nul = _call(bbox, 100, nodata="null")
    zero = _call(bbox, 100)
    rows, cols = nul["rows"], nul["cols"]
    n_none = sum(v is None for r in nul["elevations"] for v in r)
    assert n_none == nul["nodata_count"] > 0
    assert nul["nodata_count"] < rows * cols
    assert all(v is None for v in nul["elevations"][0])      # north = sea
    assert all(v is not None for v in nul["elevations"][-1])  # south = land
    assert zero["nodata_count"] == nul["nodata_count"]
    assert all(v == 0 for v in zero["elevations"][0])
    assert not any(v is None for r in zero["elevations"] for v in r)
    # land values are identical in both modes
    assert zero["elevations"][-1] == nul["elevations"][-1]


def test_declared_nodata_in_national_response():
    wcs = _wcs_returning(300, nodata=-9999, cell_value=-9999)
    with patch.object(elevation, "_wcs_bbox_query", wcs):
        nul = _call(ES_BBOX, 10, nodata="null")
        zero = _call(ES_BBOX, 10)
    assert nul["elevations"][0][0] is None
    assert nul["nodata_count"] == 1
    assert zero["elevations"][0][0] == 0
    assert zero["nodata_count"] == 1
    assert nul["elevations"][0][1] == 300


def test_invalid_nodata_value_rejected():
    from fastapi.testclient import TestClient
    from app.main import app
    r = TestClient(app).get("/api/elevation/raster", params={
        "min_lon": -1.645, "min_lat": 42.812, "max_lon": -1.635, "max_lat": 42.822,
        "nodata": "bogus"})
    assert r.status_code == 422


# --- Review focus 4: ES regression ----------------------------------------------

@pytest.mark.parametrize("res,layer", [(5, "Elevacion4258_5"), (25, "Elevacion4258_25")])
def test_es_unchanged_layer_and_values(res, layer):
    seen = {}

    async def fake(source, bbox, width, height):
        seen["layer"] = source["layer_name"]
        return _tiff(np.full((height, width), 612), bbox)

    with patch.object(elevation, "_wcs_bbox_query", AsyncMock(side_effect=fake)):
        r = _call(ES_BBOX, res)
    assert seen["layer"] == layer
    assert r["source"]["id"] == "builtin:ES"
    assert r["source"]["fallback_reason"] is None
    assert r["source"]["attribution"] is None
    assert r["nodata_count"] == 0
    assert all(v == 612 for row in r["elevations"] for v in row)
    assert all(isinstance(v, int) for row in r["elevations"] for v in row)


# --- Review focus 5: no seam across 1-degree tile boundaries --------------------

@pytest.mark.parametrize("res", [10, 30, 90])
def test_no_seam_across_tile_boundaries(cells, res):
    cells((50, 9), (50, 10), (51, 9), (51, 10))
    bbox = (9.995, 50.995, 10.005, 51.005)  # straddles both the lon and lat edges
    r = _call(bbox, res, nodata="null")
    g = np.array(r["elevations"], dtype=float)
    assert r["nodata_count"] == 0 and not np.isnan(g).any()
    assert g.shape == (r["rows"], r["cols"])
    # ramp: values fall west->east? rise with lon, rise with lat (row 0 = north)
    d_col = np.diff(g, axis=1)
    d_row = np.diff(g, axis=0)
    src_step_lon = 10.0 / PX
    src_step_lat = 100.0 / PX
    assert (d_col >= -1e-6).all() and d_col.max() <= src_step_lon + 0.02
    assert (d_row <= 1e-6).all() and -d_row.min() <= src_step_lat + 0.02


# --- National zero-mask against Copernicus ----------------------------------------

def _wcs_with_zero_cols(zero_frac, value=300):
    """National grid whose first `zero_frac` of columns is exact 0, no declared nodata."""
    async def fake(source, bbox, width, height):
        arr = np.full((height, width), value)
        arr[:, : int(round(width * zero_frac))] = 0
        return _tiff(arr, bbox)
    return AsyncMock(side_effect=fake)


def test_mostly_zero_national_serves_copernicus(cells):
    cells((42, -2))
    wcs = _wcs_with_zero_cols(1.0)
    bbox = (-1.5, 42.4, -1.49, 42.41)
    with patch.object(elevation, "_wcs_bbox_query", wcs):
        for mode in ("zero", "null"):
            r = _call(bbox, 10, nodata=mode)
            assert r["source"]["id"] == "builtin:EU"
            assert r["source"]["fallback_reason"] == "national_no_coverage"
            assert r["nodata_count"] == 0
            assert all(v is not None and v > 500 for row in r["elevations"] for v in row)


def test_border_partial_zero_null_only_in_null_mode(cells):
    cells((42, -2))
    bbox = (-1.5, 42.4, -1.49, 42.41)
    with patch.object(elevation, "_wcs_bbox_query", _wcs_with_zero_cols(0.3)):
        nul = _call(bbox, 10, nodata="null")
        zero = _call(bbox, 10)
    cols = nul["cols"]
    k = int(round(cols * 0.3))
    assert nul["source"]["id"] == "builtin:ES"
    assert all(row[:k] == [None] * k and row[k:] == [300] * (cols - k) for row in nul["elevations"])
    assert nul["nodata_count"] == k * nul["rows"]
    assert all(row[:k] == [0] * k for row in zero["elevations"])
    assert zero["nodata_count"] == 0


def test_coastal_zero_over_missing_copernicus_is_null(cells):
    cells()  # no Copernicus cell at all: sea
    bbox = (-1.5, 42.4, -1.49, 42.41)
    with patch.object(elevation, "_wcs_bbox_query", _wcs_with_zero_cols(0.3)):
        nul = _call(bbox, 10, nodata="null")
        zero = _call(bbox, 10)
    k = int(round(nul["cols"] * 0.3))
    assert nul["source"]["id"] == "builtin:ES"
    assert all(row[:k] == [None] * k for row in nul["elevations"])
    assert nul["nodata_count"] == k * nul["rows"]
    assert all(row[:k] == [0] * k for row in zero["elevations"])


def test_mask_read_failure_keeps_national_grid(cells):
    bbox = (-1.5, 42.4, -1.49, 42.41)
    with patch.object(elevation, "_wcs_bbox_query", _wcs_with_zero_cols(1.0)), \
         patch.object(raster_source, "read_copernicus_grid", side_effect=RuntimeError("s3")):
        r = _call(bbox, 10, nodata="null")
    assert r["source"]["id"] == "builtin:ES"
    assert r["source"]["fallback_reason"] is None
    assert r["nodata_count"] == 0
    assert all(v == 0 for row in r["elevations"] for v in row)


def test_no_zero_cells_no_copernicus_call():
    with patch.object(elevation, "_wcs_bbox_query", _wcs_returning(612)), \
         patch.object(raster_source, "read_copernicus_grid", side_effect=AssertionError("called")) as cop:
        r = _call(ES_BBOX, 10, nodata="null")
    assert cop.call_count == 0
    assert r["source"]["id"] == "builtin:ES"


# --- Deadline ---------------------------------------------------------------------

def test_default_budgets():
    assert elevation.REQUEST_DEADLINE_S == 12.0
    assert elevation.NATIONAL_TIMEOUT_S == 6.0


def test_slow_national_gets_copernicus_within_budget(cells):
    cells((42, -2))

    async def slow(source, bbox, width, height):
        await asyncio.sleep(30)

    with patch.object(elevation, "NATIONAL_TIMEOUT_S", 0.3), \
         patch.object(elevation, "_wcs_bbox_query", AsyncMock(side_effect=slow)):
        t = time.monotonic()
        r = _call((-1.5, 42.4, -1.49, 42.41), 10)
        took = time.monotonic() - t
    assert r["source"]["fallback_reason"] == "national_error"
    assert took < 3.0


def test_slow_copernicus_errors_within_budget(cells):
    def slow(*a, **k):
        time.sleep(2.0)

    async def timed():
        t = time.monotonic()
        try:
            await elevation.get_elevation_raster(
                min_lon=DE_BBOX[0], min_lat=DE_BBOX[1], max_lon=DE_BBOX[2], max_lat=DE_BBOX[3],
                resolution_m=10, purpose=elevation.PurposeEnum.auto,
                nodata=elevation.NodataEnum.zero, _tenant_id="acme")
        except elevation.HTTPException as e:
            return e, time.monotonic() - t
        raise AssertionError("expected an error")

    with patch.object(elevation, "REQUEST_DEADLINE_S", 0.5), \
         patch.object(raster_source, "read_copernicus_grid", side_effect=slow):
        # the worker thread is abandoned on timeout; asyncio.run joining it at
        # shutdown is a test artifact, so time inside the loop.
        err, took = asyncio.run(timed())
    assert err.status_code == 504
    assert took < 1.0


def test_slow_mask_read_keeps_national_grid(cells):
    def slow(*a, **k):
        time.sleep(2.0)

    with patch.object(elevation, "REQUEST_DEADLINE_S", 0.5), \
         patch.object(elevation, "_wcs_bbox_query", _wcs_with_zero_cols(1.0)), \
         patch.object(raster_source, "read_copernicus_grid", side_effect=slow):
        r = _call((-1.5, 42.4, -1.49, 42.41), 10)
    assert r["source"]["id"] == "builtin:ES"
    assert all(v == 0 for row in r["elevations"] for v in row)


# --- Exact-0.0 sea inside a coastal Copernicus tile -------------------------------

def _write_coastal_cell(dirpath, lat_i, lon_i):
    arr = _ramp(lat_i, lon_i)
    arr[: PX // 2, :] = 0.0  # northern half is sea, flattened to exactly 0.0
    with rasterio.open(
        dirpath / f"{lat_i}_{lon_i}.tif", "w", driver="GTiff", height=PX, width=PX, count=1,
        dtype="float32", crs="EPSG:4326",
        transform=from_bounds(lon_i, lat_i, lon_i + 1, lat_i + 1, PX, PX),
    ) as dst:
        dst.write(arr, 1)


@pytest.fixture
def coastal(tmp_path):
    for cell in ((48, 11), (42, -2)):
        _write_coastal_cell(tmp_path, *cell)

    def paths_for(bbox):
        return [str(tmp_path / f"{math.floor(bbox[1])}_{math.floor(bbox[0])}.tif")]

    with patch.object(raster_source, "covering_copernicus_paths", paths_for):
        yield


def test_copernicus_exact_zero_sea_is_nodata(coastal):
    bbox = (11.4, 48.45, 11.41, 48.55)  # straddles the sea/land line at 48.5
    nul = _call(bbox, 100, nodata="null")
    zero = _call(bbox, 100)
    n_none = sum(v is None for r in nul["elevations"] for v in r)
    assert n_none == nul["nodata_count"] > 0
    assert nul["elevations"][0][0] is None and nul["elevations"][-1][0] is not None
    assert zero["nodata_count"] == nul["nodata_count"]  # same meaning in both modes
    assert zero["elevations"][0][0] == 0 and not any(v is None for r in zero["elevations"] for v in r)


def test_national_zero_over_copernicus_sea_is_null(coastal):
    bbox = (-1.5, 42.6, -1.49, 42.7)  # inside the all-0.0 half of the (42, -2)... see below
    # cell (42,-2) spans lon -2..-1, lat 42..43; sea = lat > 42.5
    with patch.object(elevation, "_wcs_bbox_query", _wcs_with_zero_cols(0.3)):
        nul = _call(bbox, 100, nodata="null")
        zero = _call(bbox, 100)
    k = int(round(nul["cols"] * 0.3))
    assert nul["source"]["id"] == "builtin:ES"
    assert all(row[:k] == [None] * k for row in nul["elevations"])
    assert nul["nodata_count"] == k * nul["rows"]
    assert all(row[:k] == [0] * k for row in zero["elevations"])


# --- Zero-mask gate (M-6) ---------------------------------------------------------

def test_zero_mode_below_half_makes_no_mask_read():
    with patch.object(elevation, "_wcs_bbox_query", _wcs_with_zero_cols(0.49)), \
         patch.object(raster_source, "read_copernicus_grid", side_effect=AssertionError("called")) as cop:
        r = _call((-1.5, 42.4, -1.49, 42.41), 10)
    assert cop.call_count == 0
    assert r["source"]["id"] == "builtin:ES"


def test_zero_mode_above_half_reads_and_serves_copernicus(cells):
    cells((42, -2))
    with patch.object(elevation, "_wcs_bbox_query", _wcs_with_zero_cols(0.6)):
        r = _call((-1.5, 42.4, -1.49, 42.41), 10)
    assert r["source"]["id"] == "builtin:EU"
    assert r["source"]["fallback_reason"] == "national_no_coverage"


def test_declared_nodata_with_zeros_makes_no_mask_read():
    async def fake(source, bbox, width, height):
        arr = np.zeros((height, width))
        return _tiff(arr, bbox, nodata=-9999)

    with patch.object(elevation, "_wcs_bbox_query", AsyncMock(side_effect=fake)), \
         patch.object(raster_source, "read_copernicus_grid", side_effect=AssertionError("called")) as cop:
        _call(ES_BBOX, 10, nodata="null")
    assert cop.call_count == 0


def test_shape_mismatch_skips_mask_read():
    async def fake(source, bbox, width, height):
        return _tiff(np.zeros((3, 3)), bbox)

    with patch.object(elevation, "_wcs_bbox_query", AsyncMock(side_effect=fake)), \
         patch.object(raster_source, "read_copernicus_grid", side_effect=AssertionError("called")) as cop:
        r = _call(ES_BBOX, 10, nodata="null")
    assert cop.call_count == 0
    assert r["source"]["id"] == "builtin:ES"


# --- Size guard (I-4) ---------------------------------------------------------------

def test_copernicus_branch_rejects_huge_grid():
    with patch.object(raster_source, "read_copernicus_grid", side_effect=AssertionError("called")):
        with pytest.raises(elevation.HTTPException) as ei:
            _call((11.0, 48.0, 12.0, 49.0), 10)  # ~124 M cells
    assert ei.value.status_code == 400
    assert ei.value.detail["error"] == "grid_too_large"


def test_national_error_with_huge_grid_is_400():
    with patch.object(elevation, "_wcs_bbox_query", AsyncMock(side_effect=httpx.ReadTimeout("t"))):
        with pytest.raises(elevation.HTTPException) as ei:
            _call((-2.0, 42.0, -1.0, 43.0), 10)
    assert ei.value.status_code == 400


# --- Admission and dedicated executor ---------------------------------------------

def test_ninth_concurrent_request_gets_503_with_retry_after(cells):
    release = None

    async def run():
        nonlocal release
        release = asyncio.Event()

        async def held(source, bbox, width, height):
            await release.wait()
            return _tiff(np.full((height, width), 300), bbox)

        def call():
            return elevation.get_elevation_raster(
                min_lon=ES_BBOX[0], min_lat=ES_BBOX[1], max_lon=ES_BBOX[2], max_lat=ES_BBOX[3],
                resolution_m=10, purpose=elevation.PurposeEnum.auto,
                nodata=elevation.NodataEnum.zero, _tenant_id="acme")

        with patch.object(elevation, "_wcs_bbox_query", AsyncMock(side_effect=held)):
            tasks = [asyncio.create_task(call()) for _ in range(elevation.RASTER_MAX_INFLIGHT)]
            await asyncio.sleep(0.05)
            with pytest.raises(elevation.HTTPException) as ei:
                await call()
            release.set()
            done = await asyncio.gather(*tasks)
        return ei.value, done

    err, done = asyncio.run(run())
    assert err.status_code == 503
    assert err.detail["error"] == "raster_busy"
    assert err.headers["Retry-After"] == "5"
    assert len(done) == 8 and all(d["source"]["id"] == "builtin:ES" for d in done)
    # all slots were returned
    assert not elevation._raster_slots.locked()


def test_slot_released_after_timeout(cells):
    def slow(*a, **k):
        time.sleep(1.0)

    with patch.object(elevation, "REQUEST_DEADLINE_S", 0.3), \
         patch.object(raster_source, "read_copernicus_grid", side_effect=slow):
        with pytest.raises(elevation.HTTPException) as ei:
            _call(DE_BBOX, 10)
    assert ei.value.status_code == 504
    assert not elevation._raster_slots.locked()
    assert elevation._raster_slots._value == elevation.RASTER_MAX_INFLIGHT


def test_raster_reads_use_dedicated_executor_not_default(cells):
    cells((48, 11))

    async def run():
        loop = asyncio.get_running_loop()
        default_calls = []
        orig = loop.run_in_executor

        def spy(executor, fn, *args):
            default_calls.append(executor)
            return orig(executor, fn, *args)

        loop.run_in_executor = spy
        try:
            await elevation.get_elevation_raster(
                min_lon=DE_BBOX[0], min_lat=DE_BBOX[1], max_lon=DE_BBOX[2], max_lat=DE_BBOX[3],
                resolution_m=10, purpose=elevation.PurposeEnum.auto,
                nodata=elevation.NodataEnum.zero, _tenant_id="acme")
        finally:
            del loop.run_in_executor
        return default_calls

    executors = asyncio.run(run())
    assert executors and all(e is elevation._RASTER_EXECUTOR for e in executors)
    assert elevation._RASTER_EXECUTOR._max_workers == elevation.RASTER_MAX_WORKERS == 4
