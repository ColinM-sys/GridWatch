#!/bin/bash
# GridWatch Configuration Script
# Run this after cloning to set up all API keys

echo "=== GridWatch Configuration ==="
echo ""

# Mapbox
echo "1. MAPBOX TOKEN (Required — map won't load without it)"
echo "   Get one free at: https://account.mapbox.com/access-tokens/"
echo "   Sign up → Create token → Copy the token starting with 'pk.'"
read -p "   Paste your Mapbox token: " MAPBOX_TOKEN
if [ -n "$MAPBOX_TOKEN" ]; then
    sed -i "s/YOUR_MAPBOX_TOKEN_HERE/$MAPBOX_TOKEN/" src/hackathon_nyc/frontend/index.html
    echo "   ✓ Mapbox token set"
else
    echo "   ⚠ Skipped — map will show black"
fi
echo ""

# Ollama
echo "2. OLLAMA + NEMOTRON (Required — AI chat needs this)"
echo "   Install Ollama: https://ollama.com/download"
if command -v ollama &>/dev/null; then
    echo "   ✓ Ollama is installed"
    echo "   Pulling nemotron-mini model..."
    ollama pull nemotron-mini
    echo "   ✓ Model ready"
else
    echo "   ⚠ Ollama not installed — install from https://ollama.com/download"
    echo "   Then run: ollama pull nemotron-mini"
fi
echo ""

# Discord
echo "3. DISCORD BOT TOKEN (Recommended — enables Discord reporting)"
echo "   Get one at: https://discord.com/developers/applications"
echo "   Create New Application → Bot → Reset Token → Copy"
echo "   Also enable: Bot → Privileged Gateway Intents → Message Content Intent"
read -p "   Paste your Discord bot token (or press Enter to skip): " DISCORD_TOKEN
if [ -n "$DISCORD_TOKEN" ]; then
    echo "export DISCORD_TOKEN=$DISCORD_TOKEN" >> .env
    echo "   ✓ Discord token saved to .env"
else
    echo "   ⚠ Skipped — Discord bot won't work"
fi
echo ""

# Twilio
echo "4. TWILIO (Recommended — enables phone calls and SMS)"
echo "   Sign up free at: https://console.twilio.com (gives \$15 credit)"
echo "   Account → Account SID and Auth Token"
echo "   Phone Numbers → Buy a Number"
read -p "   Paste your Twilio Account SID (or press Enter to skip): " TWILIO_SID
if [ -n "$TWILIO_SID" ]; then
    read -p "   Paste your Twilio Auth Token: " TWILIO_TOKEN
    read -p "   Paste your Twilio Phone Number (+1234567890): " TWILIO_PHONE
    echo "export TWILIO_ACCOUNT_SID=$TWILIO_SID" >> .env
    echo "export TWILIO_AUTH_TOKEN=$TWILIO_TOKEN" >> .env
    echo "export TWILIO_PHONE_NUMBER=$TWILIO_PHONE" >> .env
    echo "   ✓ Twilio credentials saved to .env"
else
    echo "   ⚠ Skipped — phone/SMS features won't work"
fi
echo ""

# ngrok
echo "5. NGROK (Recommended if using Twilio — exposes server for webhooks)"
echo "   Sign up free at: https://dashboard.ngrok.com/signup"
echo "   Get auth token at: https://dashboard.ngrok.com/get-started/your-authtoken"
if command -v ngrok &>/dev/null; then
    echo "   ✓ ngrok is installed"
    read -p "   Paste your ngrok auth token (or press Enter to skip): " NGROK_TOKEN
    if [ -n "$NGROK_TOKEN" ]; then
        ngrok config add-authtoken "$NGROK_TOKEN"
        echo "   ✓ ngrok configured"
    fi
else
    echo "   Install: https://ngrok.com/download"
fi
echo ""

# Summary
echo "=== Configuration Complete ==="
echo ""
echo "To start GridWatch:"
echo "  source .env 2>/dev/null"
echo "  ollama serve &"
echo "  PYTHONPATH=src uvicorn hackathon_nyc.server:app --host 0.0.0.0 --port 8000 &"
echo "  cd src/hackathon_nyc/frontend && python3 -m http.server 8080 --bind 0.0.0.0 &"
if [ -n "$DISCORD_TOKEN" ]; then
    echo "  PYTHONPATH=src python3 -m hackathon_nyc.discord_bot &"
fi
if [ -n "$TWILIO_SID" ]; then
    echo "  ngrok http 8000  # Copy URL to Twilio webhook settings"
fi
echo ""
echo "Open: http://localhost:8080"
