"""Tests for ES DEMSource resolution routing (ETRS89 coverages)."""
from app.dem_sources import get_source, coverage_for_resolution


def test_es_source_uses_etrs89_25m_not_4326_500():
    es = get_source("ES")
    assert es is not None
    # The fine-resolution ETRS89 geographic coverage, NOT the coarse 4326_500.
    assert es.layer_name == "Elevacion4258_25"
    assert es.resolution == "25m"


def test_coverage_for_resolution_es_5m():
    """<=5m -> 5m ETRS89 coverage."""
    assert coverage_for_resolution("ES", 5.0) == "Elevacion4258_5"


def test_coverage_for_resolution_es_25m():
    """>5m and <=25m -> 25m ETRS89 coverage."""
    assert coverage_for_resolution("ES", 25.0) == "Elevacion4258_25"


def test_coverage_for_resolution_es_above_25m():
    """>25m -> still 25m (best available national; coarse only via fallback)."""
    assert coverage_for_resolution("ES", 200.0) == "Elevacion4258_25"


def test_coverage_for_resolution_unknown_country_returns_none():
    assert coverage_for_resolution("ZZ", 10.0) is None
