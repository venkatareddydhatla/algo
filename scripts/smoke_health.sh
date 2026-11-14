#!/usr/bin/env bash
set -euo pipefail
BASE="${1:-http://127.0.0.1:8000}"
curl -fsS "$BASE/health/live" | tee /dev/stderr
echo
curl -fsS "$BASE/health/ready" | tee /dev/stderr
echo
curl -fsS "$BASE/api/v1/signals" | tee /dev/stderr
echo
echo "smoke ok"
