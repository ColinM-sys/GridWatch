FROM python:3.12-slim

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl gcc && rm -rf /var/lib/apt/lists/*

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir discord.py aiohttp twilio chromadb pydantic \
    requests beautifulsoup4 pyyaml python-multipart Pillow faster-whisper \
    pipecat-ai[silero,websocket]

# Copy project
COPY . .

EXPOSE 8000 8080
