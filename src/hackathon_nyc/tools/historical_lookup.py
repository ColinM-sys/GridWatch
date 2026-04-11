"""RAG retrieval tool — queries the local ChromaDB built by ingest.py.

Returns top-k historical NYC Open Data chunks relevant to a free-text
query so the agent can answer questions about past incidents and trends.
"""

from pathlib import Path
from typing import Optional

DATA_DIR = Path(__file__).parent.parent.parent.parent / "data"
CHROMA_PATH = DATA_DIR / "chromadb"

# Collections worth searching for citizen-routing / dispatch context.
DEFAULT_COLLECTIONS = [
    "nyc_311_current",
    "nyc_311_historical",
    "nyc_collisions",
    "nyc_potholes",
    "nyc_rodent_inspections",
    "nyc_housing_violations",
    "nyc_flood_events",
]

_client = None
_sensor_coords: dict = {}  # sensor_id -> (lat, lon)


async def _load_sensor_coords():
    """Fetch FloodNet sensor coordinates once so flood_events records can be plotted."""
    global _sensor_coords
    if _sensor_coords:
        return
    try:
        from hackathon_nyc.tools import floodnet
        sensors = await floodnet.get_sensor_locations()
        for s in sensors:
            sid = s.get("sensor_id") or s.get("deployment_id")
            lat = s.get("latitude") or s.get("lat")
            lon = s.get("longitude") or s.get("lon")
            if sid and lat and lon:
                try:
                    _sensor_coords[str(sid)] = (float(lat), float(lon))
                except (TypeError, ValueError):
                    pass
    except Exception:
        pass


def _get_client():
    global _client
    if _client is None:
        import chromadb
        _client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    return _client


async def historical_lookup(query: str, k: int = 5,
                            collections: Optional[list[str]] = None) -> dict:
    """Search historical NYC Open Data for chunks relevant to `query`.

    Args:
        query: natural-language question (e.g. "rat complaints near Bushwick").
        k: number of results to return per collection (then merged).
        collections: override the default set of ChromaDB collections.

    Returns:
        {"query": str, "results": [{"collection","text","distance"}, ...]}
    """
    try:
        client = _get_client()
    except Exception as e:
        return {"error": f"ChromaDB not available: {e}. Run ingest.py first."}

    targets = collections or DEFAULT_COLLECTIONS
    merged: list[dict] = []

    for name in targets:
        try:
            coll = client.get_collection(name=name)
        except Exception:
            continue
        try:
            res = coll.query(query_texts=[query], n_results=k)
        except Exception:
            continue
        docs = (res.get("documents") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        for doc, dist in zip(docs, dists):
            merged.append({
                "collection": name.replace("nyc_", ""),
                "text": doc,
                "distance": float(dist) if dist is not None else None,
            })

    merged.sort(key=lambda r: (r["distance"] is None, r["distance"] or 0))
    top = merged[: k * 2]

    # If any flood_events results, preload sensor coords for join
    if any("flood_events" in c["collection"] for c in top):
        await _load_sensor_coords()

    # Extract lat/lon points from chunk text so the frontend can plot them
    import re
    points: list[dict] = []
    seen: set[tuple] = set()
    sensor_re = re.compile(r"sensor_id[\"\s:]+([A-Za-z0-9_\-]+)")
    # Match common shapes:
    #   latitude: 40.7128 | longitude: -74.006
    #   [-74.006, 40.7128]   (geojson coord pair, lon first)
    lat_re = re.compile(r"latitude[\"\s:]+(-?\d{1,2}\.\d{3,})", re.I)
    lon_re = re.compile(r"longitude[\"\s:]+(-?\d{1,3}\.\d{3,})", re.I)
    coord_re = re.compile(r"\[\s*(-7[34]\.\d{3,})\s*,\s*(40\.\d{3,})\s*\]")
    for c in top:
        text = c["text"]
        for chunk_text in text.split("\n---\n"):
            lat = lat_re.search(chunk_text)
            lon = lon_re.search(chunk_text)
            if lat and lon:
                la, lo = float(lat.group(1)), float(lon.group(1))
            else:
                m = coord_re.search(chunk_text)
                if m:
                    lo, la = float(m.group(1)), float(m.group(2))
                else:
                    # Last resort: flood_events records — look up sensor_id in coords map
                    sm = sensor_re.search(chunk_text)
                    if sm and sm.group(1) in _sensor_coords:
                        la, lo = _sensor_coords[sm.group(1)]
                    else:
                        continue
            if not (40.4 <= la <= 41.0 and -74.3 <= lo <= -73.6):
                continue
            key = (round(la, 4), round(lo, 4))
            if key in seen:
                continue
            seen.add(key)
            label = chunk_text[:120].replace("|", " ")
            points.append({"lat": la, "lon": lo,
                           "collection": c["collection"], "label": label})
            if len(points) >= 25:
                break
        if len(points) >= 25:
            break

    return {"query": query, "results": top, "points": points}
