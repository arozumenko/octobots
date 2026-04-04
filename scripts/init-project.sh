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

## Team

_Updated by supervisor. Route taskbox messages to the Worker ID column._

## Active Work

_Updated by supervisor from taskbox state._

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

# ── Seed .claude/ for Claude Code agent + skill discovery ───────────────────
# .claude/agents/ and .claude/skills/ are symlinks into octobots — source of
# truth stays in octobots/roles/, octobots/shared/agents/, octobots/skills/
#
# seed_claude_dir <target_dir> [role]
#   No role  → all roles + shared agents + all skills  (main project dir)
#   With role → that role only + shared agents + role's declared skills only
#
# Skills are declared in each role's AGENT.md frontmatter:
#   skills: [taskbox, bugfix-workflow, ...]
# If no skills: key is present, all skills are linked (safe fallback).
seed_claude_dir() {
    local target_dir="$1"
    local only_role="${2:-}"
    mkdir -p "$target_dir/.claude/agents" "$target_dir/.claude/skills"

    # Roles → .claude/agents/<role>  (all, or only the worker's own role)
    for role_dir in "$OCTOBOTS_DIR/roles"/*/; do
        local role; role="$(basename "$role_dir")"
        [[ -n "$only_role" && "$role" != "$only_role" ]] && continue
        local link="$target_dir/.claude/agents/$role"
        [[ ! -e "$link" ]] && ln -sf "$role_dir" "$link" && echo "  .claude/agents/$role"
    done

    # Shared agents → .claude/agents/<name>  (always available to all workers)
    if [[ -d "$OCTOBOTS_DIR/shared/agents" ]]; then
        for agent_dir in "$OCTOBOTS_DIR/shared/agents"/*/; do
            local name; name="$(basename "$agent_dir")"
            local link="$target_dir/.claude/agents/$name"
            [[ ! -e "$link" ]] && ln -sf "$agent_dir" "$link" && echo "  .claude/agents/$name"
        done
    fi

    # Skills — all skills for main project dir; role-filtered for workers
    local allowed_skills=()
    if [[ -n "$only_role" ]]; then
        local agent_md="$OCTOBOTS_DIR/roles/$only_role/AGENT.md"
        if [[ -f "$agent_md" ]]; then
            # Parse: skills: [foo, bar, baz]  (single-line YAML array in frontmatter)
            local skills_line; skills_line=$(grep -m1 '^skills:' "$agent_md" 2>/dev/null || true)
            if [[ -n "$skills_line" ]]; then
                # Strip "skills: [" and "]", split on commas
                local skills_val; skills_val="${skills_line#skills:}"
                skills_val="${skills_val//[/ }"
                skills_val="${skills_val//]/ }"
                IFS=', ' read -r -a allowed_skills <<< "$skills_val"
            fi
        fi
    fi

    for skill_dir in "$OCTOBOTS_DIR/skills"/*/; do
        local skill; skill="$(basename "$skill_dir")"
        # If we have an allowed list, skip skills not in it
        if [[ ${#allowed_skills[@]} -gt 0 ]]; then
            local found=0
            for s in "${allowed_skills[@]}"; do
                [[ "$s" == "$skill" ]] && found=1 && break
            done
            [[ $found -eq 0 ]] && continue
        fi
        local link="$target_dir/.claude/skills/$skill"
        [[ ! -e "$link" ]] && ln -sf "$skill_dir" "$link" && echo "  .claude/skills/$skill"
    done

    # Propagate skills installed via `npx skills add` in the project root's .claude/skills/
    # (real directories, not symlinks from octobots) into this target's .claude/skills/
    if [[ "$target_dir" != "$PROJECT_DIR" && -d "$PROJECT_DIR/.claude/skills" ]]; then
        for skill_dir in "$PROJECT_DIR/.claude/skills"/*/; do
            [[ -d "$skill_dir" ]] || continue
            # Skip if it's already a symlink (came from octobots/skills/ above)
            [[ -L "$skill_dir" ]] && continue
            local skill; skill="$(basename "$skill_dir")"
            local link="$target_dir/.claude/skills/$skill"
            [[ ! -e "$link" ]] && ln -sf "$skill_dir" "$link" && echo "  .claude/skills/$skill (installed)"
        done
    fi
}

echo ""
echo "Seeding .claude/ (agents + skills)..."
seed_claude_dir "$PROJECT_DIR"

# ── Generate OCTOBOTS.md + CLAUDE.md for a worker ───────────────────────────
# OCTOBOTS.md  — role-specific runtime config (worker ID, inbox, memory, skills)
# CLAUDE.md    — @-imports shared conventions + OCTOBOTS.md so both are auto-loaded
#
# For foreign agents: scout injects "@OCTOBOTS.md" into their CLAUDE.md instead.
generate_worker_claude() {
    local worker="$1"
    local worker_dir="$2"
    # Find AGENT.md: installed agent takes priority over bundled role
    local agent_md="$PROJECT_DIR/.claude/agents/$worker/AGENT.md"
    [[ -f "$agent_md" ]] || agent_md="$OCTOBOTS_DIR/roles/$worker/AGENT.md"

    # Parse skills list from AGENT.md frontmatter: skills: [foo, bar]
    local skills_line; skills_line=$(grep -m1 '^skills:' "$agent_md" 2>/dev/null || true)
    local skills_val=""
    if [[ -n "$skills_line" ]]; then
        skills_val="${skills_line#skills:}"
        skills_val="${skills_val//[/}"
        skills_val="${skills_val//]/}"
        skills_val=$(echo "$skills_val" | tr -d ' ')
    fi

    # OCTOBOTS.md — role-specific config, regenerated on each init run
    cat > "$worker_dir/OCTOBOTS.md" << OEOF
<!-- Generated by octobots/scripts/init-project.sh — do not edit manually -->
# Octobots Runtime Config

- **Worker ID**: \`$worker\`
- **Taskbox inbox**: \`python octobots/skills/taskbox/scripts/relay.py inbox --id $worker\`
- **Send message**: \`python octobots/skills/taskbox/scripts/relay.py send --from $worker --to <role> "message"\`
- **Ack message**: \`python octobots/skills/taskbox/scripts/relay.py ack MSG_ID "summary"\`
- **Memory file**: \`.octobots/memory/$worker.md\`
- **Notify user**: \`octobots/scripts/notify-user.sh "message"\`
- **Active skills**: ${skills_val:-all}
OEOF

    # CLAUDE.md — only written once (user may customize it; OCTOBOTS.md is always regenerated)
    if [[ ! -f "$worker_dir/CLAUDE.md" ]]; then
        cat > "$worker_dir/CLAUDE.md" << CEOF
@$OCTOBOTS_DIR/shared/conventions.md
@OCTOBOTS.md
CEOF
        echo "  $worker: CLAUDE.md + OCTOBOTS.md"
    else
        # Always regenerate OCTOBOTS.md even if CLAUDE.md exists
        echo "  $worker: OCTOBOTS.md (CLAUDE.md preserved)"
    fi
}

# ── Setup worker environments ────────────────────────────────────────────────
# All roles get a worker dir + .claude/ seeding.
# Roles with `workspace: clone` in their AGENT.md also get isolated repo clones.

# Discover all roles: installed agents (.claude/agents/) take priority,
# bundled roles (octobots/roles/) used as fallback for any not yet installed.
ALL_WORKERS=()
CLONE_WORKERS=()
declare -A _seen_roles

# 1. Installed agents (canonical source after decoupling)
if [[ -d "$PROJECT_DIR/.claude/agents" ]]; then
    for role_dir in "$PROJECT_DIR/.claude/agents"/*/; do
        [[ -f "$role_dir/AGENT.md" ]] || continue
        role="$(basename "$role_dir")"
        _seen_roles[$role]=1
        ALL_WORKERS+=("$role")
        if grep -q "^workspace:[[:space:]]*clone" "$role_dir/AGENT.md" 2>/dev/null; then
            CLONE_WORKERS+=("$role")
        fi
    done
fi

# 2. Bundled fallback (octobots/roles/) for roles not yet installed
for role_dir in "$OCTOBOTS_DIR/roles"/*/; do
    [[ -d "$role_dir" ]] || continue
    role="$(basename "$role_dir")"
    [[ -n "${_seen_roles[$role]:-}" ]] && continue
    [[ -f "$role_dir/AGENT.md" ]] || continue
    _seen_roles[$role]=1
    ALL_WORKERS+=("$role")
    if grep -q "^workspace:[[:space:]]*clone" "$role_dir/AGENT.md" 2>/dev/null; then
        CLONE_WORKERS+=("$role")
    fi
done

echo ""
echo "Setting up worker environments..."
for worker in "${ALL_WORKERS[@]}"; do
    worker_dir="$RUNTIME/workers/$worker"

    if [[ -d "$worker_dir" ]]; then
        echo "  $worker: already exists"
        # Still seed .claude/ in case new roles/skills were added
        seed_claude_dir "$worker_dir" "$worker"
        generate_worker_claude "$worker" "$worker_dir"
        continue
    fi

    mkdir -p "$worker_dir"

    # Shared resources (symlinks, no clone needed)
    ln -sf "$OCTOBOTS_DIR" "$worker_dir/octobots"
    ln -sf "$RUNTIME" "$worker_dir/.octobots"
    [[ -f "$PROJECT_DIR/AGENTS.md" ]] && ln -sf "$PROJECT_DIR/AGENTS.md" "$worker_dir/AGENTS.md"
    [[ -f "$PROJECT_DIR/.env" ]] && ln -sf "$PROJECT_DIR/.env" "$worker_dir/.env"
    [[ -f "$PROJECT_DIR/.env.octobots" ]] && ln -sf "$PROJECT_DIR/.env.octobots" "$worker_dir/.env.octobots"
    [[ -d "$PROJECT_DIR/venv" ]] && ln -sf "$PROJECT_DIR/venv" "$worker_dir/venv"
    [[ -d "$PROJECT_DIR/node_modules" ]] && ln -sf "$PROJECT_DIR/node_modules" "$worker_dir/node_modules"

    # .claude/ — worker sees only its own role + shared agents + all skills
    seed_claude_dir "$worker_dir" "$worker"

    # CLAUDE.md + OCTOBOTS.md — conventions + role config
    generate_worker_claude "$worker" "$worker_dir"

    # Worker-specific env
    cat > "$worker_dir/.env.worker" << WEOF
WORKER_ID=$worker
OCTOBOTS_ID=$worker
OCTOBOTS_DB=$RUNTIME/relay.db
WEOF

    echo "  $worker: ready"
done

# ── Clone repos into worker environments (code-writing workers only) ─────────
REPOS=()
while IFS= read -r repo; do
    [[ "$repo" == "octobots" ]] && continue
    REPOS+=("$repo")
done < <(find "$PROJECT_DIR" -mindepth 2 -maxdepth 3 -name ".git" -type d | sed "s|$PROJECT_DIR/||; s|/.git||" | sort)

if [[ ${#REPOS[@]} -gt 0 && ${#CLONE_WORKERS[@]} -gt 0 ]]; then
    echo ""
    echo "Cloning ${#REPOS[@]} repos into workspace workers (${CLONE_WORKERS[*]})..."

    for worker in "${CLONE_WORKERS[@]}"; do
        worker_dir="$RUNTIME/workers/$worker"
        cloned=0

        for repo in "${REPOS[@]}"; do
            repo_path="$PROJECT_DIR/$repo"
            [[ -d "$worker_dir/$repo" ]] && continue
            origin_url=$(cd "$repo_path" && git remote get-url origin 2>/dev/null) || continue
            mkdir -p "$(dirname "$worker_dir/$repo")"
            if git clone --quiet "$origin_url" "$worker_dir/$repo" 2>/dev/null; then
                (( cloned++ ))
            else
                echo "    ✗ $worker: failed to clone $repo (private repo or auth required)"
                echo "      Fix manually:  git clone $origin_url $worker_dir/$repo"
            fi
        done

        # Worker-specific .mcp.json (own browser, no shared CDP endpoint)
        if [[ -f "$PROJECT_DIR/.mcp.json" ]] && [[ ! -f "$worker_dir/.mcp.json" ]]; then
            python3 -c "
import json
cfg = json.load(open('$PROJECT_DIR/.mcp.json'))
pw = cfg.get('mcpServers', {}).get('playwright', {})
if 'args' in pw:
    pw['args'] = [a for a in pw['args'] if '--cdp-endpoint' not in a]
json.dump(cfg, open('$worker_dir/.mcp.json', 'w'), indent=2)
" 2>/dev/null || ln -sf "$PROJECT_DIR/.mcp.json" "$worker_dir/.mcp.json"
        fi

        [[ $cloned -gt 0 ]] && echo "  $worker: cloned $cloned repos"
    done
else
    echo ""
    echo "  No repos to clone — workspace workers (${CLONE_WORKERS[*]}) share the main workspace."
fi

echo ""
echo "Done. Structure:"
echo "  .claude/"
echo "  ├── agents/               Symlinks → octobots roles + shared agents"
echo "  └── skills/               Symlinks → octobots skills"
echo "  .octobots/"
echo "  ├── board.md              Team whiteboard"
echo "  ├── memory/               Per-role persistent learnings"
echo "  ├── roles/                Project-specific role overrides"
echo "  ├── skills/               Project-specific skills"
echo "  ├── agents/               Project-specific agents"
echo "  ├── profile.md            Project card (scout generates)"
echo "  ├── relay.db              Taskbox database"
echo "  └── workers/              Isolated worker environments (each with .claude/ seeded)"
echo "      ├── python-dev/       Own repo clones + shared venv"
echo "      ├── js-dev/           Own repo clones + shared node_modules"
echo "      └── qa-engineer/      Own repo clones"
echo ""
echo "Next: octobots/start.sh scout  (to explore and generate project config)"
