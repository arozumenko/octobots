# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Octobots is a framework for orchestrating multiple Claude Code instances as an AI development team. It's installed into target projects as a sibling directory (`octobots/`), not run standalone. Roles (PM, dev, QA, BA, tech lead, scout) are published as standalone agent repos and installed via `npx github:arozumenko/<name>-agent init`. They communicate through a shared SQLite queue (taskbox).

## Commands

```bash
# Validate infrastructure readiness
python3 octobots/scripts/check-spawn-ready.py
python3 octobots/scripts/check-spawn-ready.py --check infra-only
python3 octobots/scripts/check-spawn-ready.py --check files-only

# Check shell script syntax
bash -n octobots/scripts/*.sh

# Install Python dependencies
pip install -r octobots/scripts/requirements.txt

# Start a single role (interactive, from project root)
octobots/start.sh scout
octobots/start.sh python-dev

# Start full team (Rich TUI supervisor)
python3 octobots/scripts/supervisor.py

# Taskbox CLI
python octobots/skills/taskbox/scripts/relay.py send --from py --to pm "message"
python octobots/skills/taskbox/scripts/relay.py inbox --id pm
python octobots/skills/taskbox/scripts/relay.py ack MSG_ID "acked"

# Agent registry / team selector (non-interactive)
python3 octobots/scripts/select-agents.py --preset 0   # first preset
python3 octobots/scripts/select-agents.py --all        # all agents
```

There is no test suite for the framework itself — tests live in target projects.

## Architecture

### Role System (Decoupled)

Roles are standalone GitHub repos (`arozumenko/<name>-agent`). Each has:
- `agents/<name>/AGENT.md` — YAML frontmatter (name, model, color, `workspace`, skills list) + technical instructions
- `agents/<name>/SOUL.md` — Personality, voice, working style
- `bin/init.mjs` — Installer: copies agent files to `.claude/agents/`, `.cursor/agents/`, etc.
- `package.json` with `"bin": { "init": "./bin/init.mjs" }`

Install a role: `npx github:arozumenko/<name>-agent init --all`

Role resolution order (both `start.sh` and `supervisor.py`):
1. `.octobots/roles/<role>/` (project overrides)
2. `.claude/agents/<role>/` (installed via `npx github:<repo> init`)

`octobots/roles/` is no longer a role source. The supervisor reads from
`.claude/agents/` directly — no promotion, no moving files out of user-owned
directories. `/role add <id>` / `/role add owner/repo[@ref]` installs into
`.claude/agents/` via `registry-fetch.sh` and then launches the worker in place.
`/role remove` only tears down `.octobots/workers/<role>/` and leaves
`.claude/agents/<role>/` intact — uninstall the agent separately if you want it
gone.

Roles with `workspace: clone` get isolated repo clones under `.octobots/workers/<role>/`.

### Agent Registry

`agents.json` lists all published agents and team presets. Used by:
- `install.sh` via `scripts/select-agents.py` for cookiecutter team setup
- Scout during project onboarding to propose team adjustments

```json
{
  "agents": [{ "id": "scout", "repo": "arozumenko/scout-agent", "required": true }, ...],
  "presets": [{ "name": "Full-stack web team", "agents": [...], "qa": "qa-onetest" }, ...]
}
```

### Taskbox (Inter-Role Messaging)

All inter-role communication flows through a single SQLite database (`.octobots/relay.db`). No REST APIs, no MCP servers for messaging — just Python stdlib + Bash. The supervisor holds incoming messages until the worker acknowledges the current task, preventing pile-up.

### Session-Per-Issue Pattern

Each GitHub issue maps to a named Claude Code session (e.g., `python-dev-issue-103`). Roles use `/resume <session-name>` to restore full context when returning to a task. This keeps context isolated and prevents bloat.

### Three Communication Channels

| Channel | Purpose | Persistence |
|---|---|---|
| `board.md` | Team state — decisions, blockers, active work | Git-versioned |
| Taskbox | Task assignment, async nudges | SQLite, ephemeral |
| GitHub Issues | Permanent audit trail, every action commented | Permanent |

### Pipeline Flow

```
User → PM (Max) → BA (Alex): analyze requirements
BA → Tech Lead (Rio): epic + user stories
Tech Lead → PM: technical tasks
PM → Dev (Py/Jay): assignments
Dev → PM: PR created
PM → QA (Sage): verify PR
Any role → User: notify-user.sh → Telegram
```

### Supervisor (`scripts/supervisor.py`)

Rich TUI that manages tmux panes, polls taskbox, runs scheduled jobs, and maintains the team board. Key REPL commands:

```
/role list|add|remove|clone       # dynamic team management
/skill <role> <skill>             # add skill to running role
/schedule every 30m @pm <task>    # cron-style scheduling
/loop 10m @qa <task>              # recurring loop
```

### Skills System

Bundled skills in `skills/<name>/` are reusable capabilities symlinked into each role's `.claude/skills/`. Published skills (code-review, git-workflow, tdd, etc.) live in standalone `arozumenko/skill-*` repos and are installed via `npx skills add arozumenko/<skill-name>`. The `SKILL.md` file defines the skill per the agentskills.io spec.

Bundled skills (still in this repo):
- `taskbox` — inter-role messaging relay
- `memory` — per-role persistent memory; supervisor invokes `memory.py snapshot` at every role launch
- `bugfix-workflow` — structured bug investigation
- `implement-feature` — feature implementation workflow
- `plan-feature` — feature planning workflow
- `project-seeder` — scout's project configuration skill

Shared agents in `shared/agents/`:
- `taskbox-listener` — long-running inbox poller
- `issue-reproducer` — reproduces GitHub issues
- `rca-investigator` — root cause analysis

### Worker Environments

`scripts/init-project.sh` sets up `.octobots/workers/<role>/` for each discovered role. Each worker gets:
- Symlinks to shared resources (`octobots/`, `.octobots/`, `.env`, etc.)
- `.claude/agents/<role>/` — symlink to the role's agent dir
- `.claude/skills/` — symlinks to allowed skills (filtered by `skills:` in AGENT.md)
- `OCTOBOTS.md` — generated per-worker config (Worker ID, taskbox commands, memory path)
- `CLAUDE.md` — imports `@shared/conventions.md` + `@OCTOBOTS.md` (written once, user-editable)

Clone workers (`workspace: clone`) additionally get isolated git clones for each repo.

### Runtime Directory (`.octobots/`)

Created by `scripts/init-project.sh` in the target project, not in the octobots repo itself:
- `board.md` — Team board (auto-created if missing)
- `relay.db` — SQLite taskbox
- `memory/` — Per-role MEMORY.md files
- `workers/` — Isolated worker environments
- `schedule.json` — Persistent scheduled jobs

## Key Conventions

**Terminal Rules (Critical):** Roles run in unattended tmux panes. They must never ask questions to stdout or present options and wait. All user communication goes through `scripts/notify-user.sh` (Telegram). All teammate communication goes through taskbox.

**Three-Step Task Completion (mandatory for all roles):**
1. Comment on GitHub issue with results
2. Ack the taskbox message
3. Notify user via `notify-user.sh`

Skipping any step breaks the pipeline.

**GitHub Labels Track Status:** `ready` → `in-progress` → `review` → `testing` → `done`. Before starting a task, check for `in-progress` label to avoid duplicate work.

## Configuration

`.env.octobots` (in target project root, not in this repo):
```bash
OCTOBOTS_TG_TOKEN=...         # Telegram bot token
OCTOBOTS_TG_OWNER=...         # Telegram user ID for notifications
OCTOBOTS_WORKERS=project-manager python-dev js-dev qa-engineer ba tech-lead
OCTOBOTS_EXCLUDED_ROLES=scout
```

`.mcp.json` configures MCP servers available to all roles: Playwright, GitHub, Context7, Tavily (web search), Accessibility Scanner, Lighthouse.

## Adding a New Role

1. Create a new GitHub repo `arozumenko/<name>-agent`
2. Add `agents/<name>/AGENT.md` with YAML frontmatter and instructions
3. Add `agents/<name>/SOUL.md` with personality
4. Add `bin/init.mjs` (copy from any existing agent repo and update `AGENT_NAME`)
5. Add `package.json` with `"bin": { "init": "./bin/init.mjs" }`
6. Register in `agents.json` under `"agents"`
7. Use `workspace: clone` in AGENT.md frontmatter if the role needs an isolated repo clone

## Adding a New Skill

Follow the agentskills.io spec documented in `docs/skill-spec.md`. The `SKILL.md` file is the definition. Scripts go in `skills/<name>/scripts/`. The skill is activated by symlinking into a role's `.claude/skills/`.

For standalone skills: create `arozumenko/skill-<name>` repo and publish via `npx skills add`.
For bundled skills (framework-internal): add to `skills/<name>/` in this repo.
