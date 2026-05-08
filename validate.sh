#!/usr/bin/env bash
# Energy Dashboard — first-run validation script
# Runs after `docker compose up -d` to verify each tier is working.
# Usage: ./validate.sh [host:port]   (defaults to localhost:8400)

set -u
HOST="${1:-localhost:8400}"
BASE="http://${HOST}"

# Colors
G='\033[0;32m'; R='\033[0;31m'; Y='\033[1;33m'; D='\033[0;90m'; N='\033[0m'

echo ""
echo -e "${D}Energy Dashboard validation — ${BASE}${N}"
echo ""

# Try the structured health endpoint first (fastest, most informative)
HEALTH=$(curl -sf -m 5 "${BASE}/api/health" 2>/dev/null || echo "")
if [ -n "$HEALTH" ]; then
  STATUS=$(echo "$HEALTH" | python3 -c "import json,sys;print(json.load(sys.stdin).get('status','?'))" 2>/dev/null)
  if [ "$STATUS" = "ok" ]; then
    echo -e "${G}✓${N} All tiers healthy"
  else
    echo -e "${Y}⚠${N} Status: $STATUS"
  fi
  echo "$HEALTH" | python3 -c "
import json, sys
d = json.load(sys.stdin)
for tier, state in d.get('tiers', {}).items():
    if state.startswith('ok'):
        marker = '✓'; color = '\033[0;32m'
    elif state.startswith('not_configured'):
        marker = '○'; color = '\033[0;90m'
    else:
        marker = '✗'; color = '\033[0;31m'
    print(f'  {color}{marker}\033[0m {tier:20} {state}')
"
  echo ""
  exit 0
fi

# Fallback if /api/health doesn't exist (older deploy)
echo -e "${R}✗${N} Dashboard unreachable at ${BASE}"
echo ""
echo "Troubleshooting:"
echo "  1. Is the container running?     docker compose ps"
echo "  2. Are the logs showing errors?  docker compose logs --tail=50"
echo "  3. Is port 8400 exposed?         curl ${BASE}/  (should return HTML)"
echo ""
exit 1
