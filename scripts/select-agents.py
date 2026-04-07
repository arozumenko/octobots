#!/usr/bin/env python3
"""
Interactive team selector for octobots install.

Reads agents.json, prompts the user to choose a team preset or build a custom
selection, then prints the selected GitHub repos to stdout (one per line).

Usage:
    python3 octobots/scripts/select-agents.py                  # interactive
    python3 octobots/scripts/select-agents.py --preset 0       # preset by index (0-based), non-interactive
    python3 octobots/scripts/select-agents.py --all            # all agents, non-interactive
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
        val = input(msg)
        return val.strip() or default
    except (EOFError, KeyboardInterrupt):
        return default


def select_qa(qa_agents, current=None):
    """Let user pick a QA agent (or none). Returns agent id or None."""
    print("\n  ── QA Agent ──────────────────────────────────────────────")
    for i, a in enumerate(qa_agents, 1):
        mark = " (default)" if a["id"] == current else ""
        print(f"    {i}. {a['description']}{mark}")
    print("    s. Skip QA for now")
    print()
    choice = prompt("  Choose QA agent [1]: ", "1")
    if choice.lower() == "s":
        return None
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(qa_agents):
            return qa_agents[idx]["id"]
    except ValueError:
        pass
    return qa_agents[0]["id"] if qa_agents else None


def run_interactive(registry):
    agents = agents_by_id(registry)
    presets = registry.get("presets", [])
    qa_agents = [a for a in registry["agents"] if a.get("group") == "qa"]

    print()
    print("  ── Team Setup ────────────────────────────────────────────")
    print()
    for i, p in enumerate(presets, 1):
        print(f"    {i}. {p['name']}")
        print(f"       {p['description']}")
        if p.get("qa"):
            qa = agents.get(p["qa"], {})
            print(f"       QA: {qa.get('description', p['qa'])}")
        print()

    choice = prompt("  Choose preset [1]: ", "1")
    try:
        preset_idx = int(choice) - 1
    except ValueError:
        preset_idx = 0
    preset_idx = max(0, min(preset_idx, len(presets) - 1))
    preset = presets[preset_idx]

    selected_ids = list(preset.get("agents", []))
    default_qa = preset.get("qa")

    if preset["name"] == "Custom":
        # Custom: let user pick each agent
        print()
        print("  ── Select agents ─────────────────────────────────────────")
        non_qa = [a for a in registry["agents"] if not a.get("group") and not a.get("required")]
        for a in non_qa:
            ans = prompt(f"    Include {a['description']}? [y/n]: ", "y")
            if ans.lower() != "n":
                selected_ids.append(a["id"])

    # Always include required agents
    required = [a["id"] for a in registry["agents"] if a.get("required")]
    for r in required:
        if r not in selected_ids:
            selected_ids.insert(0, r)

    # QA selection
    chosen_qa = None
    if qa_agents:
        chosen_qa = select_qa(qa_agents, default_qa)
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
        if group:
            if group not in qa_groups:
                qa_groups[group] = a["id"]
                selected.append(a["id"])
        else:
            selected.append(a["id"])
    return selected


def run_preset(registry, idx):
    """Return agents for a specific preset by index."""
    presets = registry.get("presets", [])
    agents = agents_by_id(registry)
    if idx >= len(presets):
        idx = 0
    preset = presets[idx]
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

    # Resolve ids to repo@ref and print (ref defaults to "main")
    print()
    for aid in selected_ids:
        agent = agents.get(aid)
        if agent:
            ref = agent.get("ref", "main")
            print(f"{agent['repo']}@{ref}")


if __name__ == "__main__":
    main()
