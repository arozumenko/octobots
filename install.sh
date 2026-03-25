#!/usr/bin/env bash
# Install octobots into the current project directory.
#
# Usage (run from your project root):
#   curl -fsSL https://raw.githubusercontent.com/arozumenko/octobots/main/install.sh | bash
#
# What this does:
#   1. Clones octobots/ into your project (or updates if already present)
#   2. Installs Python dependencies
#   3. Initializes .octobots/ runtime directory and seeds .claude/

set -euo pipefail

REPO="https://github.com/arozumenko/octobots.git"
DIR="octobots"

echo "Installing octobots in $(pwd)"
echo ""

# ── Clone or update ──────────────────────────────────────────────────────────

if [[ -d "$DIR/.git" ]]; then
    echo "Updating existing octobots..."
    git -C "$DIR" pull --quiet --ff-only
    echo "  Updated to $(git -C "$DIR" rev-parse --short HEAD)"
else
    echo "Cloning octobots..."
    git clone --quiet "$REPO" "$DIR"
    echo "  Cloned $(git -C "$DIR" rev-parse --short HEAD)"
fi

# ── Python dependencies ───────────────────────────────────────────────────────

if command -v pip3 &>/dev/null; then
    echo ""
    echo "Installing Python dependencies..."
    pip3 install -q -r "$DIR/scripts/requirements.txt"
    echo "  Done"
elif command -v pip &>/dev/null; then
    echo ""
    echo "Installing Python dependencies..."
    pip install -q -r "$DIR/scripts/requirements.txt"
    echo "  Done"
else
    echo ""
    echo "  ⚠  pip not found — install manually: pip install -r octobots/scripts/requirements.txt"
fi

# ── Initialize runtime ────────────────────────────────────────────────────────

echo ""
bash "$DIR/scripts/init-project.sh"

# ── .gitignore ────────────────────────────────────────────────────────────────

GITIGNORE=".gitignore"
ENTRIES=(
    ".octobots/"
    ".mcp.json"
    ".cursor/mcp.json"
    ".windsurf/mcp.json"
    ".vscode/mcp.json"
)

if [[ -f "$GITIGNORE" || ! -f "$GITIGNORE" ]]; then
    for entry in "${ENTRIES[@]}"; do
        if ! grep -qF "$entry" "$GITIGNORE" 2>/dev/null; then
            echo "$entry" >> "$GITIGNORE"
        fi
    done
    echo "  .gitignore updated"
fi

# ── Done ─────────────────────────────────────────────────────────────────────

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  octobots installed successfully"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "Next steps:"
echo ""
echo "  1. Configure Telegram (optional but recommended):"
echo "     echo 'OCTOBOTS_TG_TOKEN=your-bot-token' >> .env.octobots"
echo "     echo 'OCTOBOTS_TG_OWNER=your-telegram-user-id' >> .env.octobots"
echo ""
echo "  2. Explore the project:"
echo "     octobots/start.sh scout"
echo ""
echo "  3. Start the team:"
echo "     python3 octobots/scripts/supervisor.py"
echo ""
echo "  4. Watch the dashboard:"
echo "     tmux attach -t octobots"
echo ""
