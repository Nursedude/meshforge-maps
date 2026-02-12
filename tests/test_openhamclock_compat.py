"""Tests for OpenHamClock API compatibility layer."""

import pytest

from src.utils.openhamclock_compat import (
    detect_variant,
    get_endpoint_map,
    normalize_band_conditions,
    normalize_de_dx,
    normalize_key_value,
    normalize_spacewx,
)


class TestDetectVariant:
    """Tests for HamClock/OpenHamClock variant detection."""

    def test_detect_hamclock(self):
        assert detect_variant("Version=3.04\nUptime=12345") == "hamclock"

    def test_detect_openhamclock(self):
        assert detect_variant("Version=OpenHamClock 1.0\nUptime=100") == "openhamclock"

    def test_detect_openhamclock_case_insensitive(self):
        assert detect_variant("Version=OPENHAMCLOCK 2.0\nFoo=bar") == "openhamclock"

    def test_detect_empty_string(self):
        assert detect_variant("") == "hamclock"

    def test_detect_none(self):
        assert detect_variant(None) == "hamclock"

    def test_detect_unrecognized(self):
        assert detect_variant("SomeOtherThing=yes") == "hamclock"


class TestGetEndpointMap:
    """Tests for endpoint map retrieval."""

    def test_hamclock_endpoints(self):
        endpoints = get_endpoint_map("hamclock")
        assert endpoints["system"] == "/get_sys.txt"
        assert endpoints["spacewx"] == "/get_spacewx.txt"
        assert endpoints["band_conditions"] == "/get_bc.txt"
        assert endpoints["voacap"] == "/get_voacap.txt"
        assert endpoints["de"] == "/get_de.txt"
        assert endpoints["dx"] == "/get_dx.txt"
        assert endpoints["dxspots"] == "/get_dxspots.txt"
        assert "config" not in endpoints

    def test_openhamclock_endpoints_include_config(self):
        endpoints = get_endpoint_map("openhamclock")
        assert endpoints["system"] == "/get_sys.txt"
        assert endpoints["config"] == "/get_config.txt"

    def test_shared_endpoints_same(self):
        hc = get_endpoint_map("hamclock")
        ohc = get_endpoint_map("openhamclock")
        for key in ("system", "spacewx", "band_conditions", "voacap", "de", "dx", "dxspots"):
            assert hc[key] == ohc[key]


class TestNormalizeKeyValue:
    """Tests for generic key normalization."""

    def test_normalize_with_aliases(self):
        parsed = {"sfi": "150", "Kp": "3"}
        aliases = {"sfi": "SFI", "kp": "Kp_canonical"}
        result = normalize_key_value(parsed, aliases)
        assert result["SFI"] == "150"
        assert result["Kp_canonical"] == "3"

    def test_unmatched_keys_preserved(self):
        parsed = {"unknown_key": "value"}
        result = normalize_key_value(parsed, {"sfi": "SFI"})
        assert result["unknown_key"] == "value"

    def test_empty_input(self):
        result = normalize_key_value({}, {"sfi": "SFI"})
        assert result == {}

    def test_case_insensitive_matching(self):
        parsed = {"SFI": "150"}
        aliases = {"sfi": "SolarFluxIndex"}
        result = normalize_key_value(parsed, aliases)
        assert result["SolarFluxIndex"] == "150"


class TestNormalizeSpacewx:
    """Tests for space weather key normalization."""

    def test_normalize_lowercase_keys(self):
        parsed = {"sfi": "150", "kp": "3", "aurora": "5"}
        result = normalize_spacewx(parsed)
        assert result["SFI"] == "150"
        assert result["Kp"] == "3"
        assert result["Aurora"] == "5"

    def test_normalize_alternate_key_names(self):
        parsed = {"flux": "120", "sunspot": "42", "pf": "10"}
        result = normalize_spacewx(parsed)
        assert result["SFI"] == "120"
        assert result["SSN"] == "42"
        assert result["Proton"] == "10"

    def test_already_canonical_keys_passed_through(self):
        parsed = {"SFI": "150", "CustomKey": "value"}
        result = normalize_spacewx(parsed)
        assert result["SFI"] == "150"
        assert result["CustomKey"] == "value"

    def test_xray_alias(self):
        result = normalize_spacewx({"x-ray": "C1.2"})
        assert result["Xray"] == "C1.2"


class TestNormalizeDeDx:
    """Tests for DE/DX location key normalization."""

    def test_normalize_longitude_alias(self):
        parsed = {"lat": "40.0", "longitude": "-74.0", "grid": "FN20"}
        result = normalize_de_dx(parsed)
        assert result["lat"] == "40.0"
        assert result["lng"] == "-74.0"
        assert result["grid"] == "FN20"

    def test_normalize_callsign_alias(self):
        parsed = {"callsign": "W1AW", "gridsquare": "FN31"}
        result = normalize_de_dx(parsed)
        assert result["call"] == "W1AW"
        assert result["grid"] == "FN31"

    def test_standard_keys_pass_through(self):
        parsed = {"lat": "40.0", "lng": "-74.0", "call": "W1AW"}
        result = normalize_de_dx(parsed)
        assert result["lat"] == "40.0"
        assert result["lng"] == "-74.0"
        assert result["call"] == "W1AW"


class TestNormalizeBandConditions:
    """Tests for band condition key normalization."""

    def test_normalize_band_keys(self):
        parsed = {"band80m": "Good", "band20m": "Fair"}
        result = normalize_band_conditions(parsed)
        assert result["80m"] == "Good"
        assert result["20m"] == "Fair"

    def test_standard_keys_pass_through(self):
        parsed = {"80m-40m": "Good", "some_other": "value"}
        result = normalize_band_conditions(parsed)
        assert result["80m-40m"] == "Good"
        assert result["some_other"] == "value"

    def test_no_overlapping_aliases(self):
        """Each band alias maps to a distinct canonical key."""
        parsed = {"band80m": "Good", "band40m": "Fair", "band30m": "Poor", "band20m": "Good"}
        result = normalize_band_conditions(parsed)
        assert result["80m"] == "Good"
        assert result["40m"] == "Fair"
        assert result["30m"] == "Poor"
        assert result["20m"] == "Good"
