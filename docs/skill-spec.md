# Octobots Skill Specification

Based on the [agentskills.io](https://agentskills.io) open standard. Skills work across Claude Code, Copilot, Cursor, and 35+ other agent products. Installable via `npx skills add <owner/repo>` from [skills.sh](https://skills.sh).

## File Structure

```
skill-name/
├── SKILL.md              # Required — metadata + instructions (agentskills.io spec)
├── setup.yaml            # Optional — octobots install config (MCP, permissions)
├── scripts/              # Optional — executable code (Python, Bash, JS)
├── references/           # Optional — detailed docs, loaded on demand
└── assets/               # Optional — templates, data files
```

Only `SKILL.md` is required. `setup.yaml` is an octobots extension — other agent clients ignore it. Everything else is progressive disclosure — loaded when the agent needs it, not at startup.

## SKILL.md Format

SKILL.md frontmatter follows the agentskills.io spec exactly. No extra fields.

```yaml
---
name: my-skill                    # Required. 1-64 chars, lowercase + hyphens only.
                                  # Must match directory name.
description: >-                   # Required. 1-1024 chars.
  What this skill does and when   # Include trigger phrases for discovery.
  to activate it. Use when the    # Write in third person.
  user asks to "do X" or "fix Y".
license: Apache-2.0               # Optional.
compatibility: Requires Python 3  # Optional. Max 500 chars. Plain string only.
metadata:                         # Optional. Flat key-value pairs only.
  author: octobots
  version: "1.0.0"
allowed-tools: Bash Read          # Optional/experimental. Space-delimited tool list.
---

# Skill Title

Concise instructions for the agent. This is Tier 2 content — loaded
when the skill activates, so keep it focused (<500 lines).

## How to Use

Steps, commands, decision trees. Reference files in `references/`
or `scripts/` for details — the agent reads them on demand (Tier 3).
```

## setup.yaml Format (Octobots Extension)

`setup.yaml` declares installation-time config read by `setup-skill.sh`. Other agent clients ignore this file. Place it alongside `SKILL.md` in the skill directory.

```yaml
# octobots-specific install config — ignored by non-octobots clients
dependencies:
  mcp:                            # MCP servers to merge into .mcp.json
    - name: my-server             # Key name in mcpServers{}
      command: npx                # Command to launch the server
      args: ["@scope/package"]    # Args array
      env: {}                     # Optional env vars

permissions:                      # Informational — shown to user before install
  filesystem: read-write          # read | read-write
  network: true                   # true | false
  shell: true                     # true | false
```

Rules:
- All fields are optional — a skill with no MCP deps needs no `setup.yaml` at all
- `mcp` entries map directly to `.mcp.json` `mcpServers` format
- `setup-skill.sh` merges MCP entries on install; existing entries with the same `name` are never overwritten (user config wins)
- `permissions` is informational only — displayed before install, not enforced at runtime

## Python Script Dependencies (PEP 723)

Declare Python dependencies inline in script files, not in frontmatter. This is the standard cross-client approach:

```python
# /// script
# dependencies = [
#   "httpx>=0.27.0",
#   "azure-identity>=1.15.0",
# ]
# ///

import httpx
# ... rest of script
```

`setup-skill.sh` detects PEP 723 blocks and runs `pip install` automatically. Scripts with PEP 723 blocks are self-contained — any tool can install and run them.

## npm / Node Dependencies

Use `npx` or `bunx` at runtime — no pre-declaration needed:

```bash
npx @playwright/mcp@latest
bunx some-tool
```

If a global install is required, declare it in `setup.yaml` under a `npm` key (octobots-only):

```yaml
dependencies:
  npm:
    - "@some/cli-tool"
```

## Naming Rules

- Lowercase letters, numbers, and hyphens only
- Cannot start or end with a hyphen
- No consecutive hyphens (`my--skill`)
- Cannot contain "anthropic" or "claude"
- Must match the parent directory name
- Prefer descriptive names: `pdf-processing` not `helper`

## Progressive Disclosure (3 Tiers)

| Tier | What | When Loaded | Budget |
|------|------|-------------|--------|
| 1. Catalog | `name` + `description` | Session startup | ~50-100 tokens |
| 2. Instructions | Full SKILL.md body | Skill activated | <5000 tokens |
| 3. Resources | scripts/, references/ | On demand | Varies |

The description is always in memory. Keep it specific enough for accurate activation but short enough to not waste context.

## Writing Good Instructions

1. **Be concise** — every token competes with conversation history
2. **Set degrees of freedom** appropriately:
   - High freedom: text instructions (multiple valid approaches)
   - Medium: pseudocode with parameters
   - Low: exact scripts (for fragile operations like DB migrations)
3. **Keep SKILL.md under 500 lines** — split details into `references/`
4. **Assume the agent is smart** — don't over-explain basic concepts
5. **Use `scripts/` for executable code** — declare Python deps via PEP 723
6. **Use forward slashes** in file paths (never backslashes)

## Installation

Skills are discovered from these locations (in precedence order):

```
.agents/skills/skill-name/        # Cross-client standard (all agents)
.claude/skills/skill-name/        # Claude Code specific
~/.agents/skills/skill-name/      # User-level, cross-client
~/.claude/skills/skill-name/      # User-level, Claude Code
```

Symlinks work — keep source in `octobots/skills/`, symlink into `.claude/skills/`:

```bash
ln -s /path/to/octobots/skills/my-skill .claude/skills/my-skill
```

Install from registry:

```bash
npx skills add arozumenko/skill-msgraph
```

## Validation Checklist

- [ ] Directory name matches `name` in frontmatter
- [ ] File is named `SKILL.md` (uppercase)
- [ ] `name` is lowercase, hyphens only, 1-64 chars
- [ ] `description` is 1-1024 chars with trigger phrases
- [ ] SKILL.md body is under 500 lines
- [ ] No `dependencies` or `permissions` blocks in SKILL.md frontmatter — those go in `setup.yaml`
- [ ] Python script deps declared via PEP 723 `# /// script` blocks, not in frontmatter
- [ ] `setup.yaml` present if skill requires MCP servers
- [ ] No hardcoded absolute paths in instructions
- [ ] Tested with target model (Haiku/Sonnet/Opus)
- [ ] Validate with: `npx skills check`

## Example: Minimal Skill

```
hello-world/
└── SKILL.md
```

```yaml
---
name: hello-world
description: Responds with a friendly greeting. Use when the user says "hello" or "hi".
---

# Hello World

Greet the user warmly. If you know their name from conversation context, use it.
```

## Example: Skill with Scripts and MCP

```
playwright-testing/
├── SKILL.md
├── setup.yaml
└── scripts/
    └── run-tests.py
```

```yaml
---
name: playwright-testing
description: UI/E2E test automation with Playwright. Use when the user asks to "test the UI", "automate browser tests", or "write E2E tests".
compatibility: Requires Node.js 18+
metadata:
  author: octobots
  version: "1.0.0"
---

# Playwright Testing

Browser-based UI and E2E testing via the Playwright MCP server.
```

```yaml
# setup.yaml
dependencies:
  mcp:
    - name: playwright
      command: npx
      args: ["@playwright/mcp@latest"]
      env: {}
permissions:
  network: true
  shell: false
```

```python
# scripts/run-tests.py
# /// script
# dependencies = ["pytest>=8.0.0"]
# ///

import subprocess
# ...
```
