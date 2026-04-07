# Running Octobots against a local Ollama model

Claude Code talks to Anthropic's Messages API. Ollama exposes an OpenAI-compatible
API, not Anthropic-compatible — so you need a tiny translation proxy in between.
Once that proxy is running, octobots can point every role at it via three env
vars in `.env.octobots`. No code changes per role.

## 1. Run Ollama

```bash
ollama serve                     # default: http://localhost:11434
ollama pull qwen2.5-coder:32b    # or llama3.1:70b, deepseek-coder-v2, etc.
```

Pick a model that's actually competent at tool use — Claude Code leans hard on
function calling. `qwen2.5-coder`, `llama3.1`, and `deepseek-coder-v2` are the
usual suspects. Smaller models will technically launch but tend to fall over
on multi-step tool chains.

## 2. Run an Anthropic-compatible proxy

Either of these works. Pick one.

### Option A — claude-code-router (purpose-built)

```bash
npm install -g @musistudio/claude-code-router
ccr start                        # listens on http://localhost:8080
```

Configure `~/.config/claude-code-router/config.json` to route to Ollama:

```json
{
  "Providers": [{
    "name": "ollama",
    "api_base_url": "http://localhost:11434/v1/chat/completions",
    "api_key": "ollama",
    "models": ["qwen2.5-coder:32b"]
  }],
  "Router": { "default": "ollama,qwen2.5-coder:32b" }
}
```

### Option B — LiteLLM (general-purpose)

```bash
pip install 'litellm[proxy]'
litellm --model ollama/qwen2.5-coder:32b --port 8080
```

LiteLLM exposes an Anthropic-compatible endpoint at `/v1/messages` when called
with `ANTHROPIC_BASE_URL=http://localhost:8080`.

## 3. Tell octobots to use it

Add to `.env.octobots` in the project root:

```bash
OCTOBOTS_LLM_PROVIDER=ollama
OCTOBOTS_OLLAMA_BASE_URL=http://localhost:8080
OCTOBOTS_OLLAMA_MODEL=qwen2.5-coder:32b
```

That's it. `octobots/start.sh <role>` and `python3 octobots/scripts/supervisor.py`
both load `.env.octobots` and forward the resulting `ANTHROPIC_BASE_URL`,
`ANTHROPIC_AUTH_TOKEN`, and `ANTHROPIC_MODEL` to every Claude Code instance they
spawn — including tmux worker panes.

## Advanced — full passthrough

If you want to bypass the shortcut (e.g. mix providers per role, point at a
remote vLLM box, use Anthropic for some roles and Ollama for others), just set
the raw vars in `.env.octobots`:

```bash
ANTHROPIC_BASE_URL=http://my-proxy.lan:4000
ANTHROPIC_AUTH_TOKEN=sk-anything
ANTHROPIC_MODEL=qwen2.5-coder:32b
ANTHROPIC_SMALL_FAST_MODEL=qwen2.5-coder:7b
```

Per-role overrides: drop a role-specific `.env` next to the worker
(`.octobots/workers/<role>/.env`) — it's symlinked from project root, so
project-wide settings apply unless you replace the symlink with a real file.

## Caveats

- Claude Code's agent loop expects strong tool-use behavior. Local models
  drop tool calls, hallucinate file paths, and miscount line numbers more
  often than Sonnet. Treat ollama-backed roles as "best effort" — fine for
  scout / BA / drafting, rough for QA / tech-lead.
- Subagents (`Agent` tool) inherit the same provider, so a slow local model
  multiplies its latency cost when delegated to.
- Telegram + GitHub bridges are unaffected; they don't go through the LLM.
