#!/bin/bash
# Claude Code status line: shows USABLE context remaining + a progress bar.
#
# `context_window.used_percentage` from Claude Code includes the auto-compact
# buffer it reserves (empirically ~16.5% as of 2026-04 — see
# github.com/anthropics/claude-code/issues/5601 and claudelog.com).
# We subtract that buffer so the number reflects what's actually usable
# before auto-compact kicks in. Bump BUFFER_PCT if a future Claude Code
# release changes the reservation.
BUFFER_PCT=16

read -r input

raw_remaining_float=$(echo "$input" | jq -r '
  (.context_window.used_percentage // empty) | tonumber | (100 - .)
' 2>/dev/null)

if [ -z "$raw_remaining_float" ]; then
  echo "ctx: --"
  exit 0
fi

# Strip any decimal part (jq may emit a float). No-op on integers.
raw_remaining=${raw_remaining_float%.*}

# Subtract the auto-compact buffer; clamp at 0.
usable=$((raw_remaining - BUFFER_PCT))
[ "$usable" -lt 0 ] && usable=0

echo "$usable" > /tmp/claude-context-remaining

# 10-segment progress bar that fills as context is consumed.
used_of_usable=$((100 - usable))
filled=$((used_of_usable / 10))
[ "$filled" -gt 10 ] && filled=10
[ "$filled" -lt 0 ] && filled=0
empty=$((10 - filled))
bar="$(printf '%*s' "$filled" '' | tr ' ' '█')$(printf '%*s' "$empty" '' | tr ' ' '░')"

GREEN="\033[32m"
YELLOW="\033[33m"
RED="\033[31m"
RESET="\033[0m"

if [ "$usable" -le 15 ]; then
  echo -e "${RED}!! CTX [${bar}] ${usable}% left${RESET} - run /save-handoff then /clear"
elif [ "$usable" -le 30 ]; then
  echo -e "${YELLOW}ctx [${bar}] ${usable}% remaining${RESET}"
else
  echo -e "${GREEN}ctx [${bar}] ${usable}%${RESET}"
fi
