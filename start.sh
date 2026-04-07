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
for cmd in python3; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "Error: $cmd not found. Install it first." >&2
        exit 1
    fi
done
# claude vs copilot binary check is deferred until we know the role's runtime.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(pwd)"
BASE_ROLES="$SCRIPT_DIR/roles"
LOCAL_ROLES="$PROJECT_DIR/.octobots/roles"

# ── Load .env.octobots ──────────────────────────────────────────────────────
# Mirrors supervisor.py load_env(): pulls KEY=VALUE pairs into the env so
# users can configure ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN / ANTHROPIC_MODEL
# (or the OCTOBOTS_LLM_PROVIDER shortcut below) without editing this script.
load_octobots_env() {
    local f
    for f in "$PROJECT_DIR/.env.octobots" "$SCRIPT_DIR/.env.octobots"; do
        [[ -f "$f" ]] || continue
        # shellcheck disable=SC2046
        set -a
        # Strip surrounding quotes from values; ignore comments/blank lines.
        while IFS= read -r line || [[ -n "$line" ]]; do
            [[ -z "$line" || "$line" == \#* || "$line" != *=* ]] && continue
            local k="${line%%=*}" v="${line#*=}"
            k="${k// /}"
            v="${v%\"}"; v="${v#\"}"; v="${v%\'}"; v="${v#\'}"
            # Don't override values already exported in the parent shell.
            [[ -z "${!k+x}" ]] && export "$k=$v"
        done < "$f"
        set +a
    done
}
load_octobots_env

# ── LLM provider shortcut ──────────────────────────────────────────────────
# OCTOBOTS_LLM_PROVIDER=ollama   → talk to a local Anthropic-compatible proxy
# OCTOBOTS_LLM_PROVIDER=anthropic → default cloud (no-op)
# Anything else is treated as "user knows what they're doing": we just pass
# whatever ANTHROPIC_* vars they set straight through to claude.
case "${OCTOBOTS_LLM_PROVIDER:-anthropic}" in
    ollama)
        # Defaults assume claude-code-router / LiteLLM running on :8080.
        # See docs/ollama.md for proxy setup.
        export ANTHROPIC_BASE_URL="${ANTHROPIC_BASE_URL:-${OCTOBOTS_OLLAMA_BASE_URL:-http://localhost:8080}}"
        export ANTHROPIC_AUTH_TOKEN="${ANTHROPIC_AUTH_TOKEN:-ollama-local}"
        export ANTHROPIC_MODEL="${ANTHROPIC_MODEL:-${OCTOBOTS_OLLAMA_MODEL:-qwen2.5-coder:32b}}"
        # Claude Code skips its own auth flow when these are present.
        ;;
    anthropic|"") : ;;
    *) : ;;  # custom provider — trust user-supplied ANTHROPIC_* vars
esac

# ── Resolve role directory ────────────────────────────────────────────────
# Resolution order:
#   1. .octobots/roles/<role>/      project overrides
#   2. .claude/agents/<role>/       installed via npx github:arozumenko/<role>-agent init
#   3. octobots/roles/<role>/       bundled fallback
resolve_role() {
    local role="$1"
    if [[ -f "$LOCAL_ROLES/$role/AGENT.md" ]]; then
        echo "$LOCAL_ROLES/$role"
    elif [[ -f "$PROJECT_DIR/.claude/agents/$role/AGENT.md" ]]; then
        echo "$PROJECT_DIR/.claude/agents/$role"
    elif [[ -f "$BASE_ROLES/$role/AGENT.md" ]]; then
        echo "$BASE_ROLES/$role"
    else
        echo ""
    fi
}

# Register the role as a Claude agent by symlinking into .claude/agents/<role>
# so that `claude --agent <role>` can discover the AGENT.md identity file.
register_agent() {
    local role="$1"
    local role_dir="$2"
    local agents_dir="$PROJECT_DIR/.claude/agents"
    mkdir -p "$agents_dir"
    local link="$agents_dir/$role"
    # Remove stale symlink (e.g. if role_dir changed between local/base)
    if [[ -L "$link" && "$(readlink "$link")" != "$role_dir" ]]; then
        rm "$link"
    fi
    if [[ ! -e "$link" ]]; then
        ln -s "$role_dir" "$link"
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
            if [[ -f "$role_dir/AGENT.md" ]]; then
                desc=$(grep -m1 '^description:' "$role_dir/AGENT.md" | sed 's/^description:[[:space:]]*//')
                # Strip YAML block scalar indicator if present (e.g. ">")
                [[ "$desc" == ">" ]] && desc=$(awk '/^description:/{found=1;next} found && /^[[:space:]]/{gsub(/^[[:space:]]+/,""); printf $0" "; next} found{exit}' "$role_dir/AGENT.md")
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

# ── Detect runtime (claude | copilot) from AGENT.md frontmatter ──────────
# Default = claude. To opt a role into Copilot CLI, add `runtime: copilot`
# to its AGENT.md frontmatter. Mixed teams are fine — every role decides
# independently, and the supervisor uses the same dispatch logic.
RUNTIME=$(awk '
    /^---[[:space:]]*$/ { fm = !fm; next }
    fm && /^runtime:/ { sub(/^runtime:[[:space:]]*/, ""); print; exit }
' "$ROLE_DIR/AGENT.md")
RUNTIME="${RUNTIME:-claude}"

# ── Local-model opt-in (Ollama) ──────────────────────────────────────────
# Pure config — set in .env.octobots, no AGENT.md edits needed:
#   OCTOBOTS_OLLAMA_ROLES="personal-assistant ba"   # who runs locally
#   OCTOBOTS_OLLAMA_MODEL=gemma4:26b                # default model
#   OCTOBOTS_OLLAMA_MODEL_PERSONAL_ASSISTANT=...    # optional per-role override
# (per-role var: uppercase the role name and replace dashes with underscores)
OLLAMA_MODEL=""
if [[ -n "${OCTOBOTS_OLLAMA_ROLES:-}" ]]; then
    for r in $OCTOBOTS_OLLAMA_ROLES; do
        if [[ "$r" == "$ROLE" ]]; then
            _role_var="OCTOBOTS_OLLAMA_MODEL_$(echo "$ROLE" | tr 'a-z-' 'A-Z_')"
            OLLAMA_MODEL="${!_role_var:-${OCTOBOTS_OLLAMA_MODEL:-}}"
            break
        fi
    done
fi

# ── Build command per runtime ────────────────────────────────────────────
case "$RUNTIME" in
    claude)
        register_agent "$ROLE" "$ROLE_DIR"
        CMD=(
            env
            "OCTOBOTS_ID=$ROLE"
            "OCTOBOTS_DB=$OCTOBOTS_DB"
            "CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD=1"
        )
        if [[ -n "$OLLAMA_MODEL" ]]; then
            # Local model via `ollama launch claude` — Ollama sets the
            # ANTHROPIC_* env vars and exec's real Claude Code under the hood.
            command -v ollama &>/dev/null || { echo "Error: ollama binary not found (role '$ROLE' is in OCTOBOTS_OLLAMA_ROLES with model $OLLAMA_MODEL)." >&2; exit 1; }
            CMD+=(ollama launch claude --model "$OLLAMA_MODEL" --yes --
                  --agent "$ROLE" --dangerously-skip-permissions)
            BANNER="Claude Code via Ollama ($OLLAMA_MODEL)"
        else
            command -v claude &>/dev/null || { echo "Error: claude binary not found." >&2; exit 1; }
            # Forward any global ANTHROPIC_* / OCTOBOTS_LLM_PROVIDER config
            # the user set in .env.octobots. Per-role ollama_model takes
            # precedence over these globals (handled by the if-branch above).
            for v in ANTHROPIC_BASE_URL ANTHROPIC_AUTH_TOKEN ANTHROPIC_MODEL ANTHROPIC_SMALL_FAST_MODEL OCTOBOTS_LLM_PROVIDER; do
                [[ -n "${!v:-}" ]] && CMD+=("$v=${!v}")
            done
            CMD+=(claude --agent "$ROLE" --dangerously-skip-permissions)
            BANNER="Claude Code"
        fi
        ;;
    copilot)
        command -v copilot &>/dev/null || { echo "Error: copilot binary not found. Install: curl -fsSL https://gh.io/copilot-install | bash" >&2; exit 1; }
        # Materialize the role into Copilot's agents dir on every launch so
        # edits to AGENT.md propagate without a manual sync step.
        python3 "$SCRIPT_DIR/scripts/sync-copilot-agents.py" "$ROLE_DIR" >&2
        # Reuse the GitHub token octobots already provisions for gh CLI.
        : "${GH_TOKEN:=${GITHUB_TOKEN:-}}"
        if [[ -z "${GH_TOKEN:-}" ]] && command -v gh &>/dev/null; then
            GH_TOKEN="$(gh auth token 2>/dev/null || true)"
        fi
        CMD=(
            env
            "OCTOBOTS_ID=$ROLE"
            "OCTOBOTS_DB=$OCTOBOTS_DB"
        )
        [[ -n "${GH_TOKEN:-}" ]] && CMD+=("GH_TOKEN=$GH_TOKEN")
        CMD+=(copilot --agent "$ROLE" --allow-all)
        BANNER="GitHub Copilot CLI"
        ;;
    *)
        echo "Error: unknown runtime '$RUNTIME' in $ROLE_DIR/AGENT.md (expected: claude | copilot)" >&2
        exit 1
        ;;
esac

if [[ "${2:-}" == "--print" ]]; then
    # Redact secrets so --print is safe to paste into bug reports / share over
    # the shoulder. Match KEY=VALUE pairs whose KEY looks sensitive.
    for item in "${CMD[@]}"; do
        if [[ "$item" == *=* ]]; then
            k="${item%%=*}"
            case "$k" in
                *TOKEN*|*SECRET*|*KEY*|*PASSWORD*|GH_TOKEN|GITHUB_TOKEN|ANTHROPIC_AUTH_TOKEN)
                    printf '%s=<redacted>\n' "$k" ;;
                *) printf '%s\n' "$item" ;;
            esac
        else
            printf '%s\n' "$item"
        fi
    done | tr '\n' ' '
    echo
    exit 0
fi

# ── Launch ───────────────────────────────────────────────────────────────
echo "Starting $BANNER as: $ROLE  [runtime: $RUNTIME]"
echo "Source: $ROLE_DIR"
echo "Taskbox: $OCTOBOTS_DB"
echo "---"
exec "${CMD[@]}"
