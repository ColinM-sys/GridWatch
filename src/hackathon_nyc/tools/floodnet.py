"""FloodNet real-time sensor tools.

Queries FloodNet NYC's sensor network for live flood depth readings
and historical flood event data. Sensors are ultrasonic depth sensors
deployed at known flood-prone locations across NYC.
"""

import aiohttp
from datetime import datetime, timedelta


FLOODNET_API = "https://data.cityofnewyork.us/resource"
FLOOD_EVENTS_ID = "aq7i-eu5q"
FLOOD_SENSORS_ID = "kb2e-tjy3"


async def get_active_floods(hours_back: int = 24) -> list[dict]:
    """Get flooding events from the last N hours.

    Args:
        hours_back: Look back this many hours (default 24)

    Returns:
        List of active/recent flood events with depth, location, timing.
    """
    cutoff = (datetime.utcnow() - timedelta(hours=hours_back)).isoformat()
    url = f"{FLOODNET_API}/{FLOOD_EVENTS_ID}.json"
    params = {
        "$where": f"flood_start > '{cutoff}'",
        "$order": "flood_start DESC",
        "$limit": "100",
    }

    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                return [{"error": f"FloodNet API returned {resp.status}"}]
            return await resp.json()


async def get_sensor_locations() -> list[dict]:
    """Get all FloodNet sensor deployment locations with coordinates.

    Returns:
        List of sensors with lat/lon, deployment date, and location description.
    """
    url = f"{FLOODNET_API}/{FLOOD_SENSORS_ID}.json"
    params = {"$limit": "500"}

    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                return [{"error": f"FloodNet API returned {resp.status}"}]
            return await resp.json()


async def get_worst_floods(top_n: int = 10) -> list[dict]:
    """Get the worst flooding events by max depth.

    Args:
        top_n: Number of worst events to return (default 10)

    Returns:
        Flood events sorted by maximum flood depth (deepest first).
    """
    url = f"{FLOODNET_API}/{FLOOD_EVENTS_ID}.json"
    params = {
        "$order": "max_depth_inches DESC",
        "$limit": str(top_n),
        "$where": "max_depth_inches IS NOT NULL",
    }

    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                return [{"error": f"FloodNet API returned {resp.status}"}]
            return await resp.json()


async def get_flood_history_for_sensor(sensor_id: str, limit: int = 50) -> list[dict]:
    """Get flood history for a specific sensor.

    Args:
        sensor_id: The sensor identifier
        limit: Max events to return

    Returns:
        Historical flood events at that sensor location.
    """
    url = f"{FLOODNET_API}/{FLOOD_EVENTS_ID}.json"
    params = {
        "$where": f"sensor_id='{sensor_id}'",
        "$order": "flood_start DESC",
        "$limit": str(limit),
    }

    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                return [{"error": f"FloodNet API returned {resp.status}"}]
            return await resp.json()
