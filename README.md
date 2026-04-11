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

## Team

Colin McDonough

## License

Built for Spark Hack NYC 2026. All rights reserved.
