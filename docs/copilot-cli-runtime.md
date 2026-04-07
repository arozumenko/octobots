# Mixing GitHub Copilot CLI and Claude Code in one team

Octobots supports two agent runtimes per role: **Claude Code** (default) and
**GitHub Copilot CLI** (opt-in). A team can mix them freely — e.g. run the
project manager on Claude Sonnet, the BA on Copilot's `gpt-5`, and the dev on
Claude Opus, all in the same supervisor.

## How to opt a role into Copilot CLI

Add one line to that role's `AGENT.md` frontmatter:

```yaml
---
name: ba
description: Business analyst — turns vague asks into crisp user stories.
runtime: copilot          # ← this is the only required change
model: gpt-5              # optional; defaults to Copilot's account default
---
```

That's it. Both `octobots/start.sh ba` and `python3 octobots/scripts/supervisor.py`
will detect `runtime: copilot` and launch the role with `copilot --agent ba --allow-all`
instead of `claude --agent ba --dangerously-skip-permissions`. Roles without a
`runtime:` field stay on Claude Code — no migration needed.

## What happens under the hood

1. **Frontmatter translation.** `scripts/sync-copilot-agents.py` reads the
   octobots `AGENT.md` and writes a Copilot-flavored `.agent.md` into
   `$COPILOT_HOME/agents/<role>.agent.md` (default `~/.copilot/agents/`). It
   keeps `name`, `description`, `model`, `tools`, `mcp-servers`, etc., and
   drops Claude-only fields like `color`, `workspace`, and `skills` (preserved
   as a comment block in the output for traceability). Claude model aliases
   (`sonnet`, `opus`, `haiku`) are mapped to their Copilot string forms; any
   unknown model name is passed through unchanged so users can write
   `gpt-5`, `claude-sonnet-4.5`, etc., directly.
2. **Authentication.** Copilot CLI reads `GH_TOKEN` (or `GITHUB_TOKEN`).
   Octobots already provisions a token for the `gh` CLI via `gh-token.py`,
   so the supervisor and `start.sh` reuse it — no extra config.
3. **Headless mode.** `--allow-all` is Copilot's equivalent of Claude's
   `--dangerously-skip-permissions`. Required because roles run in unattended
   tmux panes and must never block on a permission prompt.
4. **Same env contract.** `OCTOBOTS_ID`, `OCTOBOTS_DB`, and the taskbox /
   notify-user / GitHub bridges all work identically — they're shell scripts
   that read env vars, not anything runtime-specific. The three-step task
   completion ritual (issue comment → ack → notify-user) is unchanged.

## Capability matrix

| Concept | Claude Code | Copilot CLI | Notes |
|---|---|---|---|
| Binary | `claude` | `copilot` | Both must be on `PATH` if you mix |
| Auth | Anthropic OAuth / `ANTHROPIC_*` | `GH_TOKEN` env | Reused from `gh-token.py` |
| Skip approvals | `--dangerously-skip-permissions` | `--allow-all` / `--yolo` | Equivalent |
| Custom agent file | `.claude/agents/<role>/AGENT.md` | `$COPILOT_HOME/agents/<role>.agent.md` | Auto-translated on launch |
| Agent select flag | `--agent <name>` | `--agent <name>` | Same flag name, lucky |
| One-shot prompt | `-p` / `--print` | `--prompt` | Both supported |
| Project instructions | `CLAUDE.md` | `AGENTS.md` | If you symlink one to the other, both runtimes will load it |
| MCP config | `.mcp.json` | per-agent `mcp-servers:` frontmatter or `$COPILOT_HOME/mcp.json` | **No automatic translation yet** — see gaps |
| Subagent (`Agent` tool) | built-in | not equivalent | Copilot roles can't delegate to subagents |
| Session resume by name | `claude --resume <name>` | `/resume` interactive picker | **session-per-issue pattern needs adaptation** for Copilot roles |
| Octobots skills | `.claude/skills/*/SKILL.md` symlinks | Not auto-mounted | Skills that are pure instructions still work via the agent body; skills that depend on `.claude/skills/` symlinks won't |

## Known gaps (read before assigning critical work to a Copilot role)

1. **Session-per-issue.** Octobots uses `claude --resume python-dev-issue-103`
   to restore full per-issue context. Copilot CLI's `/resume` is an interactive
   picker, not a name lookup. Until we wire something up (probably stash the
   Copilot session ID in the taskbox row keyed by issue number), Copilot roles
   start fresh per invocation. Use them for stateless tasks first: BA story
   drafting, code review, scout discovery — not long-lived multi-day fix work.
2. **MCP translation.** `.mcp.json` is Claude-format. Copilot wants either
   per-agent `mcp-servers:` blocks or its own `$COPILOT_HOME/mcp.json`. Today
   Copilot roles get only the MCP servers their `.agent.md` declares. The
   Playwright / GitHub / Context7 / Tavily / Lighthouse MCPs from `.mcp.json`
   will need to be added to each Copilot agent's frontmatter (or a shared
   `$COPILOT_HOME/mcp.json`) manually.
3. **Subagents.** Roles that delegate via the `Agent` tool (e.g. PM spawning
   `taskbox-listener`, scout calling `Explore`) will not work as Copilot
   roles, because Copilot CLI doesn't expose an equivalent subagent
   primitive. Keep these on Claude.
4. **Skills system.** Octobots' `skills/` directory is symlinked into
   `.claude/skills/` for each Claude worker. Copilot has its own `SKILL.md`
   spec and a different lookup path. Most octobots "skills" are really
   markdown instructions plus helper scripts — those still work because the
   scripts are invoked via shell, not by the agent runtime. Skills that
   depend on the runtime auto-discovering `SKILL.md` files (not many today)
   will silently no-op for Copilot roles.
5. **Model strings.** The translator maps `sonnet/opus/haiku` to
   `claude-sonnet-4.5/claude-opus-4.5/claude-haiku-4.5`. If Copilot renames
   them, edit `MODEL_MAP` in `scripts/sync-copilot-agents.py`. Setting
   `model:` to a literal Copilot model name (`gpt-5`, `o4-mini`, etc.)
   bypasses the map.

## Quick test

```bash
# Translate one role and inspect the output
python3 octobots/scripts/sync-copilot-agents.py \
    octobots/roles/personal-assistant \
    --copilot-home /tmp/copilot-test

cat /tmp/copilot-test/agents/personal-assistant.agent.md
```

To actually launch a Copilot-backed role, edit its `AGENT.md` to add
`runtime: copilot`, then:

```bash
octobots/start.sh personal-assistant --print   # inspect the env+command
octobots/start.sh personal-assistant           # launch for real
```

## When to use which runtime

| Role kind | Recommended runtime | Why |
|---|---|---|
| Long-lived dev / QA owning multi-day issues | Claude Code | Session-per-issue resume + subagents |
| PM / orchestrator using `Agent` delegation | Claude Code | Subagent tool |
| Scout / BA / one-shot reviewers | Either | Both fine; Copilot may be cheaper |
| Roles where `gh` integration matters (PR review, issue triage) | Copilot CLI | Native GitHub auth and surfaces |
| Roles that need a non-Anthropic model (`gpt-5`, etc.) | Copilot CLI | Direct access without a proxy |
| Local / offline work | Claude Code + Ollama (see `docs/ollama.md`) | Copilot CLI requires github.com auth |

## Future work

- Resolve the session-per-issue gap (probably: store Copilot session IDs in
  the taskbox, look them up on `/resume`).
- Auto-translate `.mcp.json` → `$COPILOT_HOME/mcp.json` on supervisor start.
- Bundle a `runtime: copilot` example role (e.g. `code-reviewer`) so users
  have a known-good template.
