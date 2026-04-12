#!/bin/bash
# evolve.sh — Claude Code as the evolution engine for live.opensculpt.ai
# Runs on your laptop, reads demands from live OS, fixes code locally.
#
# Usage:
#   ./scripts/evolve.sh                  # one-shot: fix top demand
#   ./scripts/evolve.sh --loop           # continuous: check every 30 min
#   ./scripts/evolve.sh --loop --interval 600  # custom interval (seconds)
#
# Prerequisites:
#   - claude CLI installed and authenticated
#   - SCULPT_LIVE_API_KEY env var set (dashboard API key for live.opensculpt.ai)

set -euo pipefail

LIVE_URL="${SCULPT_LIVE_URL:-https://live.opensculpt.ai}"
API_KEY="${SCULPT_LIVE_API_KEY:-}"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
INTERVAL=1800  # 30 minutes default
LOOP=false

# Parse args
while [[ $# -gt 0 ]]; do
  case $1 in
    --loop) LOOP=true; shift ;;
    --interval) INTERVAL="$2"; shift 2 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

if [[ -z "$API_KEY" ]]; then
  echo "Set SCULPT_LIVE_API_KEY env var to the dashboard API key"
  exit 1
fi

evolve_once() {
  echo "[$(date)] Checking demands from $LIVE_URL ..."

  # Fetch demands
  DEMANDS=$(curl -sf "$LIVE_URL/api/evolution/demands" \
    -H "X-API-Key: $API_KEY" 2>/dev/null || echo '{"total_signals":0}')

  ACTIVE=$(echo "$DEMANDS" | grep -o '"active":[0-9]*' | grep -o '[0-9]*' || echo "0")

  if [[ "$ACTIVE" == "0" ]]; then
    echo "[$(date)] No active demands. OS is healthy."
    return 0
  fi

  echo "[$(date)] $ACTIVE active demands found. Spawning Claude Code..."

  # Also fetch current status for context
  STATUS=$(curl -sf "$LIVE_URL/api/status" \
    -H "X-API-Key: $API_KEY" 2>/dev/null || echo '{}')

  # Spawn Claude Code to fix the top demand
  cd "$REPO_DIR"
  claude -p "You are the evolution engine for a live OpenSculpt instance at $LIVE_URL.

The OS detected these demands (problems it cannot solve with Haiku):

$DEMANDS

OS status: $STATUS

Instructions:
1. Read the top ACTIVE demand carefully
2. Understand what failed and why
3. Read the relevant source files in agos/
4. Write a real fix (code change, not a skill doc or prompt rule)
5. Run tests: python -m pytest tests/ --ignore=tests/test_frontend_playwright.py -q
6. If tests pass, commit with message: 'evolution: fix <demand description>'

Do NOT:
- Generate placeholder code
- Write skill docs or prompt rules
- Make changes unrelated to the demand
- Skip testing

If the demand is an infrastructure limitation (no Docker daemon, missing binary) that can't be fixed in code, skip it and say why."

  if [[ $? -eq 0 ]]; then
    echo "[$(date)] Claude Code session complete. Check git log for changes."
  else
    echo "[$(date)] Claude Code session failed."
  fi
}

if $LOOP; then
  echo "Evolution loop started (interval: ${INTERVAL}s). Ctrl+C to stop."
  while true; do
    evolve_once
    echo "[$(date)] Next check in ${INTERVAL}s..."
    sleep "$INTERVAL"
  done
else
  evolve_once
fi
