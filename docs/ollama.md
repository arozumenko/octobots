# Running Octobots roles on a local Ollama model

As of Ollama v0.20+, Ollama exposes an Anthropic-compatible API directly and
ships an `ollama launch claude` wrapper that boots real Claude Code against a
local model with no proxy, no env-var juggling, and no extra binaries.

Octobots integrates this at the role level: you opt **specific roles** into
Ollama via `.env.octobots`, and they get wrapped with `ollama launch claude`
automatically. Other roles in the same supervisor session keep talking to
cloud Claude (or Copilot CLI). Mixed teams just work.

## Quick start (PA on local Gemma 4, rest of the team on cloud)

```bash
# 1. On the box that will run the supervisor
ollama pull gemma4:26b
ollama serve                    # leave running; listens on :11434
```

```bash
# 2. Add three lines to .env.octobots in your project root
OCTOBOTS_OLLAMA_ROLES=personal-assistant
OCTOBOTS_OLLAMA_MODEL=gemma4:26b
# (optional) per-role overrides — uppercase the role, dashes → underscores:
# OCTOBOTS_OLLAMA_MODEL_PERSONAL_ASSISTANT=gemma4:26b
```

```bash
# 3. Boot
python3 octobots/scripts/supervisor.py --workers personal-assistant
# /bridge        ← start Telegram bridge when ready
```

That's it. The PA worker will be launched as:

```
ollama launch claude --model gemma4:26b --yes -- \
    --agent personal-assistant --dangerously-skip-permissions
```

…which is exactly the command you'd type by hand to run Claude Code against
Ollama, just with octobots' env vars (`OCTOBOTS_ID`, `OCTOBOTS_DB`, etc.)
preserved so taskbox / notify-user / GitHub bridges keep working.

## How role selection works

| `.env.octobots` setting | Effect |
|---|---|
| _unset_ | All roles run on cloud Claude (default behavior, unchanged) |
| `OCTOBOTS_OLLAMA_ROLES=personal-assistant` | Only PA runs locally; everyone else on cloud |
| `OCTOBOTS_OLLAMA_ROLES="personal-assistant ba"` | PA + BA local, dev + PM + tech-lead + QA cloud |
| `OCTOBOTS_OLLAMA_MODEL=gemma4:26b` | Default model for everyone in the list above |
| `OCTOBOTS_OLLAMA_MODEL_PERSONAL_ASSISTANT=qwen2.5:32b` | Per-role override (PA gets Qwen, others get the default) |

The role-name → env-var transform is straightforward: uppercase, dashes
become underscores. `personal-assistant` → `OCTOBOTS_OLLAMA_MODEL_PERSONAL_ASSISTANT`.
`tech-lead` → `OCTOBOTS_OLLAMA_MODEL_TECH_LEAD`.

Per-role overrides are read first; if unset, the role falls back to
`OCTOBOTS_OLLAMA_MODEL`. If that's also unset for a role that's listed in
`OCTOBOTS_OLLAMA_ROLES`, the role launches with no model — which `ollama
launch` will reject. Set `OCTOBOTS_OLLAMA_MODEL` as the default to avoid this.

## Sanity check

`octobots/start.sh personal-assistant --print` shows the resolved command
without launching it. Tokens are auto-redacted, so it's safe to paste:

```bash
OCTOBOTS_OLLAMA_ROLES=personal-assistant \
OCTOBOTS_OLLAMA_MODEL=gemma4:26b \
octobots/start.sh personal-assistant --print
```

Expected output ends with:

```
… ollama launch claude --model gemma4:26b --yes -- --agent personal-assistant --dangerously-skip-permissions
```

If you see plain `claude --agent …` instead, the role isn't matched against
`OCTOBOTS_OLLAMA_ROLES` — check spelling and that `.env.octobots` is being
loaded.

## Picking a model

Claude Code leans hard on tool use. Smaller models will technically launch
but tend to fall over on multi-step tool chains. Reasonable starting points:

| Model | Size | Good for |
|---|---|---|
| `gemma4:26b` | ~18 GB | PA, journaling, summarization, knowledge-base curation |
| `gemma4:31b` | ~20 GB | Same, with a bit more headroom |
| `qwen2.5-coder:32b` | ~20 GB | Dev / QA roles where code edits matter |
| `llama3.1:70b` | ~40 GB | Strongest tool-use among local options |
| `kimi-k2.5:cloud` | _cloud_ | Ollama Cloud — bigger, faster, still routed via `ollama launch` |

The "right" model depends on what each role does. PA (this guide's example)
mostly summarizes and writes notes — Gemma 4 26B is fine. Don't put a
tech-lead role on a 7B model and expect it to write production-ready epics.

## Caveats

- **`ollama serve` must be running** on the same box as the supervisor (or
  reachable on the network — set `OLLAMA_HOST` if remote).
- **First launch downloads tens of GB.** Pull the model ahead of time with
  `ollama pull <tag>` so the first agent boot isn't a 20-minute wait.
- **Octobots subagents inherit the same model.** When a role spawns a
  subagent (e.g. PM delegating to `taskbox-listener`), the subagent runs on
  the *same* local model — multiplying its latency. If you orchestrate
  heavily with subagents, keep the orchestrator on cloud Claude and put
  the leaf workers on local models.
- **Telegram + GitHub bridges are unaffected.** They don't go through the
  LLM at all — they're shell scripts reading env vars.

## Alternative: standalone, no octobots

If you just want to verify Ollama + Claude Code work on your box before
plugging into octobots:

```bash
ollama launch claude --model gemma4:26b
```

That drops you into a normal Claude Code session backed by Gemma 4. No
octobots, no taskbox, no roles — just the plain CLI. Useful as a smoke test.
