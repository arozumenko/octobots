# Session Lifecycle

Roles run as **long-lived processes** — the terminal stays open. But work happens in **units**, and context must be cleared between units to prevent bloat.

**The process persists. The context resets.**

## Session Boundaries Per Role

| Role | Session = | Start By | End When |
|------|-----------|----------|----------|
| **Scout** | One project seed | `start.sh scout` | AGENTS.md + .octobots/ generated, team notified |
| **BA** | One epic decomposition | PM sends goal via taskbox | All stories created with ACs, handed to tech lead |
| **Tech Lead** | One story decomposition | BA sends stories via taskbox | All tasks created with deps, handed to PM |
| **PM** | One check-in cycle | User asks for status, or inbox has updates | Status reported, tasks distributed, blockers routed |
| **Python Dev** | One task | PM assigns via taskbox | Task done, PR submitted, issue commented, PM notified |
| **JS Dev** | One task | PM assigns via taskbox | Task done, PR submitted, issue commented, PM notified |
| **QA** | One test session | PM routes completed work | All tests run, bugs filed, results commented on issues |

## Session Lifecycle

### 1. Start — Restore Context (30 seconds)

Every session begins the same way:

```
Read MEMORY.md           ← what I learned before
Read AGENTS.md           ← project context
Check taskbox inbox      ← what's waiting for me
Check GitHub issues      ← current state of my work
```

This replaces conversation history. You don't need to remember the last session — the external state tells you everything.

### 2. Work — One Unit

Do the work for your unit. Stay focused:
- Developer: one task, not three
- BA: one epic, not the whole roadmap
- PM: one check-in cycle, not the whole sprint

### 3. Checkpoint — Save Before Context Reset

Before resetting context, persist everything important:

```
Update MEMORY.md         ← new learnings, gotchas, decisions
Comment on GitHub issues ← work done, status changes
Send taskbox messages    ← notify next role in the pipeline
Update issue labels      ← reflect current status
```

### 4. Signal — Tell the User

After checkpoint, tell the user: **"Unit complete. Ready for `/clear` when you want to start the next unit."**

Claude Code cannot call `/clear` or `/compact` on itself — these are user-initiated commands. So the role explicitly signals when a context reset would be beneficial.

### 5. Context Management

**What happens automatically:**
- **Auto-compact** fires when context approaches the window limit — no action needed
- **CLAUDE.md** (role definition) always survives compaction — it's reloaded automatically
- **PostCompact hooks** re-inject critical reminders after auto-compact

**What the user does:**
- `/clear` between units of work (role will signal when ready)
- `/compact` mid-unit if the role reports context pressure
- `/context` to check current usage

**What the role does:**
- Persist learnings to MEMORY.md BEFORE context fills up — don't wait
- Keep GitHub issues updated — they're the external memory
- Signal the user when a unit is complete and context reset would help
- After auto-compact: re-read MEMORY.md to restore learned context

### 6. Wait for Next Work

After the user clears context, the role starts fresh:
- Reads MEMORY.md, checks inbox, checks issues
- **Developers/QA**: Taskbox polling via `/loop` or manual inbox check
- **PM**: User prompt or periodic check-in
- **BA/Tech Lead**: Taskbox message from upstream role

## What Goes Where

| Information | Lives In | Why |
|-------------|----------|-----|
| Task status, comments, evidence | GitHub Issues | Permanent, auditable, shared |
| Learnings, patterns, gotchas | MEMORY.md | Survives across sessions |
| "Hey, your task is ready" | Taskbox | Real-time notification |
| Implementation details | Code + PR | Where it belongs |
| Conversation history | Nowhere | Disposable, not persisted |

**Conversation context is ephemeral.** Anything worth keeping goes to MEMORY.md or GitHub Issues before the session ends.

## Session-per-Ticket Architecture

The supervisor maps GitHub issues to Claude Code named sessions:

```
Issue #103 → session "python-dev-issue-103" → full context preserved
Issue #104 → session "python-dev-issue-104" → separate context
Issue #103 again → resume "python-dev-issue-103" → picks up where left off
```

**How it works:**
- PM sends a task via taskbox with the issue number: "Work on #103: implement login"
- Supervisor extracts `#103`, maps to session `python-dev-issue-103`
- Supervisor sends `/resume python-dev-issue-103` to the role's tmux pane
- If session exists: full context restored — picks up where it left off
- If new: Claude creates a fresh session automatically
- Previous session stays intact on disk — no `/clear` needed
- `/resume` switches sessions without destroying anything

**Benefits:**
- No context blowup — each task has its own context window
- No `/clear` needed — switching sessions does it automatically
- Resumable — come back to a task days later with full context
- Auditable — session maps 1:1 to GitHub issue

**Task messages MUST include issue number** for session mapping:
```
"TASK-003 (#103): Implement login endpoint. See issue for details."
          ^^^^
          supervisor extracts this
```

If no issue number is found, a unique session is created per message.

## Worktree Isolation

Code-writing roles (python-dev, js-dev, qa-engineer) get their own git worktree per task:

```
project/                              ← main branch (PM, BA, TL work here)
.worktrees/
├── python-dev-issue-103/             ← branch feat/issue-103
│   ├── src/                          ← full repo copy
│   ├── .venv → ../../.venv           ← symlinked if deps unchanged
│   ├── .env.shared → ../../.env.shared
│   └── .env.local                    ← unique port, test schema
├── js-dev-issue-104/                 ← branch feat/issue-104
└── qa-engineer-issue-103/            ← branch feat/issue-103 (testing Py's work)
```

**Why:** Two devs editing the same files = merge conflicts. Worktrees give each worker their own copy on their own branch. Conflicts resolve at PR merge time, not during development.

**Environment per worktree:**
- `.env.shared` → symlinked (same API keys, DB URL)
- `.env.local` → generated (unique port, worker ID, test schema)
- `.venv` / `node_modules` → symlinked if deps match, fresh install if not

**Lifecycle:**
1. Task arrives → `worktree-manager.sh create worker-id issue-number`
2. Worker works in the worktree on branch `feat/issue-NNN`
3. Worker commits, pushes, creates PR
4. After merge → `worktree-manager.sh destroy worker-id issue-number`

**Non-code roles** (PM, BA, tech lead, scout) work from the main directory — they don't edit code.

## Mid-Unit Context Pressure

You cannot call `/compact` yourself. But you can **signal the user** when context is getting heavy:

> "I've read 12 files and context is getting large. Good point to `/compact` if you want to free space before I start the implementation."

**When to signal:**
- After reading 10+ files
- After 20+ tool calls in one unit
- Before switching from exploration to implementation
- When you notice you're re-reading files you already read (sign of compaction having dropped them)

**What auto-compact preserves:**
- CLAUDE.md (your role definition) — always reloaded
- Recent messages and tool results
- Key code snippets

**What auto-compact may lose:**
- File contents read early in the session
- Detailed tool outputs from many steps ago
- Reasoning from early exploration

**Mitigation:** Save important findings to MEMORY.md or GitHub issue comments BEFORE context fills up. Don't wait for the end of the unit.

## MEMORY.md Hygiene

MEMORY.md is NOT a session log. It's a knowledge base.

**DO save:**
- Project patterns discovered: "auth uses middleware X, not decorator Y"
- Team dynamics: "python-dev prefers tasks with clear interface contracts"
- Gotchas: "the test DB resets on every CI run — don't depend on seed data"
- Decisions: "we chose JWT over sessions because of microservice architecture"

**DON'T save:**
- "Today I worked on task #103" (that's in the issue)
- "I sent a message to py via taskbox" (that's in taskbox)
- "The tests passed" (that's in the PR/issue)

Keep MEMORY.md under 50 lines. Prune old entries when adding new ones.

## Multi-Task Flow

When a developer has multiple tasks in the queue, process them **sequentially with context resets**:

```
1. Check inbox → claim task A
2. Do task A
3. Checkpoint (issue comment, notify PM, update MEMORY.md)
4. Signal user: "Task A complete. /clear recommended before next task."
5. [user runs /clear]
6. Read MEMORY.md → check inbox → claim task B
7. Do task B
8. Checkpoint
9. Signal user: "Task B complete. /clear recommended."
```

**Signal `/clear` between tasks.** Task A's context (file reads, diffs, errors) is irrelevant to task B and wastes context window. Fresh start = better work.

If the user doesn't `/clear`, the role should still proceed but note that context is carrying previous task baggage.

Never work on two tasks simultaneously. Context pollution is worse than sequential slowdown.

## PM Session Pattern (Most Complex)

PM sessions are unique — they're coordination, not implementation:

```
1. Check inbox             ← responses from team
2. Check GitHub issues     ← current state
3. Compile status          ← what's done/blocked/pending
4. Distribute new tasks    ← from tech lead's queue
5. Route blockers          ← to the right person
6. Report to user          ← summary
7. Update MEMORY.md        ← team dynamics, velocity notes
8. Exit
```

PM sessions should be SHORT. `/clear` after every check-in cycle. If you're in a session for more than 10 minutes, you're doing someone else's job.
