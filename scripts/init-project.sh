#!/usr/bin/env bash
# Initialize .octobots/ runtime directory for a project.
#
# Creates the directory structure that roles read/write at runtime.
# Safe to run multiple times — only creates missing files, never overwrites.
#
# Usage:
#   octobots/scripts/init-project.sh                          # standard init
#   octobots/scripts/init-project.sh --update                 # re-fetch all from octobots.yaml
#   octobots/scripts/init-project.sh --role <owner/repo[@ref]> # add one role ad-hoc

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OCTOBOTS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_DIR="$(pwd)"
RUNTIME="$PROJECT_DIR/.octobots"

# ── Argument parsing ──────────────────────────────────────────────────────────
UPDATE=0
ADHOC_ROLE=""
ADHOC_SKILL=""
MODE=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --update)         UPDATE=1; shift ;;
        --role)           ADHOC_ROLE="${2:?--role requires owner/repo}"; shift 2 ;;
        --skill)          ADHOC_SKILL="${2:?--skill requires owner/repo}"; shift 2 ;;
        --mode)           MODE="${2:?--mode requires a value}"; shift 2 ;;
        *)                shift ;;
    esac
done

echo "Initializing .octobots/ in $PROJECT_DIR"

# Normalise mode aliases
[[ "$MODE" == "pa" ]] && MODE="personal-assistant"

# ── Create directory structure ──────────────────────────────────────────────
# Memory lives at .agents/memory/ (project-root, IDE-neutral) — not .octobots/memory/.
# See "Create memory dirs for installed roles" below.
mkdir -p "$RUNTIME/roles"
mkdir -p "$RUNTIME/skills"
mkdir -p "$RUNTIME/agents"
mkdir -p "$RUNTIME/registry"

# ── Personal Assistant mode — extra directories + persona templates ─────────
if [[ "$MODE" == "personal-assistant" ]]; then
    mkdir -p "$RUNTIME/pa-inbox/processed"
    mkdir -p "$RUNTIME/persona"

    # Copy persona templates if not already present — look in the installed PA
    # agent first, then fall back to any bundled copy under octobots/roles/.
    PA_PERSONA_SRC=""
    for candidate in \
        "$PROJECT_DIR/.claude/agents/personal-assistant/persona" \
        "$OCTOBOTS_DIR/roles/personal-assistant/persona"; do
        if [[ -d "$candidate" ]]; then
            PA_PERSONA_SRC="$candidate"
            break
        fi
    done
    if [[ -n "$PA_PERSONA_SRC" ]]; then
        for tmpl in USER.md TOOLS.md access-control.yaml; do
            if [[ ! -f "$RUNTIME/persona/$tmpl" ]]; then
                cp "$PA_PERSONA_SRC/$tmpl" "$RUNTIME/persona/$tmpl"
                echo "  Created persona/$tmpl"
            fi
        done
    fi

    # Write .env.octobots for single-worker PA mode
    if [[ ! -f "$PROJECT_DIR/.env.octobots" ]]; then
        cat > "$PROJECT_DIR/.env.octobots" << 'ENVEOF'
OCTOBOTS_WORKERS=personal-assistant
OCTOBOTS_EXCLUDED_ROLES=project-manager,python-dev,js-dev,qa-engineer,ba,tech-lead,scout
ENVEOF
        echo "  Created .env.octobots (PA mode)"
    fi

    echo ""
    echo "Personal Assistant mode enabled."
fi

# ── Create board.md (team whiteboard) ───────────────────────────────────────
if [[ ! -f "$RUNTIME/board.md" ]]; then
    if [[ "$MODE" == "personal-assistant" ]]; then
        cat > "$RUNTIME/board.md" << 'EOF'
# PA Board

## Status

_Updated by supervisor._

## Open Loops

## Notes
EOF
    else
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
    fi
    echo "  Created board.md"
fi

# ── Create memory dirs for installed roles ──────────────────────────────────
# Memory lives at .agents/memory/<role>/ (IDE-neutral). Scout seeds a
# project_briefing.md curated entry here when it onboards the project —
# this block only ensures the directory structure exists so agents can
# write immediately.
AGENTS_MEMORY="$PROJECT_DIR/.agents/memory"
mkdir -p "$AGENTS_MEMORY"
for role_dir in "$PROJECT_DIR/.claude/agents"/*/; do
    [[ -f "$role_dir/AGENT.md" ]] || continue
    role="$(basename "$role_dir")"
    role_mem="$AGENTS_MEMORY/$role"
    if [[ ! -d "$role_mem" ]]; then
        mkdir -p "$role_mem/daily"
        echo "  Created .agents/memory/$role/"
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

# ── Fetch from octobots.yaml (project composition file) ──────────────────────
# Parses octobots.yaml and prints "roles|skills <TAB> owner/repo <TAB> ref" lines.
_parse_octobots_yaml() {
    local yaml="$PROJECT_DIR/octobots.yaml"
    [[ -f "$yaml" ]] || return 0
    python3 - "$yaml" << 'PYEOF'
import sys, re

yaml_path = sys.argv[1]
text = open(yaml_path).read()

section = None
item = {}

for line in text.splitlines():
    stripped = line.strip()
    if not stripped or stripped.startswith('#'):
        continue
    if re.match(r'^roles:\s*$', stripped):
        section = 'roles'; item = {}; continue
    if re.match(r'^skills:\s*$', stripped):
        section = 'skills'; item = {}; continue
    if re.match(r'^version:', stripped) or re.match(r'^[a-z]', stripped) and ':' in stripped and not stripped.startswith('-'):
        section = None; continue
    if section and stripped.startswith('- repo:'):
        if item.get('repo'):
            print(f"{section}\t{item['repo']}\t{item.get('ref', 'main')}")
        item = {'repo': stripped[7:].strip().strip('"').strip("'")}
    elif section and re.match(r'^ref:', stripped) and item:
        item['ref'] = stripped[4:].strip().strip('"').strip("'")

if section and item.get('repo'):
    print(f"{section}\t{item['repo']}\t{item.get('ref', 'main')}")
PYEOF
}

_fetch_component() {
    local section="$1" repo="$2" ref="$3"
    local type; type=$([[ "$section" == "roles" ]] && echo "agent" || echo "skill")
    local name="${repo##*/}"
    name="${name%-agent}"; name="${name#skill-}"; name="${name#sdlc:}"

    # Check already installed (skip unless --update)
    if [[ $UPDATE -eq 0 ]]; then
        local check_dir
        [[ "$section" == "roles" ]] && check_dir="$PROJECT_DIR/.claude/agents" || check_dir="$PROJECT_DIR/.claude/skills"
        if [[ -d "$check_dir/$name" ]]; then
            echo "  ✓ $name (already installed)"
            return 0
        fi
    fi

    echo "  Fetching $section: $repo@$ref"
    bash "$SCRIPT_DIR/registry-fetch.sh" "$type" "$repo" "$ref" \
        || echo "  ⚠  Failed to fetch $repo"
}

OCTOBOTS_YAML="$PROJECT_DIR/octobots.yaml"
if [[ -f "$OCTOBOTS_YAML" ]]; then
    echo ""
    echo "Reading octobots.yaml..."
    while IFS=$'\t' read -r section repo ref; do
        [[ -z "$repo" ]] && continue
        _fetch_component "$section" "$repo" "$ref"
    done < <(_parse_octobots_yaml)
fi

# Ad-hoc fetch via --role / --skill flags
if [[ -n "$ADHOC_ROLE" ]]; then
    repo="${ADHOC_ROLE%%@*}"; ref="${ADHOC_ROLE#*@}"
    [[ "$ref" == "$repo" ]] && ref="main"
    echo ""; echo "Fetching role: $repo@$ref"
    bash "$SCRIPT_DIR/registry-fetch.sh" agent "$repo" "$ref" \
        || echo "  ⚠  Failed to fetch $repo"
fi
if [[ -n "$ADHOC_SKILL" ]]; then
    repo="${ADHOC_SKILL%%@*}"; ref="${ADHOC_SKILL#*@}"
    [[ "$ref" == "$repo" ]] && ref="main"
    echo ""; echo "Fetching skill: $repo@$ref"
    bash "$SCRIPT_DIR/registry-fetch.sh" skill "$repo" "$ref" \
        || echo "  ⚠  Failed to fetch $repo"
fi

# ── Seed .claude/ for Claude Code agent + skill discovery ───────────────────
# .claude/agents/ and .claude/skills/ in each worker dir are symlinks to the
# canonical install locations in the project root: .claude/agents/ (or
# .octobots/roles/ for overrides) and .claude/skills/.
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
    # Sources: project overrides in .octobots/roles/ take priority over installed
    # agents in .claude/agents/. The supervisor installs agents via `npx github:<repo> init`
    # which writes to .claude/agents/, so that is the canonical install location.
    local _seeded_roles=""
    for src_dir in "$PROJECT_DIR/.octobots/roles" "$PROJECT_DIR/.claude/agents"; do
        [[ -d "$src_dir" ]] || continue
        # Skip if src == target agents dir (would create self-symlinks)
        [[ "$src_dir" == "$target_dir/.claude/agents" ]] && continue
        for role_dir in "$src_dir"/*/; do
            [[ -f "$role_dir/AGENT.md" ]] || continue
            local role; role="$(basename "$role_dir")"
            case ",$_seeded_roles," in *",$role,"*) continue ;; esac
            [[ -n "$only_role" && "$role" != "$only_role" ]] && continue
            local link="$target_dir/.claude/agents/$role"
            if [[ ! -e "$link" ]]; then
                ln -sf "$role_dir" "$link"
                echo "  .claude/agents/$role"
            fi
            _seeded_roles="${_seeded_roles:+$_seeded_roles,}$role"
        done
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
        local agent_md="$PROJECT_DIR/.octobots/roles/$only_role/AGENT.md"
        [[ -f "$agent_md" ]] || agent_md="$PROJECT_DIR/.claude/agents/$only_role/AGENT.md"
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
    # (real directories, not symlinks from octobots) into this target's .claude/skills/.
    # Apply the same per-role allowlist as bundled skills above — otherwise published
    # skills leak into every worker regardless of what the role declared.
    if [[ "$target_dir" != "$PROJECT_DIR" && -d "$PROJECT_DIR/.claude/skills" ]]; then
        for skill_dir in "$PROJECT_DIR/.claude/skills"/*/; do
            [[ -d "$skill_dir" ]] || continue
            # Skip if it's already a symlink (came from octobots/skills/ above)
            [[ -L "$skill_dir" ]] && continue
            local skill; skill="$(basename "$skill_dir")"
            if [[ ${#allowed_skills[@]} -gt 0 ]]; then
                local found=0
                for s in "${allowed_skills[@]}"; do
                    [[ "$s" == "$skill" ]] && found=1 && break
                done
                [[ $found -eq 0 ]] && continue
            fi
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
    # Find AGENT.md: project override takes priority over installed agent
    local agent_md="$PROJECT_DIR/.octobots/roles/$worker/AGENT.md"
    [[ -f "$agent_md" ]] || agent_md="$PROJECT_DIR/.claude/agents/$worker/AGENT.md"

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
- **Memory dir**: \`.agents/memory/$worker/\` (MEMORY.md index + curated entries + daily/ logs — see the \`memory\` skill)
- **Active skills**: ${skills_val:-all}

## Notifying the user (Telegram)

Use the **\`notify\` MCP tool** (\`mcp__notify__notify\`) — never print messages
as plain text and never embed long payloads inside Bash commands.

**Send a short text message** (auto-prefixed with your role badge):
\`\`\`
notify(message="Deployed v1.2 to staging — ready for QA")
\`\`\`

**Send a long message or report** (>4000 chars is auto-uploaded as a .md file):
\`\`\`
notify(message="…full report markdown here…")
\`\`\`

**Send a file as an attachment** (any type — .md, .pdf, .png, .ogg, .log, .zip, …):
\`\`\`
notify(message="QA report for #103", file=".octobots/reports/qa-103.pdf")
\`\`\`
\`message\` becomes the Telegram caption, \`file\` is the path to upload.
The transport (photo / voice / audio / document) is chosen automatically by
file extension. Use this for screenshots, voice notes, logs, PDFs, or anything
you want delivered as a file rather than inline text.

Rules:
- One Bash tool call per notification. Do not chain or background it.
- First positional arg = message/caption. Optional: \`--file <path>\` to attach.
- Messages longer than 4000 chars without \`--file\` are auto-uploaded as .md.
- If Telegram is not configured the script exits 0 with \`{"status":"skipped"}\` — safe to ignore.

## Delegating work to other roles

**NEVER use the Claude Code Agent tool to do another role's work.**

You have access to other agents in .claude/agents/, but those are for
lightweight sub-tasks within YOUR own context (e.g., taskbox-listener for inbox
polling, issue-reproducer for bug repro). They are NOT a substitute for
sending work through taskbox to the actual role running in its own tmux pane.

**To assign work to another role, use taskbox:**
\`\`\`bash
python octobots/skills/taskbox/scripts/relay.py send --from $worker --to <role> "TASK-NNN (#issue): description"
\`\`\`

The supervisor routes the message to the recipient's tmux pane. The recipient
works in their own isolated context with their own skills, tools, and memory.

**Wrong:** Using the Agent tool to spawn python-dev and write code in PM's context.
**Right:** Sending a taskbox message to python-dev, who works in their own worktree.
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

# Discover all roles from .claude/agents/ — the canonical install location.
# Project overrides live in .octobots/roles/ and shadow installed agents of the
# same name; we walk both and dedupe with .octobots/roles/ winning.
ALL_WORKERS=()
CLONE_WORKERS=()
_seen_roles=""

for src_dir in "$PROJECT_DIR/.octobots/roles" "$PROJECT_DIR/.claude/agents"; do
    [[ -d "$src_dir" ]] || continue
    for role_dir in "$src_dir"/*/; do
        [[ -f "$role_dir/AGENT.md" ]] || continue
        role="$(basename "$role_dir")"
        case ",$_seen_roles," in *",$role,"*) continue ;; esac
        _seen_roles="${_seen_roles:+$_seen_roles,}$role"
        ALL_WORKERS+=("$role")
        if grep -q "^workspace:[[:space:]]*clone" "$role_dir/AGENT.md" 2>/dev/null; then
            CLONE_WORKERS+=("$role")
        fi
    done
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

# ── Install skill dependencies ────────────────────────────────────────────────
if [[ ${#ALL_WORKERS[@]} -gt 0 ]]; then
    echo ""
    echo "Installing skill dependencies..."
    for worker in "${ALL_WORKERS[@]}"; do
        bash "$OCTOBOTS_DIR/scripts/setup-skill.sh" --role "$worker" 2>/dev/null || true
    done
fi

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
if [[ "$MODE" == "personal-assistant" ]]; then
    echo "Done. Structure:"
    echo "  .octobots/"
    echo "  ├── board.md              PA board"
    echo "  ├── persona/"
    echo "  │   ├── USER.md           Your profile (edit: timezone, quiet hours)"
    echo "  │   ├── TOOLS.md          Environment config (edit: vault path, filters)"
    echo "  │   └── access-control.yaml  Routing rules"
    echo "  ├── pa-inbox/             Drop files here for PA to process"
    echo "  │   └── processed/        Processed file archive"
    echo "  ├── relay.db              Taskbox database"
    echo "  └── workers/"
    echo "      └── personal-assistant/"
    echo ""
    echo "Next steps:"
    echo "  1. Edit .octobots/persona/USER.md — add your timezone and quiet hours"
    echo "  2. Edit .octobots/persona/TOOLS.md — add Obsidian vault path and email filters"
    echo "  3. Run: python3 .claude/skills/msgraph/scripts/auth.py login  (if using M365)"
    echo "  4. Start: python3 octobots/scripts/supervisor.py"
    echo "  5. Drop files into .octobots/pa-inbox/ to send to your PA"
else
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
fi
