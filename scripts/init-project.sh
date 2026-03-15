#!/usr/bin/env bash
# Initialize .octobots/ runtime directory for a project.
#
# Creates the directory structure that roles read/write at runtime.
# Safe to run multiple times — only creates missing files, never overwrites.
#
# Usage:
#   octobots/scripts/init-project.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OCTOBOTS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_DIR="$(pwd)"
RUNTIME="$PROJECT_DIR/.octobots"

echo "Initializing .octobots/ in $PROJECT_DIR"

# ── Create directory structure ──────────────────────────────────────────────
mkdir -p "$RUNTIME/memory"
mkdir -p "$RUNTIME/roles"
mkdir -p "$RUNTIME/skills"
mkdir -p "$RUNTIME/agents"

# ── Create board.md (team whiteboard) ───────────────────────────────────────
if [[ ! -f "$RUNTIME/board.md" ]]; then
    cat > "$RUNTIME/board.md" << 'EOF'
# Team Board

Shared state for all octobots roles. Read before starting work. Update when you learn something the team should know.

## Active Work

| Role | Task | Issue | Status |
|------|------|-------|--------|
| — | — | — | — |

## Decisions

## Blockers

## Shared Findings

## Parking Lot
EOF
    echo "  Created board.md"
fi

# ── Create memory files for base roles ──────────────────────────────────────
for role_dir in "$OCTOBOTS_DIR/roles"/*/; do
    role="$(basename "$role_dir")"
    memory_file="$RUNTIME/memory/${role}.md"
    if [[ ! -f "$memory_file" ]]; then
        cat > "$memory_file" << EOF
# Memory — $role

Persistent learnings from past conversations. Read this before starting work.

## Project Knowledge

## Lessons Learned

## Notes
EOF
        echo "  Created memory/$role.md"
    fi
done

# ── Create profile.md if missing ────────────────────────────────────────────
if [[ ! -f "$RUNTIME/profile.md" ]]; then
    cat > "$RUNTIME/profile.md" << 'EOF'
---
project: unnamed
languages: []
---

# Project Profile

Run `octobots/start.sh scout` to auto-generate this file.
EOF
    echo "  Created profile.md (run scout to populate)"
fi

# ── Initialize taskbox DB ───────────────────────────────────────────────────
export OCTOBOTS_DB="$RUNTIME/relay.db"
python3 "$OCTOBOTS_DIR/skills/taskbox/scripts/relay.py" init > /dev/null 2>&1 || true
echo "  Taskbox DB: $RUNTIME/relay.db"

# ── Setup worker environments (code-writing roles) ─────────────────────────
CODE_WORKERS="python-dev js-dev qa-engineer"

# Discover all git repos in workspace
REPOS=()
while IFS= read -r repo; do
    # Skip octobots itself
    [[ "$repo" == "octobots" ]] && continue
    REPOS+=("$repo")
done < <(find "$PROJECT_DIR" -maxdepth 3 -name ".git" -type d | sed "s|$PROJECT_DIR/||; s|/.git||" | sort)

if [[ ${#REPOS[@]} -gt 0 ]]; then
    echo ""
    echo "Found ${#REPOS[@]} repos: ${REPOS[*]}"
    echo "Setting up worker environments..."

    for worker in $CODE_WORKERS; do
        worker_dir="$RUNTIME/workers/$worker"

        if [[ -d "$worker_dir" ]]; then
            echo "  $worker: already exists"
            continue
        fi

        echo "  $worker: cloning repos..."
        mkdir -p "$worker_dir"

        for repo in "${REPOS[@]}"; do
            repo_path="$PROJECT_DIR/$repo"
            origin_url=$(cd "$repo_path" && git remote get-url origin 2>/dev/null)

            if [[ -n "$origin_url" ]]; then
                # Clone into worker's workspace
                mkdir -p "$(dirname "$worker_dir/$repo")"
                git clone --quiet "$origin_url" "$worker_dir/$repo" 2>/dev/null || {
                    echo "    ✗ Failed to clone $repo"
                    continue
                }
            fi
        done

        # Generate worker-specific .mcp.json (own browser, no shared CDP endpoint)
        if [[ -f "$PROJECT_DIR/.mcp.json" ]]; then
            python3 -c "
import json
cfg = json.load(open('$PROJECT_DIR/.mcp.json'))
# Strip --cdp-endpoint so each worker spawns its own browser (headed by default)
pw = cfg.get('mcpServers', {}).get('playwright', {})
if 'args' in pw:
    pw['args'] = [a for a in pw['args'] if '--cdp-endpoint' not in a]
json.dump(cfg, open('$worker_dir/.mcp.json', 'w'), indent=2)
" 2>/dev/null || ln -sf "$PROJECT_DIR/.mcp.json" "$worker_dir/.mcp.json"
        fi
        [[ -f "$PROJECT_DIR/.env" ]] && ln -sf "$PROJECT_DIR/.env" "$worker_dir/.env"
        [[ -f "$PROJECT_DIR/.env.octobots" ]] && ln -sf "$PROJECT_DIR/.env.octobots" "$worker_dir/.env.octobots"
        [[ -d "$PROJECT_DIR/venv" ]] && ln -sf "$PROJECT_DIR/venv" "$worker_dir/venv"
        [[ -d "$PROJECT_DIR/node_modules" ]] && ln -sf "$PROJECT_DIR/node_modules" "$worker_dir/node_modules"
        ln -sf "$PROJECT_DIR/octobots" "$worker_dir/octobots"
        ln -sf "$RUNTIME" "$worker_dir/.octobots"

        # Symlink AGENTS.md from main workspace
        [[ -f "$PROJECT_DIR/AGENTS.md" ]] && ln -sf "$PROJECT_DIR/AGENTS.md" "$worker_dir/AGENTS.md"

        # Worker-specific env
        cat > "$worker_dir/.env.worker" << WEOF
WORKER_ID=$worker
OCTOBOTS_ID=$worker
OCTOBOTS_DB=$RUNTIME/relay.db
WEOF

        echo "    Cloned ${#REPOS[@]} repos, symlinked shared resources"
    done
else
    echo ""
    echo "  No sub-repos found — workers will share the main workspace."
fi

echo ""
echo "Done. Structure:"
echo "  .octobots/"
echo "  ├── board.md              Team whiteboard"
echo "  ├── memory/               Per-role persistent learnings"
echo "  ├── roles/                Project-specific role overrides"
echo "  ├── skills/               Project-specific skills"
echo "  ├── agents/               Project-specific agents"
echo "  ├── profile.md            Project card (scout generates)"
echo "  ├── relay.db              Taskbox database"
echo "  └── workers/              Isolated worker environments"
echo "      ├── python-dev/       Own repo clones + shared venv"
echo "      ├── js-dev/           Own repo clones + shared node_modules"
echo "      └── qa-engineer/      Own repo clones"
echo ""
echo "Next: octobots/start.sh scout  (to explore and generate project config)"
