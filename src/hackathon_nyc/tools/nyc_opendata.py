"""NYC Open Data SODA API client tools.

Queries NYC Open Data datasets via the Socrata Open Data API (SODA).
Endpoints: https://data.cityofnewyork.us/resource/{dataset_id}.json
"""

import aiohttp

NYC_OPENDATA_BASE = "https://data.cityofnewyork.us/resource"

DATASETS = {
    # Core
    "311_current": "erm2-nwe9",            # 311 Service Requests 2020-Present (20M+)
    "311_historical": "76ig-c548",          # 311 Service Requests 2010-2019
    # Flooding / Environmental
    "air_quality": "c3uy-2p5r",             # Air Quality (PM2.5, NO2)
    "flood_events": "aq7i-eu5q",            # FloodNet: Street Flooding Events
    "flood_sensors": "kb2e-tjy3",           # FloodNet: Sensor Locations
    "flood_vulnerability": "mrjc-v9pm",     # Flood Vulnerability Index
    "heat_vulnerability": "4mhf-duep",      # Heat Vulnerability Index
    "street_trees": "uvpi-gqnh",            # Street Tree Census (666K)
    "greenhouse_gas": "wq7q-htne",          # Greenhouse Gas Emissions
    "floodplain_2050": "27ya-gqtm",         # Future Floodplain 2050s
    "community_gardens": "p78i-pat6",       # GreenThumb Garden Info
    "pluto": "64uk-42ks",                   # Land Use Tax Lot Output
    # Safety
    "collisions": "h9gi-nx95",              # Motor Vehicle Collisions (2.2M, daily)
    "nypd_complaints": "5uac-w243",         # NYPD Complaints Current YTD (580K)
    # Health
    "restaurant_inspections": "43nn-pn8j",  # Restaurant Inspections (296K, daily)
    "rodent_inspections": "p937-wjvj",      # Rodent Inspections (3M, daily)
    # Housing
    "housing_violations": "wvxf-dwi5",      # Housing Code Violations (10.8M, daily)
    "evictions": "6z8x-wfk4",              # Evictions (126K, daily)
    # Infrastructure
    "potholes": "x9wy-ing4",               # Pothole Work Orders (399K)
    "construction_permits": "rbx6-tga4",    # Active Construction Permits (917K, daily)
}


async def query_dataset(
    dataset_key: str,
    where_clause: str = "",
    select: str = "",
    limit: int = 100,
    order: str = "",
) -> list[dict]:
    """Query an NYC Open Data dataset via SODA API.

    Args:
        dataset_key: Key from DATASETS dict (e.g. '311_current', 'flood_events')
        where_clause: SoQL WHERE filter (e.g. "complaint_type='Sewer'")
        select: SoQL SELECT fields (e.g. "latitude,longitude,complaint_type")
        limit: Max rows to return (default 100, max 50000)
        order: SoQL ORDER BY (e.g. "created_date DESC")

    Returns:
        List of record dicts from the API.
    """
    dataset_id = DATASETS.get(dataset_key)
    if not dataset_id:
        return [{"error": f"Unknown dataset '{dataset_key}'. Available: {list(DATASETS.keys())}"}]

    url = f"{NYC_OPENDATA_BASE}/{dataset_id}.json"
    params = {"$limit": str(limit)}
    if where_clause:
        params["$where"] = where_clause
    if select:
        params["$select"] = select
    if order:
        params["$order"] = order

    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                return [{"error": f"API returned {resp.status}: {await resp.text()}"}]
            return await resp.json()


async def get_311_complaints(
    complaint_type: str = "",
    borough: str = "",
    zip_code: str = "",
    limit: int = 50,
) -> list[dict]:
    """Get 311 service requests filtered by type, borough, or zip code.

    Args:
        complaint_type: Filter by complaint type (e.g. 'Noise - Residential', 'Sewer', 'Rodent')
        borough: Filter by borough (e.g. 'BROOKLYN', 'MANHATTAN')
        zip_code: Filter by incident zip code
        limit: Max results (default 50)

    Returns:
        List of 311 complaint records with location, status, and resolution.
    """
    conditions = []
    if complaint_type:
        conditions.append(f"complaint_type='{complaint_type}'")
    if borough:
        conditions.append(f"borough='{borough.upper()}'")
    if zip_code:
        conditions.append(f"incident_zip='{zip_code}'")

    where = " AND ".join(conditions) if conditions else ""
    return await query_dataset(
        "311_current",
        where_clause=where,
        select="unique_key,created_date,complaint_type,descriptor,borough,incident_zip,latitude,longitude,status,resolution_description",
        limit=limit,
        order="created_date DESC",
    )


async def get_flood_events(limit: int = 50) -> list[dict]:
    """Get recent FloodNet street flooding events with sensor data.

    Returns flood events including max depth, duration, onset/drain time.
    """
    return await query_dataset(
        "flood_events",
        limit=limit,
        order="flood_start DESC" if limit <= 1000 else "",
    )


async def get_flood_sensors() -> list[dict]:
    """Get all FloodNet sensor deployment locations and metadata."""
    return await query_dataset("flood_sensors", limit=500)


async def get_air_quality(neighborhood: str = "", limit: int = 100) -> list[dict]:
    """Get air quality data (PM2.5, NO2) by NYC neighborhood.

    Args:
        neighborhood: Filter by neighborhood name (partial match)
        limit: Max results
    """
    where = f"upper(geo_place_name) like '%{neighborhood.upper()}%'" if neighborhood else ""
    return await query_dataset("air_quality", where_clause=where, limit=limit)


async def get_flood_vulnerability(limit: int = 100) -> list[dict]:
    """Get flood vulnerability index scores by neighborhood."""
    return await query_dataset("flood_vulnerability", limit=limit)


async def get_311_complaint_stats(
    complaint_type: str = "",
    borough: str = "",
    group_by: str = "complaint_type",
) -> list[dict]:
    """Get aggregated 311 complaint statistics.

    Args:
        complaint_type: Filter by type
        borough: Filter by borough
        group_by: Field to group by (default: complaint_type)

    Returns:
        Aggregated counts grouped by the specified field.
    """
    conditions = []
    if complaint_type:
        conditions.append(f"complaint_type='{complaint_type}'")
    if borough:
        conditions.append(f"borough='{borough.upper()}'")

    where = " AND ".join(conditions) if conditions else ""
    return await query_dataset(
        "311_current",
        where_clause=where,
        select=f"{group_by}, count(*) as count",
        limit=50,
        order="count DESC",
    )
