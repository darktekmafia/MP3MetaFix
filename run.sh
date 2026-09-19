#!/usr/bin/env bash
# ==============================================================================
# MP3MetaFix - Local Development / Quickstart Runner
# ==============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PORT="${MP3METAFIX_PORT:-8844}"
HOST="${MP3METAFIX_HOST:-127.0.0.1}"

echo "=================================================="
echo "          Starting MP3MetaFix (Dev Mode)          "
echo "=================================================="

# Check Python & Virtualenv
if [ ! -d ".venv" ]; then
    echo "[*] Creating virtual environment..."
    python3 -m venv .venv
    .venv/bin/pip install --upgrade pip
    .venv/bin/pip install -r backend/requirements.txt
fi

echo "[+] Starting server at http://${HOST}:${PORT}"
echo "[+] Press Ctrl+C to stop"
echo "=================================================="

exec .venv/bin/uvicorn backend.main:app --host "$HOST" --port "$PORT" --reload
