#!/usr/bin/env python3
"""
Apply setup.yaml dependencies for bundled octobots skills.

Reads setup.yaml from each skill in octobots/skills/ and merges MCP server
entries into .mcp.json. Existing entries are never overwritten (user config wins).

Usage:
    DEST=octobots python3 octobots/scripts/apply-skill-deps.py
"""

import json
import os
import sys
from pathlib import Path

# ── Minimal YAML parser for setup.yaml ────────────────────────────────────────
# Handles only the subset we produce: nested keys, string values, lists of dicts.
# Falls back to PyYAML if available.

def _parse_yaml_simple(text):
    """Parse the setup.yaml structure without PyYAML dependency."""
    try:
        import yaml
        return yaml.safe_load(text) or {}
    except ImportError:
        pass

    # Hand-rolled parser for our known setup.yaml shape:
    #   dependencies:
    #     mcp:
    #       - name: foo
    #         command: npx
    #         args: ["a", "b"]
    #         env: {}
    result = {}
    current_path = []
    current_list = None
    current_list_item = None

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue

        indent = len(line) - len(line.lstrip())
        stripped = line.lstrip()

        if stripped.startswith("- "):
            # List item start
            if current_list is not None:
                if current_list_item is not None:
                    current_list.append(current_list_item)
                current_list_item = {}
            rest = stripped[2:].strip()
            if ":" in rest:
                k, _, v = rest.partition(":")
                if current_list_item is not None:
                    current_list_item[k.strip()] = _parse_scalar(v.strip())
            continue

        if ":" in stripped:
            k, _, v = stripped.partition(":")
            k = k.strip()
            v = v.strip()

            if current_list_item is not None and indent > 4:
                current_list_item[k] = _parse_scalar(v)
                continue

            if current_list_item is not None:
                current_list.append(current_list_item)
                current_list_item = None
                current_list = None

            if not v:
                # Nested key
                node = result
                for p in current_path:
                    node = node.setdefault(p, {})
                node[k] = {}
                current_path = current_path[:indent // 2] + [k]
            else:
                node = result
                for p in current_path[: indent // 2]:
                    node = node.setdefault(p, {})
                parsed = _parse_scalar(v)
                if isinstance(parsed, list):
                    current_list = parsed
                    node[k] = parsed
                else:
                    node[k] = parsed

    if current_list_item is not None and current_list is not None:
        current_list.append(current_list_item)

    return result


def _parse_scalar(v):
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1]
        items = [i.strip().strip('"').strip("'") for i in inner.split(",") if i.strip()]
        return items
    if v == "{}":
        return {}
    if v in ("true", "True"):
        return True
    if v in ("false", "False"):
        return False
    return v.strip('"').strip("'")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    dest = os.environ.get("DEST", "octobots")
    skills_dir = Path(dest) / "skills"
    mcp_file = Path(".mcp.json")

    if not skills_dir.is_dir():
        print(f"  — {skills_dir} not found, skipping")
        return

    # Load existing .mcp.json
    if mcp_file.exists():
        try:
            cfg = json.loads(mcp_file.read_text())
        except (json.JSONDecodeError, OSError):
            cfg = {}
    else:
        cfg = {}
    cfg.setdefault("mcpServers", {})

    added = []
    skipped = []

    # Scan bundled skills (octobots/skills/) AND installed skills (.claude/skills/)
    scan_dirs = [skills_dir, Path(".claude/skills")]
    for scan_dir in scan_dirs:
        if not scan_dir.is_dir():
            continue
        for setup_yaml in sorted(scan_dir.glob("*/setup.yaml")):
            skill = setup_yaml.parent.name
            try:
                data = _parse_yaml_simple(setup_yaml.read_text())
            except Exception as e:
                print(f"  ⚠  {skill}/setup.yaml: parse error — {e}", file=sys.stderr)
                continue

            for server in (data.get("dependencies") or {}).get("mcp") or []:
                name = server.get("name")
                if not name:
                    continue
                if name in cfg["mcpServers"]:
                    skipped.append(f"{skill}/{name}")
                    continue
                entry = {
                    "command": server.get("command", "npx"),
                    "args": server.get("args", []),
                }
                env = server.get("env")
                if env and isinstance(env, dict):
                    entry["env"] = env
                cfg["mcpServers"][name] = entry
                added.append(f"{skill}/{name}")

    if added or skipped:
        mcp_file.write_text(json.dumps(cfg, indent=2) + "\n")

    for s in added:
        print(f"  ✓ MCP: {s}")
    for s in skipped:
        print(f"  — MCP: {s} (already configured, skipping)")
    if not added and not skipped:
        print("  — no MCP dependencies found in bundled or installed skills")


if __name__ == "__main__":
    main()
