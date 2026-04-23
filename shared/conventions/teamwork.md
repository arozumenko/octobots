# Teamwork Conventions

All octobots roles follow these conventions. Read this before starting work.

## Team Board

Read `.octobots/board.md` at the start of every session. Update it when you:
- **Start or finish** a task → update Active Work
- **Make a decision** → add to Decisions (include WHY)
- **Get blocked** → add to Blockers (include what you need and from whom)
- **Discover something non-obvious** → add to Shared Findings
- **Have an idea that's out of scope** → add to Parking Lot

The board is the team's shared memory. If you don't write it down, no one else knows.

## Three Communication Channels

1. **BOARD.md** — shared team state (decisions, blockers, active work, findings)
2. **Taskbox** — real-time nudges between roles (ephemeral)
3. **GitHub Issues** — permanent audit trail (every meaningful action gets a comment)

**Rule: decisions and findings → BOARD.md. Task status → GitHub Issues. Quick nudges → Taskbox.**

See `shared/conventions/audit-trail.md` for the full audit trail protocol.

## Issue References

Always include the issue number when communicating:
- Taskbox messages: "TASK-003 (#103) is ready for you"
- Commits: "Fix login validation (#103)"
- PRs: "Closes #103"
- Comments: reference parent epic, blocking issues, related work

## Deduplication: GitHub Issue = Source of Truth

Multiple inputs (Telegram, GitHub assignment, taskbox) can trigger the same task. Before starting work on any issue:

1. **Check the issue labels** — if `in-progress`, someone's already on it
2. **Check the comments** — if a role already posted "Started", don't duplicate
3. **Claim it** — when you start, immediately add `in-progress` label and comment "Started"

```bash
# Check before starting
gh issue view <NUMBER> --repo <REPO> --json labels,comments

# Claim when starting
gh issue edit <NUMBER> --repo <REPO> --add-label "in-progress"
gh issue comment <NUMBER> --repo <REPO> --body "🔧 **Started** by $OCTOBOTS_ID"
```

If you receive a task that's already in-progress, ack with: "Already being handled by [role]. Skipping."

## Status Labels

Use labels to track issue lifecycle:

```
ready → in-progress → review → testing → done
                                  ↓
                              bug-found → in-progress
```

Update labels when status changes:
```bash
gh issue edit 103 --add-label "in-progress" --remove-label "ready"
```

## Every Message Gets a Response

**Every taskbox message you receive MUST get a response.** No exceptions.

- **Task assignment** → ack when done (3-step completion)
- **Question from another role** → answer it via taskbox, even if the answer is "I don't know, ask tech-lead"
- **Status request** → respond with your current state
- **Bug report from QA** → acknowledge, say what you'll do about it

If you can't respond immediately (busy with another task), at least ack with: "Received, will look at this after I finish current task."

**Silence breaks the pipeline.** The sender doesn't know if you received the message, if you're working on it, or if you're stuck. Always respond.

## Per-Role Dispatch Rules

When the supervisor delivers a Taskbox message to a pane it appends a RULES
block that tells the agent what to do after completing the work.  By default
the block assumes a dev-workflow role: comment on the GitHub issue, ack via
`relay.py`, notify via MCP.

### RULES.md file

Each agent directory can contain a `RULES.md` file alongside `AGENT.md`:

```
.claude/agents/<role>/
├── AGENT.md       ← frontmatter: name, description, tools, theme …
├── SOUL.md        ← optional personality/voice
└── RULES.md       ← NEW: rules appended to every dispatched message
```

Worker-style roles (no shell access, no GitHub issues, ack via MCP tool)
create a `RULES.md` to replace the default dev-workflow block:

```
RULES: You MUST respond to this message.

Analyze the meal photo per the meal-analysis skill and call
submit_meal_analysis with your results. Optionally call notify() when done.
Ack is handled internally by submit_meal_analysis — you do not need to call
relay.py.
```

### Resolution order

The supervisor looks for `RULES.md` in this order:

1. `.octobots/roles/<role>/RULES.md` — project-local override (beats installed)
2. `.claude/agents/<role>/RULES.md` — installed agent default
3. `shared/default_rules.md` in the octobots repo — bundled fallback
4. Hardcoded string — last-resort fallback when the bundled file is absent

### Placeholder substitution

`RULES.md` content is rendered with `str.format_map` before it is appended to
the prompt.  Supported placeholders:

| Placeholder | Value |
|---|---|
| `{msg_id}` | The Taskbox message id |
| `{octobots_dir}` | Absolute path to the octobots installation directory |

Unknown placeholders are silently replaced with an empty string — your
template will not raise an error if a future supervisor version adds new
placeholders.

### Fallback behaviour

- If `RULES.md` is **absent** or **blank** (only whitespace), the bundled
  `shared/default_rules.md` is used.  Existing roles without a `RULES.md`
  continue to work identically.
- Create a non-empty `RULES.md` to fully replace the default block.
  There is no "extend" mode — if you provide `RULES.md` you own the entire
  rules string.
- To override the rules for a role you cannot modify upstream (e.g. a
  third-party agent), create `.octobots/roles/<role>/RULES.md` in your
  project — it takes priority over the installed agent's file.

## Agent Tool vs Taskbox

The Agent tool spawns sub-agents in YOUR context window. Taskbox sends messages to
other roles running in their OWN tmux panes with their OWN context.

**Use the Agent tool for:**
- Lightweight sub-tasks that belong to YOUR role (taskbox-listener, issue-reproducer, rca-investigator)
- Tasks that need YOUR context (reading files you already loaded, analyzing something you're looking at)

**Use taskbox for:**
- All work that belongs to another role (coding, testing, analysis, decomposition)
- Anything that should happen in another role's isolated workspace
- Task assignments, handoffs, status requests

**NEVER use the Agent tool to do another role's job.** The Agent tool does not have access
to the other role's worktree, memory, skills, or MCP servers. Work done via Agent
runs in your context, consumes your context window, and produces results that
are invisible to the role that should own them.

## Handoff Protocol

When passing work to another role:
1. Comment on the issue (record)
2. Update the label (status)
3. Send taskbox message (notification)

Never skip step 1. Steps 2-3 can be omitted for minor updates.

## Notify User (Telegram)

Any role can send a notification directly to the user via Telegram by calling
the `notify` MCP tool (`mcp__notify__notify`):

```
notify(message="Your message here")
notify(message="Report ready", file="/abs/path/to/report.md")
```

The tool reads `OCTOBOTS_ID` from env to tag the message with your role name,
and routes file attachments to photo / voice / audio / document automatically
based on extension. See `no-terminal-interaction.md` for details.

**When to notify:**
- Task completed
- Blocker that needs user decision
- Question that can't be resolved within the team
- Significant milestone

**Don't notify on:** every step, routine status, inter-role handoffs (use taskbox for those).

## Self-Improvement

You can create new skills and agents to extend your own capabilities or the team's. When you notice a repeating pattern, a workflow that could be automated, or a gap in the team's tooling — build it.

### Creating a Skill

If you find yourself repeating a multi-step workflow, extract it into a skill:

```bash
# Create in project-specific skills (shared with all roles)
mkdir -p .octobots/skills/my-new-skill
```

Write `SKILL.md` with YAML frontmatter and instructions. See `octobots/docs/skill-spec.md` for the full format:

```yaml
---
name: my-new-skill
description: >-
  What this skill does and when to use it.
  Use when the user asks to "do X" or "fix Y".
---

# My New Skill

Steps, commands, decision trees.
```

Then symlink it to your role (and any other roles that should have it):

```bash
ln -s ../../../../.octobots/skills/my-new-skill roles/<role>/.claude/skills/my-new-skill
```

### Creating an Agent

For specialized sub-tasks that benefit from isolation (separate context window, specific model):

```bash
mkdir -p .octobots/agents/my-agent
```

Write `AGENT.md`:

```yaml
---
name: my-agent
description: >-
  What this agent does. Use when [trigger conditions].
model: sonnet
tools: [Read, Grep, Glob, Bash]
---

# My Agent

Full instructions for the agent's task.
```

Symlink to roles that should have access:

```bash
ln -s ../../../../.octobots/agents/my-agent roles/<role>/.claude/agents/my-agent
```

### When to Create What

| You notice... | Create a... |
|--------------|-------------|
| Repeating multi-step workflow | Skill |
| Task that needs a separate context/model | Agent |
| Pattern that all roles should follow | Convention (in `shared/conventions/`) |
| Project-specific knowledge | Entry in your MEMORY.md |

### Picking Up New Capabilities

Claude Code discovers skills and agents **at session start only** — not mid-conversation. After creating a new skill or agent, request a restart:

```bash
python3 octobots/skills/taskbox/scripts/relay.py send --from $OCTOBOTS_ID --to supervisor "restart"
```

The supervisor will exit your session and relaunch you fresh. On restart you'll automatically discover everything in `.claude/skills/` and `.claude/agents/`.

### Guidelines

- **Name clearly** — `deploy-staging` not `helper`. The name is how others find it.
- **Write good descriptions** — include trigger phrases so the agent knows when to activate the skill.
- **Keep SKILL.md under 500 lines** — split details into `references/` subdirectory.
- **Test before sharing** — verify the skill works in your own session before symlinking to other roles.
- **Announce to the team** — post on `.octobots/board.md` when you create something useful so other roles know about it.
