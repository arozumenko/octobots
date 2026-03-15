# Building an AI Development Team with Claude Code

*How I turned multiple Claude Code instances into a coordinated software team — with roles, personalities, Telegram interface, and GitHub integration.*

---

## The Problem

AI coding assistants are powerful — but they're solo players. You talk to one instance, it writes code, you review. What if you could have a **full team** — a PM who coordinates, a BA who writes stories, developers who implement, QA who tests — all running as separate Claude Code processes, communicating through a shared queue, and reporting back to you via Telegram?

That's what Octobots is.

![Telegram conversation](assets/1_telegramconv.png)

> **Note:** This is a demo setup for quick start — tmux dashboard, visible browser windows, interactive supervisor prompt. It's designed for verifying the pipeline works and tuning role behavior on your codebase.
>
>  Production runs are headless. The supervisor polls taskbox and GitHub in the background. Issues get assigned, teams spin up per feature, workers scale horizontally. The same roles, the same pipeline, but fully autonomous.  

---

## Architecture in 30 Seconds

```
You (Telegram)
  │ send-keys
  ▼
tmux "octobots" ─── 6 Claude Code sessions, tiled
  ├── 📋 pm     ← receives your messages, distributes work
  ├── 📝 ba     ← breaks goals into user stories
  ├── 🏗️ tl     ← decomposes stories into tasks
  ├── 🐍 py     ← Python backend development
  ├── ⚡ js     ← JS/TS frontend development
  └── 🧪 qa     ← testing and verification
```

Each role is a separate Claude Code process with its own personality, instructions, and persistent memory. They communicate through a SQLite message queue (taskbox) and document everything on GitHub Issues.

![tmux dashboard](assets/2_tmux.png)

---

## How It Started: Two Claude Codes Talking

The first question was simple: **how do two Claude Code processes communicate?**

MCP servers? Too many companies block them. WebSockets? Overkill. The answer turned out to be the simplest thing possible: **a SQLite database with a Python CLI wrapper**.

```bash
# Send a message
python relay.py send --from py --to qa "Issue #103 fixed. PR #45 ready for testing."

# Check inbox
python relay.py inbox --id qa

# Acknowledge
python relay.py ack MSG_ID "All tests pass."
```

No servers. No daemons. No pip dependencies. Just `sqlite3` from Python's stdlib with WAL mode for concurrent access. I called it **Taskbox**.

![Taskbox architecture](assets/3_taskbox.png)

---

## From Chat to Team: The Role System

A Claude Code instance becomes a "role" through three files:

```
roles/python-dev/
├── SOUL.md      ← personality, voice, quirks
├── CLAUDE.md    ← technical instructions, workflow, constraints
└── .claude/
    ├── skills/  ← shared capabilities (symlinked)
    └── agents/  ← sub-agents (symlinked)
```

**SOUL.md** — inspired by [OpenClaw's SOUL.md](https://github.com/aaronjmars/soul.md) personality system — is where the fun is. Each role has a distinct personality:

> **Py** — calm, methodical, dry humor. Runs `py_compile` compulsively. Gets suspicious when tests pass on the first try.

> **Jay** — energetic, opinionated. Says "shipped" after deploys. Feels a disturbance in the force when someone uses `any`.

> **Sage** — evidence-obsessed QA. Takes screenshots compulsively. Calls flaky tests "trust erosion."

> **Max** — sharp PM. Counts tasks. Has a sixth sense for scope creep.

These aren't gimmicks — personality influences how the agent communicates. A calm, precise QA engineer writes better bug reports than a generic assistant.

---

## The Pipeline

Work flows through the team like a real development process:

```
1. You → pm: "We need user authentication"
2. pm → ba: "Analyze auth requirements"
3. ba creates epic + user stories with acceptance criteria
4. ba → tl: "Stories ready for decomposition"
5. tl reads the codebase, designs interfaces, creates tasks
6. tl → pm: "6 tasks, 3 parallel groups, 1 risk"
7. pm → py: "TASK-003 (#103): login endpoint"
8. pm → js: "TASK-004 (#104): login form"
9. py & js work in parallel (isolated git worktrees)
10. py → pm: "Done. PR #45."
11. pm → qa: "Verify #103"
12. qa tests, finds bug, reports on GitHub issue
13. qa → pm → py: "Bug: uppercase email causes 500"
14. py fixes, qa re-verifies
15. All roles → You (Telegram): status updates throughout
```

---

## The Supervisor: A Rich TUI Control Center

The supervisor manages all workers from a single terminal:

```bash
octobots/supervisor.sh
```

It provides an interactive command prompt with Rich formatting:

![Supervisor TUI](assets/4_supervisor.png)

```
octobots> /status       ← worker states + last output
octobots> /health       ← system health check
octobots> /tasks        ← taskbox queue stats
octobots> /logs py 20   ← last 20 lines from py's pane
octobots> /bridge       ← start Telegram bridge
octobots> /restart qa   ← relaunch QA
octobots> /board        ← team whiteboard
```

---

## Messenger-First: Talk to Your Team

You don't need to sit in front of tmux. Talk to the team from your phone — Telegram is the default bridge, but the architecture supports any messenger (Slack, Discord, Teams) as a pluggable connector:

```
You: @py fix the login bug in authService.ts
→ 🐍 py

[py] Started. Reading authService.ts...
[py] Found it — missing null check on line 47. Fixing.
[py] Done. PR #52. All tests pass.

You: @qa verify PR #52
→ 🧪 qa

[qa] Testing login flow...
[qa] ✅ Verified. Login works with all email formats.
```

Reply routing works naturally — swipe-reply to a `[py]` message and it goes back to py:

![Reply routing](assets/7_reply_to_message.png)

Short aliases: `@pm`, `@ba`, `@tl`, `@py`, `@js`, `@qa` — two letters, fast to type.

---

## Session-per-Ticket: No Context Blowup

Each GitHub issue maps to a Claude Code named session:

```
Issue #103 → session "py-issue-103"   ← full context preserved
Issue #107 → session "py-issue-107"   ← separate context
Back to #103 → /resume py-issue-103   ← context restored
```

The supervisor switches sessions via `/resume`. Each task gets its own context window. No blowup from accumulating unrelated work. Come back to an issue a week later — full context restored.

![Session-per-ticket](assets/8_sessions.png)

---

## Parallel Workers, Zero Conflicts

Code-writing roles get isolated environments with their own repo clones:

```
.octobots/workers/
├── py/
│   ├── core/          ← own git clone, own branch
│   ├── services/      ← own clones
│   ├── venv → shared  ← symlinked deps
│   └── .mcp.json      ← own Playwright browser
├── js/
│   └── ...            ← same structure
└── qa/
    └── ...            ← same structure
```

Each developer works on their own branch in their own directory. No merge conflicts during development. PRs are where integration happens.

Each worker even gets its own browser instance for Playwright testing — no more "agent A navigated and broke agent B's page."

---

## The Multi-Repo Reality

Most production projects aren't a single repo. My test project — OneTest — has **14 repositories**: a React frontend, 8 Python microservices, 3 shared libraries, a test automation suite, and deployment configs. All cloned into one workspace. No root git repo.

This breaks the common "git worktree" approach to worker isolation:

```
git worktree add .worktrees/py-issue-42    ← fails: workspace root isn't a git repo
```

Worktrees operate within a single repository. In a multi-repo workspace, there's nothing to branch from at the root level. I tried it — the supervisor logged `worktree creation failed` and workers couldn't start.

**The fix: full workspace clones per worker.** During `init-project.sh`, the setup script discovers all git repos in the workspace and clones each one into the worker's isolated directory:

```bash
# What init-project.sh does for each code worker
for repo in core services/gateway services/membership ...; do
    git clone $(git -C $repo remote get-url origin) .octobots/workers/py/$repo
done
```

The result: each worker has a complete copy of the workspace with independently branchable repos. Shared resources (venv, database, `.env`) are symlinked — not copied.

```
Shared (one copy):              Isolated (per worker):
├── venv/                       ├── core/          ← own clone
├── PostgreSQL                  ├── services/*/    ← own clones
├── .env                        ├── lib/*/         ← own clones
├── .mcp.json                   └── .mcp.json      ← own Playwright browser
└── octobots/
```

**Why not just use branches in shared repos?** Because two agents editing the same file in the same directory causes actual file conflicts — not merge conflicts, but "I saved my change and your change disappeared" conflicts. Git branches don't isolate the working tree.

**The trade-off is disk space.** Each worker clones 14 repos. For OneTest, that's ~200MB per worker × 3 workers = ~600MB. On modern machines with fast SSDs, this is negligible. The init takes about a minute (one-time) and workers are fully isolated after that.

**Database and ports:** Workers share the same dev database (different features touch different data). Each worker gets a unique port assignment in `.env.worker` so they can run the app independently for testing. Shared venv means no reinstalling packages — unless a worker's branch changes dependencies.

![Multi-repo worker isolation](assets/5_multirepo.png)

---

## The Audit Trail: GitHub Issues as Source of Truth

Every meaningful action gets a comment on the relevant GitHub issue:

```
octobotsai [bot]
📋 [pm] Assigned to py. Priority: high.

octobotsai [bot]
🐍 [py] Started. Approach: using existing auth middleware.

octobotsai [bot]
🐍 [py] Done. PR #45. JWT login + rate limiting. All tests pass.

octobotsai [bot]
🧪 [qa] Testing: 4 scenarios. Login, invalid creds, rate limit, token expiry.

octobotsai [bot]
🧪 [qa] ✅ Verified. All scenarios pass.
```

All posted by the `octobotsai` GitHub App — not your personal account. Full traceability, zero manual documentation.

![Octobots gitflow](assets/6_gitflow.png)

---

## The Shared Whiteboard

Teams need shared state beyond tickets. `BOARD.md` is the team's whiteboard — any role can read and write:

```markdown
## Active Work
| Role | Task | Issue | Status |
|------|------|-------|--------|
| py   | Login endpoint | #103 | PR submitted |
| js   | Login form | #104 | in progress |

## Decisions
- JWT over sessions (microservice architecture) — decided by tl

## Blockers
- qa: blocked on #103, waiting for py's PR to merge

## Shared Findings
- Auth middleware is deprecated — found by py, flagged for tl
```

---

## Framework vs Runtime: Clean Separation

```
octobots/              ← framework (git pull for updates, read-only)
├── roles/               base templates
├── skills/              11 shared skills
├── shared/              conventions, agents
└── scripts/             supervisor, bridge, relay

.octobots/             ← runtime (project-specific, workers write here)
├── board.md             team whiteboard
├── memory/py.md         Py remembers across sessions
├── workers/py/          isolated repo clones
├── roles/               override base roles
└── relay.db             taskbox database
```

`git pull` in `octobots/` is always safe. Project customizations in `.octobots/` are never touched. Override any base role by copying it to `.octobots/roles/` and editing.

---

## GitHub App: The Team's Identity

All comments show up as `octobotsai [bot]` — not your personal account. Each role prefixes its messages:

```
octobotsai [bot]
📋 [pm] Bug #41 received. Routed to tech lead.

octobotsai [bot]
🏗️ [tl] Root cause: fetchTags reducer doesn't unwrap .items. One-line fix.

octobotsai [bot]
🐍 [py] Fix applied. PR #45.

octobotsai [bot]
🧪 [qa] ✅ Verified. Tag autocomplete working.
```

Setting it up takes 5 minutes: create a GitHub App, generate a private key, add credentials to `.env.octobots`. The supervisor injects `GH_TOKEN` into every worker automatically.

---

## GitHub Projects: The Visual Task Board

Issues assigned to `octobotsai[bot]` get picked up automatically. A GitHub Projects board tracks the pipeline visually:

```
📥 Inbox    │ 🔍 Triage │ 🐍 Dev: py │ ⚡ Dev: js │ 🧪 Testing │ ✅ Done
────────────┼───────────┼────────────┼───────────┼───────────┼──────────
#45 new bug │ #42 pm    │ #41 py     │ #43 js    │ #39 qa    │ #38 ✓
#46 feature │           │            │           │           │ #37 ✓
```

The GitHub bridge polls the board, routes new inbox items to PM, and syncs column moves as workers progress. Drag a card manually? The bridge picks it up and notifies the right role.

---

## Setting Up for a New Project

```bash
# 1. Clone octobots into your project
git clone git@github.com:onetest-ai/octobots.git octobots

# 2. Install dependencies
pip install -r octobots/requirements.txt

# 3. Initialize runtime directory (creates .octobots/, clones repos for workers)
octobots/scripts/init-project.sh

# 4. Run the scout (explores codebase, generates AGENTS.md)
octobots/start.sh scout

# 5. Start the team
octobots/supervisor.sh
octobots> /bridge
octobots> /health

# 6. Talk to Max via Telegram
"Hey Max, we need to fix the tag autocomplete bug in issue #41"
```

From zero to a working AI team in 6 commands.

---

## Inspiration & Prior Art

Multi-agent coding is a fast-moving space. Octobots isn't the first — and it borrows ideas from projects that came before:

- **[OpenClaw SOUL.md](https://github.com/aaronjmars/soul.md)** — the personality file concept. "Your agent reads itself into being." Octobots adopted this directly.
- **[claude-squad](https://github.com/smtg-ai/claude-squad)** — tmux-based multi-Claude management. Proved the tmux model works for parallel agents.
- **[Composio Agent Orchestrator](https://github.com/ComposioHQ/agent-orchestrator)** — parallel coding agents with git worktrees, CI fixes, code reviews. The closest in spirit — framework-agnostic, production-focused.
- **[Agentrooms](https://github.com/baryhuang/claude-code-by-agents)** — desktop app for multi-agent Claude Code with @mentions routing. Polished UX approach.
- **[CrewAI](https://crewai.com)** — role-playing agent framework (44k+ GitHub stars). Pioneered the "agents with roles" concept.
- **[Anthropic's own work](https://www.anthropic.com/engineering/building-c-compiler)** — 16 parallel Claudes building a C compiler. Proved the scale works.
- **[GitHub Agentic Workflows](https://github.blog/ai-and-ml/automate-repository-tasks-with-github-agentic-workflows/)** — agents integrated into the SDLC. The direction the industry is heading.

**The main inspiration is [OpenClaw](https://openclaw.ai)** — but with a much simpler setup. No framework to learn, no SDK to integrate. Just markdown files, bash scripts, and a SQLite queue. The whole thing runs on a single Claude Code Max+ subscription ($200/month) — enough to handle a full team of 6 agents on a real project. That's cheaper than one junior developer's daily rate, running 24/7.

**Where Octobots differs from the rest:** It's not a framework or SDK — it's config files and scripts. You can read every file and understand what it does. Messenger-first (not desktop-app-first), multi-repo aware (tested on 14 repositories), and designed around the framework/runtime split so `git pull` never conflicts with your project state.

---

## What I Learned

**1. Personality matters.** A QA agent with "evidence-obsessed, screenshots everything" in its SOUL.md writes fundamentally better bug reports than one with generic instructions.

**2. "Act, don't ask" is critical.** Early versions had PM asking "should I route this?" — defeating the purpose. The fix: make autonomous action a top-level rule.

**3. Every message needs a response.** Workers would finish tasks but never ack. The pipeline stalled silently. Now it's a mandatory 3-step completion: comment on issue, ack taskbox, notify user.

**4. Testing must be mandatory.** Without explicit "you MUST test your changes" constraints, agents submit untested code. Now it's a MANDATORY section in every coding role.

**5. Session-per-ticket is the right model.** Context blowup was my biggest worry. Mapping GitHub issues to Claude Code sessions solved it completely.

**6. SQLite beats everything for IPC.** I considered MCP, WebSockets, Redis. A SQLite file with WAL mode handles concurrent access from 6+ processes perfectly. Zero dependencies, zero ops.

**7. Bot identity matters.** When all comments show as your personal account, you can't tell human from bot. A GitHub App gives the team its own identity — `octobotsai[bot]` with role prefixes. Clear audit trail.

**8. Deduplication is critical.** Multiple input channels (Telegram, GitHub assignment, taskbox) can trigger the same task twice. GitHub issue labels as source of truth + "check before starting" convention prevents duplicate work.

---

## The Stack

| Component | Technology | Why |
|-----------|-----------|-----|
| Agents | Claude Code (Opus) | Full coding capability + tool use |
| Communication | SQLite (taskbox) | Zero deps, concurrent safe, dead simple |
| Orchestration | Python + Rich TUI | Interactive supervisor with slash commands |
| Panes | tmux | Tiled dashboard, detach/attach, themed borders |
| User interface | Telegram Bot API | Mobile-friendly, reply routing, @aliases |
| Audit trail | GitHub Issues + App | `octobotsai[bot]` identity, full traceability |
| Task tracking | GitHub Projects v2 | Visual board, column-based pipeline |
| Skills | agentskills.io format | 11 skills, cross-tool compatible |
| Isolation | Git clones per worker | Parallel devs, own browsers, zero conflicts |
| Auth | GitHub App + JWT | Bot identity, auto-rotating tokens |

---

*Built with Claude Code. Orchestrated by Octobots. Tested on a real project with 14 repositories and 8 microservices.*
