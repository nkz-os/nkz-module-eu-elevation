"""Tests for SourceRegistry — elev source discovery + filtering."""
import pytest
import time
from unittest.mock import AsyncMock, patch, MagicMock
from app.services.source_registry import SourceRegistry, SourceEntry
from app.dem_sources import DEMSource


def make_dem_source(country_code="ES", resolution="5m", bbox=(-10.0, 35.0, 5.0, 44.0)):
    return DEMSource(
        country_code=country_code,
        country_name="Spain",
        service_url="https://example.com/wcs",
        service_type="WCS",
        format="GeoTIFF",
        resolution=resolution,
        bbox=bbox,
        layer_name="elevation",
    )


def make_orion_source(
    source_id="urn:ngsi-ld:ElevationSource:test-1",
    category="lidar_dtm",
    is_bare_earth=True,
    accuracy_v=0.30,
    priority=50,
    tenant_id=None,
    location=(-2.0, 42.0, -1.0, 43.0),  # (min_lon, min_lat, max_lon, max_lat)
):
    min_lon, min_lat, max_lon, max_lat = location
    return {
        "id": source_id,
        "type": "ElevationSource",
        "name": {"type": "Property", "value": "Test LiDAR DTM"},
        "category": {"type": "Property", "value": category},
        "isBareEarth": {"type": "Property", "value": is_bare_earth},
        "accuracyVerticalM": {"type": "Property", "value": accuracy_v},
        "resolutionM": {"type": "Property", "value": 2},
        "priority": {"type": "Property", "value": priority},
        "tenantId": {"type": "Property", "value": tenant_id} if tenant_id else {"type": "Property", "value": None},
        "sourceUrl": {"type": "Property", "value": "http://minio:9000/test.tif"},
        "location": {
            "type": "GeoProperty",
            "value": {
                "type": "Polygon",
                "coordinates": [[
                    [min_lon, min_lat],
                    [max_lon, min_lat],
                    [max_lon, max_lat],
                    [min_lon, max_lat],
                    [min_lon, min_lat],
                ]]
            }
        },
    }


class TestSourceRegistry:

    @pytest.mark.asyncio
    async def test_merge_builtin_and_orion_sources(self):
        """Built-in sources are always included; Orion-LD sources augment them."""
        builtin = [make_dem_source()]
        orion_entities = [make_orion_source()]

        registry = SourceRegistry(builtin_sources=builtin)
        registry._global_entities = orion_entities  # mock cached global sources
        registry._last_fetch_global = time.time()   # pretend cache is fresh

        sources = await registry.get_sources_for_point(42.5, -1.5)
        assert len(sources) >= 2  # at least one builtin + one orion

    @pytest.mark.asyncio
    async def test_filter_by_tenant(self):
        """Sources with tenant_id are only returned for that tenant."""
        builtin = [make_dem_source()]
        global_source = make_orion_source(source_id="g", tenant_id=None)
        tenant_source = make_orion_source(source_id="t", tenant_id="tenant_x")

        registry = SourceRegistry(builtin_sources=builtin)
        registry._global_entities = [global_source]
        registry._tenant_entities["tenant_x"] = [tenant_source]
        registry._last_fetch_global = time.time()

        # Without tenant → only global
        sources_no_tenant = await registry.get_sources_for_point(42.5, -1.5)
        source_ids = [s.get('id') for s in sources_no_tenant]
        assert "t" not in [str(x) for x in source_ids]

        # With matching tenant → global + tenant-specific
        sources_with_tenant = await registry.get_sources_for_point(42.5, -1.5, tenant_id="tenant_x")
        source_ids_w = [s.get('id') for s in sources_with_tenant]
        assert any("t" in str(x) for x in source_ids_w)

    @pytest.mark.asyncio
    async def test_filter_by_purpose_precision(self):
        """Purpose='precision' requires isBareEarth=true and accuracy<=1m."""
        builtin = []
        good = make_orion_source(source_id="good", is_bare_earth=True, accuracy_v=0.5, priority=10)
        bad_accuracy = make_orion_source(source_id="bad_acc", is_bare_earth=True, accuracy_v=5.0, priority=50)
        bad_dsm = make_orion_source(source_id="bad_dsm", is_bare_earth=False, accuracy_v=0.3, priority=100)

        registry = SourceRegistry(builtin_sources=builtin)
        registry._global_entities = [good, bad_accuracy, bad_dsm]
        registry._last_fetch_global = time.time()

        sources = await registry.get_sources_for_point(42.5, -1.5, purpose="precision")
        source_ids = [s.get("id") for s in sources]
        assert "good" in source_ids
        assert "bad_acc" not in source_ids
        assert "bad_dsm" not in source_ids

    @pytest.mark.asyncio
    async def test_filter_by_purpose_routing(self):
        """Purpose='routing' requires isBareEarth=true."""
        builtin = []
        dsm = make_orion_source(source_id="dsm", is_bare_earth=False)
        dtm = make_orion_source(source_id="dtm", is_bare_earth=True)

        registry = SourceRegistry(builtin_sources=builtin)
        registry._global_entities = [dsm, dtm]
        registry._last_fetch_global = time.time()

        sources = await registry.get_sources_for_point(42.5, -1.5, purpose="routing")
        source_ids = [s.get("id") for s in sources]
        assert "dsm" not in source_ids
        assert "dtm" in source_ids

    @pytest.mark.asyncio
    async def test_sort_by_priority_then_accuracy(self):
        """Sources sort by priority desc, then accuracy asc."""
        builtin = []
        high_prio = make_orion_source(source_id="high", priority=80, accuracy_v=1.0)
        mid_prio_low_acc = make_orion_source(source_id="mid_bad", priority=50, accuracy_v=2.0)
        mid_prio_high_acc = make_orion_source(source_id="mid_good", priority=50, accuracy_v=0.1)

        registry = SourceRegistry(builtin_sources=builtin)
        registry._global_entities = [mid_prio_low_acc, high_prio, mid_prio_high_acc]
        registry._last_fetch_global = time.time()

        sources = await registry.get_sources_for_point(42.5, -1.5)
        ordered_ids = [s.get("id") for s in sources]
        # high priority first, then accuracy within same priority tier
        assert ordered_ids[0] == "high"
        # mid_good (accuracy 0.1) before mid_bad (accuracy 2.0) — same priority
        idx_good = ordered_ids.index("mid_good")
        idx_bad = ordered_ids.index("mid_bad")
        assert idx_good < idx_bad

    @pytest.mark.asyncio
    async def test_coverage_filter_point(self):
        """Sources whose bbox does not contain the point are excluded."""
        builtin = []
        covers = make_orion_source(
            source_id="covers",
            location=(-2.0, 42.0, -1.0, 43.0),  # contains (42.5, -1.5)
        )
        outside = make_orion_source(
            source_id="outside",
            location=(5.0, 48.0, 6.0, 49.0),  # far away
        )

        registry = SourceRegistry(builtin_sources=builtin)
        registry._global_entities = [covers, outside]
        registry._last_fetch_global = time.time()

        sources = await registry.get_sources_for_point(42.5, -1.5)
        source_ids = [s.get("id") for s in sources]
        assert "covers" in source_ids
        assert "outside" not in source_ids
