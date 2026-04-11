"""Background monitoring agent for NYC Urban Intelligence System.

Runs every 5 minutes and:
- Polls FloodNet sensors for new flood events
- Checks 311 API for new complaints in tracked categories
- Cross-references: if 3+ flood reports near a spiking sensor -> auto-confirm
- Detects anomalies: sudden spikes in complaints for a zip code
- Creates incidents automatically from data signals

Registered as a FastAPI background task on startup in server.py.
"""

import asyncio
import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from math import radians, cos, sin, sqrt, atan2

from hackathon_nyc import db
from hackathon_nyc.tools import floodnet, nyc_opendata

logger = logging.getLogger(__name__)

# Module-level state
_monitor_task: asyncio.Task | None = None
_last_flood_ids: set[str] = set()
_last_311_keys: set[str] = set()
_complaint_history: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
# { zip_code: { category: count_this_cycle } }

POLL_INTERVAL_SECONDS = 300  # 5 minutes
TRACKED_311_TYPES = [
    "Sewer",
    "Noise - Residential",
    "Noise - Commercial",
    "Rodent",
    "HEAT/HOT WATER",
    "Water System",
    "Street Condition",
]
ANOMALY_THRESHOLD_MULTIPLIER = 4  # 400% spike triggers anomaly
CROSS_REF_RADIUS_MILES = 0.25
CROSS_REF_THRESHOLD = 3  # 3+ reports to auto-confirm


def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate great-circle distance in miles between two lat/lon points."""
    R = 3959.0  # Earth radius in miles
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))


async def _poll_floodnet() -> list[dict]:
    """Check FloodNet for new flood events in the last poll interval.

    Returns list of new flood events not seen in the previous cycle.
    """
    global _last_flood_ids

    try:
        # Look back slightly more than the poll interval to avoid gaps
        events = await floodnet.get_active_floods(hours_back=1)
        if not events or (len(events) == 1 and "error" in events[0]):
            return []

        new_events = []
        current_ids = set()
        for event in events:
            eid = event.get("unique_id") or event.get("sensor_id", "") + event.get("flood_start", "")
            current_ids.add(eid)
            if eid not in _last_flood_ids:
                new_events.append(event)

        _last_flood_ids = current_ids
        return new_events
    except Exception as e:
        logger.error("[Monitor] FloodNet poll failed: %s", e)
        return []


async def _poll_311() -> list[dict]:
    """Check 311 API for new complaints in our tracked categories.

    Returns list of new complaints not seen in the previous cycle.
    """
    global _last_311_keys

    try:
        all_new = []
        current_keys = set()
        for ctype in TRACKED_311_TYPES:
            complaints = await nyc_opendata.get_311_complaints(complaint_type=ctype, limit=20)
            if not complaints or (len(complaints) == 1 and "error" in complaints[0]):
                continue
            for c in complaints:
                key = c.get("unique_key", "")
                current_keys.add(key)
                if key and key not in _last_311_keys:
                    all_new.append(c)

        _last_311_keys = current_keys
        return all_new
    except Exception as e:
        logger.error("[Monitor] 311 poll failed: %s", e)
        return []


def _cross_reference_floods(new_floods: list[dict], new_311: list[dict]) -> list[dict]:
    """Cross-reference flood sensor spikes with 311 sewer/flood complaints.

    If 3+ flood/sewer 311 reports are near a spiking sensor, return the
    cluster for auto-confirmation.
    """
    clusters = []

    for flood in new_floods:
        try:
            flat = float(flood.get("latitude", 0))
            flon = float(flood.get("longitude", 0))
            if flat == 0 or flon == 0:
                continue
        except (ValueError, TypeError):
            continue

        nearby_reports = []
        for complaint in new_311:
            ctype = (complaint.get("complaint_type") or "").lower()
            if "sewer" not in ctype and "flood" not in ctype and "water" not in ctype:
                continue
            try:
                clat = float(complaint.get("latitude", 0))
                clon = float(complaint.get("longitude", 0))
                if clat == 0 or clon == 0:
                    continue
            except (ValueError, TypeError):
                continue

            if _haversine_miles(flat, flon, clat, clon) <= CROSS_REF_RADIUS_MILES:
                nearby_reports.append(complaint)

        if len(nearby_reports) >= CROSS_REF_THRESHOLD:
            clusters.append({
                "sensor_event": flood,
                "nearby_311_count": len(nearby_reports),
                "nearby_reports": nearby_reports[:5],
            })

    return clusters


def _detect_anomalies(new_311: list[dict]) -> list[dict]:
    """Detect anomalies: sudden spike in complaint counts per zip+category.

    Compares this cycle's counts against the running average from
    _complaint_history. A 400%+ spike triggers an anomaly alert.
    """
    global _complaint_history

    # Count this cycle
    cycle_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for c in new_311:
        zip_code = c.get("incident_zip", "unknown")
        category = c.get("complaint_type", "unknown")
        cycle_counts[zip_code][category] += 1

    anomalies = []
    for zip_code, cats in cycle_counts.items():
        for category, count in cats.items():
            prev = _complaint_history.get(zip_code, {}).get(category, 0)
            if prev > 0 and count >= prev * ANOMALY_THRESHOLD_MULTIPLIER:
                anomalies.append({
                    "type": "anomaly",
                    "zip_code": zip_code,
                    "category": category,
                    "current_count": count,
                    "previous_count": prev,
                    "multiplier": round(count / prev, 1) if prev > 0 else 999,
                    "message": (
                        f"{category} complaints in zip {zip_code} up "
                        f"{round(count / prev * 100)}% this cycle "
                        f"({prev} -> {count})"
                    ),
                })

    # Update history with this cycle's data (simple rolling: just store latest)
    for zip_code, cats in cycle_counts.items():
        for category, count in cats.items():
            _complaint_history[zip_code][category] = count

    return anomalies


async def _create_auto_incident(title: str, category: str, description: str,
                                 severity: str, lat: float | None, lon: float | None,
                                 source: str, sensor_id: str = "", related_311: str = ""):
    """Create an incident from automated monitoring signals."""
    try:
        incident = db.create_incident(
            title=title,
            category=category,
            description=description,
            severity=severity,
            latitude=lat,
            longitude=lon,
            source=source,
            related_sensor_id=sensor_id,
            related_311_id=related_311,
        )
        logger.info("[Monitor] Auto-created incident #%s: %s", incident["id"][:8], title)
        return incident
    except Exception as e:
        logger.error("[Monitor] Failed to create incident: %s", e)
        return None


async def _run_cycle():
    """Execute one monitoring cycle."""
    logger.info("[Monitor] Starting monitoring cycle at %s", datetime.utcnow().isoformat())

    # Poll both data sources concurrently
    new_floods, new_311 = await asyncio.gather(
        _poll_floodnet(),
        _poll_311(),
    )

    logger.info("[Monitor] Found %d new flood events, %d new 311 complaints",
                len(new_floods), len(new_311))

    # --- Process new flood events -> create incidents ---
    for flood in new_floods:
        try:
            depth = float(flood.get("max_depth_inches", 0) or flood.get("depth_inches", 0))
            if depth <= 0:
                continue
            sensor_id = flood.get("sensor_id", "unknown")
            try:
                lat = float(flood.get("latitude", 0))
                lon = float(flood.get("longitude", 0))
            except (ValueError, TypeError):
                lat, lon = None, None

            severity = "low"
            if depth >= 3:
                severity = "medium"
            if depth >= 6:
                severity = "high"
            if depth >= 12:
                severity = "critical"

            await _create_auto_incident(
                title=f"FloodNet: {depth:.1f}in at sensor {sensor_id}",
                category="flooding",
                description=(
                    f"Automated detection from FloodNet sensor {sensor_id}. "
                    f"Max depth: {depth:.1f} inches. "
                    f"Start: {flood.get('flood_start', 'N/A')}."
                ),
                severity=severity,
                lat=lat, lon=lon,
                source="monitor_floodnet",
                sensor_id=sensor_id,
            )
        except Exception as e:
            logger.error("[Monitor] Error processing flood event: %s", e)

    # --- Cross-reference floods + 311 -> auto-confirm ---
    clusters = _cross_reference_floods(new_floods, new_311)
    for cluster in clusters:
        sensor = cluster["sensor_event"]
        sensor_id = sensor.get("sensor_id", "unknown")
        try:
            lat = float(sensor.get("latitude", 0))
            lon = float(sensor.get("longitude", 0))
        except (ValueError, TypeError):
            lat, lon = None, None

        incident = await _create_auto_incident(
            title=f"CONFIRMED: Flooding near sensor {sensor_id} ({cluster['nearby_311_count']} reports)",
            category="flooding",
            description=(
                f"Auto-confirmed by cross-referencing FloodNet sensor {sensor_id} spike "
                f"with {cluster['nearby_311_count']} nearby 311 sewer/flood complaints. "
                f"This meets the {CROSS_REF_THRESHOLD}+ report threshold."
            ),
            severity="high",
            lat=lat, lon=lon,
            source="monitor_crossref",
            sensor_id=sensor_id,
        )
        if incident:
            # Auto-confirm since we have sensor + citizen corroboration
            db.confirm_incident(incident["id"], confirmed_by="monitor_crossref")
            logger.info("[Monitor] Auto-confirmed incident #%s (cross-referenced)", incident["id"][:8])

    # --- Detect anomalies ---
    anomalies = _detect_anomalies(new_311)
    for anomaly in anomalies:
        logger.warning("[Monitor] ANOMALY: %s", anomaly["message"])
        await _create_auto_incident(
            title=f"Anomaly: {anomaly['category']} spike in {anomaly['zip_code']}",
            category=anomaly["category"].lower().replace(" - ", "_").replace(" ", "_"),
            description=(
                f"Automated anomaly detection: {anomaly['message']}. "
                f"This is a {anomaly['multiplier']}x increase from the previous monitoring cycle."
            ),
            severity="medium",
            lat=None, lon=None,
            source="monitor_anomaly",
        )

    logger.info("[Monitor] Cycle complete. Clusters=%d, Anomalies=%d",
                len(clusters), len(anomalies))


async def _monitor_loop():
    """Main loop that runs _run_cycle every POLL_INTERVAL_SECONDS."""
    # Wait a bit on startup to let the app fully initialize
    await asyncio.sleep(10)
    logger.info("[Monitor] Background monitor started (interval=%ds)", POLL_INTERVAL_SECONDS)

    while True:
        try:
            await _run_cycle()
        except asyncio.CancelledError:
            logger.info("[Monitor] Monitor task cancelled, shutting down.")
            break
        except Exception as e:
            logger.error("[Monitor] Unhandled error in monitor cycle: %s", e, exc_info=True)

        try:
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            logger.info("[Monitor] Monitor sleep cancelled, shutting down.")
            break


async def start_monitor():
    """Start the background monitoring task. Called from server.py lifespan."""
    global _monitor_task
    if _monitor_task is None or _monitor_task.done():
        _monitor_task = asyncio.create_task(_monitor_loop())
        logger.info("[Monitor] Background monitor task created.")


async def stop_monitor():
    """Stop the background monitoring task. Called from server.py lifespan."""
    global _monitor_task
    if _monitor_task is not None and not _monitor_task.done():
        _monitor_task.cancel()
        try:
            await _monitor_task
        except asyncio.CancelledError:
            pass
        logger.info("[Monitor] Background monitor stopped.")
    _monitor_task = None
