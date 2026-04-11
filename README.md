# GridWatch

**NYC Urban Intelligence & Dispatch Platform**

AI-powered multi-agent dispatch system for New York City infrastructure monitoring, built on NVIDIA Nemotron running on-device via the NVIDIA GB10 Grace Blackwell Superchip.

Built for **Spark Hack NYC 2026** (April 10-12, 2026).

## Features

### 3D Interactive Map (Mapbox GL)
- 3D building extrusions with incident-based coloring
- Buildings glow based on incident category (flooding = blue, rodents = brown, noise = purple)
- Tilted/rotated perspective with smooth camera transitions

### Live Data Layers
- **Incidents** - Dispatch CRM with real-time incident management
- **Floods** - FloodNet sensor data, 311 flood reports, FEMA 2050s floodplain overlay
- **Crashes** - NYPD motor vehicle collision data
- **Potholes** - NYC DOT pothole reports
- **Rodents** - 311 rodent complaints + DOH inspection data
- **Housing** - Class C housing violations (HPD)
- **Restaurants** - Critical health violations (DOHMH)
- **Construction** - Active DOB permits
- **Live Cameras** - 962 NYC DOT traffic cameras with live snapshots

### AI-Powered Features
- **Predictive Analytics** - Cross-references potholes + crash data to predict danger zones, flood risk scoring from sensor history + 311 complaints
- **Heatmap Overlay** - Density-weighted heatmap across all data layers
- **Cross-Correlation Engine** - Detects infrastructure events where 3+ incident types cluster (e.g., flooding + rodents + housing = infrastructure failure)
- **Impact Radius Rings** - Visualizes affected area around confirmed incidents based on category
- **Weather Alerts** - Live NWS alerts filtered to NYC, auto-highlights flood zones during flood warnings

### Multi-Channel Citizen Reporting
- **Phone Calls** - Call +1 (917) 993-7245, voice transcription via Twilio + Whisper
- **SMS/Text** - Text the same number to report incidents
- **Discord Bot** - Report via Discord with automatic geocoding
- **Web Form** - Direct dispatch entry with map pin-drop

### AI Dispatch Chat (RAG + Nemotron)
- Natural language interface for dispatchers
- RAG-powered with ChromaDB (1,800 NYC data documents across 6 collections)
- Quick-action buttons: Sitrep, Critical, Floods, Noise, By Borough, Rodents, Urgent
- Text-to-speech response output
- Incident creation via natural language ("report flooding at 350 5th Ave")

### Demo Tour
- Automated camera tour of hotspot clusters
- Narrated with severity levels and data correlations
- Full layer visualization during tour

## Architecture

```
Citizen Reports (Phone/SMS/Discord)
         |
         v
   [Twilio / Discord Bot]
         |
         v
   [FastAPI Server] <-- [Ollama + Nemotron-Mini]
    |          |
    |          v
    |     [ChromaDB RAG]
    |     (1,800 NYC docs)
    |
    v
[SQLite CRM] --> [Mapbox 3D Frontend]
                      |
                      v
               [NYC Open Data APIs]
               (311, FloodNet, Crashes,
                Potholes, Housing, etc.)
```

## Tech Stack

| Component | Technology |
|-----------|------------|
| AI Model | NVIDIA Nemotron-Mini (4.2B) via Ollama |
| Hardware | NVIDIA GB10 Grace Blackwell (Acer Veriton GN100) |
| Backend | FastAPI + Uvicorn |
| Frontend | Mapbox GL JS + deck.gl |
| Database | SQLite + ChromaDB (RAG) |
| Voice/SMS | Twilio + Whisper |
| Chat | Discord.py |
| Tunnel | ngrok |
| Data | NYC Open Data, FloodNet, NWS Weather API, NYC DOT Cameras |

## Setup

### Prerequisites
- Python 3.12+
- Ollama with `nemotron-mini` model
- Mapbox access token
- Twilio account (for phone/SMS)
- Discord bot token (for Discord intake)
- ngrok (for Twilio webhooks)

### Environment Variables
```bash
export DISCORD_TOKEN=your_discord_bot_token
export TWILIO_ACCOUNT_SID=your_twilio_sid
export TWILIO_AUTH_TOKEN=your_twilio_auth_token
export TWILIO_PHONE_NUMBER=+1234567890
```

### Run
```bash
# Terminal 1: Ollama
ollama serve

# Terminal 2: Backend
cd hackathon-nyc-v11
PYTHONPATH=src uvicorn hackathon_nyc.server:app --host 0.0.0.0 --port 8000

# Terminal 3: ngrok (for Twilio)
ngrok http 8000

# Terminal 4: Discord bot
PYTHONPATH=src python -m hackathon_nyc.discord_bot

# Open browser
http://localhost:8000
```

## Data Sources

All data is sourced from NYC Open Data and public APIs:
- [NYC 311 Service Requests](https://data.cityofnewyork.us/Social-Services/311-Service-Requests-from-2010-to-Present/erm2-nwe9)
- [FloodNet Sensors](https://data.cityofnewyork.us/Environment/FloodNet-Sensors/kb2e-tjy3)
- [Motor Vehicle Collisions](https://data.cityofnewyork.us/Public-Safety/Motor-Vehicle-Collisions-Crashes/h9gi-nx95)
- [DOT Potholes](https://data.cityofnewyork.us/Transportation/Pothole-Repair/x9wy-ing4)
- [Housing Violations](https://data.cityofnewyork.us/Housing-Development/Housing-Maintenance-Code-Violations/wvxf-dwi5)
- [Restaurant Inspections](https://data.cityofnewyork.us/Health/DOHMH-New-York-City-Restaurant-Inspection-Results/43nn-pn8j)
- [NYC DOT Traffic Cameras](https://webcams.nyctmc.org/api/cameras/)
- [NWS Weather Alerts](https://api.weather.gov/alerts/active?area=NY)

## NYC Open Data Sources Connected

| Dataset | API Endpoint | Records Used | Purpose |
|---------|-------------|-------------|---------|
| 311 Service Requests | `erm2-nwe9` | Sewer, Flooding, Noise, Rodent, Heat, Street, Tree | Primary incident feed across all complaint types |
| FloodNet Sensors | `kb2e-tjy3` | ~200 sensors | Real-time flood depth monitoring locations |
| FloodNet Events | `aq7i-eu5q` | 200 recent | Historical flood events with depth/duration |
| Motor Vehicle Collisions | `h9gi-nx95` | 500 recent | Crash locations, injuries, fatalities |
| DOT Potholes | `x9wy-ing4` | 500 recent | Pothole repair requests with geometry |
| Rodent Inspections | `p937-wjvj` | 100 recent | DOH rodent activity inspections |
| Housing Violations | `wvxf-dwi5` | 100 recent | HPD Class C (immediately hazardous) violations |
| Restaurant Inspections | `43nn-pn8j` | 100 recent | DOHMH critical food safety violations |
| Construction Permits | `rbx6-tga4` | 100 recent | Active DOB-approved construction |
| Flood Vulnerability | `mrjc-v9pm` | 200 | NYC flood vulnerability index |
| FEMA Floodplain | `27ya-gqtm` | Full GeoJSON | 2050s projected floodplain (NPCC 90th percentile) |

### External APIs
| Service | Purpose |
|---------|---------|
| NYC DOT Traffic Cameras | 962 live cameras with JPEG snapshots |
| NWS Weather Alerts | Real-time weather warnings filtered to NYC |
| Nominatim/OSM | Geocoding addresses to lat/lng |

## For Judges

### What Makes This Different
1. **100% On-Device AI** — Nemotron-Mini runs locally on the GB10 Grace Blackwell chip. No cloud API calls for inference. Privacy-first.
2. **Multi-Agent Architecture** — Router agent (query planning) + RAG agent (data retrieval from ChromaDB) + Dispatch agent (incident management) + Prediction agent (cross-correlation analytics)
3. **Real Data, Real Impact** — All data comes from live NYC Open Data APIs. Every marker on the map represents a real report, real sensor reading, or real inspection.
4. **Multi-Channel Intake** — Citizens can report via phone call, SMS, Discord, or web form. Reports are geocoded, categorized, and triaged automatically.
5. **Predictive Analytics** — Cross-correlates potholes with crashes (3.2x more crashes near potholes), noise with rodents (16.7x correlation), flooding with sewer complaints. Identifies infrastructure failure zones where 3+ incident types cluster.

### How to Demo
1. Open `http://<device-ip>:8080` in a browser
2. Click **DEMO** for an automated tour of NYC hotspots with narration
3. Click **Incidents** to see dispatch data with animated markers (music notes for noise, water puddles for flooding, scurrying rats for rodents)
4. Click **Predict** to see AI-generated danger zones based on cross-correlated data
5. Click **Cameras** to see live NYC DOT traffic camera feeds
6. Open the **AI Chat** tab and click **Sitrep** for a status report from Nemotron
7. Send a Discord message: `flooding at 350 5th Avenue Manhattan` — watch it appear on the map
8. Click **Heatmap** for a density overlay of all incident types

### Hardware
- **NVIDIA GB10 Grace Blackwell Superchip** (Acer Veriton GN100)
- 128 GB unified memory
- 3.7 TB NVMe storage
- ARM Cortex-X925 (20 cores)
- Running Ollama + Nemotron-Mini (4.2B parameters)

### RAG System
- **ChromaDB** with 6 collections, 1,800 embedded documents
- Collections: 311 complaints, flood events, collisions, potholes, housing violations, rodent inspections
- Queries relevant data before each AI chat response
- No external embedding API — uses ChromaDB's built-in embeddings

## Team

Colin McDonough

## License

Built for Spark Hack NYC 2026. All rights reserved.
