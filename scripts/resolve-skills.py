#!/usr/bin/env python3
"""
Resolve skill dependencies from installed agents.

Two modes:

  union   — parse `skills:` frontmatter from every AGENT.md under .claude/agents/,
            print the union of declared skill ids (one per line) on stdout.
            Used by install.sh to decide which published skills to install.

  verify  — for each installed agent, check that every skill it declares
            resolves to a directory under .claude/skills/. Print a table to
            stderr and exit non-zero if anything is missing.
            Used by install.sh as a post-install sanity check.

Both modes operate on the current working directory (project root). Override
with --project <path>.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def parse_skills_frontmatter(agent_md: Path) -> list[str]:
    """Extract the skills: [...] line from an AGENT.md YAML frontmatter."""
    if not agent_md.is_file():
        return []
    try:
        text = agent_md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    in_frontmatter = False
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.strip() == "---":
            if not in_frontmatter:
                in_frontmatter = True
                continue
            break
        if not in_frontmatter:
            continue
        if not line.lstrip().startswith("skills:"):
            continue
        # skills: [a, b, c]  — only the inline-array form is supported
        val = line.split(":", 1)[1].strip()
        if not (val.startswith("[") and val.endswith("]")):
            return []
        inner = val[1:-1].strip()
        if not inner:
            return []
        return [s.strip().strip('"').strip("'") for s in inner.split(",") if s.strip()]
    return []


def installed_agents(project: Path) -> list[tuple[str, Path]]:
    """Return (agent_name, AGENT.md path) for every installed agent."""
    agents_dir = project / ".claude" / "agents"
    if not agents_dir.is_dir():
        return []
    out = []
    for entry in sorted(agents_dir.iterdir()):
        agent_md = entry / "AGENT.md"
        if agent_md.is_file():
            out.append((entry.name, agent_md))
    return out


def installed_skills(project: Path) -> set[str]:
    """Names of every skill directory under .claude/skills/."""
    skills_dir = project / ".claude" / "skills"
    if not skills_dir.is_dir():
        return set()
    return {p.name for p in skills_dir.iterdir() if p.is_dir() or p.is_symlink()}


def cmd_union(project: Path) -> int:
    seen: set[str] = set()
    for _name, agent_md in installed_agents(project):
        for skill in parse_skills_frontmatter(agent_md):
            seen.add(skill)
    for skill in sorted(seen):
        print(skill)
    return 0


def cmd_verify(project: Path) -> int:
    have = installed_skills(project)
    rows: list[tuple[str, str, str]] = []  # (agent, skill, status)
    missing_total = 0
    for name, agent_md in installed_agents(project):
        declared = parse_skills_frontmatter(agent_md)
        if not declared:
            rows.append((name, "—", "ok (no skills declared)"))
            continue
        for skill in declared:
            if skill in have:
                rows.append((name, skill, "ok"))
            else:
                rows.append((name, skill, "MISSING"))
                missing_total += 1

    if not rows:
        print("No installed agents found under .claude/agents/", file=sys.stderr)
        return 0

    w_agent = max(len(r[0]) for r in rows)
    w_skill = max(len(r[1]) for r in rows)
    print("", file=sys.stderr)
    print(f"  {'AGENT'.ljust(w_agent)}  {'SKILL'.ljust(w_skill)}  STATUS", file=sys.stderr)
    print(f"  {'-' * w_agent}  {'-' * w_skill}  ------", file=sys.stderr)
    for agent, skill, status in rows:
        marker = "  " if status.startswith("ok") else "✗ "
        print(f"{marker}{agent.ljust(w_agent)}  {skill.ljust(w_skill)}  {status}", file=sys.stderr)
    print("", file=sys.stderr)

    if missing_total:
        print(f"  {missing_total} missing skill(s). Install with: npx skills add arozumenko/skill-<name>", file=sys.stderr)
        return 1
    print("  All declared skills resolved.", file=sys.stderr)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="resolve-skills.py", description=__doc__)
    p.add_argument("mode", choices=["union", "verify"])
    p.add_argument("--project", type=Path, default=Path.cwd())
    args = p.parse_args()
    if args.mode == "union":
        return cmd_union(args.project)
    return cmd_verify(args.project)


if __name__ == "__main__":
    sys.exit(main())
