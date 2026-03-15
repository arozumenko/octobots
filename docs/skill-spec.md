# Octobots Skill Specification

Based on the [agentskills.io](https://agentskills.io) open standard. Skills work across Claude Code, Copilot, Cursor, and 35+ other agent products.

## File Structure

```
skill-name/
├── SKILL.md              # Required — metadata + instructions
├── scripts/              # Optional — executable code (Python, Bash, JS)
├── references/           # Optional — detailed docs, loaded on demand
└── assets/               # Optional — templates, data files
```

Only `SKILL.md` is required. Everything else is progressive disclosure — loaded when the agent needs it, not at startup.

## SKILL.md Format

```yaml
---
name: my-skill                    # Required. 1-64 chars, lowercase + hyphens only
                                  # Must match directory name
description: >-                   # Required. 1-1024 chars
  What this skill does and when   # Include trigger phrases for discovery
  to activate it. Use when the    # Write in third person
  user asks to "do X" or "fix Y".
license: Apache-2.0               # Optional
compatibility: Requires Python 3  # Optional. Max 500 chars
metadata:                          # Optional key-value pairs
  author: octobots
  version: "1.0.0"
---

# Skill Title

Concise instructions for the agent. This is Tier 2 content — loaded
when the skill activates, so keep it focused (<500 lines).

## How to Use

Steps, commands, decision trees. Reference files in `references/`
or `scripts/` for details — the agent reads them on demand (Tier 3).
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
5. **Use `scripts/` for executable code** — self-contained, document dependencies
6. **Use forward slashes** in file paths (never backslashes)

## Installation

Skills are discovered from these locations:

```
~/.claude/skills/skill-name/SKILL.md          # Personal (all projects)
.claude/skills/skill-name/SKILL.md            # Project (this repo only)
```

Symlinks work — keep source in `octobots/skills/`, symlink into `.claude/skills/`:

```bash
ln -s /path/to/octobots/skills/my-skill .claude/skills/my-skill
```

## Validation Checklist

- [ ] Directory name matches `name` in frontmatter
- [ ] File is named `SKILL.md` (uppercase)
- [ ] `name` is lowercase, hyphens only, 1-64 chars
- [ ] `description` is 1-1024 chars with trigger phrases
- [ ] SKILL.md body is under 500 lines
- [ ] Scripts use stdlib or document dependencies in `compatibility`
- [ ] No hardcoded absolute paths in instructions
- [ ] Tested with target model (Haiku/Sonnet/Opus)

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

## Example: Skill with Scripts

```
db-migrate/
├── SKILL.md
├── scripts/
│   └── migrate.sh
└── references/
    └── schema-conventions.md
```

```yaml
---
name: db-migrate
description: Generate and run database migrations. Use when the user asks to "add a column", "create a table", or "migrate the database".
compatibility: Requires PostgreSQL client (psql) and Python 3.10+
metadata:
  author: platform-team
  version: "2.0.0"
---

# Database Migrations

Generate migration files following project conventions. See `references/schema-conventions.md` for naming and type rules.

## Steps

1. Read the current schema from `db/schema.sql`
2. Generate migration with `scripts/migrate.sh create "description"`
3. Edit the generated file to add SQL
4. Run with `scripts/migrate.sh apply`
```
