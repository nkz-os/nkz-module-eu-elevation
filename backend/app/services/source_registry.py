"""SourceRegistry — merges built-in DEM sources with Orion-LD ElevationSource entities.

Resolves the best elevation source for a point or bbox, respecting:
- Purpose-based filtering (precision, routing, weather, auto)
- Tenant isolation (tenant-private sources only visible to their tenant)
- Priority + accuracy ordering
- Coverage (spatial containment check)

Uses proper NGSI-LD headers (Link with @context, NGSILD-Tenant,
Fiware-Service) per AGENTS.md directive #3.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx

from app.dem_sources import DEMSource, DEM_SOURCES, get_source as get_builtin_source
from app.config import settings
from app.common.tenant_utils import normalize_tenant_id

logger = logging.getLogger(__name__)

# Orion-LD entity cache TTL in seconds
_ORION_CACHE_TTL = 60

# Circuit breaker: exclude sources that failed in the last N seconds
_CIRCUIT_BREAKER_SECONDS = 300


@dataclass
class SourceEntry:
    """Normalised representation of an elevation source (built-in or Orion-LD)."""
    id: str                          # urn:ngsi-ld:ElevationSource:... or country_code for builtin
    name: str
    category: str                    # copernicus_eu, lidar_dtm, cesium_world, etc.
    is_bare_earth: bool
    accuracy_v_m: Optional[float]    # vertical RMSE in meters
    resolution_m: Optional[float]    # horizontal pixel size in meters
    priority: int
    source_url: str                  # WCS endpoint or MinIO raster URL
    bbox: tuple[float, float, float, float]  # (min_lon, min_lat, max_lon, max_lat)
    tenant_id: Optional[str] = None  # None = global
    is_builtin: bool = False


# ── Purpose filter definitions ─────────────────────────────────────
_PURPOSE_FILTERS = {
    "precision": {
        "require_bare_earth": True,
        "max_accuracy_v_m": 1.0,
    },
    "routing": {
        "require_bare_earth": True,
        "max_resolution_m": 30,
    },
    "weather": {
        # Any source is fine for weather (altitude correction tolerates ±10m)
        "require_bare_earth": False,
    },
    "visualization": {
        # Any source; terrain tiles are handled separately
        "require_bare_earth": False,
    },
    "auto": {
        "require_bare_earth": False,  # Prefer but don't require
    },
}


class SourceRegistry:
    """Discovers elevation sources from built-in catalog + Orion-LD.

    Usage:
        registry = SourceRegistry(builtin_sources=DEM_SOURCES)
        sources = await registry.get_sources_for_point(lat, lon, purpose="precision")
        # sources is sorted by priority desc, accuracy asc
    """

    def __init__(self, builtin_sources: list[DEMSource] | None = None):
        self._builtin = builtin_sources or DEM_SOURCES
        # Partitioned cache — no cross-tenant cache bleeding
        self._global_entities: list[dict] = []
        self._tenant_entities: dict[str, list[dict]] = {}  # tenant_id -> entities
        self._last_fetch_global: float = 0
        self._last_fetch_tenant: dict[str, float] = {}   # tenant_id -> timestamp
        self._failed_sources: dict[str, float] = {}  # source_id -> fail timestamp

    # ── Orion-LD refresh ────────────────────────────────────────────

    async def _refresh_orion(self, tenant_id: Optional[str] = None):
        """Refresh Orion-LD cache, partitioned by scope.

        Two independent caches with separate TTLs:
        1. Global scope — refreshed when global TTL expires
        2. Per-tenant scope — refreshed when that tenant's TTL expires

        This prevents cache bleeding: Tenant A refreshing its cache does
        not block Tenant B from fetching its own private sources.
        """
        now = time.time()

        # Global scope refresh
        if now - self._last_fetch_global >= _ORION_CACHE_TTL or not self._global_entities:
            entities = await fetch_orion_elevation_sources(tenant_id=None)
            self._global_entities = entities
            self._last_fetch_global = now

        # Tenant-specific scope refresh
        if tenant_id and (
            tenant_id not in self._last_fetch_tenant
            or now - self._last_fetch_tenant[tenant_id] >= _ORION_CACHE_TTL
        ):
            entities = await fetch_orion_elevation_sources(tenant_id=tenant_id)
            # Only keep tenant-specific entities (those with non-null tenantId)
            self._tenant_entities[tenant_id] = [
                e for e in entities if _get_prop(e, "tenantId") is not None
            ]
            self._last_fetch_tenant[tenant_id] = now

    # ── Public API ──────────────────────────────────────────────────

    async def get_sources_for_point(
        self,
        lat: float,
        lon: float,
        purpose: str = "auto",
        tenant_id: Optional[str] = None,
    ) -> list[dict]:
        """Return all sources covering (lat,lon), sorted best-first.

        Returns list of dicts with keys: id, name, category, is_bare_earth,
        accuracy_v_m, resolution_m, source_url, priority, tenant_id, is_builtin.
        """
        await self._refresh_orion(tenant_id)
        all_sources = self._merge_sources(tenant_id)
        all_sources = self._filter_by_purpose(all_sources, purpose)
        all_sources = self._filter_by_bbox(all_sources, lon, lat)
        all_sources = self._exclude_failed(all_sources)
        return self._sort(all_sources)

    async def get_sources_for_bbox(
        self,
        min_lon: float,
        min_lat: float,
        max_lon: float,
        max_lat: float,
        purpose: str = "auto",
        tenant_id: Optional[str] = None,
    ) -> list[dict]:
        """Return all sources whose bbox intersects the query bbox."""
        await self._refresh_orion(tenant_id)
        all_sources = self._merge_sources(tenant_id)
        all_sources = self._filter_by_purpose(all_sources, purpose)
        all_sources = self._filter_by_bbox_intersection(
            all_sources, min_lon, min_lat, max_lon, max_lat
        )
        all_sources = self._exclude_failed(all_sources)
        return self._sort(all_sources)

    # ── Internal: source merging ────────────────────────────────────

    def _merge_sources(self, tenant_id: Optional[str]) -> list[dict]:
        """Merge built-in + global Orion-LD + tenant-specific Orion-LD sources.

        Cache partitioning guarantees no cross-tenant leakage:
        - _global_entities has entities from the platform-level scope
        - _tenant_entities[tenant_id] has private entities for this tenant only
        """
        merged = []

        # Built-in sources (always global)
        for src in self._builtin:
            merged.append({
                "id": f"builtin:{src.country_code}",
                "name": src.country_name,
                "category": (
                    "copernicus_eu" if src.fallback
                    else f"custom_wcs:{src.country_code}"
                ),
                "is_bare_earth": True,  # DEM sources are DTM by default
                "accuracy_v_m": _parse_accuracy(src.resolution),
                "resolution_m": _parse_resolution(src.resolution),
                "priority": 10 if not src.fallback else 5,
                "source_url": src.service_url,
                "tenant_id": None,
                "is_builtin": True,
                "_bbox": src.bbox,
            })

        # Global Orion-LD sources (tenantId is null)
        for entity in self._global_entities:
            merged.append(_entity_to_source_dict(entity))

        # Tenant-specific Orion-LD sources (cache keyed by tenant_id)
        if tenant_id and tenant_id in self._tenant_entities:
            for entity in self._tenant_entities[tenant_id]:
                merged.append(_entity_to_source_dict(entity))

        return merged

    # ── Internal: filtering ─────────────────────────────────────────

    def _filter_by_purpose(self, sources: list[dict], purpose: str) -> list[dict]:
        """Apply purpose-based quality filters."""
        filt = _PURPOSE_FILTERS.get(purpose, _PURPOSE_FILTERS["auto"])
        result = []

        for s in sources:
            if filt.get("require_bare_earth") and not s.get("is_bare_earth"):
                continue
            if "max_accuracy_v_m" in filt and s.get("accuracy_v_m"):
                if s["accuracy_v_m"] > filt["max_accuracy_v_m"]:
                    continue
            if "max_resolution_m" in filt and s.get("resolution_m"):
                if s["resolution_m"] > filt["max_resolution_m"]:
                    continue
            result.append(s)

        return result

    def _filter_by_bbox(self, sources: list[dict], lon: float, lat: float) -> list[dict]:
        """Keep only sources whose bbox contains the point."""
        result = []
        for s in sources:
            bbox = s.get("_bbox")
            if not bbox:
                continue
            min_lon, min_lat, max_lon, max_lat = bbox
            if min_lon <= lon <= max_lon and min_lat <= lat <= max_lat:
                result.append(s)
        return result

    def _filter_by_bbox_intersection(
        self, sources: list[dict],
        q_min_lon: float, q_min_lat: float, q_max_lon: float, q_max_lat: float,
    ) -> list[dict]:
        """Keep sources whose bbox intersects the query bbox."""
        result = []
        for s in sources:
            bbox = s.get("_bbox")
            if not bbox:
                continue
            s_min_lon, s_min_lat, s_max_lon, s_max_lat = bbox
            # Intersection check
            if s_max_lon < q_min_lon or s_min_lon > q_max_lon:
                continue
            if s_max_lat < q_min_lat or s_min_lat > q_max_lat:
                continue
            result.append(s)
        return result

    def _exclude_failed(self, sources: list[dict]) -> list[dict]:
        """Exclude sources that failed recently (circuit breaker)."""
        now = time.time()
        result = []
        for s in sources:
            sid = s.get("id", "")
            if sid in self._failed_sources:
                if now - self._failed_sources[sid] < _CIRCUIT_BREAKER_SECONDS:
                    continue
                del self._failed_sources[sid]
            result.append(s)
        return result

    def _sort(self, sources: list[dict]) -> list[dict]:
        """Sort by priority desc, accuracy asc, bare_earth preference."""
        def sort_key(s):
            # bare_earth bonus: push true sources above false ones at same priority
            bare_bonus = 1000 if s.get("is_bare_earth") else 0
            accuracy = s.get("accuracy_v_m") or 999
            # negate priority for descending sort, accuracy asc
            return (-(s.get("priority", 0) + bare_bonus), accuracy)
        return sorted(sources, key=sort_key)


# ── Orion-LD query helpers ──────────────────────────────────────────

async def fetch_orion_elevation_sources(
    orion_url: str | None = None,
    tenant_id: str | None = None,
    timeout: float = 10.0,
) -> list[dict]:
    """Query Orion-LD for ElevationSource entities.

    Uses proper NGSI-LD headers (NGSILD-Tenant, Fiware-Service, Link with
    @context) as required by AGENTS.md directive #3. No raw httpx without headers.

    If tenant_id is provided, queries that tenant's scope in addition to the
    default (platform-level) scope. Returns merged results.
    """
    base_url = orion_url or settings.ORION_URL.rstrip("/")
    url = f"{base_url}/ngsi-ld/v1/entities"
    params = {
        "type": "ElevationSource",
        "limit": 1000,
        "options": "keyValues",
    }
    context_url = os.getenv("CONTEXT_URL", "http://api-gateway-service:5000/ngsi-ld-context.json")

    all_entities: list[dict] = []

    # Query 1: platform-level scope (no tenant header → default tenant entities)
    if tenant_id is None:
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(
                    url,
                    params=params,
                    headers={
                        "Accept": "application/ld+json",
                        "Link": f'<{context_url}>; rel="http://www.w3.org/ns/json-ld#context"; type="application/ld+json"',
                    },
                )
                resp.raise_for_status()
                all_entities.extend(resp.json())
        except Exception as e:
            logger.warning("Failed to fetch ElevationSource entities (global scope): %s", e)
    else:
        # Tenant-specific scope query
        tenant_header = normalize_tenant_id(tenant_id)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(
                    url,
                    params=params,
                    headers={
                        "NGSILD-Tenant": tenant_header,
                        "Fiware-Service": tenant_header,
                        "Accept": "application/ld+json",
                        "Link": f'<{context_url}>; rel="http://www.w3.org/ns/json-ld#context"; type="application/ld+json"',
                    },
                )
                resp.raise_for_status()
                all_entities.extend(resp.json())
        except Exception as e:
            logger.warning(
                "Failed to fetch ElevationSource entities for tenant %s: %s",
                tenant_id, e
            )

    return all_entities


# ── Internal helpers ────────────────────────────────────────────────

def _entity_to_source_dict(entity: dict) -> dict:
    """Convert an NGSI-LD ElevationSource entity dict to a SourceRegistry source dict."""
    return {
        "id": entity.get("id", ""),
        "name": _get_prop(entity, "name") or entity.get("id", ""),
        "category": _get_prop(entity, "category") or "unknown",
        "is_bare_earth": _get_prop(entity, "isBareEarth", False),
        "accuracy_v_m": _get_prop(entity, "accuracyVerticalM"),
        "resolution_m": _get_prop(entity, "resolutionM"),
        "priority": _get_prop(entity, "priority", 10),
        "source_url": _get_prop(entity, "sourceUrl", ""),
        "tenant_id": _get_prop(entity, "tenantId"),
        "is_builtin": False,
        "_bbox": _extract_bbox(entity),
    }


def _get_prop(entity: dict, key: str, default=None):
    """Extract a Property value from an NGSI-LD entity."""
    prop = entity.get(key, {})
    if isinstance(prop, dict):
        return prop.get("value", default)
    return prop if prop is not None else default


def _extract_bbox(entity: dict) -> tuple | None:
    """Extract bounding box from an NGSI-LD GeoProperty."""
    location = entity.get("location", {})
    if isinstance(location, dict):
        value = location.get("value", {})
    else:
        return None
    if not value:
        return None
    coords = value.get("coordinates", [])
    if not coords or not coords[0]:
        return None
    ring = coords[0]
    xs = [pt[0] for pt in ring]
    ys = [pt[1] for pt in ring]
    return (min(xs), min(ys), max(xs), max(ys))


def _parse_resolution(res_str: str | None) -> float | None:
    """Parse resolution string like '5m' or '0.5m' to float meters."""
    if not res_str:
        return None
    try:
        return float(res_str.replace("m", "").strip())
    except (ValueError, AttributeError):
        return None


def _parse_accuracy(res_str: str | None) -> float | None:
    """Estimate vertical accuracy from horizontal resolution (rough heuristic)."""
    res = _parse_resolution(res_str)
    if res is None:
        return None
    # Vertical accuracy is typically 1-3x horizontal resolution for DEMs
    return res * 2.0
