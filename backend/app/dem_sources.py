"""
EU/UK DEM Source Catalog — Pre-configured data sources for elevation terrain generation.

Each source defines a national or pan-European Digital Elevation Model (DEM) endpoint.
The ETL pipeline uses these sources to download elevation data (WCS/WMS/GeoTIFF),
reproject to EPSG:4326, and generate Cesium Quantized Mesh tiles.

Usage:
    from app.dem_sources import DEM_SOURCES, get_source, get_sources_for_bbox
"""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class DEMSource:
    """A national or regional DEM data source."""
    country_code: str
    country_name: str
    service_url: str
    service_type: str  # "WCS" | "WMS" | "DOWNLOAD" | "REST"
    format: str  # "GeoTIFF" | "ASCII" | "WCS" | "WMS"
    resolution: str  # e.g. "1m", "0.5m", "25m"
    bbox: tuple[float, float, float, float]  # (west, south, east, north) EPSG:4326
    layer_name: Optional[str] = None  # WCS/WMS layer identifier
    notes: str = ""
    fallback: bool = False  # True = pan-European fallback source
    requires_preprocessing: str = ""  # Special preprocessing notes


# =============================================================================
# Complete EU + UK DEM Source Registry
# =============================================================================

DEM_SOURCES: list[DEMSource] = [
    # -------------------------------------------------------------------------
    # WESTERN EUROPE
    # -------------------------------------------------------------------------
    DEMSource(
        country_code="ES",
        country_name="España",
        service_url="https://servicios.idee.es/wcs-inspire/mdt",
        service_type="WCS",
        format="GEOTIFFINT16",
        resolution="25m",
        bbox=(-18.2, 27.6, 4.4, 43.8),  # Includes Canarias
        layer_name="Elevacion4258_25",  # ETRS89 geographic 25m (best national, all Spain)
        notes="IGN PNOA MDT via WCS 1.0.0, ETRS89 geographic. Coberturas finas: "
              "Elevacion4258_25 (25m), Elevacion4258_5 (5m). "
              "Elevacion4326_* solo 500m/1000m (grueso) — no usar."
    ),
    DEMSource(
        country_code="PT",
        country_name="Portugal",
        service_url="https://servicos.dgterritorio.pt/wcs/mdr",
        service_type="WCS",
        format="GeoTIFF",
        resolution="2m",
        bbox=(-31.3, 30.0, -6.2, 42.2),  # Includes Açores, Madeira
        fallback=True,  # Redirects to Copernicus 30m while DGT endpoint is down
        notes="DGT - Modelos Digitais do Relevo. ENDPOINT CAÍDO (2026-06-05): "
              "servicos.dgterritorio.pt migró a cartografia.dgterritorio.gov.pt. "
              "El WCS del MDT no está disponible públicamente en la nueva infraestructura. "
              "Posible endpoint futuro: cartografia.dgterritorio.gov.pt/wcs-inspire/mdt25. "
              "Contacto DGT: loja@dgterritorio.pt. "
              "Mientras tanto se usa Copernicus GLO-30 (30m) vía fallback paneuropeo."
    ),
    DEMSource(
        country_code="FR",
        country_name="France",
        service_url="https://data.geopf.fr/wms-r/wms",
        service_type="WMS",
        format="GeoTIFF",
        resolution="1m",
        bbox=(-5.2, 41.3, 9.6, 51.1),
        layer_name="ELEVATION.ELEVATIONGRIDCOVERAGE",
        notes="IGN RGE ALTI via Géoplateforme WMS raster"
    ),
    DEMSource(
        country_code="BE",
        country_name="Belgium",
        service_url="https://wcs.ngi.be/elevation/wcs",
        service_type="WCS",
        format="GeoTIFF",
        resolution="1m",
        bbox=(2.5, 49.5, 6.4, 51.5),
        notes="NGI DTM Visualization"
    ),
    DEMSource(
        country_code="NL",
        country_name="Netherlands",
        service_url="https://service.pdok.nl/rws/actueel-hoogtebestand-nederland/wcs/v1_0",
        service_type="WCS",
        format="GeoTIFF",
        resolution="0.5m",
        bbox=(3.3, 50.7, 7.3, 53.6),
        notes="AHN3/AHN4 - Actueel Hoogtebestand Nederland"
    ),
    DEMSource(
        country_code="LU",
        country_name="Luxembourg",
        service_url="https://download.data.public.lu/resources/mnt/",
        service_type="DOWNLOAD",
        format="GeoTIFF",
        resolution="1m",
        bbox=(5.7, 49.4, 6.5, 50.2),
        notes="ACT - Modèle Numérique de Terrain (MNT)"
    ),
    DEMSource(
        country_code="IE",
        country_name="Ireland",
        service_url="https://inspireservices.geohive.ie/data/rest/services/INSPIRE/ElevationGrid/MapServer",
        service_type="REST",
        format="GeoTIFF",
        resolution="10m",
        bbox=(-10.7, 51.3, -5.5, 55.5),
        notes="OSi/GeoHive API. Resolución 2m a 10m"
    ),

    # -------------------------------------------------------------------------
    # CENTRAL EUROPE
    # -------------------------------------------------------------------------
    DEMSource(
        country_code="DE",
        country_name="Germany",
        service_url="https://sgx.geodatenzentrum.de/wcs_dgm200",
        service_type="WCS",
        format="GeoTIFF",
        resolution="200m",
        bbox=(5.9, 47.3, 15.1, 55.1),
        notes="BKG DGM200 (free). Higher res (10m, 1m) available per Bundesland"
    ),
    DEMSource(
        country_code="AT",
        country_name="Austria",
        service_url="https://gis.bev.gv.at/geoserver/wcs",
        service_type="WCS",
        format="GeoTIFF",
        resolution="10m",
        bbox=(9.5, 46.4, 17.2, 49.0),
        notes="BEV DTM Austria"
    ),
    DEMSource(
        country_code="CH",
        country_name="Switzerland",
        service_url="https://wms.geo.admin.ch/",
        service_type="WMS",
        format="GeoTIFF",
        resolution="2m",
        bbox=(5.9, 45.8, 10.5, 47.8),
        layer_name="ch.swisstopo.swissalti3d-reliefschattierung",
        notes="swisstopo swissALTI3D. Free. 0.5m y 2m disponibles"
    ),
    DEMSource(
        country_code="CZ",
        country_name="Czech Republic",
        service_url="https://ags.cuzk.cz/arcgis2/services/dmr5g/ImageServer/WCSServer",
        service_type="WCS",
        format="GeoTIFF",
        resolution="1m",
        bbox=(12.1, 48.6, 18.9, 51.1),
        notes="ČÚZK DMR 5G (1m), DMR 4G (5m)"
    ),

    # -------------------------------------------------------------------------
    # NORTHERN EUROPE
    # -------------------------------------------------------------------------
    DEMSource(
        country_code="GB",
        country_name="United Kingdom",
        service_url="https://environment.data.gov.uk/spatialdata/lidar-composite-digital-terrain-model-dtm-1m/wcs",
        service_type="WCS",
        format="GeoTIFF",
        resolution="1m",
        bbox=(-8.6, 49.9, 1.8, 60.9),
        notes="Environment Agency LIDAR Composite DTM. 1m y 2m"
    ),
    DEMSource(
        country_code="DK",
        country_name="Denmark",
        service_url="https://services.datafordeler.dk/DHMTerraen/dhm_terraen/1.0.0/wcs",
        service_type="WCS",
        format="GeoTIFF",
        resolution="0.4m",
        bbox=(8.1, 54.6, 15.2, 57.8),
        notes="SDFI - Danmarks Højdemodel"
    ),
    DEMSource(
        country_code="SE",
        country_name="Sweden",
        service_url="https://minkarta.lantmateriet.se/",
        service_type="DOWNLOAD",
        format="GeoTIFF",
        resolution="1m",
        bbox=(10.9, 55.3, 24.2, 69.1),
        notes="Lantmäteriet Markhöjdmodell. Grid ASCII / GeoTIFF"
    ),
    DEMSource(
        country_code="FI",
        country_name="Finland",
        service_url="https://avoin-karttakuva.maanmittauslaitos.fi/ortokuvat-ja-korkeusmallit/wcs/v2",
        service_type="WCS",
        format="GeoTIFF",
        resolution="2m",
        bbox=(20.6, 59.8, 31.6, 70.1),
        notes="NLS GeoCubes API DEM2"
    ),
    DEMSource(
        country_code="NO",
        country_name="Norway",
        service_url="https://wms.geonorge.no/skwms1/wcs.hoyde-dtm-nhm-25832",
        service_type="WCS",
        format="GeoTIFF",
        resolution="1m",
        bbox=(4.6, 58.0, 31.2, 71.2),
        notes="Kartverket Nasjonal høydemodell DTM"
    ),

    # -------------------------------------------------------------------------
    # SOUTHERN EUROPE
    # -------------------------------------------------------------------------
    DEMSource(
        country_code="IT",
        country_name="Italy",
        service_url="http://tinitaly.pi.ingv.it/TINItaly_1_1/wcs",
        service_type="WCS",
        format="GeoTIFF",
        resolution="10m",
        bbox=(6.6, 36.6, 18.5, 47.1),
        notes="INGV TINITALY DEM 10m"
    ),
    DEMSource(
        country_code="SI",
        country_name="Slovenia",
        service_url="https://gis.arso.gov.si/geoserver/dmv/wcs",
        service_type="WCS",
        format="ASCII",
        resolution="1m",
        bbox=(13.4, 45.4, 16.6, 46.9),
        requires_preprocessing="Requiere corrección de inversión de ejes X/Y previa a GDAL",
        notes="GURS Digital Terrain Model"
    ),

    # -------------------------------------------------------------------------
    # EASTERN EUROPE
    # -------------------------------------------------------------------------
    DEMSource(
        country_code="PL",
        country_name="Poland",
        service_url="https://mapy.geoportal.gov.pl/wss/service/PZGIK/NMT/WCS/DigitalTerrainModel",
        service_type="WCS",
        format="GeoTIFF",
        resolution="1m",
        bbox=(14.1, 49.0, 24.2, 54.9),
        notes="PZGIK NMT. GeoTIFF / ASCII Raster"
    ),

    # -------------------------------------------------------------------------
    # PAN-EUROPEAN FALLBACK
    # -------------------------------------------------------------------------
    DEMSource(
        country_code="EU",
        country_name="Pan-European (Copernicus DEM 30m)",
        service_url="/vsis3/copernicus-dem-30m",
        service_type="DOWNLOAD",
        format="GeoTIFF",
        resolution="30m",
        bbox=(-32.0, 27.0, 45.0, 72.0),
        fallback=True,
        notes="Copernicus GLO-30 DEM via AWS S3 (no auth). GDAL access requires "
              "AWS_NO_SIGN_REQUEST=YES and AWS_S3_ENDPOINT=s3.amazonaws.com. "
              "Tiles are 1°×1° COG files. Use scripts/ingest_copernicus_eu.py for batch ingestion."
    ),
]


# =============================================================================
# Lookup helpers
# =============================================================================

_BY_CODE: dict[str, DEMSource] = {s.country_code: s for s in DEM_SOURCES}


def get_source(country_code: str) -> DEMSource | None:
    """Get a DEM source by ISO country code (e.g. 'ES', 'GB', 'EU')."""
    return _BY_CODE.get(country_code.upper())


def get_all_sources(include_fallback: bool = True) -> list[DEMSource]:
    """Get all registered DEM sources."""
    if include_fallback:
        return DEM_SOURCES
    return [s for s in DEM_SOURCES if not s.fallback]


def get_sources_for_bbox(
    west: float, south: float, east: float, north: float
) -> list[DEMSource]:
    """Get all DEM sources whose BBOX intersects the given area."""
    results = []
    for src in DEM_SOURCES:
        sw, ss, se, sn = src.bbox
        # Check intersection
        if sw < east and se > west and ss < north and sn > south:
            results.append(src)
    # Non-fallback first, then fallback
    results.sort(key=lambda s: (s.fallback, s.country_code))
    return results


# Per-country fine-resolution ETRS89 coverage routing for the /raster endpoint.
# Map country_code -> {max_resolution_m -> coverage_name}. Selection picks the
# finest available coverage at or above the requested resolution.
_FINE_COVERAGES: dict[str, list[tuple[int, str]]] = {
    # ES: ETRS89 geographic (EPSG:4258). Verified 2026-06-24 against the IGN WCS.
    # <=5m -> 5m; <=25m -> 25m; >25m -> 25m (best national).
    "ES": [(5, "Elevacion4258_5"), (25, "Elevacion4258_25"), (10**9, "Elevacion4258_25")],
}


def coverage_for_resolution(country_code: str, resolution_m: float) -> str | None:
    """Pick the finest WCS coverage for a country at the requested resolution.

    Returns the coverage name (layer) or None if the country has no fine-coverage
    routing (caller falls back to the source's default layer_name / Copernicus).
    """
    table = _FINE_COVERAGES.get(country_code)
    if not table:
        return None
    for max_m, name in table:
        if resolution_m <= max_m:
            return name
    return table[-1][1]  # coarsest fine-coverage
