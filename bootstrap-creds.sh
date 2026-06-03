#!/usr/bin/env bash
#
# Redeem a fresh launch token into ~/.datly-mcp/credentials.json so the
# datly-mcp server can boot without racing the 60s launch-token TTL.
#
# Usage:
#   1. Mint a token at  /account/mcp-tokens  (in the Datly web app)
#   2. Run this IMMEDIATELY, pasting the raw launch_token:
#        ./bootstrap-creds.sh <launch_token>
#   3. Restart Claude Code — the MCP finds the cached creds and boots.
#
# Override the API root with DATLY_API_URL (default http://localhost:8005/api).
set -euo pipefail

TOKEN="${1:-}"
if [ -z "$TOKEN" ]; then
    echo "Usage: $0 <launch_token>   (mint one at /account/mcp-tokens first)" >&2
    exit 2
fi

API="${DATLY_API_URL:-http://localhost:8005/api}"

resp="$(curl -s -w $'\n%{http_code}' -X POST "$API/mcp/exchange" \
    -H 'Content-Type: application/json' \
    -d "{\"launch_token\":\"$TOKEN\"}")"
code="$(printf '%s' "$resp" | tail -n1)"
body="$(printf '%s' "$resp" | sed '$d')"

if [ "$code" != "200" ]; then
    echo "Redeem failed (HTTP $code): $body" >&2
    echo "The launch token is single-use and lasts ~60s — mint a fresh one and retry fast." >&2
    exit 1
fi

mkdir -p ~/.datly-mcp
printf '%s' "$body" | python3 -c '
import json, sys, os, stat
d = json.load(sys.stdin)
path = os.path.expanduser("~/.datly-mcp/credentials.json")
with open(path, "w") as f:
    json.dump({"access_token": d["access"], "refresh_token": d["refresh"]}, f)
os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
print("OK cached ->", path)
'
