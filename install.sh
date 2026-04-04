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

# ── Install published skills via npx skills add ───────────────────────────────

echo ""
echo "Installing skills..."
if command -v npx &>/dev/null; then
    PUBLISHED_SKILLS=(
        "arozumenko/browser-verify"
        "arozumenko/skill-tdd"
        "arozumenko/skill-code-review"
        "arozumenko/skill-git-workflow"
        "arozumenko/skill-issue-tracking"
        "arozumenko/skill-playwright-testing"
    )
    for skill_repo in "${PUBLISHED_SKILLS[@]}"; do
        repo_name="${skill_repo##*/}"
        skill_name="${repo_name#skill-}"
        if [[ -d ".claude/skills/$skill_name" ]]; then
            echo "  ✓ $skill_name (already installed)"
        else
            if npx skills add "$skill_repo" --yes 2>/dev/null; then
                echo "  ✓ $skill_name"
            else
                echo "  ⚠  $skill_name — install failed, will use bundled fallback"
            fi
        fi
    done
else
    echo "  ⚠  npx not found — skipping skill install (Node.js required)"
    echo "     Install manually: npx skills add arozumenko/skill-tdd  (etc.)"
fi

# ── Select and install agents ─────────────────────────────────────────────────

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

    while IFS= read -r agent_repo; do
        [[ -z "$agent_repo" ]] && continue
        repo_name="${agent_repo##*/}"
        if npx "github:$agent_repo" init --all 2>/dev/null; then
            echo "  ✓ $repo_name"
        else
            echo "  ⚠  $repo_name — install failed"
        fi
    done <<< "$SELECTED_REPOS"
else
    echo "  ⚠  npx not found — skipping agent install (Node.js required)"
    echo "     Install manually: npx github:arozumenko/scout-agent init  (etc.)"
fi

# ── Process setup.yaml for bundled skills (MCP + other deps) ─────────────────

echo ""
echo "Configuring skill dependencies..."
DEST="$DEST" python3 "$DEST/scripts/apply-skill-deps.py"

# ── Initialize runtime ────────────────────────────────────────────────────────

echo ""
bash "$DEST/scripts/init-project.sh"

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
