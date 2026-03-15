#!/usr/bin/env bash
# Launch a Claude Code instance with a specific role.
#
# Resolution order:
#   1. .octobots/roles/<role>/   (project overrides)
#   2. octobots/roles/<role>/    (base framework)
#
# Usage:
#   octobots/start.sh <role>           # e.g. python-dev, js-dev
#   octobots/start.sh <role> --print   # print the command without running
#   octobots/start.sh --list           # list available roles

set -euo pipefail

# ── Preflight ───────────────────────────────────────────────────────────────
for cmd in claude python3; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "Error: $cmd not found. Install it first." >&2
        exit 1
    fi
done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(pwd)"
BASE_ROLES="$SCRIPT_DIR/roles"
LOCAL_ROLES="$PROJECT_DIR/.octobots/roles"

# ── Resolve role directory (.octobots/ overrides octobots/) ───────────────
resolve_role() {
    local role="$1"
    if [[ -f "$LOCAL_ROLES/$role/CLAUDE.md" ]]; then
        echo "$LOCAL_ROLES/$role"
    elif [[ -f "$BASE_ROLES/$role/CLAUDE.md" ]]; then
        echo "$BASE_ROLES/$role"
    else
        echo ""
    fi
}

# ── List roles (merged from both sources) ─────────────────────────────────
if [[ "${1:-}" == "--list" ]]; then
    echo "Available roles:"
    declare -A seen
    for roles_dir in "$LOCAL_ROLES" "$BASE_ROLES"; do
        [[ -d "$roles_dir" ]] || continue
        for role_dir in "$roles_dir"/*/; do
            [[ -d "$role_dir" ]] || continue
            role="$(basename "$role_dir")"
            [[ -n "${seen[$role]:-}" ]] && continue
            seen[$role]=1
            if [[ -f "$role_dir/CLAUDE.md" ]]; then
                desc=$(grep -m1 '^# ' "$role_dir/CLAUDE.md" | sed 's/^# //')
                source=""
                [[ "$roles_dir" == "$LOCAL_ROLES" ]] && source=" (project)"
                echo "  $role  —  $desc$source"
            fi
        done
    done
    exit 0
fi

# ── Validate role ───────────────────────────────────────────────────────────
ROLE="${1:?Usage: octobots/start.sh <role>}"
ROLE_DIR=$(resolve_role "$ROLE")

if [[ -z "$ROLE_DIR" ]]; then
    echo "Error: role '$ROLE' not found." >&2
    echo "Checked: $LOCAL_ROLES/$ROLE/ and $BASE_ROLES/$ROLE/" >&2
    echo "Run 'octobots/start.sh --list' to see available roles." >&2
    exit 1
fi

# ── Ensure .octobots runtime dirs exist ──────────────────────────────────
mkdir -p "$PROJECT_DIR/.octobots/memory"

# ── Initialize taskbox DB ────────────────────────────────────────────────
export OCTOBOTS_DB="$PROJECT_DIR/.octobots/relay.db"
python3 "$SCRIPT_DIR/skills/taskbox/scripts/relay.py" init > /dev/null 2>&1 || true

# ── Build command ────────────────────────────────────────────────────────
CMD=(
    env
    "OCTOBOTS_ID=$ROLE"
    "OCTOBOTS_DB=$OCTOBOTS_DB"
    "CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD=1"
    claude
    --add-dir "$ROLE_DIR"
    --dangerously-skip-permissions
)

if [[ "${2:-}" == "--print" ]]; then
    echo "${CMD[@]}"
    exit 0
fi

# ── Launch ───────────────────────────────────────────────────────────────
echo "Starting Claude Code as: $ROLE"
echo "Source: $ROLE_DIR"
echo "Taskbox: $OCTOBOTS_DB"
echo "---"
exec "${CMD[@]}"
