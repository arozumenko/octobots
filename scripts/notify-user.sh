#!/usr/bin/env bash
# Send a notification to the user via Telegram.
#
# Usage:
#   octobots/scripts/notify-user.sh "message text"
#   octobots/scripts/notify-user.sh "message" --from "python-dev"
#
# Reads OCTOBOTS_TG_TOKEN and OCTOBOTS_TG_OWNER from .env.octobots
# or environment. Does nothing if Telegram is not configured.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MESSAGE="${1:-}"
FROM_ROLE="${OCTOBOTS_ID:-unknown}"

# Parse --from flag
shift || true
while [[ $# -gt 0 ]]; do
    case "$1" in
        --from) FROM_ROLE="$2"; shift 2 ;;
        *) shift ;;
    esac
done

if [[ -z "$MESSAGE" ]]; then
    echo '{"error": "no message provided"}'
    exit 1
fi

# Load .env.octobots if not already in env
if [[ -z "${OCTOBOTS_TG_TOKEN:-}" ]]; then
    for env_file in "$SCRIPT_DIR/../.env.octobots" ".env.octobots"; do
        if [[ -f "$env_file" ]]; then
            while IFS='=' read -r key value; do
                key=$(echo "$key" | tr -d ' ')
                value=$(echo "$value" | tr -d ' ' | tr -d '"' | tr -d "'")
                [[ -z "$key" || "$key" == \#* ]] && continue
                export "$key=$value" 2>/dev/null || true
            done < "$env_file"
            break
        fi
    done
fi

TOKEN="${OCTOBOTS_TG_TOKEN:-}"
CHAT_ID="${OCTOBOTS_TG_OWNER:-}"

if [[ -z "$TOKEN" || -z "$CHAT_ID" ]]; then
    echo '{"status": "skipped", "reason": "Telegram not configured"}'
    exit 0
fi

# Format with HTML role badge
FORMATTED="<b>[$FROM_ROLE]</b> $MESSAGE"

# Send via Telegram Bot API with HTML parse mode
RESPONSE=$(curl -s -X POST "https://api.telegram.org/bot${TOKEN}/sendMessage" \
    -H "Content-Type: application/json" \
    -d "{\"chat_id\": \"${CHAT_ID}\", \"parse_mode\": \"HTML\", \"text\": $(echo "$FORMATTED" | python3 -c 'import sys,json; print(json.dumps(sys.stdin.read()))')}" \
    2>/dev/null) || {
    echo '{"error": "curl failed"}'
    exit 1
}

# Return result
OK=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('ok', False))" 2>/dev/null)
if [[ "$OK" == "True" ]]; then
    echo '{"status": "sent"}'
else
    echo "{\"error\": \"telegram API\", \"response\": \"$RESPONSE\"}"
    exit 1
fi
