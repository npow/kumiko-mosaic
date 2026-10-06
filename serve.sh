#!/usr/bin/env bash
# Start the kumiko mosaic web app, reachable on the tailnet (binds all interfaces).
cd "$(dirname "$0")"
. .venv/bin/activate
exec uvicorn kumiko_mosaic.web:app --host 0.0.0.0 --port "${PORT:-8000}"
