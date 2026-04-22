#!/usr/bin/env bash
# Install (or update) octobots into the current project directory.
#
# Usage (run from your project root):
#   curl -fsSL https://raw.githubusercontent.com/arozumenko/octobots/main/install.sh | bash
#
# What this does:
#   - Downloads a tarball from GitHub (no git clone, no nested repo)
#   - Extracts to /tmp, copies framework files to ./octobots/
#   - Installs Python dependencies
#   - Initializes .octobots/ runtime directory and seeds .claude/
#   - Guides you through filling in .env.octobots
#
# Safe to re-run — updates framework without touching .octobots/ (your runtime).

set -euo pipefail

REPO="arozumenko/octobots"
BRANCH="main"
TARBALL_URL="https://github.com/$REPO/archive/refs/heads/$BRANCH.tar.gz"
TMP_DIR=$(mktemp -d)
DEST="octobots"
ENV_FILE=".env.octobots"

cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT

# ── Helpers ───────────────────────────────────────────────────────────────────

# Read a value from .env.octobots if it exists
env_get() {
    local key="$1"
    grep -m1 "^${key}=" "$ENV_FILE" 2>/dev/null | cut -d= -f2- || true
}

# Write or update a key in .env.octobots
env_set() {
    local key="$1" val="$2"
    if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
        # Update existing line (portable sed)
        local tmp; tmp=$(mktemp)
        sed "s|^${key}=.*|${key}=${val}|" "$ENV_FILE" > "$tmp" && mv "$tmp" "$ENV_FILE"
    else
        echo "${key}=${val}" >> "$ENV_FILE"
    fi
}

# Prompt with an existing value shown; skip if already set and user just presses Enter
ask() {
    local key="$1"
    local prompt="$2"
    local default="${3:-}"
    local current; current=$(env_get "$key")

    local display_default="${current:-$default}"
    if [[ -n "$display_default" ]]; then
        printf "  %s [%s]: " "$prompt" "$display_default"
    else
        printf "  %s: " "$prompt"
    fi

    local input
    read -r input </dev/tty || input=""
    local value="${input:-$display_default}"

    if [[ -n "$value" ]]; then
        env_set "$key" "$value"
        echo "    ✓ $key set"
    else
        echo "    — skipped"
    fi
}

# ── Download & extract ────────────────────────────────────────────────────────

echo "Installing octobots in $(pwd)"
echo ""
echo "Downloading..."
curl -fsSL "$TARBALL_URL" -o "$TMP_DIR/octobots.tar.gz"
tar -xzf "$TMP_DIR/octobots.tar.gz" -C "$TMP_DIR"
SRC="$TMP_DIR/octobots-$BRANCH"

# ── Copy framework files ──────────────────────────────────────────────────────

echo "Copying to ./$DEST/..."
rm -rf "./$DEST"
cp -r "$SRC" "./$DEST"

# Write SHA-256 manifest so update.sh can detect drift on next run.
# Format is shasum -c compatible; lines starting with `#` are metadata.
{
    printf '# octobots install manifest — do not edit\n'
    printf '# source: https://github.com/%s\n' "$REPO"
    printf '# ref: %s\n' "$BRANCH"
    printf '# installed-at: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "./$DEST/.octobots-manifest"
(cd "./$DEST" && find . -type f \
    ! -name '.octobots-manifest' \
    ! -path './.git/*' \
    -print0 \
    | LC_ALL=C sort -z \
    | xargs -0 shasum -a 256) >> "./$DEST/.octobots-manifest"

# ── Python dependencies ───────────────────────────────────────────────────────

echo ""
echo "Installing Python dependencies..."
if command -v pip3 &>/dev/null; then
    pip3 install -q -r "$DEST/scripts/requirements.txt"
elif command -v pip &>/dev/null; then
    pip install -q -r "$DEST/scripts/requirements.txt"
else
    echo "  ⚠  pip not found — run: pip install -r octobots/scripts/requirements.txt"
fi

# ── Select and install agents (BEFORE skills, so skills can be derived) ──────

echo ""
echo "Setting up your team..."
if command -v npx &>/dev/null; then
    # Run interactive agent selector (prompts on /dev/tty, repos printed to stdout)
    SELECTED_REPOS=$(python3 "$DEST/scripts/select-agents.py" </dev/tty) || {
        echo "  ⚠  Agent selection failed — installing scout + pm + python-dev as defaults"
        SELECTED_REPOS="arozumenko/scout-agent
arozumenko/pm-agent
arozumenko/python-dev-agent"
    }

    # Two install paths:
    #   sdlc:<name>      → batched into a single sdlc-skills installer call
    #   owner/repo@ref   → installed individually (third-party agent repos)
    SDLC_AGENT_NAMES=""
    while IFS= read -r agent_entry; do
        [[ -z "$agent_entry" ]] && continue
        if [[ "$agent_entry" == sdlc:* ]]; then
            name="${agent_entry#sdlc:}"
            SDLC_AGENT_NAMES="${SDLC_AGENT_NAMES:+$SDLC_AGENT_NAMES,}$name"
        else
            agent_repo="${agent_entry%@*}"
            agent_ref="${agent_entry##*@}"
            [[ "$agent_ref" == "$agent_entry" ]] && agent_ref="main"
            repo_name="${agent_repo##*/}"
            if npx "github:${agent_repo}#${agent_ref}" init --all 2>/dev/null; then
                echo "  ✓ $repo_name @ $agent_ref"
            else
                echo "  ⚠  $repo_name @ $agent_ref — install failed"
            fi
        fi
    done <<< "$SELECTED_REPOS"

    if [[ -n "$SDLC_AGENT_NAMES" ]]; then
        if npx -y github:arozumenko/sdlc-skills init \
            --agents "$SDLC_AGENT_NAMES" --target claude --yes 2>&1 | sed 's/^/    /'; then
            echo "  ✓ sdlc-skills agents: $SDLC_AGENT_NAMES"
        else
            echo "  ⚠  sdlc-skills agent install failed ($SDLC_AGENT_NAMES)"
        fi
    fi
else
    echo "  ⚠  npx not found — skipping agent install (Node.js required)"
    echo "     Install manually: npx github:arozumenko/scout-agent init  (etc.)"
fi

# ── Install skills declared by selected agents ───────────────────────────────
# Source of truth: the union of `skills:` frontmatter from every installed agent.
# Each declared id is looked up in skills.json:
#   monorepo: sdlc-skills  → batched into one sdlc-skills installer call
#   repo: owner/repo       → fetched individually via registry-fetch.sh
#   not listed             → warned and skipped

echo ""
echo "Installing skills required by your team..."
if command -v npx &>/dev/null; then
    REQUIRED_SKILLS=$(python3 "$DEST/scripts/resolve-skills.py" union)

    if [[ -z "$REQUIRED_SKILLS" ]]; then
        echo "  — no skills declared by selected agents"
    else
        SDLC_SKILL_NAMES=""
        while IFS= read -r skill_id; do
            [[ -z "$skill_id" ]] && continue
            if [[ -d ".claude/skills/$skill_id" ]]; then
                echo "  ✓ $skill_id (already present)"
                continue
            fi
            # Resolve skill_id against skills.json → "monorepo" | "<owner/repo>[@ref]" | "unknown"
            resolution=$(python3 - "$DEST/skills.json" "$skill_id" <<'PY'
import json, sys
registry, skill_id = sys.argv[1], sys.argv[2]
try:
    data = json.load(open(registry))
except Exception:
    print("unknown"); sys.exit(0)
for entry in data.get("skills", []):
    if entry.get("id") != skill_id:
        continue
    if entry.get("monorepo"):
        print("monorepo"); sys.exit(0)
    repo = entry.get("repo")
    if repo:
        ref = entry.get("ref", "main")
        print(f"{repo}@{ref}"); sys.exit(0)
    break
print("unknown")
PY
)
            case "$resolution" in
                monorepo)
                    SDLC_SKILL_NAMES="${SDLC_SKILL_NAMES:+$SDLC_SKILL_NAMES,}$skill_id"
                    ;;
                unknown)
                    echo "  ⚠  $skill_id — not in skills.json, skipping"
                    ;;
                *)
                    repo="${resolution%@*}"
                    ref="${resolution##*@}"
                    if bash "$DEST/scripts/registry-fetch.sh" skill "$repo" "$ref" >/dev/null; then
                        echo "  ✓ $skill_id (from $repo@$ref)"
                    else
                        echo "  ⚠  $skill_id — fetch failed from $repo@$ref"
                    fi
                    ;;
            esac
        done <<< "$REQUIRED_SKILLS"

        if [[ -n "$SDLC_SKILL_NAMES" ]]; then
            if npx -y github:arozumenko/sdlc-skills init \
                --skills "$SDLC_SKILL_NAMES" --target claude --yes 2>&1 | sed 's/^/    /'; then
                echo "  ✓ sdlc-skills: $SDLC_SKILL_NAMES"
            else
                echo "  ⚠  sdlc-skills skill install failed ($SDLC_SKILL_NAMES)"
            fi
        fi
    fi
else
    echo "  ⚠  npx not found — skipping skill install (Node.js required)"
fi

# ── Process setup.yaml for bundled skills (MCP + other deps) ─────────────────

echo ""
echo "Configuring skill dependencies..."
DEST="$DEST" python3 "$DEST/scripts/apply-skill-deps.py"

# ── Initialize runtime ────────────────────────────────────────────────────────

echo ""
bash "$DEST/scripts/init-project.sh"

# ── Verify every agent's declared skills resolved ────────────────────────────

echo ""
echo "Verifying skill resolution..."
python3 "$DEST/scripts/resolve-skills.py" verify || \
    echo "  (some skills missing — install manually with 'npx skills add' or fix the agent's skills: list)"

# ── .gitignore ────────────────────────────────────────────────────────────────

for entry in "octobots/" ".octobots/" ".env.octobots" ".mcp.json" ".cursor/mcp.json" ".windsurf/mcp.json" ".vscode/mcp.json"; do
    grep -qF "$entry" ".gitignore" 2>/dev/null || echo "$entry" >> ".gitignore"
done

# ── Guided .env.octobots setup ────────────────────────────────────────────────

touch "$ENV_FILE"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Configuration — $ENV_FILE"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Press Enter to keep existing/default values."
echo "  Leave blank to skip optional fields."
echo ""

# ── Telegram ─────────────────────────────────────────────────────────────────
echo "── Telegram (user interface) ──────────────────"
echo "  Create a bot at https://t.me/BotFather → copy the token."
echo "  Get your user ID from https://t.me/userinfobot"
echo ""
ask "OCTOBOTS_TG_TOKEN"      "Bot token"
ask "OCTOBOTS_TG_OWNER"      "Your Telegram user ID (owner)"
echo ""

# ── GitHub ───────────────────────────────────────────────────────────────────
echo "── GitHub integration (optional) ─────────────"
echo "  Issues assigned to your bot user are auto-routed to the PM."
echo "  Leave blank to skip — you can add these later."
echo ""
ask "OCTOBOTS_ISSUE_REPO"    "Issue repo (e.g. owner/repo)"
echo ""
echo "  GitHub App (optional — for private repos and higher rate limits):"
ask "OCTOBOTS_GH_APP_ID"         "App ID"
ask "OCTOBOTS_GH_APP_PRIVATE_KEY_PATH" "Private key path (e.g. ./gh-app.pem)"
ask "OCTOBOTS_GH_INSTALLATION_ID"     "Installation ID"
ask "OCTOBOTS_GH_ORG"               "GitHub org (for project board creation)"
echo ""

# ── Workers ───────────────────────────────────────────────────────────────────
echo "── Workers (optional) ────────────────────────"
echo "  By default all roles in octobots/roles/ are started."
echo "  'scout' is excluded from the supervisor by default."
echo ""
ask "OCTOBOTS_EXCLUDED_ROLES" "Roles to exclude from supervisor" "scout"
ask "OCTOBOTS_WORKERS"        "Explicit worker list (space-separated, leave blank for auto)"
echo ""

# ── Advanced ─────────────────────────────────────────────────────────────────
echo "── Advanced (optional) ───────────────────────"
ask "OCTOBOTS_TMUX"   "tmux session name"   "octobots"
ask "OCTOBOTS_PM_PANE" "PM pane name"       "project-manager"
echo ""

# ── Done ─────────────────────────────────────────────────────────────────────

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  octobots installed"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "Start the team:"
echo ""
echo "  octobots/start.sh scout               # explore and configure"
echo "  python3 octobots/scripts/supervisor.py  # start all workers"
echo ""
echo "Re-run this script at any time to update octobots."
echo ""
