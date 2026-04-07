#!/usr/bin/env python3
"""
Translate octobots AGENT.md files into GitHub Copilot CLI .agent.md files.

Octobots roles use Claude-Code-flavored frontmatter (name, model, color,
workspace, skills, description). Copilot CLI custom agents use a different
schema documented at:
    https://docs.github.com/en/copilot/reference/custom-agents-configuration

This script reads a single role's AGENT.md, drops Claude-only fields, maps the
model name, and writes the result to:

    $COPILOT_HOME/agents/<role>.agent.md     (default: ~/.copilot/agents/)

It is invoked by start.sh and supervisor.py *only* for roles whose AGENT.md
declares `runtime: copilot`. Roles without that field stay on Claude Code and
this script is never touched — zero risk to existing teams.

Usage:
    sync-copilot-agents.py <role-dir>             # translate one role
    sync-copilot-agents.py --all <roles-dir>      # translate every AGENT.md under
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

# Claude → Copilot model name mapping. Pass-through if unknown so users can
# put a literal Copilot model string in AGENT.md (e.g. "gpt-5") and have it work.
MODEL_MAP = {
    "sonnet": "claude-sonnet-4.5",
    "opus":   "claude-opus-4.5",
    "haiku":  "claude-haiku-4.5",
}

# Frontmatter keys Copilot understands. Anything else from AGENT.md is dropped
# (kept in the body as a comment block for traceability).
COPILOT_KEYS = {
    "name", "description", "target", "tools", "model",
    "disable-model-invocation", "user-invocable", "mcp-servers", "metadata",
}

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Cheap YAML-ish parser. Handles flat key: value lines + simple lists.
    Good enough for AGENT.md, which never nests deeply. If a role grows real
    YAML needs, swap this for PyYAML — kept stdlib-only on purpose."""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    fm_block, body = m.group(1), m.group(2)
    fm: dict = {}
    for line in fm_block.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        fm[k.strip()] = v.strip()
    return fm, body


def translate(role_dir: Path, copilot_home: Path) -> Path:
    agent_md = role_dir / "AGENT.md"
    if not agent_md.is_file():
        raise FileNotFoundError(f"{agent_md} missing")

    fm, body = parse_frontmatter(agent_md.read_text())
    role_name = fm.get("name") or role_dir.name

    # Build Copilot frontmatter from the subset of fields it understands.
    out_fm: dict[str, str] = {"name": role_name}
    if "description" in fm:
        out_fm["description"] = fm["description"]
    else:
        # description is REQUIRED by Copilot — synthesize one rather than fail.
        out_fm["description"] = f"Octobots role: {role_name}"

    if "model" in fm:
        out_fm["model"] = MODEL_MAP.get(fm["model"], fm["model"])

    # Pass through any keys the user already wrote in Copilot dialect.
    for k, v in fm.items():
        if k in COPILOT_KEYS and k not in out_fm:
            out_fm[k] = v

    # Preserve dropped keys as a comment so the diff between runtimes is auditable.
    dropped = {k: v for k, v in fm.items()
               if k not in COPILOT_KEYS and k not in {"runtime"}}
    dropped_block = ""
    if dropped:
        dropped_block = (
            "<!-- octobots: fields dropped on copilot translation:\n"
            + "\n".join(f"  {k}: {v}" for k, v in dropped.items())
            + "\n-->\n\n"
        )

    fm_text = "---\n" + "".join(f"{k}: {v}\n" for k, v in out_fm.items()) + "---\n\n"
    out_text = fm_text + dropped_block + body.lstrip()

    out_dir = copilot_home / "agents"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{role_name}.agent.md"
    out_path.write_text(out_text)
    return out_path


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("path", help="Role dir (one AGENT.md) or roles parent dir with --all")
    p.add_argument("--all", action="store_true", help="Walk PATH for every AGENT.md")
    p.add_argument("--copilot-home",
                   default=os.environ.get("COPILOT_HOME", str(Path.home() / ".copilot")),
                   help="Override $COPILOT_HOME (default: ~/.copilot)")
    args = p.parse_args()

    copilot_home = Path(args.copilot_home).expanduser()
    root = Path(args.path).expanduser().resolve()

    targets: list[Path] = []
    if args.all:
        targets = [a.parent for a in root.rglob("AGENT.md")]
    else:
        targets = [root]

    for role_dir in targets:
        try:
            out = translate(role_dir, copilot_home)
            print(f"✓ {role_dir.name} → {out}")
        except FileNotFoundError as e:
            print(f"✗ {role_dir.name}: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
