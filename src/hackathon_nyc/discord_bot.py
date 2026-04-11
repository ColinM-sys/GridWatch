"""Simple Discord bot that forwards messages to the NYC dispatch webhook.

No LLM needed — just forwards the message text, the webhook handles
geocoding and categorization.

Run: PYTHONPATH=src python -m hackathon_nyc.discord_bot
"""

import os
import aiohttp
import discord

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
WEBHOOK_URL = "http://localhost:8000/api/webhook/report"
SUBSCRIBE_URL = "http://localhost:8000/api/alerts/subscribe"

# Keywords that indicate a report (not casual chat)
REPORT_KEYWORDS = [
    "flood", "sewer", "noise", "loud", "rat", "rodent", "mouse",
    "heat", "hot water", "pothole", "crash", "accident", "fire",
    "tree", "water", "construction", "broken", "damaged", "leak",
    "smell", "gas", "electric", "power", "sidewalk", "street",
]

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)


@client.event
async def on_ready():
    print(f"NYC Dispatch Bot online as {client.user}")
    await client.change_presence(activity=discord.CustomActivity(name="NYC Urban Intelligence"))


@client.event
async def on_message(message):
    # Ignore own messages
    if message.author == client.user:
        return

    # Only respond to DMs or when mentioned in a server
    is_dm = isinstance(message.channel, discord.DMChannel)
    is_mentioned = client.user in message.mentions
    if not is_dm and not is_mentioned:
        return

    text = message.content.replace(f"<@{client.user.id}>", "").strip()
    if not text:
        return

    text_lower = text.lower()

    # Check for alert subscription
    if text_lower.startswith("alert ") or text_lower.startswith("subscribe ") or text_lower.startswith("notify "):
        address = text[text.index(" ")+1:].strip()
        await message.add_reaction("👀")
        async with aiohttp.ClientSession() as session:
            async with session.post(SUBSCRIBE_URL, json={
                "name": str(message.author),
                "contact": str(message.author.id),
                "contact_type": "discord",
                "address": address,
                "radius_miles": 1.0,
            }) as resp:
                if resp.status == 200:
                    await message.add_reaction("🔔")
                else:
                    await message.add_reaction("❌")
        return

    # Check if it looks like a report
    is_report = any(kw in text_lower for kw in REPORT_KEYWORDS)
    if not is_report and not is_dm:
        # In servers, ignore non-report messages even if mentioned
        return

    # Clean message for better geocoding
    text = text.replace(' and ', ' & ').replace(' AND ', ' & ')

    # Forward to webhook
    await message.add_reaction("👀")

    async with aiohttp.ClientSession() as session:
        async with session.post(WEBHOOK_URL, json={
            "message": text,
            "source": "discord",
            "user": str(message.author),
        }) as resp:
            if resp.status == 200:
                data = await resp.json()
                incident_id = data.get("id", "?")
                await message.add_reaction("✅")
                # Brief confirmation
                addr = data.get("address", "")
                cat = data.get("category", "other")
                short_addr = addr[:40] + "..." if len(addr) > 40 else addr
                await message.reply(f"Incident **#{incident_id}** created — {cat} near {short_addr}", mention_author=False)
            else:
                await message.add_reaction("❌")


if __name__ == "__main__":
    client.run(DISCORD_TOKEN)
