"""Register custom tool groups for the NYC Hackathon agents.

Registers four function groups + one parallel executor:
  - nyc_flood_tools: FloodNet sensor queries and flood data
  - nyc_311_tools: 311 complaint queries and aggregation
  - nyc_geo_tools: Geocoding and spatial utilities
  - nyc_crm_tools: Incident CRM for dispatchers (create, update, resolve, delete)
  - parallel_agent_query: Runs FloodWatch + 311 agents concurrently via asyncio.gather
"""

import asyncio
import json
from collections.abc import AsyncGenerator

from pydantic import Field

from nat.builder.builder import Builder
from nat.builder.function import FunctionGroup
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function, register_function_group
from nat.data_models.component_ref import FunctionRef
from nat.data_models.function import FunctionBaseConfig, FunctionGroupBaseConfig

from hackathon_nyc.tools import nyc_opendata, floodnet, geocoding, historical_lookup
from hackathon_nyc import db


# ---------------------------------------------------------------------------
# FloodNet / Environmental Tools
# ---------------------------------------------------------------------------

class FloodToolConfig(FunctionGroupBaseConfig, name="nyc_flood_tools"):
    include: list[str] = Field(
        default_factory=lambda: [
            "get_active_floods",
            "get_flood_sensors",
            "get_worst_floods",
            "get_flood_history",
            "get_flood_vulnerability",
            "get_air_quality",
            "query_nyc_dataset",
        ],
        description="Flood and environmental monitoring tools",
    )


@register_function_group(config_type=FloodToolConfig)
async def nyc_flood_tools(_config: FloodToolConfig, _builder: Builder) -> AsyncGenerator[FunctionGroup, None]:
    group = FunctionGroup(config=_config)

    async def _get_active_floods(hours_back: int = 24) -> str:
        """Get flooding events from the last N hours from FloodNet sensors across NYC."""
        result = await floodnet.get_active_floods(hours_back)
        return json.dumps(result[:20], indent=2, default=str)

    async def _get_flood_sensors() -> str:
        """Get all FloodNet sensor deployment locations and coordinates across NYC."""
        result = await floodnet.get_sensor_locations()
        return json.dumps(result, indent=2, default=str)

    async def _get_worst_floods(top_n: int = 10) -> str:
        """Get the worst flooding events by maximum water depth in inches."""
        result = await floodnet.get_worst_floods(top_n)
        return json.dumps(result, indent=2, default=str)

    async def _get_flood_history(sensor_id: str) -> str:
        """Get flood history for a specific FloodNet sensor by its sensor ID."""
        result = await floodnet.get_flood_history_for_sensor(sensor_id)
        return json.dumps(result[:20], indent=2, default=str)

    async def _get_flood_vulnerability(limit: int = 50) -> str:
        """Get flood vulnerability index scores by NYC neighborhood."""
        result = await nyc_opendata.get_flood_vulnerability(limit)
        return json.dumps(result, indent=2, default=str)

    async def _get_air_quality(neighborhood: str = "") -> str:
        """Get air quality data (PM2.5, NO2) by NYC neighborhood. Optionally filter by neighborhood name."""
        result = await nyc_opendata.get_air_quality(neighborhood)
        return json.dumps(result[:20], indent=2, default=str)

    async def _query_nyc_dataset(dataset_key: str, where_clause: str = "", limit: int = 50) -> str:
        """Query any NYC Open Data dataset by key. Available datasets: 311_current, air_quality, flood_events, flood_sensors, flood_vulnerability, heat_vulnerability, street_trees, greenhouse_gas, community_gardens, pluto."""
        result = await nyc_opendata.query_dataset(dataset_key, where_clause=where_clause, limit=limit)
        return json.dumps(result[:20], indent=2, default=str)

    group.add_function(name="get_active_floods", fn=_get_active_floods, description=_get_active_floods.__doc__)
    group.add_function(name="get_flood_sensors", fn=_get_flood_sensors, description=_get_flood_sensors.__doc__)
    group.add_function(name="get_worst_floods", fn=_get_worst_floods, description=_get_worst_floods.__doc__)
    group.add_function(name="get_flood_history", fn=_get_flood_history, description=_get_flood_history.__doc__)
    group.add_function(name="get_flood_vulnerability", fn=_get_flood_vulnerability, description=_get_flood_vulnerability.__doc__)
    group.add_function(name="get_air_quality", fn=_get_air_quality, description=_get_air_quality.__doc__)
    group.add_function(name="query_nyc_dataset", fn=_query_nyc_dataset, description=_query_nyc_dataset.__doc__)

    yield group


# ---------------------------------------------------------------------------
# 311 Complaint / Human Impact Tools
# ---------------------------------------------------------------------------

class ThreeOneOneToolConfig(FunctionGroupBaseConfig, name="nyc_311_tools"):
    include: list[str] = Field(
        default_factory=lambda: [
            "get_311_complaints",
            "get_311_stats",
            "get_311_by_location",
            "search_311_by_keyword",
        ],
        description="NYC 311 service request tools",
    )


@register_function_group(config_type=ThreeOneOneToolConfig)
async def nyc_311_tools(_config: ThreeOneOneToolConfig, _builder: Builder) -> AsyncGenerator[FunctionGroup, None]:
    group = FunctionGroup(config=_config)

    async def _get_311_complaints(complaint_type: str = "", borough: str = "", zip_code: str = "", limit: int = 20) -> str:
        """Get recent 311 service requests. Filter by complaint_type (e.g. 'Noise - Residential', 'Sewer', 'Rodent', 'HEAT/HOT WATER'), borough (e.g. 'BROOKLYN'), or zip_code."""
        result = await nyc_opendata.get_311_complaints(complaint_type, borough, zip_code, limit)
        return json.dumps(result, indent=2, default=str)

    async def _get_311_stats(complaint_type: str = "", borough: str = "", group_by: str = "complaint_type") -> str:
        """Get aggregated 311 complaint statistics. Returns counts grouped by complaint_type, borough, or other field."""
        result = await nyc_opendata.get_311_complaint_stats(complaint_type, borough, group_by)
        return json.dumps(result, indent=2, default=str)

    async def _get_311_by_location(lat: float, lon: float, radius_meters: int = 500, limit: int = 20) -> str:
        """Get 311 complaints near a specific lat/lon location within a radius in meters."""
        where = f"within_circle(location, {lat}, {lon}, {radius_meters})"
        result = await nyc_opendata.query_dataset(
            "311_current",
            where_clause=where,
            select="unique_key,created_date,complaint_type,descriptor,latitude,longitude,status",
            limit=limit,
        )
        return json.dumps(result, indent=2, default=str)

    async def _search_311_by_keyword(keyword: str, limit: int = 20) -> str:
        """Search 311 complaints by keyword in the descriptor field."""
        where = f"upper(descriptor) like '%{keyword.upper()}%'"
        result = await nyc_opendata.query_dataset(
            "311_current",
            where_clause=where,
            select="unique_key,created_date,complaint_type,descriptor,borough,latitude,longitude,status",
            limit=limit,
        )
        return json.dumps(result, indent=2, default=str)

    group.add_function(name="get_311_complaints", fn=_get_311_complaints, description=_get_311_complaints.__doc__)
    group.add_function(name="get_311_stats", fn=_get_311_stats, description=_get_311_stats.__doc__)
    group.add_function(name="get_311_by_location", fn=_get_311_by_location, description=_get_311_by_location.__doc__)
    group.add_function(name="search_311_by_keyword", fn=_search_311_by_keyword, description=_search_311_by_keyword.__doc__)

    yield group


# ---------------------------------------------------------------------------
# Geocoding / Spatial Tools
# ---------------------------------------------------------------------------

class GeoToolConfig(FunctionGroupBaseConfig, name="nyc_geo_tools"):
    include: list[str] = Field(
        default_factory=lambda: ["geocode_address", "reverse_geocode", "find_nearest_sensors"],
        description="Geocoding and spatial utility tools",
    )


@register_function_group(config_type=GeoToolConfig)
async def nyc_geo_tools(_config: GeoToolConfig, _builder: Builder) -> AsyncGenerator[FunctionGroup, None]:
    group = FunctionGroup(config=_config)

    async def _geocode_address(address: str) -> str:
        """Convert a NYC street address to lat/lon coordinates."""
        result = await geocoding.geocode_address(address)
        return json.dumps(result, indent=2)

    async def _reverse_geocode(lat: float, lon: float) -> str:
        """Convert lat/lon coordinates to a street address."""
        result = await geocoding.reverse_geocode(lat, lon)
        return json.dumps(result, indent=2)

    async def _find_nearest_sensors(lat: float, lon: float, top_n: int = 5) -> str:
        """Find the nearest FloodNet sensors to a given lat/lon location."""
        sensors = await floodnet.get_sensor_locations()
        nearest = geocoding.find_nearest_points(lat, lon, sensors, top_n)
        return json.dumps(nearest, indent=2, default=str)

    async def _historical_lookup(query: str, k: int = 5) -> str:
        """RAG: search NYC Open Data history (311, collisions, potholes, rats, housing, floods) for context relevant to the query. Use this for any question about past incidents, trends, or what happened before in a neighborhood."""
        result = await historical_lookup.historical_lookup(query, k=k)
        return json.dumps(result, indent=2, default=str)

    group.add_function(name="geocode_address", fn=_geocode_address, description=_geocode_address.__doc__)
    group.add_function(name="reverse_geocode", fn=_reverse_geocode, description=_reverse_geocode.__doc__)
    group.add_function(name="find_nearest_sensors", fn=_find_nearest_sensors, description=_find_nearest_sensors.__doc__)
    group.add_function(name="historical_lookup", fn=_historical_lookup, description=_historical_lookup.__doc__)

    yield group


# ---------------------------------------------------------------------------
# CRM / Incident Management Tools (Dispatcher Interface)
# ---------------------------------------------------------------------------

class CRMToolConfig(FunctionGroupBaseConfig, name="nyc_crm_tools"):
    include: list[str] = Field(
        default_factory=lambda: [
            "create_incident",
            "list_incidents",
            "update_incident",
            "resolve_incident",
            "delete_incident",
            "get_incident",
            "get_incident_stats",
        ],
        description="Incident CRM tools for dispatchers to manage events on the map",
    )


@register_function_group(config_type=CRMToolConfig)
async def nyc_crm_tools(_config: CRMToolConfig, _builder: Builder) -> AsyncGenerator[FunctionGroup, None]:
    group = FunctionGroup(config=_config)

    async def _create_incident(
        title: str,
        category: str,
        description: str = "",
        severity: str = "medium",
        latitude: float = None,
        longitude: float = None,
        address: str = "",
        borough: str = "",
        zip_code: str = "",
        assigned_to: str = "",
    ) -> str:
        """Create a new incident on the map. Categories: flooding, sewer, noise, rodent, heat, air_quality, street_condition, water, tree, other. Severity: low, medium, high, critical."""
        result = db.create_incident(
            title=title, category=category, description=description,
            severity=severity, latitude=latitude, longitude=longitude,
            address=address, borough=borough, zip_code=zip_code,
            assigned_to=assigned_to, source="agent",
        )
        return json.dumps(result, indent=2, default=str)

    async def _list_incidents(status: str = "", category: str = "", borough: str = "", limit: int = 50) -> str:
        """List all incidents, optionally filtered by status (open, in_progress, resolved), category, or borough."""
        result = db.list_incidents(status=status, category=category, borough=borough, limit=limit)
        return json.dumps(result, indent=2, default=str)

    async def _update_incident(
        incident_id: str,
        status: str = "",
        severity: str = "",
        assigned_to: str = "",
        notes: str = "",
        message: str = "",
    ) -> str:
        """Update an existing incident. Change status (open, in_progress, resolved), severity, assignment, or add notes."""
        result = db.update_incident(
            incident_id,
            status=status or None,
            severity=severity or None,
            assigned_to=assigned_to if assigned_to else None,
            notes=notes or None,
            message=message,
            updated_by="agent",
        )
        if not result:
            return json.dumps({"error": f"Incident {incident_id} not found"})
        return json.dumps(result, indent=2, default=str)

    async def _resolve_incident(incident_id: str, resolution_notes: str = "") -> str:
        """Mark an incident as resolved with optional resolution notes."""
        result = db.update_incident(
            incident_id,
            status="resolved",
            notes=resolution_notes or None,
            message="Incident resolved",
            updated_by="agent",
        )
        if not result:
            return json.dumps({"error": f"Incident {incident_id} not found"})
        return json.dumps(result, indent=2, default=str)

    async def _delete_incident(incident_id: str) -> str:
        """Delete an incident from the system entirely."""
        success = db.delete_incident(incident_id)
        if not success:
            return json.dumps({"error": f"Incident {incident_id} not found"})
        return json.dumps({"deleted": True, "id": incident_id})

    async def _get_incident(incident_id: str) -> str:
        """Get full details and history for a specific incident by its ID."""
        incident = db.get_incident(incident_id)
        if not incident:
            return json.dumps({"error": f"Incident {incident_id} not found"})
        history = db.get_incident_history(incident_id)
        incident["history"] = history
        return json.dumps(incident, indent=2, default=str)

    async def _get_incident_stats() -> str:
        """Get dashboard statistics: counts by status, category, borough, severity."""
        result = db.get_stats()
        return json.dumps(result, indent=2, default=str)

    async def _subscribe_alerts(
        name: str,
        contact: str,
        address: str,
        contact_type: str = "sms",
        radius_miles: float = 1.0,
        categories: str = "",
    ) -> str:
        """Subscribe a person to alerts for incidents near their address. They'll be notified when new incidents happen within their radius. contact_type: sms, whatsapp, email, discord. categories: comma-separated filter (e.g. 'flooding,sewer') or empty for all."""
        # Geocode the address first
        geo_result = await geocoding.geocode_address(address)
        if "error" in geo_result:
            return json.dumps({"error": f"Could not geocode address: {geo_result['error']}"})
        result = db.subscribe_alerts(
            name=name, contact=contact, contact_type=contact_type,
            latitude=geo_result["lat"], longitude=geo_result["lon"],
            address=address, radius_miles=radius_miles, categories=categories,
        )
        return json.dumps(result, indent=2, default=str)

    async def _list_subscriptions() -> str:
        """List all active alert subscriptions."""
        result = db.list_subscriptions()
        return json.dumps(result, indent=2, default=str)

    async def _check_alerts(incident_id: str) -> str:
        """Check which subscribers should be alerted for a given incident. Only confirmed incidents trigger alerts. Incidents are confirmed by dispatchers or auto-confirmed after 3+ independent reports."""
        incident = db.get_incident(incident_id)
        if not incident:
            return json.dumps({"error": f"Incident {incident_id} not found"})
        if not incident.get("confirmed"):
            return json.dumps({
                "incident_id": incident_id,
                "alert_status": "NOT_CONFIRMED",
                "report_count": incident.get("report_count", 1),
                "message": f"Incident not yet confirmed. Has {incident.get('report_count', 1)} report(s), needs 3 or dispatcher confirmation. No alerts sent.",
            }, indent=2, default=str)
        if not incident.get("latitude") or not incident.get("longitude"):
            return json.dumps({"error": "Incident has no coordinates"})
        subscribers = db.find_subscribers_near(
            incident["latitude"], incident["longitude"], incident.get("category", ""),
        )
        return json.dumps({
            "incident_id": incident_id,
            "incident_title": incident["title"],
            "confirmed": True,
            "report_count": incident.get("report_count", 1),
            "subscribers_to_alert": subscribers,
            "count": len(subscribers),
        }, indent=2, default=str)

    async def _confirm_incident(incident_id: str) -> str:
        """Dispatcher action: manually confirm an incident so it triggers alerts to nearby subscribers."""
        result = db.confirm_incident(incident_id, confirmed_by="dispatcher")
        if not result:
            return json.dumps({"error": f"Incident {incident_id} not found"})
        return json.dumps(result, indent=2, default=str)

    async def _unsubscribe(subscription_id: str) -> str:
        """Unsubscribe a person from alerts by their subscription ID."""
        success = db.unsubscribe(subscription_id)
        if not success:
            return json.dumps({"error": f"Subscription {subscription_id} not found"})
        return json.dumps({"unsubscribed": True, "id": subscription_id})

    group.add_function(name="create_incident", fn=_create_incident, description=_create_incident.__doc__)
    group.add_function(name="list_incidents", fn=_list_incidents, description=_list_incidents.__doc__)
    group.add_function(name="update_incident", fn=_update_incident, description=_update_incident.__doc__)
    group.add_function(name="resolve_incident", fn=_resolve_incident, description=_resolve_incident.__doc__)
    group.add_function(name="delete_incident", fn=_delete_incident, description=_delete_incident.__doc__)
    group.add_function(name="get_incident", fn=_get_incident, description=_get_incident.__doc__)
    group.add_function(name="get_incident_stats", fn=_get_incident_stats, description=_get_incident_stats.__doc__)
    group.add_function(name="subscribe_alerts", fn=_subscribe_alerts, description=_subscribe_alerts.__doc__)
    group.add_function(name="list_subscriptions", fn=_list_subscriptions, description=_list_subscriptions.__doc__)
    group.add_function(name="check_alerts", fn=_check_alerts, description=_check_alerts.__doc__)
    group.add_function(name="confirm_incident", fn=_confirm_incident, description=_confirm_incident.__doc__)
    group.add_function(name="unsubscribe", fn=_unsubscribe, description=_unsubscribe.__doc__)

    yield group


# ---------------------------------------------------------------------------
# Parallel Agent Executor
# Runs two sub-agents concurrently using asyncio.gather
# ---------------------------------------------------------------------------

class ParallelAgentQueryConfig(FunctionBaseConfig, name="parallel_agent_query"):
    """Runs two sub-agents in parallel and returns combined results."""
    agent_1: FunctionRef = Field(description="First sub-agent (e.g. floodwatch_agent)")
    agent_2: FunctionRef = Field(description="Second sub-agent (e.g. command_center_agent)")
    description: str = Field(
        default="Query both FloodWatch and 311 Command Center agents simultaneously. "
                "Use this when a question involves BOTH environmental data AND complaint data, "
                "or when you want a comprehensive cross-domain analysis. "
                "Both agents run in parallel for faster results.",
    )


@register_function(config_type=ParallelAgentQueryConfig)
async def parallel_agent_query(config: ParallelAgentQueryConfig, builder: Builder) -> AsyncGenerator:
    """Build a function that runs two sub-agents concurrently."""

    # Resolve both sub-agent references from the builder
    agent_1 = await builder.get_function(config.agent_1)
    agent_2 = await builder.get_function(config.agent_2)

    agent_1_name = config.agent_1 if isinstance(config.agent_1, str) else str(config.agent_1)
    agent_2_name = config.agent_2 if isinstance(config.agent_2, str) else str(config.agent_2)

    async def _parallel_query(query: str) -> str:
        """Run both FloodWatch and 311 Command Center agents in parallel on the same query and return combined results."""

        # Fire both agents concurrently
        result_1, result_2 = await asyncio.gather(
            agent_1.ainvoke(query),
            agent_2.ainvoke(query),
            return_exceptions=True,
        )

        # Format results, handling any errors gracefully
        parts = []
        if isinstance(result_1, Exception):
            parts.append(f"=== {agent_1_name} ===\nERROR: {result_1}")
        else:
            parts.append(f"=== {agent_1_name} ===\n{result_1}")

        if isinstance(result_2, Exception):
            parts.append(f"=== {agent_2_name} ===\nERROR: {result_2}")
        else:
            parts.append(f"=== {agent_2_name} ===\n{result_2}")

        return "\n\n".join(parts)

    yield FunctionInfo.from_fn(_parallel_query, description=config.description)
