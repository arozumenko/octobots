#!/usr/bin/env bash
# Send a notification to the user via Telegram.
#
# Usage:
#   octobots/scripts/notify-user.sh "message text"
#   octobots/scripts/notify-user.sh "message" --from "python-dev"
#
# If the message exceeds 4000 characters, it is automatically sent as a
# .md document attachment instead of a text message (Telegram's limit is
# 4096 chars). A short preview is included as the document caption.
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
# Search order: project root (via git), cwd, octobots repo root
if [[ -z "${OCTOBOTS_TG_TOKEN:-}" ]]; then
    PROJECT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || echo "")"
    for env_file in \
        "${PROJECT_ROOT:+$PROJECT_ROOT/.env.octobots}" \
        ".env.octobots" \
        "$SCRIPT_DIR/../.env.octobots" \
        "$SCRIPT_DIR/../../.env.octobots"; do
        [[ -z "$env_file" ]] && continue
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

# If the message is too long for a text message, send as a document
MSG_LEN=${#MESSAGE}
if [[ "$MSG_LEN" -gt 4000 ]]; then
    # Extract a short preview for the caption (first non-empty line, trimmed)
    PREVIEW=$(echo "$MESSAGE" | head -5 | tr '\n' ' ' | cut -c1-150)
    CAPTION="[${FROM_ROLE}] ${PREVIEW}..."

    # Write to temp file and send as document
    TEMP_FILE=$(mktemp "/tmp/octobots-notify-XXXXXX.md")
    echo "$MESSAGE" > "$TEMP_FILE"

    RESPONSE=$(curl -s -X POST "https://api.telegram.org/bot${TOKEN}/sendDocument" \
        -F "chat_id=${CHAT_ID}" \
        -F "document=@${TEMP_FILE};filename=${FROM_ROLE}-$(date +%H%M%S).md" \
        -F "caption=${CAPTION}" \
        2>/dev/null) || {
        rm -f "$TEMP_FILE"
        echo '{"error": "curl failed"}'
        exit 1
    }
    rm -f "$TEMP_FILE"
else
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
fi

# Return result
OK=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('ok', False))" 2>/dev/null)
if [[ "$OK" == "True" ]]; then
    echo '{"status": "sent"}'
else
    echo "{\"error\": \"telegram API\", \"response\": \"$RESPONSE\"}"
    exit 1
fi
