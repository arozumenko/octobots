#!/usr/bin/env bash
# Fetch a role or skill from a GitHub repo into the project.
#
# Usage:
#   registry-fetch.sh agent <owner/repo> [ref]   # → .claude/agents/<name>/
#   registry-fetch.sh skill <owner/repo> [ref]   # → .claude/skills/<name>/
#
# Fetch strategy (in order):
#   1. npx "github:<repo>" init --all  (agents)
#      npx skills add <repo> --yes     (skills)
#   2. git clone --depth 1 → .octobots/registry/<repo-name>/
#      then symlink into .claude/agents/ or .claude/skills/
#
# Exits 0 on success, 1 on failure.
# Prints the installed name to stdout on success (useful for callers).

set -euo pipefail

TYPE="${1:?Usage: registry-fetch.sh <agent|skill> <owner/repo> [ref]}"
REPO="${2:?Missing repo argument}"
REF="${3:-main}"

PROJECT_DIR="$(pwd)"
RUNTIME="$PROJECT_DIR/.octobots"
REGISTRY="$RUNTIME/registry"
repo_name="${REPO##*/}"   # e.g. "pm-agent" from "arozumenko/pm-agent"

# ── Agent fetch ───────────────────────────────────────────────────────────────

fetch_agent() {
    mkdir -p "$PROJECT_DIR/.claude/agents"

    # Strategy 1: npx github:<repo> init --all
    if command -v npx &>/dev/null; then
        # Snapshot before so we can detect what was newly installed
        before=$(ls "$PROJECT_DIR/.claude/agents/" 2>/dev/null | sort || true)
        if npx "github:$REPO" init --all 2>/dev/null; then
            after=$(ls "$PROJECT_DIR/.claude/agents/" 2>/dev/null | sort || true)
            installed=$(comm -13 <(echo "$before") <(echo "$after") | head -1)
            # Fall back to guessing if comm gives nothing (already existed)
            [[ -z "$installed" ]] && installed="${repo_name%-agent}"
            echo "$installed"
            return 0
        fi
    fi

    # Strategy 2: git clone → .octobots/registry/<repo-name>/
    local clone_dir="$REGISTRY/$repo_name"
    mkdir -p "$REGISTRY"

    if [[ -d "$clone_dir/.git" ]]; then
        echo "  Updating $repo_name..." >&2
        git -C "$clone_dir" fetch --quiet --depth 1 origin "$REF" 2>/dev/null \
            && git -C "$clone_dir" checkout --quiet FETCH_HEAD 2>/dev/null || true
    else
        [[ -d "$clone_dir" ]] && rm -rf "$clone_dir"
        echo "  Cloning $REPO@$REF..." >&2
        git clone --quiet --depth 1 --branch "$REF" \
            "https://github.com/$REPO" "$clone_dir" 2>/dev/null \
            || { echo "  ✗ Failed to clone $REPO" >&2; return 1; }
    fi

    # Symlink each agents/<name>/ into .claude/agents/
    local installed=""
    for agent_dir in "$clone_dir/agents"/*/; do
        [[ -f "$agent_dir/AGENT.md" ]] || continue
        local name; name="$(basename "$agent_dir")"
        local link="$PROJECT_DIR/.claude/agents/$name"
        [[ -L "$link" ]] && rm -f "$link"
        [[ -e "$link" ]] || ln -sf "$agent_dir" "$link"
        echo "  → .claude/agents/$name" >&2
        installed="$name"
    done

    if [[ -z "$installed" ]]; then
        echo "  ✗ No agents/<name>/AGENT.md found in $REPO" >&2
        return 1
    fi
    echo "$installed"
}

# ── Skill fetch ───────────────────────────────────────────────────────────────

fetch_skill() {
    mkdir -p "$PROJECT_DIR/.claude/skills"

    # Strategy 1: npx skills add <repo> --yes
    if command -v npx &>/dev/null; then
        before=$(ls "$PROJECT_DIR/.claude/skills/" 2>/dev/null | sort || true)
        if npx skills add "$REPO" --yes 2>/dev/null; then
            after=$(ls "$PROJECT_DIR/.claude/skills/" 2>/dev/null | sort || true)
            installed=$(comm -13 <(echo "$before") <(echo "$after") | head -1)
            [[ -z "$installed" ]] && installed="${repo_name#skill-}"
            echo "$installed"
            return 0
        fi
    fi

    # Strategy 2: git clone → .octobots/registry/<repo-name>/
    local clone_dir="$REGISTRY/$repo_name"
    mkdir -p "$REGISTRY"

    if [[ -d "$clone_dir/.git" ]]; then
        echo "  Updating $repo_name..." >&2
        git -C "$clone_dir" fetch --quiet --depth 1 origin "$REF" 2>/dev/null \
            && git -C "$clone_dir" checkout --quiet FETCH_HEAD 2>/dev/null || true
    else
        [[ -d "$clone_dir" ]] && rm -rf "$clone_dir"
        echo "  Cloning $REPO@$REF..." >&2
        git clone --quiet --depth 1 --branch "$REF" \
            "https://github.com/$REPO" "$clone_dir" 2>/dev/null \
            || { echo "  ✗ Failed to clone $REPO" >&2; return 1; }
    fi

    # Derive skill name from SKILL.md name: field, fallback to repo name
    local skill_name=""
    if [[ -f "$clone_dir/SKILL.md" ]]; then
        skill_name=$(grep -m1 '^name:' "$clone_dir/SKILL.md" \
            | sed 's/^name:[[:space:]]*//' | tr -d '"'"'" | tr -d ' ' || true)
    fi
    [[ -z "$skill_name" ]] && skill_name="${repo_name#skill-}"

    local link="$PROJECT_DIR/.claude/skills/$skill_name"
    [[ -L "$link" ]] && rm -f "$link"
    [[ -e "$link" ]] || ln -sf "$clone_dir" "$link"
    echo "  → .claude/skills/$skill_name" >&2

    echo "$skill_name"
}

# ── Dispatch ──────────────────────────────────────────────────────────────────

case "$TYPE" in
    agent) fetch_agent ;;
    skill) fetch_skill ;;
    *) echo "Error: unknown type '$TYPE'. Use 'agent' or 'skill'." >&2; exit 1 ;;
esac
