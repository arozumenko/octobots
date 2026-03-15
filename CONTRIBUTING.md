# Contributing to Octobots

Thanks for your interest in contributing!

## Quick Start

```bash
git clone git@github.com:onetest-ai/octobots.git
cd octobots
pip install -r requirements.txt
./start.sh --list   # verify setup
```

## What to Contribute

- **New roles** — add to `roles/<name>/` with SOUL.md + CLAUDE.md
- **New skills** — add to `skills/<name>/SKILL.md` following [skill-spec](docs/skill-spec.md)
- **Bridge integrations** — Slack, Discord, Linear, etc. (like `scripts/telegram-bridge.py`)
- **Supervisor commands** — add to `scripts/supervisor.py`
- **Bug fixes** — especially in taskbox, supervisor, and bridge
- **Documentation** — setup guides, architecture docs, examples

## Structure

```
octobots/          ← framework (read-only for users)
├── roles/           base role templates
├── skills/          shared skills
├── shared/          conventions, agents
├── scripts/         supervisor, bridges, relay
└── docs/            documentation
```

## Guidelines

1. **Test your changes.** Run `start.sh --list` and verify scripts work.
2. **Keep roles generic.** Don't add project-specific details to base roles — those go in `.octobots/roles/` overrides.
3. **Skills follow agentskills.io format.** See [skill-spec](docs/skill-spec.md).
4. **Python scripts use stdlib where possible.** Taskbox (relay.py) has zero deps. Supervisor and bridges use `rich`, `python-telegram-bot`, `cryptography`.
5. **Bash scripts must pass `bash -n`** syntax check.
6. **No secrets in code.** Use `.env.octobots` for tokens.

## Pull Requests

1. Fork the repo
2. Create a branch: `feat/my-feature` or `fix/my-fix`
3. Make changes
4. Test locally
5. Submit PR with clear description

## Reporting Issues

Use [GitHub Issues](https://github.com/onetest-ai/octobots/issues). Include:
- What you expected
- What happened
- Steps to reproduce
- Relevant logs or screenshots

## License

By contributing, you agree that your contributions will be licensed under the Apache-2.0 License.
