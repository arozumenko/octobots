"""Runtime agent metadata loader.

Walks `.claude/agents/<name>/AGENT.md` in the current project, parses the
YAML frontmatter, and overlays values from `octobots/agent-overrides.json`
for agents that don't ship `group`/`theme`/`aliases` fields (typically
third-party agents from other orgs).

Consumed by:
  - supervisor.py     → ROLE_THEME (tmux pane styling)
  - scripts/roles.py  → ROLE_ALIASES, ROLE_DISPLAY (shorthand + labels)

Contract: after install, each agent's AGENT.md is the canonical source.
Overrides only fill in missing fields or re-skin agents the user can't
modify upstream. Adding a new agent to the team requires zero Python
changes — the new agent's frontmatter is enough.
"""
from __future__ import annotations

import json
from pathlib import Path

try:
    import yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False


OCTOBOTS_DIR = Path(__file__).parent.parent
PROJECT_DIR = Path.cwd()
INSTALLED_AGENTS = PROJECT_DIR / ".claude" / "agents"
OVERRIDES_PATH = OCTOBOTS_DIR / "agent-overrides.json"


def _parse_frontmatter(agent_md: Path) -> dict:
    if not agent_md.is_file():
        return {}
    if not _HAS_YAML:
        return {}
    try:
        text = agent_md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    try:
        data = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _load_overrides() -> dict:
    try:
        return json.loads(OVERRIDES_PATH.read_text()).get("overrides", {}) or {}
    except (OSError, ValueError):
        return {}


def _merge(base: dict, overlay: dict) -> dict:
    """Overlay wins per-key. Nested dicts merge one level deep."""
    out = dict(base)
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = {**out[k], **v}
        else:
            out[k] = v
    return out


def load_agents() -> dict[str, dict]:
    """Return {agent_dir_name: merged_metadata} for every installed agent."""
    if not INSTALLED_AGENTS.is_dir():
        return {}
    overrides = _load_overrides()
    out: dict[str, dict] = {}
    for entry in sorted(INSTALLED_AGENTS.iterdir()):
        if not entry.is_dir():
            continue
        agent_md = entry / "AGENT.md"
        if not agent_md.is_file():
            continue
        name = entry.name
        data = _parse_frontmatter(agent_md)
        if name in overrides:
            data = _merge(data, overrides[name])
        out[name] = data
    return out


def role_themes() -> dict[str, dict[str, str]]:
    """{role: {color, icon, name}} for tmux pane styling."""
    out: dict[str, dict[str, str]] = {}
    for name, meta in load_agents().items():
        theme = meta.get("theme") or {}
        out[name] = {
            "color": theme.get("color", "colour250"),
            "icon": theme.get("icon", "🤖"),
            "name": theme.get("short_name", name),
        }
    return out


def get_dispatch_rules(role: str) -> str | None:
    """Return the content of a role's RULES.md, or None if absent/empty.

    Resolution order:
      1. ``.octobots/roles/<role>/RULES.md``  (project-local override)
      2. ``.claude/agents/<role>/RULES.md``   (installed agent default)

    Returns None when no RULES.md exists or its content is blank/whitespace.
    The caller passes the result to ``render_dispatch_rules``; None causes the
    supervisor to use the bundled ``DEFAULT_DISPATCH_RULES`` fallback.
    """
    runtime_dir = PROJECT_DIR / ".octobots"
    local = runtime_dir / "roles" / role / "RULES.md"
    installed = INSTALLED_AGENTS / role / "RULES.md"
    for candidate in (local, installed):
        if candidate.is_file():
            try:
                content = candidate.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                continue
            if content:
                return content
    return None


def role_aliases() -> tuple[dict[str, str], dict[str, str]]:
    """Return (ROLE_ALIASES, ROLE_DISPLAY) built from installed agents."""
    aliases: dict[str, str] = {
        "all": "all",
        "everyone": "all",
        "team": "all",
    }
    display: dict[str, str] = {}
    for name, meta in load_agents().items():
        aliases[name] = name
        for alias in meta.get("aliases") or []:
            aliases[alias] = name
        theme = meta.get("theme") or {}
        icon = theme.get("icon", "🤖")
        short = theme.get("short_name", name)
        display[name] = f"{icon} {short}"
    return aliases, display
