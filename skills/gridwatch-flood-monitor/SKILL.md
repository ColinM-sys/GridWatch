---
name: gridwatch-flood-monitor
description: Monitor NYC FloodNet sensors and predict flood risk using cross-correlated data
version: 1.0.0
author: Colin McDonough
tags: [nyc, flooding, prediction, sensors, climate]
model: nemotron-mini
---

# GridWatch Flood Monitor

Monitor real-time flood conditions across NYC using FloodNet sensor data, 311 complaints, weather alerts, and predictive analytics.

## What This Skill Does

1. **Live Sensor Monitoring** — Pulls data from 200+ FloodNet sensors across NYC
2. **Flood History Analysis** — Queries historical flood events with depth and duration
3. **311 Cross-Reference** — Correlates sewer/flooding complaints with sensor data
4. **Weather Alert Integration** — Checks NWS for active flood warnings
5. **Risk Prediction** — Scores each sensor location by:
   - Number of past flood events
   - Maximum recorded depth
   - Nearby sewer complaints
   - Active weather alerts
6. **FEMA Floodplain Overlay** — Shows 2050s projected floodplain boundaries
7. **Auto-Alert** — Notifies subscribed dispatchers when flood risk is elevated

## Data Sources
- FloodNet Sensors (data.cityofnewyork.us/resource/kb2e-tjy3)
- FloodNet Flood Events (data.cityofnewyork.us/resource/aq7i-eu5q)
- 311 Sewer/Flooding Complaints (data.cityofnewyork.us/resource/erm2-nwe9)
- FEMA 2050s Floodplain (data.cityofnewyork.us/api/geospatial/27ya-gqtm)
- NWS Weather Alerts (api.weather.gov/alerts/active?area=NY)

## Predictive Model
Risk score = (flood_count * 2) + (max_depth * 0.5) + (nearby_sewer_complaints * 0.3)
Locations scoring above 2.0 are flagged as at-risk.
Weather alerts multiply the base risk by 1.5x.
