"""
Address and landmark search for South Africa.

Two interchangeable providers behind one function:

    search(query) -> SearchOutcome(hits, message, provider)

  - "google"    Places API (New) Text Search — handles street addresses and named
                landmarks in a single call. Requires GOOGLE_MAPS_API_KEY.
  - "nominatim" OpenStreetMap — free, no key, rate-limited to ~1 req/sec. Used as
                the automatic fallback so the app still demos without a Google key.

Every failure path (missing key, HTTP error, malformed payload) returns an empty
hit list with an explanatory message. This module never raises to the caller.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import requests

# South Africa bounding box: (min_lat, min_lon, max_lat, max_lon)
SA_BOUNDS: tuple[float, float, float, float] = (-35.0, 16.0, -22.0, 33.5)

GOOGLE_TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
GOOGLE_FIELD_MASK = (
    "places.displayName,places.formattedAddress,places.location,places.types"
)
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_USER_AGENT = "STAM-IRM-LocationCapture/1.0 (Gauteng GT/GDeG/031/2025)"

REQUEST_TIMEOUT_SECONDS = 12


@dataclass(frozen=True)
class GeocodeHit:
    """One search result."""
    name: str
    formatted_address: str
    lat: float
    lon: float
    provider: str = ""
    place_types: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        if self.formatted_address and self.formatted_address != self.name:
            return f"{self.name} — {self.formatted_address}"
        return self.name


@dataclass(frozen=True)
class SearchOutcome:
    """Result of a search: hits plus a human-readable status message."""
    hits: tuple[GeocodeHit, ...] = ()
    message: str = ""
    provider: str = ""
    ok: bool = True

    def __bool__(self) -> bool:
        return bool(self.hits)


# ── Bounds ────────────────────────────────────────────────────────────────────

def within_south_africa(lat: float | None, lon: float | None) -> bool:
    """True when the coordinate falls inside the South African bounding box."""
    if lat is None or lon is None:
        return False
    try:
        lat_f, lon_f = float(lat), float(lon)
    except (TypeError, ValueError):
        return False
    min_lat, min_lon, max_lat, max_lon = SA_BOUNDS
    return min_lat <= lat_f <= max_lat and min_lon <= lon_f <= max_lon


# ── Configuration ─────────────────────────────────────────────────────────────

def _get_secret(name: str, default: str = "") -> str:
    """Resolve a value from Streamlit secrets (cloud) then env var (local)."""
    try:
        import streamlit as st
        value = st.secrets.get(name, "")
        if value:
            return str(value)
    except Exception:
        pass
    return os.environ.get(name, default)


def google_api_key() -> str:
    return _get_secret("GOOGLE_MAPS_API_KEY").strip()


def active_provider() -> str:
    """
    Which provider will actually be used.

    Honours CAPTURE_GEOCODER ("google" | "nominatim") but falls back to Nominatim
    whenever Google is requested without a key, so search always works.
    """
    preferred = (_get_secret("CAPTURE_GEOCODER", "google") or "google").lower()
    if preferred == "google" and not google_api_key():
        return "nominatim"
    return "nominatim" if preferred == "nominatim" else "google"


# ── Response parsing (pure — unit tested without network) ─────────────────────

def parse_google_response(payload: dict, limit: int = 8) -> list[GeocodeHit]:
    """Convert a Places Text Search payload into hits inside South Africa."""
    hits: list[GeocodeHit] = []
    for place in (payload or {}).get("places", []) or []:
        location = place.get("location") or {}
        lat, lon = location.get("latitude"), location.get("longitude")
        if not within_south_africa(lat, lon):
            continue
        name = (place.get("displayName") or {}).get("text") or ""
        address = place.get("formattedAddress") or ""
        if not name and not address:
            continue
        hits.append(GeocodeHit(
            name=name or address,
            formatted_address=address,
            lat=float(lat),
            lon=float(lon),
            provider="google",
            place_types=tuple(place.get("types") or ()),
        ))
        if len(hits) >= limit:
            break
    return hits


def parse_nominatim_response(payload: list, limit: int = 8) -> list[GeocodeHit]:
    """Convert a Nominatim JSON array into hits inside South Africa."""
    hits: list[GeocodeHit] = []
    for item in payload or []:
        try:
            lat, lon = float(item.get("lat")), float(item.get("lon"))
        except (TypeError, ValueError):
            continue
        if not within_south_africa(lat, lon):
            continue
        display = item.get("display_name") or ""
        name = item.get("name") or display.split(",")[0].strip()
        if not name:
            continue
        hits.append(GeocodeHit(
            name=name,
            formatted_address=display,
            lat=lat,
            lon=lon,
            provider="nominatim",
            place_types=tuple(t for t in (item.get("type"), item.get("class")) if t),
        ))
        if len(hits) >= limit:
            break
    return hits


# ── Providers ─────────────────────────────────────────────────────────────────

def _search_google(query: str, limit: int) -> SearchOutcome:
    key = google_api_key()
    if not key:
        return SearchOutcome(
            message="GOOGLE_MAPS_API_KEY is not set — add it to .env or Streamlit "
                    "secrets to enable Google search.",
            provider="google",
            ok=False,
        )

    min_lat, min_lon, max_lat, max_lon = SA_BOUNDS
    body = {
        "textQuery": query,
        "regionCode": "ZA",
        "maxResultCount": max(1, min(limit, 20)),
        "locationRestriction": {
            "rectangle": {
                "low":  {"latitude": min_lat, "longitude": min_lon},
                "high": {"latitude": max_lat, "longitude": max_lon},
            }
        },
    }
    try:
        response = requests.post(
            GOOGLE_TEXT_SEARCH_URL,
            json=body,
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": key,
                "X-Goog-FieldMask": GOOGLE_FIELD_MASK,
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code != 200:
            # Never surface the response body — it can echo the API key.
            return SearchOutcome(
                message=f"Google Places search failed (HTTP {response.status_code}). "
                        "Check the API key, billing, and that the Places API (New) "
                        "is enabled.",
                provider="google",
                ok=False,
            )
        hits = parse_google_response(response.json(), limit)
    except requests.RequestException:
        return SearchOutcome(
            message="Could not reach Google Places (network error). "
                    "Draw on the map or enter coordinates manually.",
            provider="google",
            ok=False,
        )
    except ValueError:
        return SearchOutcome(
            message="Google Places returned an unreadable response.",
            provider="google",
            ok=False,
        )

    message = "" if hits else f"No South African places matched “{query}”."
    return SearchOutcome(hits=tuple(hits), message=message, provider="google")


def _search_nominatim(query: str, limit: int) -> SearchOutcome:
    min_lat, min_lon, max_lat, max_lon = SA_BOUNDS
    try:
        response = requests.get(
            NOMINATIM_URL,
            params={
                "q": query,
                "format": "jsonv2",
                "countrycodes": "za",
                "limit": max(1, min(limit, 20)),
                "addressdetails": 0,
                "viewbox": f"{min_lon},{max_lat},{max_lon},{min_lat}",
                "bounded": 1,
            },
            headers={"User-Agent": NOMINATIM_USER_AGENT},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code != 200:
            return SearchOutcome(
                message=f"OpenStreetMap search failed (HTTP {response.status_code}).",
                provider="nominatim",
                ok=False,
            )
        hits = parse_nominatim_response(response.json(), limit)
    except requests.RequestException:
        return SearchOutcome(
            message="Could not reach OpenStreetMap (network error). "
                    "Draw on the map or enter coordinates manually.",
            provider="nominatim",
            ok=False,
        )
    except ValueError:
        return SearchOutcome(
            message="OpenStreetMap returned an unreadable response.",
            provider="nominatim",
            ok=False,
        )

    message = "" if hits else f"No South African places matched “{query}”."
    return SearchOutcome(hits=tuple(hits), message=message, provider="nominatim")


# ── Public API ────────────────────────────────────────────────────────────────

def search(query: str, limit: int = 8) -> SearchOutcome:
    """
    Search for an address or landmark in South Africa.

    Results are cached for an hour when Streamlit is available, so repeated
    searches during a session cost nothing.
    """
    query = (query or "").strip()
    if len(query) < 3:
        return SearchOutcome(message="Enter at least 3 characters to search.", ok=False)

    provider = active_provider()
    try:
        import streamlit as st
        cached = st.cache_data(ttl=3600, show_spinner=False)(_search_uncached)
        return cached(query, limit, provider)
    except Exception:
        return _search_uncached(query, limit, provider)


def _search_uncached(query: str, limit: int, provider: str) -> SearchOutcome:
    if provider == "nominatim":
        return _search_nominatim(query, limit)
    outcome = _search_google(query, limit)
    # A missing key or dead endpoint should not block the user — fall back.
    if not outcome.ok:
        fallback = _search_nominatim(query, limit)
        if fallback.hits:
            return SearchOutcome(
                hits=fallback.hits,
                message=f"{outcome.message} Showing OpenStreetMap results instead.",
                provider="nominatim",
            )
    return outcome
