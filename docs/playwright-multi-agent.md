# Playwright with Multiple Agents

## The Problem

If `.mcp.json` uses `--cdp-endpoint`, all agents share one browser instance. When agent A navigates to page X, agent B's page changes too. Tests interfere with each other.

## The Fix

Remove `--cdp-endpoint` from `.mcp.json`. Each Claude Code instance spawns its own Playwright MCP process (via stdio), which launches its own headless browser. Zero sharing, zero conflict.

### Shared browser (BAD for multi-agent):
```json
{
  "playwright": {
    "type": "stdio",
    "command": "npx",
    "args": ["@playwright/mcp@latest", "--cdp-endpoint=http://localhost:9222"]
  }
}
```

### Isolated browsers (GOOD for multi-agent):
```json
{
  "playwright": {
    "type": "stdio",
    "command": "npx",
    "args": ["@playwright/mcp@latest", "--headless"]
  }
}
```

Each worker gets its own browser because:
- Playwright MCP runs as a **stdio** server — one process per Claude Code instance
- Each process launches its own Chromium
- No shared state between workers

### Headed mode for debugging (optional)

If you want to SEE what a specific agent is doing, use `--cdp-endpoint` in that agent's `.mcp.json` only. In the isolated worker setup (`.octobots/workers/<role>/`), each worker has its own `.mcp.json`:

```bash
# Override just QA's Playwright to use a visible browser
cat > .octobots/workers/qa-engineer/.mcp.json << 'EOF'
{
  "mcpServers": {
    "playwright": {
      "type": "stdio",
      "command": "npx",
      "args": ["@playwright/mcp@latest", "--cdp-endpoint=http://localhost:9222"]
    }
  }
}
EOF
```

Other workers keep using headless.

## Port Conflicts

Headless Playwright doesn't use ports — each instance is fully isolated. No port management needed.

If you DO need CDP for debugging multiple agents simultaneously:
```
qa-engineer: --cdp-endpoint=http://localhost:9222
js-dev:      --cdp-endpoint=http://localhost:9223
python-dev:  --cdp-endpoint=http://localhost:9224
```

But this is rarely needed. Headless mode handles 99% of cases.
