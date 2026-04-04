# octobots.yaml — Project Composition File

`octobots.yaml` lives in your **project root** (alongside `.env.octobots`). It declares the exact roles and skills your project uses, with optional version pins. Check it into git for reproducibility.

## Format

```yaml
version: "1.0"

roles:
  - repo: arozumenko/pm-agent
    ref: main                       # branch, tag, or SHA
  - repo: arozumenko/python-dev-agent
    ref: main
  - repo: arozumenko/qa-engineer-agent
    ref: v1.2.0                     # pin to a specific release
  - repo: myorg/custom-agent        # private or unlisted agent
    ref: main

skills:
  - repo: arozumenko/skill-tdd
    ref: main
  - repo: arozumenko/skill-code-review
    ref: main
  - repo: arozumenko/skill-msgraph
    ref: main
  - repo: myorg/skill-internal      # private skill
    ref: feature/new-workflow
```

## Fields

| Field | Required | Description |
|---|---|---|
| `version` | No | Schema version (`"1.0"`) |
| `roles[].repo` | Yes | GitHub repo in `owner/repo` format |
| `roles[].ref` | No | Branch, tag, or SHA. Default: `main` |
| `skills[].repo` | Yes | GitHub repo in `owner/repo` format |
| `skills[].ref` | No | Branch, tag, or SHA. Default: `main` |

## How init-project.sh uses it

```bash
# First init — fetches declared roles + skills
octobots/scripts/init-project.sh

# Re-fetch everything at their declared refs (update)
octobots/scripts/init-project.sh --update

# Add one role ad-hoc (not in octobots.yaml)
octobots/scripts/init-project.sh --role arozumenko/scout-agent
octobots/scripts/init-project.sh --role myorg/custom-agent@v2.0

# Add one skill ad-hoc
octobots/scripts/init-project.sh --skill arozumenko/skill-msgraph
```

## Fetch strategy

For each declared role or skill, `registry-fetch.sh` tries in order:

1. **`npx github:<repo> init --all`** (roles) or **`npx skills add <repo>`** (skills) — uses the agentskills.io/npx ecosystem, installs into `.claude/agents/` or `.claude/skills/`
2. **`git clone --depth 1 --branch <ref>`** fallback — clones into `.octobots/registry/<repo-name>/`, then symlinks into `.claude/agents/<name>/` or `.claude/skills/<name>/`

The git clone fallback works for private repos and repos not published to npm.

## What stays bundled

These octobots-internal skills are **not** declared in `octobots.yaml` — they are symlinked unconditionally by `init-project.sh`:

- `skills/taskbox/` — inter-role messaging infrastructure
- `skills/bugfix-workflow/` — structured bug investigation
- `skills/implement-feature/` — feature implementation workflow
- `skills/plan-feature/` — feature planning workflow
- `skills/project-seeder/` — scout's project configuration skill

## Runtime: supervisor REPL

```
/role add arozumenko/scout-agent         # fetch + start
/role add arozumenko/scout-agent@v2.0    # fetch specific version + start
/skill add arozumenko/skill-msgraph      # fetch + link into all active workers
/skill add myorg/skill-internal@main     # private skill via git clone fallback
```

## .gitignore

The fetched content lives in `.octobots/registry/` and `.claude/` — both gitignored. `octobots.yaml` itself should be committed:

```gitignore
# These are gitignored by install.sh:
octobots/
.octobots/
.claude/
.env.octobots

# Commit this:
# octobots.yaml
```
