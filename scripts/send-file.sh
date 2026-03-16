#!/usr/bin/env bash
# Send a file to the user via Telegram.
#
# Usage:
#   octobots/scripts/send-file.sh /path/to/file.md
#   octobots/scripts/send-file.sh /path/to/file.md "optional caption"
#   octobots/scripts/send-file.sh /path/to/file.md "caption" --from "ba"
#
# Also accepts piped content (sends as a .md document):
#   echo "content" | octobots/scripts/send-file.sh --stdin "filename.md" "caption"
#
# Reads OCTOBOTS_TG_TOKEN and OCTOBOTS_TG_OWNER from .env.octobots
# or environment. Does nothing if Telegram is not configured.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
FROM_ROLE="${OCTOBOTS_ID:-unknown}"
STDIN_MODE=false
FILE_PATH=""
CAPTION=""
STDIN_FILENAME=""

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --from) FROM_ROLE="$2"; shift 2 ;;
        --stdin) STDIN_MODE=true; STDIN_FILENAME="${2:-document.md}"; shift 2 ;;
        *)
            if [[ -z "$FILE_PATH" ]]; then
                FILE_PATH="$1"
            elif [[ -z "$CAPTION" ]]; then
                CAPTION="$1"
            fi
            shift
            ;;
    esac
done

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

# Handle stdin mode — write piped content to a temp file
TEMP_FILE=""
if [[ "$STDIN_MODE" == "true" ]]; then
    TEMP_FILE=$(mktemp "/tmp/octobots-send-XXXXXX")
    # Preserve the original extension from the filename
    EXT="${STDIN_FILENAME##*.}"
    mv "$TEMP_FILE" "${TEMP_FILE}.${EXT}"
    TEMP_FILE="${TEMP_FILE}.${EXT}"
    cat > "$TEMP_FILE"
    FILE_PATH="$TEMP_FILE"
fi

if [[ -z "$FILE_PATH" || ! -f "$FILE_PATH" ]]; then
    echo '{"error": "file not found or not provided"}'
    [[ -n "$TEMP_FILE" ]] && rm -f "$TEMP_FILE"
    exit 1
fi

# Format caption with role badge
if [[ -n "$CAPTION" ]]; then
    FORMATTED_CAPTION="[${FROM_ROLE}] ${CAPTION}"
else
    FORMATTED_CAPTION="[${FROM_ROLE}] $(basename "$FILE_PATH")"
fi

# Determine filename for Telegram (use stdin filename or actual filename)
if [[ "$STDIN_MODE" == "true" ]]; then
    SEND_FILENAME="$STDIN_FILENAME"
else
    SEND_FILENAME="$(basename "$FILE_PATH")"
fi

# Send via Telegram Bot API sendDocument
RESPONSE=$(curl -s -X POST "https://api.telegram.org/bot${TOKEN}/sendDocument" \
    -F "chat_id=${CHAT_ID}" \
    -F "document=@${FILE_PATH};filename=${SEND_FILENAME}" \
    -F "caption=${FORMATTED_CAPTION}" \
    2>/dev/null) || {
    [[ -n "$TEMP_FILE" ]] && rm -f "$TEMP_FILE"
    echo '{"error": "curl failed"}'
    exit 1
}

# Clean up temp file
[[ -n "$TEMP_FILE" ]] && rm -f "$TEMP_FILE"

# Return result
OK=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('ok', False))" 2>/dev/null)
if [[ "$OK" == "True" ]]; then
    echo '{"status": "sent"}'
else
    echo "{\"error\": \"telegram API\", \"response\": \"$RESPONSE\"}"
    exit 1
fi
