#!/usr/bin/env bash
# Install Python packages in editable mode for local dry-run development.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

python3 -m pip install -U pip setuptools wheel
python3 -m pip install -e ./packages/domain \
  -e ./packages/broker-upstox \
  -e ./packages/ta-core \
  -e ./packages/news-rss \
  -e ./packages/sentiment \
  -e ./packages/hybrid-engine \
  -e ./packages/risk \
  -e ./packages/notify \
  -e ./packages/persistence \
  -e ./apps/api \
  -e ./apps/worker \
  pytest pytest-asyncio

echo "Installed. Next:"
echo "  cp .env.example .env && chmod 600 .env"
echo "  docker compose -f deploy/docker-compose.yml up --build"
echo "  # or: cd apps/api && uvicorn app.main:app --host 127.0.0.1 --port 8000"
