# Setup Guide

## Prerequisites

- **Claude Code CLI** — `claude` must be on PATH
- **Python 3.10+** — with `rich` library for supervisor TUI
- **tmux** — for worker panes (`brew install tmux`)
- **gh CLI** — for GitHub issue tracking (`brew install gh`)
- **Git**

```bash
# Install Python deps (first time)
pip install -r octobots/scripts/requirements.txt
```

## Installation

```bash
# Clone into your project
cd /path/to/your-project
git clone git@github.com:onetest-ai/octobots.git octobots

# Or as a submodule
git submodule add git@github.com:onetest-ai/octobots.git octobots

# Initialize project runtime
octobots/scripts/init-project.sh
```

This creates `.octobots/` with:
- `board.md` — team whiteboard
- `memory/` — per-role persistent learnings
- `roles/` — project-specific role overrides
- `skills/` — project-specific skills
- `workers/` — isolated environments for code workers (multi-repo)
- `relay.db` — taskbox database

## First Run: Scout

```bash
octobots/start.sh scout
```

Kit explores the codebase and generates:
- `AGENTS.md` — project context all roles read
- `.octobots/profile.md` — project card
- `.octobots/conventions.md` — detected coding standards
- `.octobots/architecture.md` — system design map
- `.octobots/testing.md` — test infrastructure

## Running the Team

Two terminals:

**Terminal 1 — Supervisor** (Rich TUI + all workers in tmux):
```bash
octobots/supervisor.sh
```

The supervisor:
- Launches all workers as Claude Code instances in tmux panes
- Polls taskbox and routes tasks to workers
- Provides an interactive command prompt

**Terminal 2 — Telegram bridge**:
```bash
venv/bin/python octobots/scripts/telegram-bridge.py
```

### Supervisor Commands

```
/status              Worker states and last output
/workers             Panes, sources (base/project), environments
/tasks               Taskbox stats (pending/processing/done)
/logs <role> [N]     Last N lines from a worker's pane
/send <role> <msg>   Send a message directly to a worker
/restart <role|all>  Exit + relaunch a worker
/clear <role>        Send /clear to a worker
/board               Show team board (BOARD.md)
/health              System health check
/stop                Graceful shutdown
/help                Command reference
```

### Watching Workers in tmux

```bash
# All workers tiled in one view
tmux attach -t octobots

# Inside tmux:
# Ctrl+B, q     — show pane numbers, press number to select
# Ctrl+B, z     — zoom into current pane (toggle)
# Ctrl+B, d     — detach (everything keeps running)
```

### Interactive Mode (single role)

For debugging or talking to one role directly:
```bash
octobots/start.sh python-dev
octobots/start.sh --list
```

## Telegram Setup

### 1. Create a bot
1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. Send `/newbot`, follow prompts
3. Copy the bot token

### 2. Get your user ID
1. Message [@userinfobot](https://t.me/userinfobot) on Telegram
2. Copy your user ID

### 3. Configure `.env.octobots`

```bash
OCTOBOTS_TG_TOKEN=7123456789:AAHxxx...
OCTOBOTS_TG_OWNER=123456789
```

### 4. Run the bridge

```bash
pip install -r octobots/scripts/requirements.txt  # first time
python3 octobots/scripts/telegram-bridge.py
```

### 5. Usage

Messages go to PM (Max) by default. Use `@role` to address others:

```
what's the status?              → Max (PM)
@python-dev fix the login bug   → Py
@qa-engineer test issue #103    → Sage
@tech-lead decompose #100       → Rio
@ba clarify the auth story      → Alex
```

Bot commands: `/status`, `/team`, `/start`

## Framework vs Runtime

```
octobots/              ← framework (git pull for updates, read-only)
├── roles/               base role templates (SOUL.md + CLAUDE.md)
├── skills/              base skills
├── shared/              conventions, agents
└── scripts/             supervisor, bridge, init, relay

.octobots/             ← runtime (project-specific, workers read/write)
├── board.md             team whiteboard (all roles)
├── memory/              per-role learnings
│   ├── python-dev.md
│   ├── js-dev.md
│   └── ...
├── roles/               project role overrides
├── skills/              project-specific skills
├── agents/              project-specific agents
├── workers/             isolated environments (code workers)
│   ├── python-dev/        own repo clones + shared venv
│   ├── js-dev/            own repo clones + shared node_modules
│   └── qa-engineer/       own repo clones
├── relay.db             taskbox database
└── profile.md           scout output
```

**Resolution order:** `.octobots/roles/<role>/` overrides `octobots/roles/<role>/`. Same for skills and agents. `git pull` in `octobots/` is always safe.

## Configuration

All in `.env.octobots` (project root):

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `OCTOBOTS_TG_TOKEN` | For Telegram | — | Bot token from BotFather |
| `OCTOBOTS_TG_OWNER` | For Telegram | — | Your Telegram user ID |
| `OCTOBOTS_WORKERS` | No | auto-discover | Explicit worker list |
| `OCTOBOTS_EXCLUDED_ROLES` | No | `scout` | Roles to skip in supervisor |
| `OCTOBOTS_DB` | Auto-set | `.octobots/relay.db` | Taskbox database path |
| `OCTOBOTS_ID` | Auto-set | role name | Taskbox instance ID |

## MCP Servers

Configured in project root's `.mcp.json`. All roles share it. Contains API tokens — gitignore it.

```json
{
  "mcpServers": {
    "playwright": { "type": "stdio", "command": "npx", "args": ["@playwright/mcp@latest"] },
    "github": { "type": "http", "url": "https://api.githubcopilot.com/mcp/", "headers": { "Authorization": "Bearer TOKEN" } }
  }
}
```

## Customization

### Override a base role

```bash
# Copy base role and modify
cp -r octobots/roles/python-dev/ .octobots/roles/python-dev/
# Edit .octobots/roles/python-dev/CLAUDE.md with project-specific instructions
# .octobots/ version takes priority over octobots/ version
```

### Add a custom role

```bash
mkdir -p .octobots/roles/devops
# Create SOUL.md + CLAUDE.md
# Auto-discovered by supervisor on next restart
```

### Add a project-specific skill

```bash
mkdir -p .octobots/skills/deploy-staging
# Create SKILL.md following docs/skill-spec.md
```

## GitHub Integration

```bash
gh auth status    # verify auth
gh auth login     # if needed
```

All roles comment on issues automatically. Issues live in the platform repo, PRs go to specific repos.

## Troubleshooting

### Supervisor won't start
Run `/health` to check prerequisites. Install missing tools.

### Workers idle — not picking up tasks
1. `/tasks` — check if messages are pending
2. `/logs <role>` — check worker output
3. `/restart <role>` — relaunch the worker

### Duplicate work on restart
Stuck `processing` messages are marked `done` on restart (not re-queued). If you need to re-send, create a new task.

### Symlinks broken after clone
```bash
cd octobots
for role in roles/*/; do
  for skill in skills/*; do
    ln -sfn "../../../../$skill" "$role/.claude/skills/$(basename $skill)"
  done
done
```

### Worker can't find `.mcp.json`
In isolated environments (`.octobots/workers/<role>/`), `.mcp.json` is symlinked from project root. Verify: `ls -la .octobots/workers/python-dev/.mcp.json`
