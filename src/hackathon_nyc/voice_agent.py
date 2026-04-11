"""Pipecat Voice Agent — AI dispatcher that answers phone calls.

Citizens call the Twilio number → Pipecat handles real-time voice conversation →
AI creates incidents, checks sensors, alerts subscribers — all through natural speech.

Runs 100% locally:
  - STT: faster-whisper (local GPU)
  - LLM: Nemotron via Ollama (local)
  - TTS: Kokoro (local ONNX) — or swap for ElevenLabs/Cartesia
  - VAD: Silero (local)
  - Transport: Twilio WebSocket (phone number)

Setup:
  1. pip install "pipecat-ai[silero,whisper,kokoro,websocket,runner]"
  2. Set TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN env vars
  3. Configure Twilio to stream to: wss://your-ngrok-url/ws
  4. Run: python -m hackathon_nyc.voice_agent --transport twilio

For local testing (mic/speaker, no phone):
  pip install "pipecat-ai[silero,whisper,kokoro,local]"
  python -m hackathon_nyc.voice_agent --transport local
"""

import os
import json
import asyncio

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.ollama.llm import OLLamaLLMService
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.services.llm_service import FunctionCallParams
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams

# Import our CRM tools
from hackathon_nyc import db
from hackathon_nyc.tools import nyc_opendata, floodnet, geocoding


# ---------------------------------------------------------------------------
# Tool handlers — the AI can call these mid-conversation
# ---------------------------------------------------------------------------

async def handle_create_incident(params: FunctionCallParams):
    """Create a new incident from the caller's report."""
    args = params.arguments
    # Geocode if address provided
    lat, lon = None, None
    if args.get("address"):
        geo = await geocoding.geocode_address(args["address"])
        if "error" not in geo:
            lat, lon = geo["lat"], geo["lon"]

    incident = db.create_incident(
        title=args.get("title", "Voice report"),
        category=args.get("category", "other"),
        description=args.get("description", ""),
        severity=args.get("severity", "medium"),
        latitude=lat,
        longitude=lon,
        address=args.get("address", ""),
        borough=args.get("borough", ""),
        source="citizen_voice",
    )
    await params.result_callback({
        "incident_id": incident["id"],
        "confirmed": bool(incident.get("confirmed")),
        "message": f"Incident #{incident['id']} created successfully.",
    })


async def handle_check_floods(params: FunctionCallParams):
    """Check for active flooding near a location."""
    args = params.arguments
    address = args.get("address", "")
    if address:
        geo = await geocoding.geocode_address(address)
        if "error" not in geo:
            sensors = await floodnet.get_sensor_locations()
            nearest = geocoding.find_nearest_points(geo["lat"], geo["lon"], sensors, 3)
            await params.result_callback({
                "nearest_sensors": [{"id": s.get("sensor_id"), "distance_miles": s.get("distance_miles")} for s in nearest],
                "location": address,
            })
            return
    await params.result_callback({"error": "Could not find location"})


async def handle_check_complaints(params: FunctionCallParams):
    """Check recent 311 complaints near a location."""
    args = params.arguments
    complaints = await nyc_opendata.get_311_complaints(
        complaint_type=args.get("type", ""),
        borough=args.get("borough", ""),
        zip_code=args.get("zip_code", ""),
        limit=5,
    )
    summary = [{"type": c.get("complaint_type"), "status": c.get("status"), "date": c.get("created_date")} for c in complaints[:5]]
    await params.result_callback({"recent_complaints": summary, "count": len(complaints)})


async def handle_subscribe_alerts(params: FunctionCallParams):
    """Subscribe the caller to alerts near their address."""
    args = params.arguments
    address = args.get("address", "")
    geo = await geocoding.geocode_address(address)
    if "error" in geo:
        await params.result_callback({"error": f"Could not find address: {address}"})
        return
    sub = db.subscribe_alerts(
        name=args.get("name", "Voice caller"),
        contact=args.get("phone", "unknown"),
        contact_type="sms",
        latitude=geo["lat"],
        longitude=geo["lon"],
        address=address,
        radius_miles=args.get("radius_miles", 1.0),
    )
    await params.result_callback({
        "subscription_id": sub["id"],
        "message": f"Subscribed to alerts within {sub['radius_miles']} miles of {address}.",
    })


async def handle_get_incident_stats(params: FunctionCallParams):
    """Get current incident statistics."""
    stats = db.get_stats()
    await params.result_callback(stats)


# ---------------------------------------------------------------------------
# Tool schemas (what the LLM knows it can call)
# ---------------------------------------------------------------------------

tools = ToolsSchema(standard_tools=[
    FunctionSchema(
        name="create_incident",
        description="Create a new incident report on the city map. Use when a caller reports a problem.",
        properties={
            "title": {"type": "string", "description": "Brief title of the incident"},
            "category": {"type": "string", "enum": ["flooding", "sewer", "noise", "rodent", "heat", "air_quality", "street_condition", "water", "tree", "other"], "description": "Incident category"},
            "description": {"type": "string", "description": "Detailed description"},
            "severity": {"type": "string", "enum": ["low", "medium", "high", "critical"], "description": "How severe is the issue"},
            "address": {"type": "string", "description": "Street address of the incident"},
            "borough": {"type": "string", "description": "NYC borough"},
        },
        required=["title", "category", "address"],
    ),
    FunctionSchema(
        name="check_floods",
        description="Check for active flooding and nearby flood sensors at a location.",
        properties={
            "address": {"type": "string", "description": "Address to check for flooding"},
        },
        required=["address"],
    ),
    FunctionSchema(
        name="check_complaints",
        description="Check recent 311 complaints in an area.",
        properties={
            "type": {"type": "string", "description": "Complaint type filter"},
            "borough": {"type": "string", "description": "Borough filter"},
            "zip_code": {"type": "string", "description": "Zip code filter"},
        },
        required=[],
    ),
    FunctionSchema(
        name="subscribe_alerts",
        description="Subscribe the caller to incident alerts near their address.",
        properties={
            "name": {"type": "string", "description": "Caller's name"},
            "phone": {"type": "string", "description": "Phone number for SMS alerts"},
            "address": {"type": "string", "description": "Center address for alert radius"},
            "radius_miles": {"type": "number", "description": "Alert radius in miles (default 1)"},
        },
        required=["address"],
    ),
    FunctionSchema(
        name="get_incident_stats",
        description="Get current counts of open, active, and resolved incidents across the city.",
        properties={},
        required=[],
    ),
])


# ---------------------------------------------------------------------------
# Voice agent pipeline
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are the NYC Urban Intelligence voice dispatcher. You answer phone calls
from New York City residents reporting problems and requesting information.

Your personality: Professional but warm. Efficient. You speak in short, clear sentences
since this is a phone conversation. Never use markdown, bullet points, or formatting —
just natural speech.

What you can do:
- Take incident reports (flooding, sewer, noise, rodents, heat, etc.)
- Check for active flooding near an address
- Look up recent 311 complaints in an area
- Subscribe callers to alerts near their address
- Give current incident statistics
- Answer questions about historical NYC incidents and trends — for ANY question
  about past complaints, prior crashes, rat history, housing violations, floods,
  potholes, or "what has happened before" in a neighborhood, call the
  `historical_lookup` tool FIRST and base your answer on its results.

When taking a report:
1. Ask what the problem is
2. Ask for the address/location
3. Ask how severe it is
4. Create the incident
5. Confirm the incident ID
6. Offer to subscribe them to alerts for their area

Keep responses SHORT — this is a phone call, not a text chat. One to two sentences max.
Always confirm what you heard before creating an incident."""


# Transport configurations
transport_params = {
    "twilio": lambda: FastAPIWebsocketParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
    ),
    "local": lambda: TransportParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
    ),
}


async def run_bot(transport: BaseTransport, runner_args: RunnerArguments):
    # STT — try local whisper first, fall back to options
    try:
        from pipecat.services.whisper.stt import WhisperSTTService
        stt = WhisperSTTService(model_size="tiny.en")
    except ImportError:
        from pipecat.services.deepgram.stt import DeepgramSTTService
        stt = DeepgramSTTService(api_key=os.getenv("DEEPGRAM_API_KEY", ""))

    # TTS — try local kokoro first, fall back to options
    try:
        from pipecat.services.kokoro.tts import KokoroTTSService
        tts = KokoroTTSService()
    except ImportError:
        try:
            from pipecat.services.cartesia.tts import CartesiaTTSService
            tts = CartesiaTTSService(api_key=os.getenv("CARTESIA_API_KEY", ""))
        except ImportError:
            from pipecat.services.piper.tts import PiperTTSService
            tts = PiperTTSService()

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

    # Context with tools
    context = LLMContext(tools=tools)

    # Aggregators (handle turn-taking)
    user_agg, assistant_agg = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
    )

    # Pipeline: audio in → STT → LLM → TTS → audio out
    pipeline = Pipeline([
        transport.input(),
        stt,
        user_agg,
        llm,
        tts,
        transport.output(),
        assistant_agg,
    ])

    task = PipelineTask(
        pipeline,
        params=PipelineParams(enable_metrics=True),
        idle_timeout_secs=runner_args.pipeline_idle_timeout_secs if hasattr(runner_args, 'pipeline_idle_timeout_secs') else 120,
    )

    @transport.event_handler("on_client_connected")
    async def on_connected(transport, client):
        # Greet the caller
        context.add_message({"role": "system", "content": "A caller just connected. Greet them briefly."})
        await task.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_disconnected(transport, client):
        await task.cancel()

    runner = PipelineRunner(handle_sigint=True)
    await runner.run(task)


async def bot(runner_args: RunnerArguments):
    transport = await create_transport(runner_args, transport_params)
    await run_bot(transport, runner_args)


if __name__ == "__main__":
    from pipecat.runner.run import main
    main()
