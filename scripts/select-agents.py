#!/usr/bin/env python3
"""
Interactive team selector for octobots install.

Reads agents.json, prompts the user to choose a team preset or build a custom
selection, then prints the selected entries (one per line) to stdout. install.sh
consumes those lines and routes each to the correct installer:

  sdlc:<name>      → batched `npx github:arozumenko/sdlc-skills init --agents …`
  owner/repo@ref   → `npx github:<repo>#<ref> init --all` (individual)

Usage:
    python3 octobots/scripts/select-agents.py                  # interactive
    python3 octobots/scripts/select-agents.py --preset 0       # preset by index, non-interactive
    python3 octobots/scripts/select-agents.py --all            # all agents, non-interactive

Preset flow:
  - Pick one of the presets listed in agents.json
  - "Custom" enters a structured per-group picker:
      * core roles (pm, ba, tl, pa, ...) — one y/n each
      * devs (group: "dev") — multi-select (comma-separated indexes, or "all")
      * QAs (group: "qa") — single pick
  - Non-Custom presets install their `agents:` list as-is, then optionally swap the QA
"""

import json
import sys
from pathlib import Path

REGISTRY = Path(__file__).parent.parent / "agents.json"


def load_registry():
    if not REGISTRY.exists():
        return {"agents": [], "presets": []}
    return json.loads(REGISTRY.read_text())


def agents_by_id(registry):
    return {a["id"]: a for a in registry.get("agents", [])}


def prompt(msg, default=""):
    try:
        sys.stderr.write(msg)
        sys.stderr.flush()
        val = input()
        return val.strip() or default
    except (EOFError, KeyboardInterrupt):
        return default


def select_qa(qa_agents, current=None):
    """Let user pick a QA agent (or none). Returns agent id or None."""
    print("\n  ── QA Agent ──────────────────────────────────────────────", file=sys.stderr)
    default_idx = 1
    for i, a in enumerate(qa_agents, 1):
        mark = " (default)" if a["id"] == current else ""
        if a["id"] == current:
            default_idx = i
        print(f"    {i}. {a['description']}{mark}", file=sys.stderr)
    print("    s. Skip QA for now", file=sys.stderr)
    print(file=sys.stderr)
    choice = prompt(f"  Choose QA agent [{default_idx}]: ", str(default_idx))
    if choice.lower() == "s":
        return None
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(qa_agents):
            return qa_agents[idx]["id"]
    except ValueError:
        pass
    return qa_agents[default_idx - 1]["id"] if qa_agents else None


def select_devs(devs):
    """Multi-select devs. Returns list of agent ids."""
    if not devs:
        return []
    print("\n  ── Developers ────────────────────────────────────────────", file=sys.stderr)
    for i, a in enumerate(devs, 1):
        print(f"    {i}. {a['description']}", file=sys.stderr)
    print("    Enter comma-separated numbers, 'all', or blank to skip.", file=sys.stderr)
    print(file=sys.stderr)
    choice = prompt("  Pick devs [1]: ", "1")
    if not choice or choice.lower() in {"none", "skip", "s"}:
        return []
    if choice.lower() == "all":
        return [d["id"] for d in devs]
    picks: list[str] = []
    seen: set[int] = set()
    for part in choice.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            idx = int(part) - 1
        except ValueError:
            continue
        if 0 <= idx < len(devs) and idx not in seen:
            seen.add(idx)
            picks.append(devs[idx]["id"])
    return picks


def run_custom(registry):
    """Pick each agent via a structured per-group flow.

    Order: required (auto) -> core (y/n each) -> devs (multi-select) -> QA (picker).
    """
    selected: list[str] = []

    required = [a for a in registry["agents"] if a.get("required")]
    core = [a for a in registry["agents"]
            if a.get("group") == "core" and not a.get("required")]
    devs = [a for a in registry["agents"] if a.get("group") == "dev"]
    qas = [a for a in registry["agents"] if a.get("group") == "qa"]

    for a in required:
        selected.append(a["id"])
        print(f"    + {a['description']} (required)", file=sys.stderr)

    if core:
        print("\n  ── Core roles ────────────────────────────────────────────", file=sys.stderr)
        for a in core:
            ans = prompt(f"    Include {a['description']}? [Y/n]: ", "y")
            if ans.lower() not in {"n", "no"}:
                selected.append(a["id"])

    selected.extend(select_devs(devs))

    if qas:
        chosen_qa = select_qa(qas, current=qas[0]["id"])
        if chosen_qa:
            selected.append(chosen_qa)

    return selected


def run_interactive(registry):
    presets = registry.get("presets", [])

    print(file=sys.stderr)
    print("  ── Team Setup ────────────────────────────────────────────", file=sys.stderr)
    print(file=sys.stderr)
    agents = agents_by_id(registry)
    for i, p in enumerate(presets, 1):
        print(f"    {i}. {p['name']}", file=sys.stderr)
        print(f"       {p['description']}", file=sys.stderr)
        if p.get("qa"):
            qa = agents.get(p["qa"], {})
            print(f"       QA: {qa.get('description', p['qa'])}", file=sys.stderr)
        print(file=sys.stderr)

    choice = prompt("  Choose preset [1]: ", "1")
    try:
        preset_idx = int(choice) - 1
    except ValueError:
        preset_idx = 0
    preset_idx = max(0, min(preset_idx, len(presets) - 1))
    preset = presets[preset_idx]

    if preset["name"] == "Custom":
        return run_custom(registry)

    selected_ids = list(preset.get("agents", []))

    required = [a["id"] for a in registry["agents"] if a.get("required")]
    for r in required:
        if r not in selected_ids:
            selected_ids.insert(0, r)

    qa_agents = [a for a in registry["agents"] if a.get("group") == "qa"]
    if qa_agents:
        chosen_qa = select_qa(qa_agents, preset.get("qa"))
        if chosen_qa:
            selected_ids.append(chosen_qa)

    return selected_ids


def run_all(registry):
    """Return all agents (one QA: first in list)."""
    agents = registry.get("agents", [])
    qa_groups = {}
    selected = []
    for a in agents:
        group = a.get("group")
        if group == "qa":
            if group not in qa_groups:
                qa_groups[group] = a["id"]
                selected.append(a["id"])
        else:
            selected.append(a["id"])
    return selected


def run_preset(registry, idx):
    """Return agents for a specific preset by index."""
    presets = registry.get("presets", [])
    if idx >= len(presets):
        idx = 0
    preset = presets[idx]
    if preset["name"] == "Custom":
        # --preset N is non-interactive; Custom has no concrete list.
        return []
    selected = list(preset.get("agents", []))
    required = [a["id"] for a in registry["agents"] if a.get("required")]
    for r in required:
        if r not in selected:
            selected.insert(0, r)
    qa_id = preset.get("qa")
    if qa_id:
        selected.append(qa_id)
    return selected


def main():
    args = sys.argv[1:]
    registry = load_registry()
    agents = agents_by_id(registry)

    if "--all" in args:
        selected_ids = run_all(registry)
    elif "--preset" in args:
        idx_str = args[args.index("--preset") + 1] if args.index("--preset") + 1 < len(args) else "0"
        selected_ids = run_preset(registry, int(idx_str))
    else:
        selected_ids = run_interactive(registry)

    # Resolve ids to either "sdlc:<name>" (monorepo entries) or "owner/repo@ref"
    # (third-party entries). install.sh batches the sdlc: ones into a single
    # `npx github:arozumenko/sdlc-skills init --agents a,b,c` call.
    print(file=sys.stderr)
    for aid in selected_ids:
        agent = agents.get(aid)
        if not agent:
            continue
        if agent.get("monorepo") == "sdlc-skills":
            print(f"sdlc:{agent['name']}")
        else:
            ref = agent.get("ref", "main")
            print(f"{agent['repo']}@{ref}")


if __name__ == "__main__":
    main()
