"""Unit tests for services/geocoder.py — bounds checking and response parsing.

No network access: provider payloads are supplied as fixtures.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services import geocoder


# ── Bounds ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("lat,lon", [
    (-25.7406, 28.1919),   # Union Buildings, Pretoria
    (-26.2041, 28.0473),   # Johannesburg
    (-33.9249, 18.4241),   # Cape Town
    (-29.8587, 31.0218),   # Durban
])
def test_south_african_coordinates_accepted(lat, lon):
    assert geocoder.within_south_africa(lat, lon) is True


@pytest.mark.parametrize("lat,lon", [
    (-17.8252, 31.0335),    # Harare, Zimbabwe
    (51.5074, -0.1278),     # London
    (28.1919, -25.7406),    # lat/lon transposed
    (None, 28.0),
    (-26.0, None),
    ("abc", 28.0),
])
def test_coordinates_outside_south_africa_rejected(lat, lon):
    assert geocoder.within_south_africa(lat, lon) is False


# ── Google parsing ────────────────────────────────────────────────────────────

GOOGLE_PAYLOAD = {
    "places": [
        {
            "displayName": {"text": "Union Buildings"},
            "formattedAddress": "Government Ave, Pretoria, 0002, South Africa",
            "location": {"latitude": -25.7406, "longitude": 28.1919},
            "types": ["tourist_attraction", "point_of_interest"],
        },
        {
            "displayName": {"text": "Harare City Centre"},
            "formattedAddress": "Harare, Zimbabwe",
            "location": {"latitude": -17.8252, "longitude": 31.0335},
            "types": ["locality"],
        },
    ]
}


def test_google_parsing_extracts_hit():
    hits = geocoder.parse_google_response(GOOGLE_PAYLOAD)
    assert len(hits) == 1                       # the Zimbabwe result is dropped
    hit = hits[0]
    assert hit.name == "Union Buildings"
    assert hit.lat == pytest.approx(-25.7406)
    assert hit.lon == pytest.approx(28.1919)
    assert hit.provider == "google"
    assert "tourist_attraction" in hit.place_types


def test_google_parsing_respects_limit():
    payload = {"places": [
        {
            "displayName": {"text": f"Place {i}"},
            "formattedAddress": "Gauteng, South Africa",
            "location": {"latitude": -26.0 - i / 100, "longitude": 28.0},
        }
        for i in range(10)
    ]}
    assert len(geocoder.parse_google_response(payload, limit=3)) == 3


@pytest.mark.parametrize("payload", [{}, {"places": []}, {"places": None}, None])
def test_google_parsing_handles_empty_payloads(payload):
    assert geocoder.parse_google_response(payload) == []


def test_google_hit_label_combines_name_and_address():
    hit = geocoder.parse_google_response(GOOGLE_PAYLOAD)[0]
    assert hit.label == "Union Buildings — Government Ave, Pretoria, 0002, South Africa"


# ── Nominatim parsing ─────────────────────────────────────────────────────────

NOMINATIM_PAYLOAD = [
    {
        "name": "Soweto",
        "display_name": "Soweto, Johannesburg, Gauteng, South Africa",
        "lat": "-26.2678",
        "lon": "27.8586",
        "type": "suburb",
        "class": "place",
    },
    {
        "name": "Broken",
        "display_name": "Broken record",
        "lat": "not-a-number",
        "lon": "27.0",
    },
]


def test_nominatim_parsing_skips_unparseable_rows():
    hits = geocoder.parse_nominatim_response(NOMINATIM_PAYLOAD)
    assert len(hits) == 1
    assert hits[0].name == "Soweto"
    assert hits[0].provider == "nominatim"
    assert hits[0].lat == pytest.approx(-26.2678)


def test_nominatim_parsing_handles_empty():
    assert geocoder.parse_nominatim_response([]) == []
    assert geocoder.parse_nominatim_response(None) == []


# ── Search guardrails ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("query", ["", "  ", "ab"])
def test_short_queries_do_not_hit_the_network(query):
    outcome = geocoder.search(query)
    assert outcome.ok is False
    assert outcome.hits == ()
    assert "3 characters" in outcome.message


def test_provider_falls_back_to_nominatim_without_a_google_key(monkeypatch):
    monkeypatch.setattr(geocoder, "google_api_key", lambda: "")
    monkeypatch.setattr(geocoder, "_get_secret", lambda name, default="": "google"
                        if name == "CAPTURE_GEOCODER" else "")
    assert geocoder.active_provider() == "nominatim"


def test_google_provider_used_when_key_present(monkeypatch):
    monkeypatch.setattr(geocoder, "google_api_key", lambda: "fake-key")
    monkeypatch.setattr(geocoder, "_get_secret", lambda name, default="": "google"
                        if name == "CAPTURE_GEOCODER" else "")
    assert geocoder.active_provider() == "google"


def test_missing_key_returns_message_not_exception(monkeypatch):
    monkeypatch.setattr(geocoder, "google_api_key", lambda: "")
    outcome = geocoder._search_google("Union Buildings", 5)
    assert outcome.ok is False
    assert outcome.hits == ()
    assert "GOOGLE_MAPS_API_KEY" in outcome.message


def test_search_outcome_is_falsy_when_empty():
    assert not geocoder.SearchOutcome()
    assert geocoder.SearchOutcome(hits=(geocoder.GeocodeHit("x", "y", -26.0, 28.0),))
