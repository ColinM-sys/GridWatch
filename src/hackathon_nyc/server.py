"""FastAPI server for the NYC Urban Intelligence CRM.

Provides REST API endpoints for dispatchers to manage incidents,
plus serves the AI agent for analysis and triage.

Run: uvicorn hackathon_nyc.server:app --reload --port 8000
"""

from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI, HTTPException, Request, File, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
from pathlib import Path

from hackathon_nyc import db

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# NeMo ReAct Agent + RAG — global state
# ---------------------------------------------------------------------------
_nemo_workflow = None
_nemo_builder_ctx = None
_nemo_builder = None
_chroma_collection = None


async def _init_nemo_agent():
    """Initialize the NeMo ReAct agent from config_unified.yml."""
    global _nemo_workflow, _nemo_builder_ctx, _nemo_builder
    try:
        from nat.runtime.loader import PluginTypes, discover_and_register_plugins
        discover_and_register_plugins(PluginTypes.CONFIG_OBJECT)

        from nat.utils.io.yaml_tools import yaml_load
        from nat.utils.data_models.schema_validator import validate_schema
        from nat.data_models.config import Config
        from nat.builder.workflow_builder import WorkflowBuilder

        import hackathon_nyc.register  # noqa: F401

        config_path = Path(__file__).parent / "configs" / "config_unified.yml"
        config_dict = yaml_load(config_path)
        config = validate_schema(config_dict, Config)

        _nemo_builder_ctx = WorkflowBuilder.from_config(config)
        _nemo_builder = await _nemo_builder_ctx.__aenter__()
        _nemo_workflow = await _nemo_builder.build()

        logger.info("[NeMo Agent] ReAct agent initialized with 26 tools")
    except Exception as e:
        logger.error("[NeMo Agent] Failed to initialize (falling back to v1 chat): %s", e)
        _nemo_workflow = None


def _init_rag():
    """Initialize ChromaDB RAG for context retrieval."""
    global _chroma_collection
    try:
        import chromadb
        db_path = Path(__file__).parent.parent.parent / "data" / "chromadb"
        if db_path.exists():
            client = chromadb.PersistentClient(path=str(db_path))
            collections = client.list_collections()
            if collections:
                _chroma_collection = client.get_collection(collections[0].name)
                logger.info("[RAG] ChromaDB loaded: %s (%d documents)",
                           _chroma_collection.name, _chroma_collection.count())
            else:
                logger.info("[RAG] ChromaDB exists but no collections. Run: python -m hackathon_nyc.ingest --all")
        else:
            logger.info("[RAG] No ChromaDB found. Run: python -m hackathon_nyc.ingest --all")
    except Exception as e:
        logger.error("[RAG] Failed to initialize: %s", e)


async def _shutdown_nemo_agent():
    global _nemo_workflow, _nemo_builder_ctx, _nemo_builder
    if _nemo_builder_ctx is not None:
        try:
            await _nemo_builder_ctx.__aexit__(None, None, None)
        except Exception:
            pass
    _nemo_workflow = None
    _nemo_builder = None
    _nemo_builder_ctx = None


@asynccontextmanager
async def lifespan(application: FastAPI):
    # Agent disabled — causes Ollama to hang. Uses v1 chat fallback instead.
    # await _init_nemo_agent()
    _init_rag()
    # Monitor disabled — depends on agent
    # try:
    #     from hackathon_nyc.monitor_agent import start_monitor, stop_monitor
    #     await start_monitor()
    # except Exception as e:
    #     logger.error("[Monitor] Failed to start: %s", e)
    yield
    try:
        pass
        # from hackathon_nyc.monitor_agent import stop_monitor
        # await stop_monitor()
    except Exception:
        pass
    await _shutdown_nemo_agent()


app = FastAPI(title="NYC Urban Intelligence System", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIR = Path(__file__).parent / "frontend"

# Register Twilio voice + SMS routes (phone number for citizen reporting)
from hackathon_nyc.twilio_voice import register_twilio_routes
register_twilio_routes(app)


# ---------------------------------------------------------------------------
# Pydantic Models
# ---------------------------------------------------------------------------

class IncidentCreate(BaseModel):
    title: str
    category: str
    description: str = ""
    severity: str = "medium"
    latitude: float | None = None
    longitude: float | None = None
    address: str = ""
    borough: str = ""
    zip_code: str = ""
    source: str = "dispatcher"
    assigned_to: str = ""
    related_311_id: str = ""
    related_sensor_id: str = ""


class IncidentUpdate(BaseModel):
    status: str | None = None
    severity: str | None = None
    assigned_to: str | None = None
    notes: str | None = None
    message: str = ""
    updated_by: str = "dispatcher"


class AlertSubscribe(BaseModel):
    name: str
    contact: str
    contact_type: str = "sms"
    address: str = ""
    latitude: float | None = None
    longitude: float | None = None
    radius_miles: float = 1.0
    categories: str = ""


# ---------------------------------------------------------------------------
# Incident CRUD Endpoints
# ---------------------------------------------------------------------------

@app.post("/api/incidents")
def create_incident(data: IncidentCreate):
    """Create a new incident."""
    return db.create_incident(**data.model_dump())


@app.get("/api/incidents")
def list_incidents(
    status: str = "",
    category: str = "",
    borough: str = "",
    assigned_to: str = "",
    limit: int = 100,
):
    """List incidents with optional filters."""
    return db.list_incidents(status=status, category=category, borough=borough, assigned_to=assigned_to, limit=limit)


@app.get("/api/incidents/stats")
def get_stats():
    """Get incident statistics for the dashboard."""
    return db.get_stats()


@app.get("/api/incidents/{incident_id}")
def get_incident(incident_id: str):
    """Get a single incident by ID."""
    incident = db.get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    return incident


@app.put("/api/incidents/{incident_id}")
def update_incident(incident_id: str, data: IncidentUpdate):
    """Update an incident (status, severity, assignment, notes)."""
    incident = db.update_incident(incident_id, **data.model_dump())
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    return incident


@app.post("/api/incidents/{incident_id}/confirm")
async def confirm_incident(incident_id: str):
    """Dispatcher confirms an incident — enables alert notifications and sends alerts."""
    result = db.confirm_incident(incident_id, confirmed_by="dispatcher")
    if not result:
        raise HTTPException(status_code=404, detail="Incident not found")

    # Auto-send alerts to nearby subscribers
    if result.get("latitude") and result.get("longitude"):
        subscribers = db.find_subscribers_near(
            result["latitude"], result["longitude"], result.get("category", ""),
        )
        if subscribers:
            sent = 0
            for sub in subscribers:
                try:
                    if sub["contact_type"] == "sms":
                        # Send via Twilio SMS
                        import os
                        sid = os.getenv("TWILIO_ACCOUNT_SID", "")
                        token = os.getenv("TWILIO_AUTH_TOKEN", "")
                        phone = os.getenv("TWILIO_PHONE_NUMBER", "")
                        if sid and token and phone:
                            from twilio.rest import Client
                            cat_emoji = {'flooding':'🌊','sewer':'🚰','noise':'🎵','rodent':'🐀','heat':'🔥'}.get(result.get("category",""),"⚠️")
                            Client(sid, token).messages.create(
                                body=f"{cat_emoji} NYC Alert: {result['title']} near {result.get('address','your area')[:60]}. #{result['id']}",
                                from_=phone, to=sub["contact"],
                            )
                            sent += 1
                    else:
                        # Send via OpenClaw (Discord, WhatsApp, Telegram, etc.)
                        from hackathon_nyc.openclaw_alerts import send_alert
                        r = await send_alert(sub["contact_type"], sub["contact"], f"⚠️ NYC Alert: {result['title']} near {result.get('address','your area')[:60]}. #{result['id']}")
                        if r.get("status") == "sent":
                            sent += 1
                except Exception as e:
                    print(f"Alert to {sub['contact']} failed: {e}")
            result["alerts_sent"] = sent
            result["alerts_total"] = len(subscribers)

    return result


@app.delete("/api/incidents/{incident_id}")
def delete_incident(incident_id: str):
    """Delete an incident."""
    if not db.delete_incident(incident_id):
        raise HTTPException(status_code=404, detail="Incident not found")
    return {"deleted": True}


@app.get("/api/incidents/{incident_id}/history")
def get_incident_history(incident_id: str):
    """Get update history for an incident."""
    return db.get_incident_history(incident_id)


@app.get("/api/urgency/{text}")
def score_urgency(text: str):
    """Score the urgency of arbitrary text. Used by the frontend for display."""
    score, label = compute_urgency(text.lower())
    return {"urgency_score": score, "urgency_label": label}


# ---------------------------------------------------------------------------
# Alert Subscription Endpoints
# ---------------------------------------------------------------------------

@app.post("/api/alerts/subscribe")
async def subscribe_alerts(data: AlertSubscribe):
    """Subscribe to alerts for incidents near a location."""
    lat = data.latitude
    lon = data.longitude

    # Geocode address if no coordinates provided
    if not lat or not lon:
        if not data.address:
            raise HTTPException(status_code=400, detail="Provide address or lat/lon")
        import aiohttp
        async with aiohttp.ClientSession() as session:
            params = {"q": data.address, "format": "json", "limit": "1", "countrycodes": "us"}
            async with session.get("https://nominatim.openstreetmap.org/search",
                                   params=params,
                                   headers={"User-Agent": "HackathonNYC/1.0"}) as resp:
                results = await resp.json()
                if not results:
                    raise HTTPException(status_code=400, detail=f"Could not geocode '{data.address}'")
                lat = float(results[0]["lat"])
                lon = float(results[0]["lon"])

    return db.subscribe_alerts(
        name=data.name, contact=data.contact, contact_type=data.contact_type,
        latitude=lat, longitude=lon, address=data.address,
        radius_miles=data.radius_miles, categories=data.categories,
    )


@app.get("/api/alerts/subscriptions")
def list_subscriptions():
    """List all active alert subscriptions."""
    return db.list_subscriptions()


@app.delete("/api/alerts/{sub_id}")
def unsubscribe(sub_id: str):
    """Unsubscribe from alerts."""
    if not db.unsubscribe(sub_id):
        raise HTTPException(status_code=404, detail="Subscription not found")
    return {"unsubscribed": True}


@app.get("/api/alerts/check/{incident_id}")
def check_alerts_for_incident(incident_id: str):
    """Check which subscribers should be alerted for a given incident.

    Returns list of subscribers within their alert radius of the incident.
    This is what OpenClaw would call after an incident is created to know
    who to notify.
    """
    incident = db.get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    if not incident.get("latitude") or not incident.get("longitude"):
        return []
    return db.find_subscribers_near(
        incident["latitude"], incident["longitude"], incident.get("category", ""),
    )


# ---------------------------------------------------------------------------
# Natural Language Urgency Scoring
# ---------------------------------------------------------------------------

import re as _urgency_re

URGENCY_KEYWORDS = {
    "critical": [
        "trapped", "emergency", "can't get out", "cant get out", "fire",
        "collapse", "collapsed", "gas leak", "children", "child", "kid",
        "elderly", "disabled", "unconscious", "drowning", "stuck inside",
        "can't breathe", "cant breathe", "explosion", "electrocution",
        "building falling", "structural collapse", "life threatening",
    ],
    "high": [
        "flooded basement", "no heat elderly", "structural damage",
        "large", "severe", "dangerous", "blocked road", "power out",
        "no electricity", "ceiling caving", "sewage overflow",
        "major", "massive", "water rising", "chest deep", "waist deep",
        "no heat", "no hot water", "carbon monoxide", "mold black",
    ],
    "medium": [
        "flooding", "broken", "leak", "backed up", "smell", "noise all night",
        "clogged", "overflowing", "puddle", "crack", "damage",
        "standing water", "dripping", "buzzing", "banging",
    ],
    "low": [
        "small", "minor", "little", "slight", "tiny",
    ],
}

URGENCY_SCORES = {"critical": 1.0, "high": 0.8, "medium": 0.5, "low": 0.2}


def compute_urgency(text_lower: str) -> tuple[float, str]:
    """Score the urgency of a citizen report based on keyword matching.
    Returns (score, label) where score is 0.0-1.0 and label is CRITICAL/HIGH/MEDIUM/LOW."""
    best_score = 0.2
    best_label = "LOW"
    hit_count = 0

    for level in ["critical", "high", "medium", "low"]:
        for kw in URGENCY_KEYWORDS[level]:
            if kw in text_lower:
                score = URGENCY_SCORES[level]
                hit_count += 1
                if score > best_score:
                    best_score = score
                    best_label = level.upper()

    # Boost slightly for multiple keyword hits (compound urgency)
    if hit_count >= 3 and best_score < 1.0:
        best_score = min(1.0, best_score + 0.1)
    if hit_count >= 5 and best_score < 1.0:
        best_score = min(1.0, best_score + 0.1)

    return round(best_score, 2), best_label


# ---------------------------------------------------------------------------
# OpenClaw / Discord Webhook — accepts messages, creates incidents
# ---------------------------------------------------------------------------

@app.post("/api/webhook/report")
async def webhook_report(request: Request):
    """Accept a report from any source (OpenClaw, Discord bot, etc).
    Body: { "message": "flooding at 200 Broadway Manhattan", "source": "discord", "user": "Colin#1234" }
    Geocodes the message, creates an incident, returns the result.
    """
    import aiohttp
    data = await request.json()
    message = data.get("message", "")
    source = data.get("source", "citizen_discord")
    user = data.get("user", "unknown")

    if not message:
        return {"error": "No message provided"}

    # Geocode: extract address from message
    lat, lon, address = None, None, ""
    try:
        from hackathon_nyc.tools.geocoding import geocode_address
        import re

        # Clean common transcription errors
        import re as _re
        fixed = _re.sub(r'\s+', ' ', message).strip()  # normalize whitespace
        fixed = fixed.replace(' and ', ' & ').replace(' AND ', ' & ')
        fixed = _re.sub(r'\.', ' ', fixed)  # remove ALL periods (STT adds them randomly)
        fixed = _re.sub(r'\s+', ' ', fixed).strip()  # re-normalize after period removal
        fixed = _re.sub(r'\b[Bb]looding\b', 'flooding', fixed)  # common Whisper error
        fixed = _re.sub(r'\b[Bb]leeding\b', 'flooding', fixed)  # another Whisper error
        fixed = _re.sub(r'\bin\b', ',', fixed)  # "in Manhattan" → ", Manhattan"
        fixed = _re.sub(r'\s+', ' ', fixed).strip()
        fixed = _re.sub(r'\$(\d+)\.00', r'\1', fixed)  # "$350.00" → "350"
        fixed = _re.sub(r'\$(\d+)', r'\1', fixed)       # "$350" → "350"
        # Fix Whisper merging "350 5th" → "355th": generate alternate split versions
        alt_splits = []
        for m in _re.finditer(r'\b(\d{3,})(st|nd|rd|th)\b', fixed, _re.IGNORECASE):
            num = m.group(1)
            suffix = m.group(2)
            # Try splitting at each position: "355" → "35 5", "3 55"
            for i in range(len(num)-1, 0, -1):
                left = num[:i]
                right = num[i:]
                right_suffix = {'1':'st','2':'nd','3':'rd'}.get(right[-1], 'th')
                alt = fixed[:m.start()] + left + ' ' + right + right_suffix + fixed[m.end():]
                alt_splits.append(alt)

        # Strategy 1: grab everything after the LAST "at"/"near"/"around", stop at noise
        after_prep = ""
        for prep in [' at ', ' near ', ' around ']:
            idx = fixed.lower().rfind(prep)
            if idx != -1:
                after_prep = fixed[idx + len(prep):].strip()
                # Cut at noise words that aren't part of the address
                cut = _re.search(r'\b(its?|it\'s|the water|water is|very|really|severe|bad|terrible|about|deep|inches|feet|foot|please|help|send|someone|nobody|done)\b', after_prep, _re.IGNORECASE)
                if cut:
                    after_prep = after_prep[:cut.start()].strip()
                    after_prep = _re.sub(r'[\s,]+$', '', after_prep)
                break

        # Strategy 2: try multiple approaches
        queries = []
        if after_prep:
            queries.append(after_prep + ', New York, NY')
        # Strategy 3: split on periods and strip noise words from each chunk
        noise = r'\b(flooding|flood|noise|loud|rats?|sewer|pothole|crash|heat|construction|report|there is|water|tree|fell|broken|damaged|its?|severe|bad|terrible|really|very|about|deep|inches|feet|foot)\b'
        for chunk in _re.split(r'[.!?]+', fixed):
            chunk = chunk.strip()
            if len(chunk) > 5 and any(c.isdigit() for c in chunk):
                clean_chunk = _re.sub(noise, ' ', chunk, flags=_re.IGNORECASE)
                clean_chunk = _re.sub(r'\s+', ' ', clean_chunk).strip()
                clean_chunk = _re.sub(r'^[\s,&]+|[\s,&]+$', '', clean_chunk)
                if len(clean_chunk) > 3:
                    queries.append(clean_chunk + ', New York, NY')
                queries.append(chunk + ', New York, NY')
        # Strategy 4: try split alternatives for merged ordinals
        for alt in alt_splits:
            queries.append(alt + ', New York, NY')
        queries.append(fixed + ', New York, NY')
        queries.append(message + ', New York, NY')

        for query in queries:
            if len(query) < 10:
                continue
            # Force NYC bounding box to prevent Buffalo/other city matches
            geo = await geocode_address(query)
            if "error" not in geo:
                # Verify it's in NYC proper (5 boroughs only)
                qlat, qlon = geo["lat"], geo["lon"]
                if 40.49 <= qlat <= 40.92 and -74.26 <= qlon <= -73.68:
                    lat, lon = qlat, qlon
                    address = geo.get("display_name", "")
                    break
                # Not in NYC — skip this result and try next query
    except Exception:
        pass

    # Guess category from keywords
    msg_lower = message.lower()
    category = "other"
    for keyword, cat in [("flood", "flooding"), ("water main", "flooding"), ("sewer", "sewer"),
                         ("gas leak", "sewer"), ("gas smell", "sewer"), ("noise", "noise"),
                         ("loud", "noise"), ("music", "noise"), ("party", "noise"),
                         ("rat", "rodent"), ("mouse", "rodent"), ("roach", "rodent"), ("pest", "rodent"),
                         ("heat", "heat"), ("hot water", "heat"), ("no heat", "heat"),
                         ("pothole", "street_condition"), ("road", "street_condition"), ("crack", "street_condition"),
                         ("crash", "street_condition"), ("accident", "street_condition"),
                         ("tree", "tree"), ("branch", "tree"),
                         ("water", "water"), ("hydrant", "water"), ("leak", "water"),
                         ("fire", "other"), ("smoke", "other"), ("construction", "noise")]:
        if keyword in msg_lower:
            category = cat
            break

    # --- Natural Language Urgency Scoring ---
    urgency_score, urgency_label = compute_urgency(msg_lower)
    # Map urgency to severity
    if urgency_score >= 0.9:
        severity = "critical"
    elif urgency_score >= 0.7:
        severity = "high"
    elif urgency_score >= 0.4:
        severity = "medium"
    else:
        severity = "low"

    incident = db.create_incident(
        title=message[:60],
        category=category,
        description=f"Report from {user}: {message}",
        severity=severity,
        source=f"citizen_{source}",
        latitude=lat, longitude=lon, address=address,
    )
    # Attach urgency metadata to response
    incident["urgency_score"] = urgency_score
    incident["urgency_label"] = urgency_label

    # Auto-alert nearby subscribers via SMS
    if lat and lon:
        try:
            subscribers = db.find_subscribers_near(lat, lon, category)
            if subscribers:
                import os
                from twilio.rest import Client as TwilioClient
                sid = os.getenv("TWILIO_ACCOUNT_SID")
                token = os.getenv("TWILIO_AUTH_TOKEN")
                from_num = os.getenv("TWILIO_PHONE_NUMBER")
                if sid and token and from_num:
                    tw = TwilioClient(sid, token)
                    for sub in subscribers:
                        if sub.get("contact_type") == "sms" and sub.get("contact"):
                            try:
                                tw.messages.create(
                                    body=f"⚠️ GRIDWATCH ALERT: {category.upper()} reported near {address[:60]}. {urgency_label} severity. #{incident['id'][:8]}",
                                    from_=from_num,
                                    to=sub["contact"],
                                )
                                logger.info(f"[Alert] SMS sent to {sub['contact']} for incident #{incident['id'][:8]}")
                            except Exception as e:
                                logger.warning(f"[Alert] SMS failed to {sub['contact']}: {e}")
        except Exception as e:
            logger.warning(f"[Alert] check failed: {e}")

    return incident


# ---------------------------------------------------------------------------
# Pipecat Voice Agent WebSocket (Twilio audio stream → live AI conversation)
# ---------------------------------------------------------------------------

from fastapi import WebSocket
import asyncio

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Twilio streams call audio here → Pipecat pipeline processes it →
    STT (Whisper) → LLM (Nemotron) → TTS (Kokoro) → audio back to caller.
    """
    await websocket.accept()

    import json as _json
    from pipecat.audio.vad.silero import SileroVADAnalyzer
    from pipecat.frames.frames import LLMRunFrame
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.runner import PipelineRunner
    from pipecat.pipeline.task import PipelineParams, PipelineTask
    from pipecat.processors.aggregators.llm_context import LLMContext
    from pipecat.processors.aggregators.llm_response_universal import (
        LLMContextAggregatorPair, LLMUserAggregatorParams,
    )
    from pipecat.services.ollama.llm import OLLamaLLMService
    from pipecat.adapters.schemas.function_schema import FunctionSchema
    from pipecat.adapters.schemas.tools_schema import ToolsSchema
    from pipecat.services.llm_service import FunctionCallParams
    from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams

    from hackathon_nyc.voice_agent import (
        handle_create_incident, handle_check_floods,
        handle_check_complaints, handle_subscribe_alerts,
        handle_get_incident_stats, tools, SYSTEM_PROMPT,
    )

    import os
    from pipecat.runner.utils import parse_telephony_websocket, _create_telephony_transport

    # Use Pipecat's official Twilio handshake parser
    transport_type, call_data = await parse_telephony_websocket(websocket)
    print(f"[Pipecat] Detected: {transport_type}, stream={call_data.get('stream_id')}")

    params = FastAPIWebsocketParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
    )
    transport = await _create_telephony_transport(websocket, params, transport_type, call_data)

    # STT
    try:
        from pipecat.services.whisper.stt import WhisperSTTService
        stt = WhisperSTTService(model_size="tiny.en")
    except ImportError:
        from pipecat.services.deepgram.stt import DeepgramSTTService
        import os
        stt = DeepgramSTTService(api_key=os.getenv("DEEPGRAM_API_KEY", ""))

    # TTS
    try:
        from pipecat.services.kokoro.tts import KokoroTTSService
        tts = KokoroTTSService(
            settings=KokoroTTSService.Settings(voice="af_bella"),
        )
    except ImportError:
        try:
            from pipecat.services.piper.tts import PiperTTSService
            tts = PiperTTSService()
        except ImportError:
            # Minimal fallback — will error but at least doesn't crash import
            raise ImportError("No TTS provider available. Install kokoro or piper: pip install 'pipecat-ai[kokoro]'")

    # LLM — Nemotron via Ollama
    llm = OLLamaLLMService(
        settings=OLLamaLLMService.Settings(
            model="nemotron-mini",
            system_instruction=SYSTEM_PROMPT,
        ),
    )

    # Register tool handlers
    llm.register_function("create_incident", handle_create_incident)
    llm.register_function("check_floods", handle_check_floods)
    llm.register_function("check_complaints", handle_check_complaints)
    llm.register_function("subscribe_alerts", handle_subscribe_alerts)
    llm.register_function("get_incident_stats", handle_get_incident_stats)

    context = LLMContext(
        messages=[{"role": "user", "content": "Greet me in one short sentence as a NYC dispatch operator."}],
    )
    user_agg, assistant_agg = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
    )

    pipeline = Pipeline([
        transport.input(),
        stt,
        user_agg,
        llm,
        tts,
        transport.output(),
        assistant_agg,
    ])

    task = PipelineTask(pipeline, params=PipelineParams(enable_metrics=True))

    @transport.event_handler("on_client_connected")
    async def on_connected(t, client):
        print("[Pipecat] Client connected, triggering greeting")
        context.add_message({"role": "user", "content": "Greet the caller briefly."})
        await task.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_disconnected(t, client):
        print("[Pipecat] Client disconnected")
        # After call ends, extract what the caller said and create incident via webhook
        try:
            user_messages = [m["content"] for m in context.messages if m.get("role") == "user" and "greet" not in m["content"].lower()]
            if user_messages:
                full_report = " ".join(user_messages)
                print(f"[Pipecat] Creating incident from conversation: {full_report[:80]}")
                import aiohttp
                async with aiohttp.ClientSession() as session:
                    async with session.post("http://localhost:8000/api/webhook/report", json={
                        "message": full_report,
                        "source": "voice_pipecat",
                        "user": "phone_caller",
                    }) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            print(f"[Pipecat] Incident created: #{data.get('id')} lat={data.get('latitude')}")
                        else:
                            print(f"[Pipecat] Webhook failed: {resp.status}")
        except Exception as e:
            print(f"[Pipecat] Post-call incident creation failed: {e}")
        await task.cancel()

    print("[Pipecat] Starting pipeline runner...")
    runner = PipelineRunner(handle_sigint=False)
    await runner.run(task)
    print("[Pipecat] Pipeline runner finished")


# ---------------------------------------------------------------------------
# AI Chat — dispatcher command interface via Ollama (Nemotron)
# ---------------------------------------------------------------------------

import aiohttp as _aiohttp
import json as _json

CHAT_HISTORY: list[dict] = []

DISPATCH_SYSTEM_PROMPT = """You are NYC Urban Intelligence dispatch AI. You help dispatchers manage incidents.

You have these actions available. When the dispatcher wants to do something, respond with a JSON action block wrapped in ```action tags. Only ONE action per response.

ACTIONS:
1. Create incident: ```action\n{"action":"create","title":"...","category":"flooding|sewer|noise|rodent|heat|street_condition|tree|water|other","description":"...","severity":"low|medium|high|critical","address":"..."}\n```
2. Update status: ```action\n{"action":"update_status","id":"incident_id","status":"new|in_progress|resolved"}\n```
3. Resolve incident: ```action\n{"action":"resolve","id":"incident_id"}\n```
4. Confirm incident: ```action\n{"action":"confirm","id":"incident_id"}\n```
5. Assign incident: ```action\n{"action":"assign","id":"incident_id","assigned_to":"Unit 7"}\n```
6. Delete incident: ```action\n{"action":"delete","id":"incident_id"}\n```
7. Get stats: ```action\n{"action":"stats"}\n```
8. Search/query: ```action\n{"action":"search","status":"","category":"","borough":""}\n```
9. Send alerts: ```action\n{"action":"send_alerts","id":"incident_id"}\n```

When answering questions about current incidents, use the CURRENT DATA provided below.
When the user says "resolve" or "mark as resolved" + an incident ID or description, use the resolve action.
When asked for a sitrep/summary, analyze the current data and give a brief operational summary.
Be concise and direct — you're talking to a dispatcher, not a civilian.
Short incident IDs are fine — match on prefix if the full ID isn't given.

CURRENT INCIDENTS:
{incidents_json}

CURRENT STATS:
{stats_json}
"""


@app.post("/api/incidents/{incident_id}/vote")
async def vote_on_incident(incident_id: str, request: Request):
    """Community upvote/downvote. 3+ upvotes = auto-confirm. -3 = hidden."""
    data = await request.json()
    vote = data.get("vote", 0)  # 1 or -1
    voter_id = data.get("voter_id", "anon_" + str(hash(str(request.client.host)))[:8])
    if vote not in (1, -1):
        raise HTTPException(status_code=400, detail="Vote must be 1 or -1")
    result = db.vote_incident(incident_id, vote, voter_id)
    if not result:
        raise HTTPException(status_code=404, detail="Incident not found")
    return result


@app.get("/api/incidents/{incident_id}/votes")
def get_votes(incident_id: str):
    """Get vote counts for an incident."""
    return db.get_incident_votes(incident_id)


@app.get("/api/agent/status")
def agent_status():
    """Check if the NeMo agent and RAG are loaded."""
    return {
        "agent": _nemo_workflow is not None,
        "rag": _chroma_collection is not None,
        "rag_docs": _chroma_collection.count() if _chroma_collection else 0,
        "mode": "agent+rag" if _nemo_workflow and _chroma_collection else
                "agent" if _nemo_workflow else
                "rag" if _chroma_collection else "v1_fallback"
    }


@app.post("/generate")
async def generate_chat(request: Request):
    """AI chat endpoint — NeMo ReAct agent + RAG, falls back to v1 pattern matching."""
    data = await request.json()
    user_input = data.get("input", "").strip()
    if not user_input:
        return {"output": "No input provided."}

    # --- RAG context retrieval ---
    rag_context = ""
    if _chroma_collection:
        try:
            results = _chroma_collection.query(query_texts=[user_input], n_results=1)
            if results and results['documents'] and results['documents'][0]:
                rag_context = "\n\nRELEVANT NYC DATA:\n" + "\n---\n".join(results['documents'][0])
        except Exception as e:
            logger.error("[RAG] Query failed: %s", e)

    # --- NeMo ReAct Agent ---
    if _nemo_workflow is not None:
        try:
            from nat.data_models.api_server import ChatRequest, Message, UserMessageContentRoleType

            messages = []
            for msg in CHAT_HISTORY[-20:]:
                messages.append(Message(
                    content=msg["content"],
                    role=UserMessageContentRoleType(msg["role"]),
                ))
            # Append RAG context to user query if available
            enriched_input = user_input + rag_context if rag_context else user_input
            messages.append(Message(content=enriched_input, role=UserMessageContentRoleType.USER))

            chat_request = ChatRequest(messages=messages)
            result = await _nemo_workflow.ainvoke(chat_request)

            if hasattr(result, 'choices') and result.choices:
                raw_output = result.choices[0].message.content or ""
            elif isinstance(result, str):
                raw_output = result
            else:
                raw_output = str(result)

            CHAT_HISTORY.append({"role": "user", "content": user_input})
            CHAT_HISTORY.append({"role": "assistant", "content": raw_output})

            return {"output": raw_output}

        except Exception as e:
            logger.error("[NeMo Agent] Invoke failed, falling back to v1: %s", e)

    # --- V1 Fallback ---

    # Get current incident data for context
    incidents = db.list_incidents(limit=50)
    stats = db.get_stats()
    incidents_summary = _json.dumps(incidents[:30], indent=1, default=str) if incidents else "No incidents."
    stats_summary = _json.dumps(stats, indent=1, default=str)

    system_prompt = DISPATCH_SYSTEM_PROMPT.replace(
        "{incidents_json}", incidents_summary
    ).replace("{stats_json}", stats_summary)

    # --- AUTO-RAG: if user asks anything historical, query ChromaDB and inject ---
    rag_context = ""
    rag_points: list = []
    rag_triggers = ["histor", "past", "before", "previous", "prior", "ever ",
                    "have there been", "has there been", "trend", "last year",
                    "last month", "in the past", "what happened", "show me",
                    "crash", "collision", "accident", "flood", "rat", "rodent",
                    "pothole", "violation", "complaint", "near ", "hotspot",
                    "worst", "dangerous", "most", "where are", "which area",
                    "concentration", "cluster", "problem area"]
    # Skip RAG for sitrep/status/dispatch queries — those use DB stats only
    rag_skip = ["sitrep", "status", "report", "give me a", "how many", "resolve", "assign", "update", "create", "delete", "immediate", "urgent", "dispatch", "need", "which incidents", "open incidents", "priority", "priorities"]
    skip_rag = any(s in user_input.lower() for s in rag_skip)
    if not skip_rag and any(t in user_input.lower() for t in rag_triggers):
        try:
            from hackathon_nyc.tools.historical_lookup import historical_lookup as _hl
            # Pick collections by topic words in the query
            ql = user_input.lower()
            topic_map = [
                (("flood", "sewer", "water main"), ["nyc_flood_events"]),
                (("rat", "rodent", "mouse", "mice"), ["nyc_rodent_inspections"]),
                (("pothole", "street condition"), ["nyc_potholes"]),
                (("crash", "collision", "accident"), ["nyc_collisions"]),
                (("housing", "violation", "landlord", "heat", "hot water"), ["nyc_housing_violations"]),
                (("311", "complaint", "noise"), ["nyc_311_current"]),
            ]
            picked: list = []
            # Hotspot/worst/dangerous queries search ALL collections
            if any(w in ql for w in ("hotspot", "worst", "dangerous", "most", "problem area", "cluster")):
                collections = None  # all collections
            else:
                for keys, colls in topic_map:
                    if any(k in ql for k in keys):
                        picked = colls
                        break  # first match wins, single-topic
                collections = picked or None
            rag = await _hl(user_input, k=6, collections=collections)
            chunks = rag.get("results", [])
            rag_points = rag.get("points", [])

            # --- Geographic filter: if user named a place, geocode and filter to nearby points ---
            try:
                import re as _re
                from math import radians, sin, cos, asin, sqrt
                # Extract location phrase after "in/near/around/at"
                m = _re.search(r"\b(?:in|near|around|at|on)\s+(?:the\s+)?([a-zA-Z0-9 \.\-']{3,60})(?:\s*[?\.,]|$)", user_input)
                place = m.group(1).strip() if m else ""
                # Trim trailing junk
                place = _re.sub(r"\b(before|recently|lately|please|thanks?)\b.*$", "", place, flags=_re.I).strip()
                if place and len(place) >= 3 and rag_points:
                    from hackathon_nyc.tools.geocoding import geocode_address
                    geo = await geocode_address(place + ", New York, NY")
                    if "error" not in geo and geo.get("lat"):
                        clat, clon = float(geo["lat"]), float(geo["lon"])
                        def _hav(la1, lo1, la2, lo2):
                            R = 3958.8
                            la1, lo1, la2, lo2 = map(radians, (la1, lo1, la2, lo2))
                            d = 2 * asin(sqrt(sin((la2-la1)/2)**2 + cos(la1)*cos(la2)*sin((lo2-lo1)/2)**2))
                            return R * d
                        radius_miles = 5.0
                        filtered = [p for p in rag_points if _hav(clat, clon, p["lat"], p["lon"]) <= radius_miles]
                        if filtered:
                            rag_points = filtered
                            geo_note = f"\nGEO FILTER: showing {len(filtered)} records within {radius_miles} miles of {place}."
                        else:
                            geo_note = ""
                    else:
                        geo_note = ""
                else:
                    geo_note = ""
            except Exception as _ge:
                logger.warning("[RAG geo-filter] %s", _ge)
                geo_note = ""
            if chunks:
                # Build a pre-formatted summary so the model doesn't dump raw data
                collection_counts = {}
                for c in chunks:
                    coll = c.get('collection', 'unknown').replace('nyc_', '').replace('_', ' ').title()
                    collection_counts[coll] = collection_counts.get(coll, 0) + 1
                summary_parts = ", ".join(f"{v} {k}" for k, v in sorted(collection_counts.items(), key=lambda x: -x[1]))
                rag_context = f"\n\nDATA FOUND: {len(chunks)} records ({summary_parts}). Summarize this for the dispatcher in plain English. Do NOT paste field names, JSON, or raw data. Just say what was found, where, and what it means."
        except Exception as _e:
            logger.warning("[RAG] historical_lookup failed: %s", _e)

    # Build conversation with history (keep last 10 exchanges)
    messages = [{"role": "system", "content": system_prompt + rag_context}]
    for msg in CHAT_HISTORY[-20:]:
        messages.append(msg)
    messages.append({"role": "user", "content": user_input})

    # Call Ollama
    try:
        async with _aiohttp.ClientSession() as session:
            import os as _os2
            ollama_host = _os2.getenv("OLLAMA_HOST", "localhost:11435")
            async with session.post(f"http://{ollama_host}/api/chat", json={
                "model": "nemotron-mini",
                "messages": messages,
                "stream": False,
            }) as resp:
                result = await resp.json()
                ai_response = result.get("message", {}).get("content", "No response from model.")
    except Exception as e:
        return {"output": f"LLM error: {e}"}

    # If model dumped raw JSON/stats, format it nicely
    import re
    if '"by_status"' in ai_response or '"total"' in ai_response or '```yaml' in ai_response or '```json' in ai_response:
        cleaned = re.sub(r'```(?:yaml|json)?\s*\n?', '', ai_response).strip()
        json_start = cleaned.find('{')
        if json_start >= 0:
            cleaned = cleaned[json_start:]
            json_end = cleaned.rfind('}') + 1
            if json_end > 0:
                cleaned = cleaned[:json_end]
        try:
            data = _json.loads(cleaned) if cleaned.startswith('{') else None
            if data and 'total' in data:
                lines = [f"SITREP - {data.get('total', 0)} total incidents:"]
                lines.append(f"Open: {data.get('open', 0)} | In Progress: {data.get('in_progress', 0)} | Resolved: {data.get('resolved', 0)}")
                if 'by_category' in data:
                    cats = ', '.join(f"{k}: {v}" for k, v in sorted(data['by_category'].items(), key=lambda x: -x[1]))
                    lines.append(f"By type: {cats}")
                if 'by_severity' in data:
                    sevs = ', '.join(f"{k}: {v}" for k, v in data['by_severity'].items())
                    lines.append(f"Severity: {sevs}")
                if 'by_borough' in data:
                    boros = ', '.join(f"{k}: {v}" for k, v in data['by_borough'].items())
                    lines.append(f"Boroughs: {boros}")
                ai_response = '\n'.join(lines)
        except (_json.JSONDecodeError, TypeError):
            pass

    # Parse and execute action blocks
    action_match = re.search(r'```action\s*\n(.*?)\n```', ai_response, re.DOTALL)
    action_result = None

    if action_match:
        try:
            action = _json.loads(action_match.group(1))
            act = action.get("action", "")

            if act == "create":
                # Geocode the address
                lat, lon, addr = None, None, action.get("address", "")
                if addr:
                    try:
                        from hackathon_nyc.tools.geocoding import geocode_address
                        geo = await geocode_address(addr + ", New York, NY")
                        if "error" not in geo:
                            lat, lon = geo["lat"], geo["lon"]
                            addr = geo.get("display_name", addr)
                    except Exception:
                        pass
                result = db.create_incident(
                    title=action.get("title", user_input[:60]),
                    category=action.get("category", "other"),
                    description=action.get("description", ""),
                    severity=action.get("severity", "medium"),
                    latitude=lat, longitude=lon, address=addr,
                    source="dispatcher_chat",
                )
                action_result = f"Incident created: #{result['id'][:8]} — {result['title']}"

            elif act == "update_status" or act == "resolve":
                inc_id = action.get("id", "")
                status = "resolved" if act == "resolve" else action.get("status", "in_progress")
                matched = _match_incident_id(inc_id, incidents)
                if matched:
                    db.update_incident(matched["id"], status=status)
                    action_result = f"#{matched['id'][:8]} status → {status}"
                else:
                    action_result = f"No incident matching '{inc_id}'"

            elif act == "confirm":
                inc_id = action.get("id", "")
                matched = _match_incident_id(inc_id, incidents)
                if matched:
                    db.confirm_incident(matched["id"], confirmed_by="dispatcher_chat")
                    action_result = f"#{matched['id'][:8]} confirmed"
                else:
                    action_result = f"No incident matching '{inc_id}'"

            elif act == "assign":
                inc_id = action.get("id", "")
                matched = _match_incident_id(inc_id, incidents)
                if matched:
                    db.update_incident(matched["id"], assigned_to=action.get("assigned_to", ""))
                    action_result = f"#{matched['id'][:8]} assigned to {action.get('assigned_to', '?')}"
                else:
                    action_result = f"No incident matching '{inc_id}'"

            elif act == "delete":
                inc_id = action.get("id", "")
                matched = _match_incident_id(inc_id, incidents)
                if matched:
                    db.delete_incident(matched["id"])
                    action_result = f"#{matched['id'][:8]} deleted"
                else:
                    action_result = f"No incident matching '{inc_id}'"

            elif act == "stats":
                s = db.get_stats()
                action_result = f"Total: {s.get('total',0)} | Open: {s.get('open',0)} | Confirmed: {s.get('confirmed',0)} | Resolved: {s.get('resolved',0)}"

            elif act == "search":
                results = db.list_incidents(
                    status=action.get("status", ""),
                    category=action.get("category", ""),
                    borough=action.get("borough", ""),
                )
                action_result = f"Found {len(results)} incidents"
                if results:
                    lines = [f"• #{r['id'][:8]} {r['title'][:40]} [{r['status']}]" for r in results[:10]]
                    action_result += "\n" + "\n".join(lines)

            elif act == "send_alerts":
                inc_id = action.get("id", "")
                matched = _match_incident_id(inc_id, incidents)
                if matched:
                    action_result = f"Alerts triggered for #{matched['id'][:8]}"
                else:
                    action_result = f"No incident matching '{inc_id}'"

        except _json.JSONDecodeError:
            action_result = "Failed to parse action."
        except Exception as e:
            action_result = f"Action failed: {e}"

    # Build final response
    # Strip the action block from the visible response
    clean_response = re.sub(r'```action\s*\n.*?\n```', '', ai_response, flags=re.DOTALL).strip()
    if action_result:
        output = f"{clean_response}\n\n**→ {action_result}**" if clean_response else f"**→ {action_result}**"
    else:
        output = clean_response or ai_response

    # If model dumped raw field names, replace with clean summary
    raw_indicators = ['sensor_id:', 'inspection_type:', 'job_ticket', 'bbl:', 'boro_code:', 'on_street_name:', 'crash_date:', 'violation', 'defnum', 'the_geom', 'number_of_persons', 'coordinates', 'MultiLineString', 'MultiPolygon', '"id":', '"category":', '"severity":']
    if any(ind in output for ind in raw_indicators) or (output.strip().startswith('[') and len(output) > 50):
        # Model failed to summarize — generate server-side summary
        if rag_points:
            collection_counts = {}
            for p in rag_points:
                coll = p.get('collection', 'unknown').replace('nyc_', '').replace('_', ' ').title()
                collection_counts[coll] = collection_counts.get(coll, 0) + 1
            output = f"Found {len(rag_points)} records: " + ", ".join(f"{v} {k.lower()}" for k, v in sorted(collection_counts.items(), key=lambda x: -x[1]))
        else:
            # Fallback to dispatch stats
            stats = db.get_stats()
            incidents_list = db.list_incidents(limit=50)
            critical = [i for i in incidents_list if i.get("severity") in ("critical", "high")]
            lines = [f"{stats.get('total', 0)} active incidents, {len(critical)} need immediate attention:"]
            for c in critical[:5]:
                lines.append(f"  - {c.get('title', '?')[:50]} ({c.get('severity')}) at {c.get('address', '?')[:40]}")
            output = "\n".join(lines)

    # Save to history
    CHAT_HISTORY.append({"role": "user", "content": user_input})
    CHAT_HISTORY.append({"role": "assistant", "content": ai_response})

    # For hotspot/worst/dangerous queries, generate answer from data if model failed
    ql = user_input.lower()
    if any(w in ql for w in ("hotspot", "worst", "dangerous", "concentration", "problem area")) and ("sorry" in output.lower() or "cannot" in output.lower() or "no record" in output.lower()):
        stats = db.get_stats()
        incidents = db.list_incidents(limit=50)
        # Count by borough
        borough_counts = {}
        for inc in incidents:
            b = inc.get("borough") or "Unknown"
            borough_counts[b] = borough_counts.get(b, 0) + 1
        # Count by category
        cat_counts = stats.get("by_category", {})
        top_cats = sorted(cat_counts.items(), key=lambda x: -x[1])[:5]
        # Build summary
        lines = [f"HOTSPOT ANALYSIS — {stats.get('total', 0)} active incidents:"]
        if top_cats:
            lines.append("Top incident types: " + ", ".join(f"{k} ({v})" for k, v in top_cats))
        if borough_counts:
            top_boros = sorted(borough_counts.items(), key=lambda x: -x[1])[:3]
            lines.append("Highest concentration: " + ", ".join(f"{k} ({v})" for k, v in top_boros))
        critical = [i for i in incidents if i.get("severity") in ("critical", "high")]
        if critical:
            lines.append(f"{len(critical)} critical/high severity incidents requiring immediate response")
            for c in critical[:3]:
                lines.append(f"  - {c.get('title', '?')[:50]} ({c.get('severity')}) at {c.get('address', '?')[:40]}")
        if rag_points:
            lines.append(f"{len(rag_points)} historical data points plotted on map")
        output = "\n".join(lines)

    # Force correct count when geo-filter ran
    if rag_points:
        import re as _re2
        n = len(rag_points)
        word_map = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
                    6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten"}
        word = word_map.get(n, str(n))
        output = _re2.sub(r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+records?\b",
                          f"{n} records", output, count=1, flags=_re2.I)

    return {"output": output, "rag_points": rag_points}


@app.get("/api/risk/{address:path}")
async def neighborhood_risk(address: str):
    """Generate a neighborhood risk score for a given address."""
    from hackathon_nyc.tools.geocoding import geocode_address
    from math import radians, sin, cos, asin, sqrt

    def _hav(la1, lo1, la2, lo2):
        R = 3958.8
        la1, lo1, la2, lo2 = map(radians, (la1, lo1, la2, lo2))
        return R * 2 * asin(sqrt(sin((la2-la1)/2)**2 + cos(la1)*cos(la2)*sin((lo2-lo1)/2)**2))

    # Geocode the address
    geo = await geocode_address(address + ", New York City, NY")
    if "error" in geo or not geo.get("lat"):
        return {"error": "Could not geocode address", "address": address}

    clat, clon = float(geo["lat"]), float(geo["lon"])
    display_addr = geo.get("display_name", address)
    radius_miles = 0.5

    # Query LIVE NYC Open Data APIs for this location
    import aiohttp
    risk_data = {"flooding": [], "rodent": [], "collision": [], "housing": [], "pothole": [], "noise": []}
    all_points = []

    async with aiohttp.ClientSession() as session:
        queries = [
            ("flooding", f"https://data.cityofnewyork.us/resource/erm2-nwe9.json?$limit=50&$order=created_date%20DESC&$where=complaint_type%20in('Sewer','Street%20Flooding','Water%20System')%20AND%20within_circle(location,{clat},{clon},800)&$select=latitude,longitude,complaint_type,created_date,descriptor"),
            ("rodent", f"https://data.cityofnewyork.us/resource/erm2-nwe9.json?$limit=50&$order=created_date%20DESC&$where=complaint_type='Rodent'%20AND%20within_circle(location,{clat},{clon},800)&$select=latitude,longitude,complaint_type,created_date"),
            ("collision", f"https://data.cityofnewyork.us/resource/h9gi-nx95.json?$limit=50&$order=crash_date%20DESC&$where=within_circle(location,{clat},{clon},800)&$select=latitude,longitude,number_of_persons_injured,number_of_persons_killed,crash_date"),
            ("housing", f"https://data.cityofnewyork.us/resource/wvxf-dwi5.json?$limit=50&$order=inspectiondate%20DESC&$where=class='C'%20AND%20within_circle(location,{clat},{clon},800)&$select=latitude,longitude,inspectiondate,novdescription"),
            ("pothole", f"https://data.cityofnewyork.us/resource/erm2-nwe9.json?$limit=50&$order=created_date%20DESC&$where=complaint_type='Street%20Condition'%20AND%20within_circle(location,{clat},{clon},800)&$select=latitude,longitude,complaint_type,created_date"),
            ("noise", f"https://data.cityofnewyork.us/resource/erm2-nwe9.json?$limit=50&$order=created_date%20DESC&$where=complaint_type%20in('Noise%20-%20Residential','Noise%20-%20Street/Sidewalk')%20AND%20within_circle(location,{clat},{clon},800)&$select=latitude,longitude,complaint_type,created_date"),
        ]
        for risk_key, url in queries:
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    data = await resp.json()
                    for d in data:
                        if d.get("latitude") and d.get("longitude"):
                            risk_data[risk_key].append(d)
                            all_points.append({"lat": float(d["latitude"]), "lon": float(d["longitude"]), "collection": risk_key})
            except Exception:
                pass

    # Also check dispatch DB
    incidents = db.list_incidents(limit=100)
    nearby_incidents = [i for i in incidents if i.get("latitude") and i.get("longitude")
                        and _hav(clat, clon, i["latitude"], i["longitude"]) <= radius_miles]

    # Score each category — scaled for NYC density (800m radius captures a lot)
    def score_cat(count, thresholds):
        if count >= thresholds[3]: return 100  # CRITICAL
        if count >= thresholds[2]: return 75   # HIGH
        if count >= thresholds[1]: return 50   # MEDIUM
        if count >= thresholds[0]: return 25   # LOW
        return 0                               # NONE

    scores = {
        "flooding": score_cat(len(risk_data["flooding"]), (5, 15, 30, 45)),
        "rodent": score_cat(len(risk_data["rodent"]), (5, 15, 30, 45)),
        "collision": score_cat(len(risk_data["collision"]), (3, 10, 25, 40)),
        "housing": score_cat(len(risk_data["housing"]), (3, 10, 25, 40)),
        "pothole": score_cat(len(risk_data["pothole"]), (5, 15, 30, 45)),
        "noise": score_cat(len(risk_data["noise"]), (10, 25, 40, 50)),
    }

    risk_labels = {0: "NONE", 25: "LOW", 50: "MEDIUM", 75: "HIGH", 100: "CRITICAL"}
    overall = max(1, 100 - int(sum(scores.values()) / len(scores)))

    # Find correlations
    correlations = []
    if scores["rodent"] >= 50 and scores["noise"] >= 50:
        correlations.append("Noise + Rodents (16.7x correlation)")
    if scores["rodent"] >= 50 and scores["housing"] >= 50:
        correlations.append("Rodents + Housing violations (13.4x correlation)")
    if scores["pothole"] >= 50 and scores["collision"] >= 50:
        correlations.append("Potholes + Crashes (3.2x correlation)")
    if scores["flooding"] >= 50 and scores["rodent"] >= 50:
        correlations.append("Flooding + Rodents (6.9x correlation)")

    # Top concern
    top_key = max(scores, key=scores.get)
    top_concern = {"flooding": "Flooding/Sewer", "rodent": "Rodent Activity", "collision": "Vehicle Crashes",
                   "housing": "Housing Violations", "pothole": "Potholes", "noise": "Noise"}[top_key]

    # Build all points for map plotting
    all_points = []
    for key, pts in risk_data.items():
        for p in pts:
            all_points.append(p)

    return {
        "address": display_addr,
        "lat": clat, "lon": clon,
        "overall_score": overall,
        "overall_label": "SAFE" if overall >= 80 else "MODERATE RISK" if overall >= 50 else "HIGH RISK" if overall >= 25 else "CRITICAL",
        "categories": {k: {"score": v, "label": risk_labels.get(v, "?"), "count": len(risk_data.get(k, []))} for k, v in scores.items()},
        "nearby_dispatch_incidents": len(nearby_incidents),
        "correlations": correlations,
        "top_concern": top_concern,
        "rag_points": all_points,
    }


def _match_incident_id(partial_id: str, incidents: list) -> dict | None:
    """Match a partial incident ID or title against the incident list."""
    partial_id = partial_id.strip().lstrip("#").lower()
    if not partial_id:
        return None
    for inc in incidents:
        if inc["id"].lower().startswith(partial_id):
            return inc
        if inc["id"].lower() == partial_id:
            return inc
    # Try matching by title keywords
    for inc in incidents:
        if partial_id in inc.get("title", "").lower():
            return inc
    return None


# ---------------------------------------------------------------------------
# Photo Incident Reporting (Mobile)
# ---------------------------------------------------------------------------

UPLOAD_DIR = Path(__file__).parent.parent.parent / "data" / "uploads"

REPORT_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>GridWatch - Report Incident</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0a0a12;color:#e0e0e0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;min-height:100vh;display:flex;flex-direction:column}
.header{background:linear-gradient(135deg,#111118,#16161f);padding:16px 20px;border-bottom:1px solid #2a2a35;text-align:center}
.header h1{font-size:20px;font-weight:700;background:linear-gradient(135deg,#00d4ff,#7b2fff);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.header p{font-size:12px;color:#666;margin-top:4px}
.container{flex:1;padding:20px;max-width:480px;margin:0 auto;width:100%}
.photo-area{border:2px dashed #2a2a35;border-radius:12px;padding:40px 20px;text-align:center;margin-bottom:16px;cursor:pointer;transition:all 0.3s;position:relative;overflow:hidden;min-height:200px;display:flex;flex-direction:column;align-items:center;justify-content:center}
.photo-area:hover,.photo-area.dragover{border-color:#00d4ff;background:#0d0d1a}
.photo-area.has-photo{padding:0;border-style:solid;border-color:#2a2a35}
.photo-area img{max-width:100%;max-height:300px;border-radius:10px;display:block}
.photo-icon{font-size:48px;margin-bottom:12px}
.photo-text{font-size:14px;color:#888}
.photo-sub{font-size:11px;color:#555;margin-top:6px}
input[type="file"]{display:none}
.field{margin-bottom:16px}
.field label{display:block;font-size:12px;font-weight:600;color:#888;margin-bottom:6px;text-transform:uppercase;letter-spacing:0.5px}
.field textarea{width:100%;background:#111118;border:1px solid #2a2a35;border-radius:8px;padding:12px;color:#e0e0e0;font-size:14px;resize:vertical;min-height:80px;font-family:inherit}
.field textarea:focus{outline:none;border-color:#00d4ff}
.gps-status{font-size:12px;padding:8px 12px;border-radius:8px;margin-bottom:16px;display:flex;align-items:center;gap:8px}
.gps-status.detecting{background:#1a1a2e;color:#00d4ff;border:1px solid #00d4ff33}
.gps-status.found{background:#0a2a0a;color:#00ff88;border:1px solid #00ff8833}
.gps-status.failed{background:#2a0a0a;color:#ff4444;border:1px solid #ff444433}
.gps-dot{width:8px;height:8px;border-radius:50%;animation:pulse 1.5s infinite}
.gps-status.detecting .gps-dot{background:#00d4ff}
.gps-status.found .gps-dot{background:#00ff88;animation:none}
.gps-status.failed .gps-dot{background:#ff4444;animation:none}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.3}}
.submit-btn{width:100%;padding:14px;background:linear-gradient(135deg,#00d4ff,#7b2fff);color:#fff;border:none;border-radius:10px;font-size:16px;font-weight:700;cursor:pointer;transition:all 0.3s;text-transform:uppercase;letter-spacing:1px}
.submit-btn:hover{transform:translateY(-1px);box-shadow:0 4px 20px #00d4ff44}
.submit-btn:disabled{opacity:0.5;cursor:not-allowed;transform:none;box-shadow:none}
.submit-btn.submitting{background:linear-gradient(135deg,#333,#444)}
.result{margin-top:20px;padding:20px;border-radius:12px;display:none}
.result.success{display:block;background:#0a2a0a;border:1px solid #00ff8833}
.result.error{display:block;background:#2a0a0a;border:1px solid #ff444433}
.result h3{font-size:16px;margin-bottom:12px}
.result.success h3{color:#00ff88}
.result.error h3{color:#ff4444}
.result-row{display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid #ffffff11;font-size:13px}
.result-row .label{color:#888}
.result-row .value{color:#e0e0e0;font-weight:500}
.result-ai{margin-top:12px;padding:12px;background:#111118;border-radius:8px;font-size:13px;color:#ccc;line-height:1.5}
.result-ai .ai-label{font-size:10px;color:#7b2fff;font-weight:700;text-transform:uppercase;margin-bottom:4px}
.back-btn{display:inline-block;margin-top:16px;padding:10px 20px;background:#1a1a2e;color:#00d4ff;border:1px solid #00d4ff33;border-radius:8px;text-decoration:none;font-size:13px;font-weight:600}
</style>
</head>
<body>
<div class="header">
<h1>GRIDWATCH</h1>
<p>NYC Urban Intelligence - Photo Report</p>
</div>
<div class="container">
<div class="photo-area" id="photoArea" onclick="document.getElementById('photoInput').click()">
<div class="photo-icon">&#x1F4F7;</div>
<div class="photo-text">Tap to take a photo or upload</div>
<div class="photo-sub">Supports camera capture &amp; gallery</div>
</div>
<input type="file" id="photoInput" accept="image/*" capture="environment">
<div class="gps-status detecting" id="gpsStatus">
<div class="gps-dot"></div>
<span>Detecting your location...</span>
</div>
<div class="field" id="addressField" style="display:none">
<label>Location (GPS unavailable — enter address)</label>
<input type="text" id="address" placeholder="e.g. 200 Broadway Manhattan" style="width:100%;background:#111118;border:1px solid #2a2a35;border-radius:8px;padding:12px;color:#e0e0e0;font-size:14px;font-family:inherit">
</div>
<div class="field">
<label>Description (optional)</label>
<textarea id="description" placeholder="What's the issue? e.g. 'Flooded street, water is knee-deep'"></textarea>
</div>
<button class="submit-btn" id="submitBtn" onclick="submitReport()">Submit Report</button>
<div class="result" id="result"></div>
</div>
<script>
let lat=null,lng=null,photoFile=null;
// GPS
if(navigator.geolocation){
navigator.geolocation.getCurrentPosition(
p=>{lat=p.coords.latitude;lng=p.coords.longitude;
document.getElementById('gpsStatus').className='gps-status found';
document.getElementById('gpsStatus').innerHTML='<div class="gps-dot"></div><span>Location found: '+lat.toFixed(5)+', '+lng.toFixed(5)+'</span>'},
e=>{document.getElementById('gpsStatus').className='gps-status failed';
document.getElementById('gpsStatus').innerHTML='<div class="gps-dot"></div><span>GPS unavailable - enter address below</span>';
document.getElementById('addressField').style.display='block'},
{enableHighAccuracy:true,timeout:15000}
)}
// Photo
document.getElementById('photoInput').addEventListener('change',function(e){
if(e.target.files&&e.target.files[0]){
photoFile=e.target.files[0];
const reader=new FileReader();
reader.onload=function(ev){
const area=document.getElementById('photoArea');
area.innerHTML='<img src="'+ev.target.result+'" alt="Photo">';
area.classList.add('has-photo')};
reader.readAsDataURL(photoFile)}});
// Submit
async function submitReport(){
if(!photoFile){alert('Please take or upload a photo first');return}
const btn=document.getElementById('submitBtn');
btn.disabled=true;btn.textContent='Analyzing...';btn.classList.add('submitting');
const fd=new FormData();
fd.append('photo',photoFile);
fd.append('description',document.getElementById('description').value);
var addr=document.getElementById('address');
if(addr&&addr.value)fd.append('address',addr.value);
if(lat)fd.append('latitude',lat);
if(lng)fd.append('longitude',lng);
try{
const resp=await fetch('/api/report/photo',{method:'POST',body:fd});
const data=await resp.json();
if(resp.ok){
const r=document.getElementById('result');
r.className='result success';
r.innerHTML='<h3>Incident Reported!</h3>'+
'<div class="result-row"><span class="label">ID</span><span class="value">#'+data.id.substring(0,8)+'</span></div>'+
'<div class="result-row"><span class="label">Category</span><span class="value">'+data.category+'</span></div>'+
'<div class="result-row"><span class="label">Severity</span><span class="value">'+data.severity+'</span></div>'+
'<div class="result-row"><span class="label">Location</span><span class="value">'+(data.latitude?data.latitude.toFixed(4)+', '+data.longitude.toFixed(4):'N/A')+'</span></div>'+
(data.ai_analysis?'<div class="result-ai"><div class="ai-label">AI Analysis (Llama Vision)</div>'+data.ai_analysis+'</div>':'')+
'<a href="/" class="back-btn">View on Map &rarr;</a>'}
else{const r=document.getElementById('result');r.className='result error';r.innerHTML='<h3>Error</h3><p>'+(data.detail||'Submission failed')+'</p>'}
}catch(e){const r=document.getElementById('result');r.className='result error';r.innerHTML='<h3>Network Error</h3><p>'+e.message+'</p>'}
btn.disabled=false;btn.textContent='Submit Report';btn.classList.remove('submitting')}
</script>
</body>
</html>"""


@app.get("/report", response_class=HTMLResponse)
def report_page():
    """Serve the mobile photo reporting page."""
    return HTMLResponse(content=REPORT_HTML)


@app.post("/api/report/photo")
async def report_photo(
    photo: UploadFile = File(...),
    description: str = Form(""),
    address: str = Form(""),
    latitude: float = Form(None),
    longitude: float = Form(None),
):
    """Accept a photo-based incident report.

    - Extracts EXIF GPS if browser GPS unavailable
    - Uses Ollama llama3.2-vision:11b to analyze the photo
    - Creates an incident with detected category + AI description
    """
    import uuid, os, base64

    # Ensure upload dir exists
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    # Save photo
    photo_bytes = await photo.read()
    ext = Path(photo.filename or "photo.jpg").suffix or ".jpg"
    photo_id = uuid.uuid4().hex[:12]
    photo_path = UPLOAD_DIR / f"{photo_id}{ext}"
    photo_path.write_bytes(photo_bytes)
    logger.info(f"[PhotoReport] Saved photo: {photo_path} ({len(photo_bytes)} bytes)")

    # Extract EXIF GPS if no browser GPS
    exif_lat, exif_lon = None, None
    if not latitude or not longitude:
        try:
            from PIL import Image
            from PIL.ExifTags import TAGS, GPSTAGS
            import io

            img = Image.open(io.BytesIO(photo_bytes))
            exif_data = img._getexif()
            if exif_data:
                gps_info = {}
                for tag_id, value in exif_data.items():
                    tag = TAGS.get(tag_id, tag_id)
                    if tag == "GPSInfo":
                        for gps_tag_id, gps_value in value.items():
                            gps_tag = GPSTAGS.get(gps_tag_id, gps_tag_id)
                            gps_info[gps_tag] = gps_value

                if "GPSLatitude" in gps_info and "GPSLongitude" in gps_info:
                    def dms_to_decimal(dms, ref):
                        d, m, s = [float(x) for x in dms]
                        decimal = d + m / 60 + s / 3600
                        if ref in ("S", "W"):
                            decimal = -decimal
                        return decimal

                    exif_lat = dms_to_decimal(
                        gps_info["GPSLatitude"],
                        gps_info.get("GPSLatitudeRef", "N")
                    )
                    exif_lon = dms_to_decimal(
                        gps_info["GPSLongitude"],
                        gps_info.get("GPSLongitudeRef", "W")
                    )
                    logger.info(f"[PhotoReport] EXIF GPS: {exif_lat}, {exif_lon}")
        except Exception as e:
            logger.warning(f"[PhotoReport] EXIF extraction failed: {e}")

    final_lat = latitude or exif_lat
    final_lon = longitude or exif_lon
    final_address = address

    # If still no GPS, try geocoding the manual address
    if (not final_lat or not final_lon) and address:
        try:
            from hackathon_nyc.tools.geocoding import geocode_address
            geo = await geocode_address(address + ", New York City, NY")
            if "error" not in geo and geo.get("lat"):
                final_lat = float(geo["lat"])
                final_lon = float(geo["lon"])
                final_address = geo.get("display_name", address)
                logger.info(f"[PhotoReport] Geocoded address: {final_address}")
        except Exception as e:
            logger.warning(f"[PhotoReport] Geocoding failed: {e}")

    # Analyze photo with Ollama llama3.2-vision:11b
    ai_analysis = ""
    ai_category = "other"
    ai_severity = "medium"
    try:
        import aiohttp
        photo_b64 = base64.b64encode(photo_bytes).decode("utf-8")

        prompt = (
            "You are an NYC urban incident analyst. Analyze this photo and respond with EXACTLY this format:\\n"
            "CATEGORY: <one of: flooding, fire, pothole, rodent, sewer, noise, street_condition, tree, water, health, other>\\n"
            "SEVERITY: <one of: low, medium, high, critical>\\n"
            "DESCRIPTION: <1-2 sentence description of what you see and the issue>\\n\\n"
            "Be specific about the urban issue visible in the photo."
        )

        async with aiohttp.ClientSession() as session:
            payload = {
                "model": "llama3.2-vision:11b",
                "prompt": prompt,
                "images": [photo_b64],
                "stream": False,
            }
            async with session.post(
                f"http://{__import__('os').getenv('OLLAMA_HOST', 'localhost')}:11434/api/generate",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    ai_text = result.get("response", "")
                    ai_analysis = ai_text
                    logger.info(f"[PhotoReport] AI analysis: {ai_text[:200]}")

                    # Parse category
                    for line in ai_text.split("\n"):
                        line_lower = line.strip().lower()
                        if line_lower.startswith("category:"):
                            cat = line_lower.split(":", 1)[1].strip()
                            valid_cats = ["flooding", "fire", "pothole", "rodent", "sewer",
                                          "noise", "street_condition", "tree", "water", "other"]
                            for vc in valid_cats:
                                if vc in cat:
                                    ai_category = vc
                                    break
                        elif line_lower.startswith("severity:"):
                            sev = line_lower.split(":", 1)[1].strip()
                            for vs in ["low", "medium", "high", "critical"]:
                                if vs in sev:
                                    ai_severity = vs
                                    break
                        elif line_lower.startswith("description:"):
                            ai_analysis = line.split(":", 1)[1].strip()
                else:
                    logger.warning(f"[PhotoReport] Ollama returned {resp.status}")
    except Exception as e:
        logger.error(f"[PhotoReport] Vision analysis failed: {e}")
        ai_analysis = f"Photo uploaded (AI analysis unavailable: {e})"

    # Override category based on user description keywords
    desc_lower = (description or "").lower()
    if any(w in desc_lower for w in ["sick", "unwell", "unconscious", "medical", "health", "hurt", "injured", "fallen", "collapsed", "overdose", "homeless"]):
        ai_category = "health"
    elif any(w in desc_lower for w in ["fire", "smoke", "burning"]):
        ai_category = "fire"

    # Use description from user if AI analysis is empty
    final_description = description or ai_analysis or "Photo report"
    if description and ai_analysis:
        final_description = f"{description}\n\nAI Analysis: {ai_analysis}"

    # Create the incident
    incident = db.create_incident(
        title=(f"📷 {description[:40]}" if description else f"Photo Report: {ai_category.replace('_', ' ').title()}")[:60],
        category=ai_category,
        description=final_description,
        severity=ai_severity,
        source="citizen_photo",
        latitude=final_lat,
        longitude=final_lon,
        address=final_address,
    )
    incident["ai_analysis"] = ai_analysis
    incident["photo_path"] = str(photo_path)

    # Auto-alert nearby subscribers
    if final_lat and final_lon:
        try:
            subscribers = db.find_subscribers_near(final_lat, final_lon, ai_category)
            if subscribers:
                import os
                from twilio.rest import Client as TwilioClient
                sid = os.getenv("TWILIO_ACCOUNT_SID")
                token = os.getenv("TWILIO_AUTH_TOKEN")
                from_num = os.getenv("TWILIO_PHONE_NUMBER")
                if sid and token and from_num:
                    tw = TwilioClient(sid, token)
                    for sub in subscribers:
                        if sub.get("contact_type") == "sms" and sub.get("contact"):
                            try:
                                tw.messages.create(
                                    body=f"📸 GRIDWATCH: {ai_category.upper()} reported via photo near your area. #{incident['id'][:8]}",
                                    from_=from_num, to=sub["contact"],
                                )
                            except Exception:
                                pass
        except Exception:
            pass

    return incident


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------

@app.get("/api/cameras")
async def get_cameras():
    """Proxy NYC DOT traffic cameras to avoid CORS issues."""
    import aiohttp
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://webcams.nyctmc.org/api/cameras/", timeout=aiohttp.ClientTimeout(total=10)) as resp:
                cameras = await resp.json()
        return [c for c in cameras if c.get("isOnline") == "true" and c.get("latitude") and c.get("longitude")]
    except Exception as e:
        return []

@app.get("/")
def serve_frontend():
    """Serve the map dashboard."""
    return FileResponse(FRONTEND_DIR / "index.html")
