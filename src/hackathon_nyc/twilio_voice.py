"""Twilio Voice + SMS integration for citizen reporting.

Gives the system a real phone number that citizens can:
  1. Call and leave a voice message → transcribed → incident created
  2. Text/SMS a report → agent processes → incident created
  3. Receive alerts when confirmed incidents happen near them

Setup:
  1. Sign up at twilio.com (free trial gives you $15 credit + a phone number)
  2. Get your Account SID, Auth Token, and phone number
  3. Set environment variables:
     export TWILIO_ACCOUNT_SID=your_sid
     export TWILIO_AUTH_TOKEN=your_token
     export TWILIO_PHONE_NUMBER=+1234567890
  4. Set your webhook URL in Twilio console:
     Voice: POST https://your-ngrok-url/api/voice/incoming
     SMS:   POST https://your-ngrok-url/api/sms/incoming

For hackathon: use ngrok to expose localhost:8000 to the internet
  ngrok http 8000
  Then paste the ngrok URL into Twilio's webhook settings.

Run: pip install twilio
"""

import os
from fastapi import Request, Response
from hackathon_nyc import db
from hackathon_nyc.tools import geocoding

# Twilio credentials from environment
TWILIO_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE = os.getenv("TWILIO_PHONE_NUMBER", "")


def register_twilio_routes(app):
    """Register Twilio voice + SMS routes on the FastAPI app."""

    @app.post("/api/voice/incoming")
    async def handle_incoming_call(request: Request):
        """Handle incoming phone call.

        If Pipecat voice agent is running on /ws, streams audio to it for
        live AI conversation. Otherwise falls back to voicemail transcription.
        """
        # Check if Pipecat WebSocket is available
        host = request.headers.get("host", "localhost:8000")
        scheme = "wss" if request.url.scheme == "https" else "ws"

        pipecat_mode = True

        if pipecat_mode:
            twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
            <Response>
                <Connect>
                    <Stream url="{scheme}://{host}/ws">
                        <Parameter name="caller" value="{{{{From}}}}" />
                    </Stream>
                </Connect>
            </Response>"""
        else:
            twiml = """<?xml version="1.0" encoding="UTF-8"?>
            <Response>
                <Say voice="alice">
                    Welcome to NYC Urban Intelligence. Please describe the incident
                    you'd like to report, including the location. Press any key when done,
                    or hang up after the beep.
                </Say>
                <Record
                    action="/api/voice/recording"
                    transcribe="true"
                    transcribeCallback="/api/voice/transcription"
                    maxLength="120"
                    playBeep="true"
                />
            </Response>"""
        return Response(content=twiml, media_type="application/xml")

    @app.post("/api/voice/incoming-voicemail")
    async def handle_incoming_voicemail(request: Request):
        """Fallback: basic voicemail if Pipecat isn't available."""
        twiml = """<?xml version="1.0" encoding="UTF-8"?>
        <Response>
            <Say voice="alice">
                Welcome to NYC Urban Intelligence. Please describe the incident
                you'd like to report, including the location. Press any key when done,
                or hang up after the beep.
            </Say>
            <Record
                action="/api/voice/recording"
                transcribe="true"
                transcribeCallback="/api/voice/transcription"
                maxLength="120"
                playBeep="true"
            />
        </Response>"""
        return Response(content=twiml, media_type="application/xml")

    @app.post("/api/voice/recording")
    async def handle_recording(request: Request):
        """Called after voicemail recording is saved."""
        twiml = """<?xml version="1.0" encoding="UTF-8"?>
        <Response>
            <Say voice="alice">Thank you. Your report has been received. Goodbye.</Say>
            <Hangup/>
        </Response>"""
        return Response(content=twiml, media_type="application/xml")

    @app.post("/api/voice/transcription")
    async def handle_transcription(request: Request):
        """Voicemail fallback: transcription → incident."""
        form = await request.form()
        transcription = form.get("TranscriptionText", "")
        caller = form.get("From", "unknown")
        recording_url = form.get("RecordingUrl", "")

        if not transcription:
            return {"status": "no transcription"}

        # Try to geocode address from transcription
        lat, lon, address = None, None, ""
        try:
            import re
            # Fix common Twilio transcription errors
            transcription = re.sub(r'\$(\d+)\.00', r'\1', transcription)
            transcription = re.sub(r'\$(\d+)', r'\1', transcription)
            cleaned = re.sub(r'\b(report|there is|flooding|flood|noise|rats?|sewer|pothole|water|about|deep|inches|feet|foot|the|a|an|i want to)\b', ' ', transcription, flags=re.IGNORECASE)
            cleaned = re.sub(r'\s+', ' ', cleaned).strip()
            for query in [cleaned + ', New York City', transcription + ', New York']:
                geo = await geocoding.geocode_address(query)
                if "error" not in geo:
                    lat, lon = geo["lat"], geo["lon"]
                    address = geo.get("display_name", "")
                    break
        except Exception:
            pass

        incident = db.create_incident(
            title=f"Voice report: {transcription[:60]}",
            category="other",
            description=f"Voicemail from {caller}: {transcription}\n\nRecording: {recording_url}",
            source="citizen_voice",
            latitude=lat,
            longitude=lon,
            address=address,
        )

        if TWILIO_SID and caller != "unknown":
            try:
                from twilio.rest import Client
                client = Client(TWILIO_SID, TWILIO_TOKEN)
                client.messages.create(
                    body=f"NYC Urban Intelligence: Report #{incident['id']} received.",
                    from_=TWILIO_PHONE,
                    to=caller,
                )
            except Exception as e:
                print(f"SMS confirmation failed: {e}")

        return {"status": "incident_created", "id": incident["id"]}

    @app.post("/api/sms/incoming")
    async def handle_incoming_sms(request: Request):
        """Handle incoming SMS — create incident from text message."""
        form = await request.form()
        body = form.get("Body", "")
        sender = form.get("From", "unknown")

        if not body:
            twiml = """<?xml version="1.0" encoding="UTF-8"?>
            <Response><Message>Please describe the incident you want to report.</Message></Response>"""
            return Response(content=twiml, media_type="application/xml")

        # Check if it's an alert subscription request
        body_lower = body.lower()
        if body_lower.startswith("alert ") or body_lower.startswith("subscribe "):
            # e.g. "ALERT 123 Main St Brooklyn" or "SUBSCRIBE 10001"
            address = body[body.index(" ")+1:].strip()
            geo = await geocoding.geocode_address(address)
            if "error" not in geo:
                sub = db.subscribe_alerts(
                    name=sender, contact=sender, contact_type="sms",
                    latitude=geo["lat"], longitude=geo["lon"],
                    address=address, radius_miles=1.0,
                )
                reply = f"Subscribed to alerts within 1 mile of {address}. Reply STOP to unsubscribe."
            else:
                reply = f"Couldn't find that address. Try: ALERT 123 Main St, Brooklyn NY"

        elif body_lower == "stop" or body_lower == "unsubscribe":
            # Find and deactivate their subscription
            subs = db.list_subscriptions()
            for s in subs:
                if s["contact"] == sender:
                    db.unsubscribe(s["id"])
            reply = "You've been unsubscribed from all alerts."

        else:
            # Create incident from SMS — geocode address from message
            lat, lon, address = None, None, ""
            try:
                import re
                cleaned = re.sub(r'\b(report|there is|flooding|flood|noise|rats?|sewer|pothole|water|about|deep|inches|the|a|an)\b', ' ', body, flags=re.IGNORECASE)
                cleaned = re.sub(r'\s+', ' ', cleaned).strip()
                for query in [cleaned + ', New York City', body + ', New York']:
                    geo = await geocoding.geocode_address(query)
                    if "error" not in geo:
                        lat, lon = geo["lat"], geo["lon"]
                        address = geo.get("display_name", "")
                        break
            except Exception:
                pass

            incident = db.create_incident(
                title=f"SMS report: {body[:60]}",
                category="other",
                description=f"SMS from {sender}: {body}",
                source="citizen_sms",
                latitude=lat,
                longitude=lon,
                address=address,
            )
            reply = f"Report #{incident['id']} received. A dispatcher will review it. Text ALERT + your address to get nearby incident alerts."

        twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
        <Response><Message>{reply}</Message></Response>"""
        return Response(content=twiml, media_type="application/xml")

    @app.post("/api/alerts/send-sms")
    async def send_alert_sms(request: Request):
        """Send SMS alerts to subscribers near an incident.
        Call this after confirming an incident.
        Body: { "incident_id": "abc123" }
        """
        if not TWILIO_SID:
            return {"error": "Twilio not configured"}

        data = await request.json()
        incident_id = data.get("incident_id")
        incident = db.get_incident(incident_id)
        if not incident or not incident.get("confirmed"):
            return {"error": "Incident not found or not confirmed"}

        subscribers = db.find_subscribers_near(
            incident["latitude"], incident["longitude"],
            incident.get("category", ""),
        )

        if not subscribers:
            return {"sent": 0, "message": "No subscribers in range"}

        from twilio.rest import Client
        client = Client(TWILIO_SID, TWILIO_TOKEN)
        sent = 0

        for sub in subscribers:
            try:
                cat_emoji = {
                    'flooding': '🌊', 'sewer': '🚰', 'noise': '🎵',
                    'rodent': '🐀', 'heat': '🔥', 'other': '⚠️',
                }.get(incident["category"], '⚠️')

                client.messages.create(
                    body=f"{cat_emoji} NYC Alert: {incident['title']} near {incident.get('address', 'your area')}. {incident.get('description', '')}",
                    from_=TWILIO_PHONE,
                    to=sub["contact"],
                )
                sent += 1
            except Exception as e:
                print(f"Alert SMS to {sub['contact']} failed: {e}")

        return {"sent": sent, "total_subscribers": len(subscribers)}
