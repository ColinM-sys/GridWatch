"""OpenClaw integration — send alerts via multiple channels.

Uses OpenClaw CLI to send alert messages through WhatsApp, Telegram,
Discord, and other connected channels. Our system handles the logic,
OpenClaw handles the delivery.

Channels OpenClaw supports:
  - WhatsApp (link phone via: openclaw channels login --channel whatsapp)
  - Telegram (add bot token via: openclaw channels add --channel telegram --token BOT_TOKEN)
  - Discord (already configured)
  - Signal, Slack, iMessage, IRC, etc.

Usage:
  from hackathon_nyc.openclaw_alerts import send_alert
  await send_alert(channel="discord", target="user_id", message="Flooding near you!")
"""

import asyncio
import json


async def send_alert(channel: str, target: str, message: str) -> dict:
    """Send an alert message via OpenClaw.

    Args:
        channel: 'discord', 'whatsapp', 'telegram', 'signal', etc.
        target: user ID, phone number, or chat ID depending on channel
        message: the alert text

    Returns:
        dict with status and details
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "openclaw", "message", "send",
            "--channel", channel,
            "--target", target,
            "--message", message,
            "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)

        if proc.returncode == 0:
            try:
                return {"status": "sent", "channel": channel, "target": target, "detail": json.loads(stdout.decode())}
            except json.JSONDecodeError:
                return {"status": "sent", "channel": channel, "target": target}
        else:
            return {"status": "failed", "channel": channel, "error": stderr.decode().strip()}
    except asyncio.TimeoutError:
        return {"status": "timeout", "channel": channel}
    except Exception as e:
        return {"status": "error", "channel": channel, "error": str(e)}


async def broadcast_incident_alert(incident: dict, subscribers: list[dict]) -> list[dict]:
    """Send alerts to all subscribers near a confirmed incident.

    Args:
        incident: the incident dict from the database
        subscribers: list of subscriber dicts from find_subscribers_near()

    Returns:
        list of send results
    """
    cat_emoji = {
        'flooding': '🌊', 'sewer': '🚰', 'noise': '🎵',
        'rodent': '🐀', 'heat': '🔥', 'air_quality': '💨',
        'street_condition': '🚧', 'water': '💧', 'tree': '🌳',
    }.get(incident.get("category", ""), '⚠️')

    message = (
        f"{cat_emoji} NYC Alert: {incident['title']}\n"
        f"Near: {incident.get('address', 'your area')}\n"
        f"Severity: {incident.get('severity', 'medium')}\n"
        f"Report #{incident['id']}"
    )

    results = []
    tasks = []

    for sub in subscribers:
        channel = sub.get("contact_type", "discord")
        target = sub.get("contact", "")

        # Map contact_type to OpenClaw channel names
        channel_map = {
            "sms": "whatsapp",  # prefer WhatsApp over SMS for OpenClaw
            "whatsapp": "whatsapp",
            "discord": "discord",
            "telegram": "telegram",
            "signal": "signal",
            "email": "discord",  # fallback
        }
        oc_channel = channel_map.get(channel, "discord")
        tasks.append(send_alert(oc_channel, target, message))

    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        results = [r if isinstance(r, dict) else {"status": "error", "error": str(r)} for r in results]

    return results
