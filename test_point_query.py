"""Tests for point elevation query."""
import pytest
import sys
sys.path.insert(0, 'backend')
from app.services.point_query import resolve_source


def test_resolve_source_spain():
    src = resolve_source(40.4168, -3.7038)  # Madrid
    assert src is not None
    assert src.country_code == "ES"


def test_resolve_source_france():
    src = resolve_source(48.8566, 2.3522)  # Paris
    assert src is not None
    assert src.country_code == "FR"


def test_resolve_source_ocean_returns_none():
    src = resolve_source(45.0, -40.0)  # Mid-Atlantic
    assert src is None


def test_resolve_source_canarias():
    src = resolve_source(28.2916, -16.6291)  # Tenerife
    assert src is not None
    assert src.country_code == "ES"
