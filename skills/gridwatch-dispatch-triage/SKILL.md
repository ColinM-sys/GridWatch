---
name: gridwatch-dispatch-triage
description: Auto-triage incoming citizen reports with severity scoring, categorization, and agency recommendations
version: 1.0.0
author: Colin McDonough
tags: [nyc, dispatch, triage, emergency, ai-agent]
model: nemotron-mini
---

# GridWatch Dispatch Triage

Automatically process incoming citizen reports from multiple channels and triage them for dispatch.

## What This Skill Does

When a citizen report arrives via SMS, phone call, or Discord:

1. **Categorize** the incident using keyword analysis (flooding, rodent, noise, crash, gas leak, etc.)
2. **Score urgency** using natural language analysis:
   - CRITICAL: trapped, fire, gas leak, collapse, children endangered
   - HIGH: flooded basement, structural damage, no heat elderly
   - MEDIUM: flooding, broken, leak, backed up
   - LOW: small, minor, slight
3. **Geocode** the reported address to precise lat/lng
4. **Check history** via ChromaDB RAG for repeat-offender locations
5. **Find nearby incidents** within 500m for cluster detection
6. **Recommend agency** based on incident type:
   - Flooding/Sewer: DEP + FDNY
   - Rodents: DOHMH
   - Crashes: NYPD + EMS
   - Housing: HPD
   - Noise: DEP + NYPD
   - Potholes: DOT
7. **Auto-alert** nearby SMS subscribers via Twilio
8. **Create incident** in dispatch database with all metadata

## Input Channels
- Phone calls: Twilio + Whisper transcription
- SMS/Text: Twilio webhook
- Discord: Bot with keyword detection
- Web form: Direct dispatch entry

## Auto-Alert Flow
When an incident is created near a subscribed location, the system automatically sends an SMS alert to the subscriber via Twilio with incident details and severity.
