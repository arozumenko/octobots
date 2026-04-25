#!/usr/bin/env bash
# Claude Code status line: shows USABLE context remaining + a progress bar.
#
# `context_window.used_percentage` from Claude Code includes the auto-compact
# buffer it reserves (empirically ~16.5% as of 2026-04 — see
# github.com/anthropics/claude-code/issues/5601 and claudelog.com).
# We subtract that buffer so the number reflects what's actually usable
# before auto-compact kicks in. Bump BUFFER_PCT if a future Claude Code
# release changes the reservation.
set -uo pipefail
BUFFER_PCT=16

# jq isn't on macOS by default; degrade gracefully if missing.
if ! command -v jq >/dev/null 2>&1; then
  echo "ctx: --"
  exit 0
fi

# Timeout protects against the script being invoked with no stdin.
read -r -t 1 input || input=""

raw_remaining_float=$(printf '%s' "$input" | jq -r '
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

# 10-segment progress bar that fills as the usable budget is consumed
# (full bar = no usable budget left). Scale relative to the usable max
# (100 - BUFFER_PCT), not the total context — otherwise the bar shows
# ~16% pre-filled even at zero real usage.
max_usable=$((100 - BUFFER_PCT))
[ "$max_usable" -lt 1 ] && max_usable=1   # belt-and-braces: avoid div by zero
used_of_usable=$((max_usable - usable))
bar_fill_pct=$((used_of_usable * 100 / max_usable))
filled=$((bar_fill_pct / 10))
[ "$filled" -gt 10 ] && filled=10
[ "$filled" -lt 0 ] && filled=0
empty=$((10 - filled))
# `printf '%*s' N ''` pads to N spaces, then tr swaps to the bar glyph.
# Note: █ and ░ are multi-byte UTF-8 — don't "simplify" with single chars.
bar="$(printf '%*s' "$filled" '' | tr ' ' '█')$(printf '%*s' "$empty" '' | tr ' ' '░')"

GREEN="\033[32m"
YELLOW="\033[33m"
RED="\033[31m"
RESET="\033[0m"

# 15% remaining ≈ imminent auto-compact; 30% ≈ time to plan a /save-handoff.
if [ "$usable" -le 15 ]; then
  echo -e "${RED}!! CTX [${bar}] ${usable}% left${RESET} - run /save-handoff then /clear"
elif [ "$usable" -le 30 ]; then
  echo -e "${YELLOW}ctx [${bar}] ${usable}% remaining${RESET}"
else
  echo -e "${GREEN}ctx [${bar}] ${usable}%${RESET}"
fi
