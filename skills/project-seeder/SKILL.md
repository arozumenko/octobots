---
name: project-seeder
description: Generate AGENTS.md and .octobots/ configuration files for a new project. Use when the user asks to "seed the project", "onboard this repo", "generate project config", "create AGENTS.md", or after the scout has explored the codebase.
license: Apache-2.0
compatibility: Requires project root write access. No external dependencies.
metadata:
  author: octobots
  version: "0.1.0"
---

# Project Seeder

Generate the configuration files that octobots roles need to work in a project.

## What Gets Generated

```
project-root/
├── AGENTS.md                     ← Main project context (all roles read this)
└── .octobots/
    ├── profile.md                ← Quick-reference project card
    ├── architecture.md           ← System design map (if complex enough)
    ├── conventions.md            ← Detected coding standards
    └── testing.md                ← Test infrastructure details
```

Not every project needs all files. Skip what's not relevant.

## Step 1: Generate AGENTS.md

This is the most important output. Every role reads it. Use the template in `references/templates.md` and fill it with actual findings.

**Key sections:**
- Project overview (1 paragraph)
- Tech stack (languages, frameworks, databases, infra)
- Repository structure (directory tree with annotations)
- Build & run commands (install, dev, test, lint, deploy)
- Coding conventions (detected from codebase)
- Testing (framework, commands, patterns)
- CI/CD (what runs, where, how)
- Environment (required vars, .env setup)

**Rules:**
- Only document what you've verified. Don't guess build commands.
- Include the ACTUAL commands from package.json scripts, Makefile targets, CI config.
- Note inconsistencies: "README says `npm test` but CI runs `npx jest --ci`"
- Keep it under 200 lines. Link to `.octobots/` files for details.

## Step 2: Generate .octobots/profile.md

Quick-reference card with YAML frontmatter:

```yaml
---
project: repo-name
team: team-name (ask if unknown)
issue-tracker: URL (detect from .git/config, README, or ask)
default-branch: main/master/develop (detect from git)
languages: [python, typescript]
---
```

## Step 3: Generate .octobots/conventions.md (if patterns detected)

Only create if you found clear patterns. Document what IS, not what should be.

Structure:
- Naming conventions (files, variables, classes)
- Import ordering
- Error handling patterns
- Code organization (layers, modules)
- Comment/documentation style

## Step 4: Generate .octobots/architecture.md (if complex)

Only for multi-service or non-trivial architectures:
- Service/component map
- Data flow between components
- API boundaries
- Database schema overview
- Infrastructure diagram (text-based)

## Step 5: Generate .octobots/testing.md (if test infra exists)

QA engineer reads this. Include:
- Test framework and config
- How to run tests (exact commands)
- Fixture/setup patterns
- Test data strategy
- CI test pipeline
- Coverage tools
- Known flaky areas

## Validation

After generating, verify:

```bash
# Files exist
ls AGENTS.md .octobots/profile.md

# AGENTS.md is readable
wc -l AGENTS.md  # should be under 200 lines

# No secrets leaked
grep -ri "password\|secret\|token\|api_key" AGENTS.md .octobots/ || echo "clean"
```

## Details

See `references/templates.md` for full templates for each file.
