"""Local SQLite database for incident management (CRM).

Stores incidents that dispatchers can create, update, assign, and resolve.
Runs on-device — no external database needed. Perfect for DGX Spark hackathon.
"""

import sqlite3
import json
import uuid
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent.parent.parent / "data" / "incidents.db"


def get_db() -> sqlite3.Connection:
    """Get a database connection, creating tables if needed."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _create_tables(conn)
    return conn


def _create_tables(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS incidents (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT,
            category TEXT NOT NULL,
            severity TEXT DEFAULT 'medium',
            status TEXT DEFAULT 'open',
            confirmed INTEGER DEFAULT 0,
            report_count INTEGER DEFAULT 1,
            assigned_to TEXT,
            latitude REAL,
            longitude REAL,
            address TEXT,
            borough TEXT,
            zip_code TEXT,
            source TEXT DEFAULT 'dispatcher',
            related_311_id TEXT,
            related_sensor_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            resolved_at TEXT,
            notes TEXT
        );

        CREATE TABLE IF NOT EXISTS incident_updates (
            id TEXT PRIMARY KEY,
            incident_id TEXT NOT NULL,
            update_type TEXT NOT NULL,
            message TEXT,
            updated_by TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (incident_id) REFERENCES incidents(id)
        );

        -- Add upvote/downvote columns if they don't exist
        CREATE TABLE IF NOT EXISTS incident_votes (
            incident_id TEXT NOT NULL,
            vote INTEGER NOT NULL,  -- 1 = upvote, -1 = downvote
            voter_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(incident_id, voter_id),
            FOREIGN KEY (incident_id) REFERENCES incidents(id)
        );

        CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents(status);
        CREATE INDEX IF NOT EXISTS idx_incidents_category ON incidents(category);
        CREATE INDEX IF NOT EXISTS idx_incidents_borough ON incidents(borough);
        CREATE INDEX IF NOT EXISTS idx_incidents_created ON incidents(created_at);

        CREATE TABLE IF NOT EXISTS alert_subscriptions (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            contact TEXT NOT NULL,
            contact_type TEXT DEFAULT 'sms',
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            address TEXT,
            radius_miles REAL DEFAULT 1.0,
            categories TEXT DEFAULT '',
            active INTEGER DEFAULT 1,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_alerts_active ON alert_subscriptions(active);
    """)
    conn.commit()


def vote_incident(incident_id: str, vote: int, voter_id: str = "anon") -> dict | None:
    """Upvote (+1) or downvote (-1) an incident. Auto-confirms at 3+ net upvotes."""
    conn = get_db()
    now = datetime.utcnow().isoformat()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO incident_votes (incident_id, vote, voter_id, created_at) VALUES (?, ?, ?, ?)",
            (incident_id, vote, voter_id, now),
        )
        conn.commit()
    except Exception:
        pass

    # Count net votes
    row = conn.execute(
        "SELECT COALESCE(SUM(vote), 0) as net FROM incident_votes WHERE incident_id = ?",
        (incident_id,),
    ).fetchone()
    net_votes = row["net"] if row else 0

    # Auto-confirm at 3+ net upvotes
    if net_votes >= CONFIRM_THRESHOLD:
        conn.execute("UPDATE incidents SET confirmed = 1, updated_at = ? WHERE id = ?", (now, incident_id))
        conn.commit()

    # Auto-hide at -3 net downvotes
    if net_votes <= -3:
        conn.execute("UPDATE incidents SET status = 'resolved', notes = 'Community downvoted', updated_at = ? WHERE id = ?", (now, incident_id))
        conn.commit()

    conn.close()
    result = get_incident(incident_id)
    if result:
        result["net_votes"] = net_votes
    return result


def get_incident_votes(incident_id: str) -> dict:
    """Get vote counts for an incident."""
    conn = get_db()
    row = conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN vote=1 THEN 1 ELSE 0 END),0) as up, COALESCE(SUM(CASE WHEN vote=-1 THEN 1 ELSE 0 END),0) as down, COALESCE(SUM(vote),0) as net FROM incident_votes WHERE incident_id = ?",
        (incident_id,),
    ).fetchone()
    conn.close()
    return {"up": row["up"], "down": row["down"], "net": row["net"]} if row else {"up": 0, "down": 0, "net": 0}


# ---------------------------------------------------------------------------
# CRUD Operations
# ---------------------------------------------------------------------------

# Auto-confirm threshold: this many independent reports = confirmed
CONFIRM_THRESHOLD = 3
# Cluster radius: reports within this distance (miles) of an existing
# open incident in the same category are treated as duplicate reports
CLUSTER_RADIUS_MILES = 0.25


def _find_nearby_incident(conn, latitude, longitude, category, radius=CLUSTER_RADIUS_MILES):
    """Find an existing open incident near these coordinates in the same category."""
    if not latitude or not longitude:
        return None
    import math
    rows = conn.execute(
        "SELECT * FROM incidents WHERE status != 'resolved' AND category = ?",
        (category,),
    ).fetchall()
    for row in rows:
        r = dict(row)
        if not r.get("latitude") or not r.get("longitude"):
            continue
        # Quick haversine
        R = 3959
        dlat = math.radians(latitude - r["latitude"])
        dlon = math.radians(longitude - r["longitude"])
        a = (math.sin(dlat / 2) ** 2 +
             math.cos(math.radians(r["latitude"])) *
             math.cos(math.radians(latitude)) *
             math.sin(dlon / 2) ** 2)
        dist = R * 2 * math.asin(math.sqrt(a))
        if dist <= radius:
            return r
    return None


def create_incident(
    title: str,
    category: str,
    description: str = "",
    severity: str = "medium",
    latitude: float = None,
    longitude: float = None,
    address: str = "",
    borough: str = "",
    zip_code: str = "",
    source: str = "dispatcher",
    assigned_to: str = "",
    related_311_id: str = "",
    related_sensor_id: str = "",
) -> dict:
    """Create a new incident. If a similar open incident exists nearby, bumps
    its report_count instead of creating a duplicate. Dispatcher-created
    incidents are auto-confirmed. Citizen reports auto-confirm after reaching
    CONFIRM_THRESHOLD independent reports.
    """
    conn = get_db()
    now = datetime.utcnow().isoformat()

    # Check for clustering: is there already a similar incident nearby?
    if latitude and longitude and source not in ("dispatcher",):
        existing = _find_nearby_incident(conn, latitude, longitude, category)
        if existing:
            new_count = existing["report_count"] + 1
            auto_confirm = new_count >= CONFIRM_THRESHOLD
            updates = ["report_count = ?", "updated_at = ?"]
            params = [new_count, now]
            if auto_confirm and not existing["confirmed"]:
                updates.append("confirmed = 1")
            params.append(existing["id"])
            conn.execute(
                f"UPDATE incidents SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            # Log the additional report
            conn.execute(
                """INSERT INTO incident_updates (id, incident_id, update_type, message, updated_by, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (str(uuid.uuid4())[:8], existing["id"], "additional_report",
                 f"New report from {source}: {title}. Total reports: {new_count}.",
                 source, now),
            )
            conn.commit()
            conn.close()
            result = get_incident(existing["id"])
            result["_clustered"] = True
            result["_message"] = f"Merged with existing incident #{existing['id']} ({new_count} reports)"
            return result

    # No cluster match — create new incident
    incident_id = str(uuid.uuid4())[:8]
    # Dispatcher and sensor sources are auto-confirmed
    confirmed = 1 if source in ("dispatcher", "sensor", "311") else 0

    conn.execute(
        """INSERT INTO incidents
           (id, title, description, category, severity, status, confirmed,
            report_count, assigned_to, latitude, longitude, address, borough,
            zip_code, source, related_311_id, related_sensor_id, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, 'open', ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (incident_id, title, description, category, severity, confirmed,
         assigned_to, latitude, longitude, address, borough, zip_code, source,
         related_311_id, related_sensor_id, now, now),
    )
    conn.commit()
    conn.close()
    return get_incident(incident_id)


def confirm_incident(incident_id: str, confirmed_by: str = "dispatcher") -> dict | None:
    """Manually confirm an incident (dispatcher action). Confirmed incidents trigger alerts."""
    conn = get_db()
    now = datetime.utcnow().isoformat()
    conn.execute(
        "UPDATE incidents SET confirmed = 1, updated_at = ? WHERE id = ?",
        (now, incident_id),
    )
    conn.execute(
        """INSERT INTO incident_updates (id, incident_id, update_type, message, updated_by, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (str(uuid.uuid4())[:8], incident_id, "confirmed",
         f"Incident confirmed by {confirmed_by}", confirmed_by, now),
    )
    conn.commit()
    conn.close()
    return get_incident(incident_id)


def get_incident(incident_id: str) -> dict | None:
    """Get a single incident by ID."""
    conn = get_db()
    row = conn.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_incidents(
    status: str = "",
    category: str = "",
    borough: str = "",
    assigned_to: str = "",
    limit: int = 100,
) -> list[dict]:
    """List incidents with optional filters."""
    conn = get_db()
    conditions = []
    params = []

    if status:
        conditions.append("status = ?")
        params.append(status)
    if category:
        conditions.append("category = ?")
        params.append(category)
    if borough:
        conditions.append("borough = ?")
        params.append(borough.upper())
    if assigned_to:
        conditions.append("assigned_to = ?")
        params.append(assigned_to)

    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    rows = conn.execute(
        f"SELECT * FROM incidents {where} ORDER BY created_at DESC LIMIT ?",
        params + [limit],
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_incident(
    incident_id: str,
    status: str = None,
    severity: str = None,
    assigned_to: str = None,
    notes: str = None,
    message: str = "",
    updated_by: str = "dispatcher",
) -> dict | None:
    """Update an incident's status, severity, assignment, or notes."""
    conn = get_db()
    now = datetime.utcnow().isoformat()

    updates = ["updated_at = ?"]
    params = [now]

    if status:
        updates.append("status = ?")
        params.append(status)
        if status == "resolved":
            updates.append("resolved_at = ?")
            params.append(now)
    if severity:
        updates.append("severity = ?")
        params.append(severity)
    if assigned_to is not None:
        updates.append("assigned_to = ?")
        params.append(assigned_to)
    if notes is not None:
        updates.append("notes = ?")
        params.append(notes)

    params.append(incident_id)
    conn.execute(
        f"UPDATE incidents SET {', '.join(updates)} WHERE id = ?",
        params,
    )

    # Log the update
    conn.execute(
        """INSERT INTO incident_updates (id, incident_id, update_type, message, updated_by, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (str(uuid.uuid4())[:8], incident_id, status or "update", message, updated_by, now),
    )
    conn.commit()
    conn.close()
    return get_incident(incident_id)


def delete_incident(incident_id: str) -> bool:
    """Delete an incident."""
    conn = get_db()
    cursor = conn.execute("DELETE FROM incidents WHERE id = ?", (incident_id,))
    conn.execute("DELETE FROM incident_updates WHERE incident_id = ?", (incident_id,))
    conn.commit()
    conn.close()
    return cursor.rowcount > 0


def get_incident_history(incident_id: str) -> list[dict]:
    """Get the update history for an incident."""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM incident_updates WHERE incident_id = ? ORDER BY created_at DESC",
        (incident_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_stats() -> dict:
    """Get incident statistics for the dashboard."""
    conn = get_db()
    stats = {}

    # By status
    rows = conn.execute("SELECT status, COUNT(*) as count FROM incidents GROUP BY status").fetchall()
    stats["by_status"] = {r["status"]: r["count"] for r in rows}

    # By category
    rows = conn.execute("SELECT category, COUNT(*) as count FROM incidents GROUP BY category ORDER BY count DESC").fetchall()
    stats["by_category"] = {r["category"]: r["count"] for r in rows}

    # By borough
    rows = conn.execute("SELECT borough, COUNT(*) as count FROM incidents WHERE borough != '' GROUP BY borough ORDER BY count DESC").fetchall()
    stats["by_borough"] = {r["borough"]: r["count"] for r in rows}

    # By severity
    rows = conn.execute("SELECT severity, COUNT(*) as count FROM incidents GROUP BY severity").fetchall()
    stats["by_severity"] = {r["severity"]: r["count"] for r in rows}

    # Totals
    stats["total"] = conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
    stats["open"] = stats["by_status"].get("open", 0)
    stats["in_progress"] = stats["by_status"].get("in_progress", 0)
    stats["resolved"] = stats["by_status"].get("resolved", 0)

    conn.close()
    return stats


# ---------------------------------------------------------------------------
# Alert Subscriptions
# ---------------------------------------------------------------------------

def subscribe_alerts(
    name: str,
    contact: str,
    latitude: float,
    longitude: float,
    contact_type: str = "sms",
    address: str = "",
    radius_miles: float = 1.0,
    categories: str = "",
) -> dict:
    """Subscribe a person to alerts for incidents near their location."""
    conn = get_db()
    now = datetime.utcnow().isoformat()
    sub_id = str(uuid.uuid4())[:8]

    conn.execute(
        """INSERT INTO alert_subscriptions
           (id, name, contact, contact_type, latitude, longitude, address,
            radius_miles, categories, active, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
        (sub_id, name, contact, contact_type, latitude, longitude,
         address, radius_miles, categories, now),
    )
    conn.commit()
    conn.close()
    return get_subscription(sub_id)


def get_subscription(sub_id: str) -> dict | None:
    """Get a single subscription."""
    conn = get_db()
    row = conn.execute("SELECT * FROM alert_subscriptions WHERE id = ?", (sub_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_subscriptions(active_only: bool = True) -> list[dict]:
    """List all alert subscriptions."""
    conn = get_db()
    where = "WHERE active = 1" if active_only else ""
    rows = conn.execute(f"SELECT * FROM alert_subscriptions {where} ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def unsubscribe(sub_id: str) -> bool:
    """Deactivate an alert subscription."""
    conn = get_db()
    cursor = conn.execute("UPDATE alert_subscriptions SET active = 0 WHERE id = ?", (sub_id,))
    conn.commit()
    conn.close()
    return cursor.rowcount > 0


def find_subscribers_near(latitude: float, longitude: float, category: str = "") -> list[dict]:
    """Find all active subscribers whose alert radius covers the given location.

    Uses haversine approximation: 1 degree lat ~ 69 miles, 1 degree lon ~ 53 miles (at NYC latitude).
    """
    conn = get_db()
    rows = conn.execute("SELECT * FROM alert_subscriptions WHERE active = 1").fetchall()
    conn.close()

    import math
    results = []
    for row in rows:
        r = dict(row)
        # Haversine
        R = 3959
        dlat = math.radians(latitude - r["latitude"])
        dlon = math.radians(longitude - r["longitude"])
        a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(r["latitude"])) * math.cos(math.radians(latitude)) * math.sin(dlon / 2) ** 2
        dist = R * 2 * math.asin(math.sqrt(a))

        if dist <= r["radius_miles"]:
            # If subscriber filtered by category, check match
            if r["categories"] and category:
                sub_cats = [c.strip().lower() for c in r["categories"].split(",")]
                if category.lower() not in sub_cats:
                    continue
            r["distance_miles"] = round(dist, 2)
            results.append(r)

    return sorted(results, key=lambda x: x["distance_miles"])
