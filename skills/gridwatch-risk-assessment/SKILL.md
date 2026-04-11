---
name: gridwatch-risk-assessment
description: Analyze neighborhood infrastructure risk for any NYC address using live city data
version: 1.0.0
author: Colin McDonough
tags: [nyc, infrastructure, risk, safety, urban-intelligence]
model: nemotron-mini
---

# GridWatch Risk Assessment

Analyze infrastructure risk for any New York City address by querying live city data sources in real-time.

## What This Skill Does

Given any NYC address, this skill:

1. **Geocodes** the address to lat/lng coordinates
2. **Queries 6 live NYC Open Data APIs** within 800m radius:
   - 311 Flooding/Sewer complaints
   - Rodent inspection reports
   - Motor vehicle collisions
   - HPD housing violations (Class C hazardous)
   - Street condition/pothole reports
   - Noise complaints
3. **Scores each category** from NONE to CRITICAL
4. **Detects cross-correlations** between incident types:
   - Noise + Rodents: 16.7x more likely to co-occur
   - Rodents + Housing violations: 13.4x correlation
   - Potholes + Crashes: 3.2x correlation
   - Flooding + Rodents: 6.9x correlation
5. **Returns overall infrastructure health score** (0-100)
6. **Plots historical data points** on an interactive 3D map

## Usage

### Via API
```
GET /api/risk/<address>
```

Example: `GET /api/risk/200 Broadway Manhattan`

### Via Chat
Click the Risk Score button in the AI Chat tab, or type:
```
risk score for 125 Canal Street Manhattan
```

## Data Sources
- NYC 311 Service Requests (Sewer, Flooding, Noise, Rodent, Street Condition)
- Motor Vehicle Collisions (NYPD crash data)
- HPD Housing Violations (Class C hazardous)
- All queried in real-time using Socrata within_circle geo-filter

## Hardware
Runs on NVIDIA GB10 Grace Blackwell with Nemotron-Mini 4.2B via Ollama.
