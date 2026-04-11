#!/bin/bash
# GridWatch Quick Setup
# Works on Mac, Linux, and DGX Spark

echo "=== GridWatch Setup ==="

# Check Docker
if ! command -v docker &>/dev/null; then
    echo "Docker not found. Install Docker first: https://docs.docker.com/get-docker/"
    exit 1
fi

# Copy env file if not exists
if [ ! -f .env ]; then
    cp .env.example .env
    echo "Created .env — edit with your credentials (optional for basic demo)"
fi

# Start everything
echo "Starting GridWatch..."
docker compose up -d

# Wait for Ollama and pull model
echo "Waiting for Ollama to start..."
sleep 15
echo "Pulling Nemotron-Mini model (2.7GB)..."
docker compose exec ollama ollama pull nemotron-mini

echo ""
echo "=== GridWatch is running ==="
echo "Map:     http://localhost:8080"
echo "API:     http://localhost:8000"
echo "Report:  http://localhost:8000/report"
echo ""
echo "To stop: docker compose down"
