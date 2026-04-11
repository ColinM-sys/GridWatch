"""Geocoding and geospatial utility tools.

Uses the free Nominatim (OpenStreetMap) API for geocoding.
No API key required.
"""

import aiohttp
import math

NOMINATIM_URL = "https://nominatim.openstreetmap.org"
HEADERS = {"User-Agent": "HackathonNYC-FloodWatch/1.0"}


async def geocode_address(address: str) -> dict:
    """Convert a street address to lat/lon coordinates.

    Args:
        address: Street address (e.g. "123 Main St, Brooklyn, NY")

    Returns:
        Dict with lat, lon, display_name, and bounding box.
    """
    params = {
        "q": address,
        "format": "json",
        "limit": "1",
        "countrycodes": "us",
    }

    async with aiohttp.ClientSession(headers=HEADERS) as session:
        async with session.get(f"{NOMINATIM_URL}/search", params=params) as resp:
            if resp.status != 200:
                return {"error": f"Geocoding failed: {resp.status}"}
            results = await resp.json()
            if not results:
                return {"error": f"No results for '{address}'"}
            r = results[0]
            return {
                "lat": float(r["lat"]),
                "lon": float(r["lon"]),
                "display_name": r["display_name"],
                "boundingbox": r.get("boundingbox"),
            }


async def reverse_geocode(lat: float, lon: float) -> dict:
    """Convert lat/lon coordinates to a street address.

    Args:
        lat: Latitude
        lon: Longitude

    Returns:
        Dict with address components and display name.
    """
    params = {
        "lat": str(lat),
        "lon": str(lon),
        "format": "json",
    }

    async with aiohttp.ClientSession(headers=HEADERS) as session:
        async with session.get(f"{NOMINATIM_URL}/reverse", params=params) as resp:
            if resp.status != 200:
                return {"error": f"Reverse geocoding failed: {resp.status}"}
            result = await resp.json()
            return {
                "display_name": result.get("display_name"),
                "address": result.get("address", {}),
            }


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance in miles between two lat/lon points."""
    R = 3959  # Earth radius in miles
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def find_nearest_points(lat: float, lon: float, points: list[dict], top_n: int = 5) -> list[dict]:
    """Find the nearest N points to a given location.

    Args:
        lat: Reference latitude
        lon: Reference longitude
        points: List of dicts with 'latitude' and 'longitude' keys
        top_n: Number of nearest points to return

    Returns:
        Sorted list of nearest points with distance_miles added.
    """
    for p in points:
        try:
            p_lat = float(p.get("latitude", 0))
            p_lon = float(p.get("longitude", 0))
            p["distance_miles"] = haversine_distance(lat, lon, p_lat, p_lon)
        except (ValueError, TypeError):
            p["distance_miles"] = float("inf")

    return sorted(points, key=lambda x: x["distance_miles"])[:top_n]
