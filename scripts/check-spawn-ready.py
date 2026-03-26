#!/usr/bin/env python3
"""Octobots spawn readiness checker.

Run from the project directory (not the octobots directory).

Usage:
  python3 octobots/scripts/check-spawn-ready.py
  python3 octobots/scripts/check-spawn-ready.py --check infra-only
  python3 octobots/scripts/check-spawn-ready.py --check files-only

Checks:
  1  relay.db writable                          [critical]
  2  role memory files exist + non-empty        [warning]
  3  AGENT.md + SOUL.md present for all roles   [critical]
  4  .claude/agents symlinks resolve            [warning]
  5  roles.py ROLE_ALIASES has all personas     [critical]
  6  ROLE_DISPLAY icons match ROLE_THEME        [warning]
  7  telegram-bridge /team lists all roles      [warning]
  8  .env.worker OCTOBOTS_ID matches dir        [warning]
  9  CLAUDE.md exists                           [critical]
  10 AGENTS.md exists                           [critical]

infra-only: runs checks 5-7
files-only: runs checks 1-4, 8-10
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────

OCTOBOTS_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = Path.cwd()
MANIFEST_PATH = PROJECT_DIR / ".octobots" / "roles-manifest.yaml"


# ── Result types ─────────────────────────────────────────────────────────────

PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"
SKIP = "SKIP"


@dataclass
class CheckResult:
    number: int
    label: str
    status: str
    message: str
    critical: bool = False


# ── Role loading ─────────────────────────────────────────────────────────────

def load_manifest() -> dict:
    """Parse roles-manifest.yaml without PyYAML. Returns dict of role_id -> attrs."""
    if not MANIFEST_PATH.exists():
        return {}

    roles: dict[str, dict] = {}
    current_role: str | None = None
    indent_roles = False

    for raw in MANIFEST_PATH.read_text().splitlines():
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue

        # Top-level "roles:" key
        if re.match(r"^roles\s*:", line):
            indent_roles = True
            continue

        if indent_roles:
            # Role ID line: "  python-dev:" (2-space indent, ends with colon)
            m = re.match(r"^  ([a-z][a-z0-9-]+)\s*:", line)
            if m:
                current_role = m.group(1)
                roles[current_role] = {}
                continue

            # Attribute line: "    persona: Kit" (4-space indent)
            if current_role:
                m = re.match(r"^    (\w+)\s*:\s*(.+)", line)
                if m:
                    key, val = m.group(1), m.group(2).strip().strip('"\'')
                    # Convert booleans
                    if val.lower() == "true":
                        val = True
                    elif val.lower() == "false":
                        val = False
                    roles[current_role][key] = val

    return roles


def get_configured_roles() -> list[str]:
    """Return list of role IDs to check against."""
    manifest = load_manifest()
    if manifest:
        return list(manifest.keys())
    # Fall back to enumerating roles/ directory in octobots
    roles_dir = OCTOBOTS_DIR / "roles"
    if roles_dir.exists():
        return [d.name for d in sorted(roles_dir.iterdir()) if d.is_dir()]
    return []


def get_role_personas() -> dict[str, str]:
    """Return {role_id: persona_name} from manifest (lowercase persona)."""
    manifest = load_manifest()
    return {
        role_id: str(attrs.get("persona", "")).lower()
        for role_id, attrs in manifest.items()
        if attrs.get("persona")
    }


# ── Script parsers ────────────────────────────────────────────────────────────

def _extract_dict_block(text: str, dict_name: str) -> str:
    """Extract the content of a top-level dict literal by name."""
    pattern = rf"^{re.escape(dict_name)}\s*[=:][^{{]*\{{"
    m = re.search(pattern, text, re.MULTILINE)
    if not m:
        return ""
    start = m.end() - 1  # position of opening {
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return ""


def parse_roles_py() -> tuple[dict[str, str], dict[str, str]]:
    """Return (ROLE_ALIASES, ROLE_DISPLAY) from scripts/roles.py."""
    roles_py = OCTOBOTS_DIR / "scripts" / "roles.py"
    if not roles_py.exists():
        return {}, {}
    text = roles_py.read_text()

    def parse_str_dict(block: str) -> dict[str, str]:
        pairs = re.findall(r'"([^"]+)"\s*:\s*"([^"]+)"', block)
        return {k: v for k, v in pairs}

    aliases_block = _extract_dict_block(text, "ROLE_ALIASES")
    display_block = _extract_dict_block(text, "ROLE_DISPLAY")
    return parse_str_dict(aliases_block), parse_str_dict(display_block)


def parse_supervisor_role_theme() -> dict[str, dict[str, str]]:
    """Return {role_id: {color, icon, name}} from scripts/supervisor.py."""
    sup_py = OCTOBOTS_DIR / "scripts" / "supervisor.py"
    if not sup_py.exists():
        return {}
    text = sup_py.read_text()
    block = _extract_dict_block(text, "ROLE_THEME")
    if not block:
        return {}

    result: dict[str, dict[str, str]] = {}
    # Match entries like: "python-dev": {"color": "...", "icon": "...", "name": "..."}
    entry_pat = re.compile(
        r'"([^"]+)"\s*:\s*\{([^}]+)\}', re.DOTALL
    )
    kv_pat = re.compile(r'"(\w+)"\s*:\s*"([^"]*)"')
    for m in entry_pat.finditer(block):
        role_id = m.group(1)
        kv_text = m.group(2)
        attrs = {k: v for k, v in kv_pat.findall(kv_text)}
        result[role_id] = attrs
    return result


def parse_telegram_team_roles() -> list[str]:
    """Return list of shortnames mentioned in telegram-bridge.py cmd_team tuples."""
    tg_py = OCTOBOTS_DIR / "scripts" / "telegram-bridge.py"
    if not tg_py.exists():
        return []
    text = tg_py.read_text()

    # Find the cmd_team function block
    m = re.search(r"async def cmd_team\b.*?(?=\nasync def |\ndef |\Z)", text, re.DOTALL)
    if not m:
        return []

    # Extract tuples like ("📋", "pm", "Coordination, status")
    tuples = re.findall(r'\(\s*"[^"]*"\s*,\s*"([^"]+)"\s*,\s*"[^"]*"\s*\)', m.group())
    return tuples


# ── Individual checks ─────────────────────────────────────────────────────────

def check_1_relay_db() -> CheckResult:
    db = PROJECT_DIR / ".octobots" / "relay.db"
    parent = db.parent
    if db.exists():
        ok = os.access(db, os.W_OK)
        msg = "writable" if ok else f"exists but not writable: {db}"
        return CheckResult(1, "relay.db", PASS if ok else FAIL, msg, critical=True)
    if parent.exists():
        ok = os.access(parent, os.W_OK)
        msg = "not yet initialized (parent dir writable — OK)" if ok else f"parent dir not writable: {parent}"
        return CheckResult(1, "relay.db", PASS if ok else FAIL, msg, critical=True)
    return CheckResult(1, "relay.db", FAIL, f".octobots/ directory missing: {parent}", critical=True)


def check_2_memory_files(roles: list[str]) -> CheckResult:
    if not roles:
        return CheckResult(2, "memory files", SKIP, "no roles configured")
    mem_dir = PROJECT_DIR / ".octobots" / "memory"
    missing, empty = [], []
    for role_id in roles:
        f = mem_dir / f"{role_id}.md"
        if not f.exists():
            missing.append(role_id)
        elif f.stat().st_size < 10:
            empty.append(role_id)

    if missing:
        return CheckResult(2, "memory files", WARN, f"missing: {', '.join(missing)}")
    if empty:
        return CheckResult(2, "memory files", WARN, f"empty (not seeded): {', '.join(empty)}")
    return CheckResult(2, "memory files", PASS, f"{len(roles)}/{len(roles)} roles have seeded memory files")


def check_3_role_files(roles: list[str]) -> CheckResult:
    if not roles:
        return CheckResult(3, "role files", SKIP, "no roles configured", critical=True)
    roles_dir = OCTOBOTS_DIR / "roles"
    missing = []
    for role_id in roles:
        role_dir = roles_dir / role_id
        if not (role_dir / "AGENT.md").exists():
            missing.append(f"{role_id}/AGENT.md")
        if not (role_dir / "SOUL.md").exists():
            missing.append(f"{role_id}/SOUL.md")
    if missing:
        return CheckResult(3, "role files", FAIL, f"missing: {', '.join(missing)}", critical=True)
    return CheckResult(3, "role files", PASS, f"AGENT.md + SOUL.md present for all {len(roles)} roles")


def check_4_agent_symlinks(roles: list[str]) -> CheckResult:
    agents_dir = PROJECT_DIR / ".claude" / "agents"
    if not agents_dir.exists():
        return CheckResult(4, "agent symlinks", WARN, ".claude/agents/ not present (run start.sh first to create symlinks)")
    broken = []
    for role_id in roles:
        link = agents_dir / role_id
        if link.is_symlink() and not link.exists():
            broken.append(role_id)
        elif not link.exists():
            broken.append(role_id)
    if broken:
        return CheckResult(4, "agent symlinks", WARN, f"broken/missing: {', '.join(broken)}")
    return CheckResult(4, "agent symlinks", PASS, f"all {len(roles)} role symlinks resolve")


def check_5_role_aliases(roles: list[str]) -> CheckResult:
    aliases, _ = parse_roles_py()
    if not aliases:
        return CheckResult(5, "roles.py aliases", FAIL, "could not parse scripts/roles.py", critical=True)
    personas = get_role_personas()
    missing = []
    for role_id, persona in personas.items():
        if persona and persona not in aliases:
            missing.append(f"{persona} → {role_id}")
    if missing:
        return CheckResult(5, "roles.py aliases", FAIL, f"persona aliases missing: {', '.join(missing)}", critical=True)
    return CheckResult(5, "roles.py aliases", PASS, "all persona names registered in ROLE_ALIASES")


def check_6_display_vs_theme(roles: list[str]) -> CheckResult:
    _, display = parse_roles_py()
    theme = parse_supervisor_role_theme()
    if not display or not theme:
        return CheckResult(6, "display icons", SKIP, "could not parse roles.py or supervisor.py")

    mismatches = []
    for role_id in roles:
        d_entry = display.get(role_id, "")  # e.g. "🐍 py"
        t_entry = theme.get(role_id, {})
        if not d_entry or not t_entry:
            continue
        parts = d_entry.split(" ", 1)
        d_icon = parts[0] if parts else ""
        d_name = parts[1] if len(parts) > 1 else ""
        t_icon = t_entry.get("icon", "")
        t_name = t_entry.get("name", "")
        if d_icon != t_icon:
            mismatches.append(f"{role_id}: ROLE_DISPLAY icon '{d_icon}' != ROLE_THEME icon '{t_icon}'")
        if d_name != t_name:
            mismatches.append(f"{role_id}: ROLE_DISPLAY name '{d_name}' != ROLE_THEME name '{t_name}'")

    if mismatches:
        return CheckResult(6, "display icons", WARN, "; ".join(mismatches))
    return CheckResult(6, "display icons", PASS, "ROLE_DISPLAY icons match ROLE_THEME")


def check_7_telegram_team(roles: list[str]) -> CheckResult:
    tg_file = OCTOBOTS_DIR / "scripts" / "telegram-bridge.py"
    if not tg_file.exists():
        return CheckResult(7, "telegram /team", SKIP, "telegram-bridge.py not present")

    aliases, _ = parse_roles_py()
    team_names = parse_telegram_team_roles()
    if not team_names:
        return CheckResult(7, "telegram /team", WARN, "could not parse cmd_team tuples")

    # For each role, check that its shortname (from ROLE_THEME or aliases) appears in team_names
    theme = parse_supervisor_role_theme()
    missing = []
    for role_id in roles:
        shortname = theme.get(role_id, {}).get("name", role_id)
        if shortname not in team_names:
            missing.append(f"{role_id} (shortname: {shortname})")

    if missing:
        return CheckResult(7, "telegram /team", WARN, f"not listed in /team: {', '.join(missing)}")
    return CheckResult(7, "telegram /team", PASS, "all configured roles listed in /team command")


def check_8_env_worker(roles: list[str]) -> CheckResult:
    workers_dir = PROJECT_DIR / ".octobots" / "workers"
    if not workers_dir.exists():
        return CheckResult(8, ".env.worker", SKIP, "workers/ dir not present (spawn not yet run)")

    mismatches = []
    for worker_dir in sorted(workers_dir.iterdir()):
        if not worker_dir.is_dir():
            continue
        env_file = worker_dir / ".env.worker"
        if not env_file.exists():
            mismatches.append(f"{worker_dir.name}: .env.worker missing")
            continue
        content = env_file.read_text()
        m = re.search(r"^OCTOBOTS_ID\s*=\s*(.+)$", content, re.MULTILINE)
        if not m:
            mismatches.append(f"{worker_dir.name}: OCTOBOTS_ID not set")
            continue
        declared = m.group(1).strip().strip("\"'")
        if declared != worker_dir.name:
            mismatches.append(f"{worker_dir.name}: OCTOBOTS_ID='{declared}' doesn't match dir name")

    if mismatches:
        return CheckResult(8, ".env.worker", WARN, "; ".join(mismatches))
    return CheckResult(8, ".env.worker", PASS, ".env.worker files consistent with worker dir names")


def check_9_claude_md() -> CheckResult:
    f = PROJECT_DIR / "CLAUDE.md"
    if f.exists():
        return CheckResult(9, "CLAUDE.md", PASS, "exists", critical=True)
    return CheckResult(9, "CLAUDE.md", FAIL, f"not found at {f}", critical=True)


def check_10_agents_md() -> CheckResult:
    f = PROJECT_DIR / "AGENTS.md"
    if f.exists():
        return CheckResult(10, "AGENTS.md", PASS, "exists", critical=True)
    return CheckResult(10, "AGENTS.md", FAIL, f"not found at {f}", critical=True)


# ── Output ────────────────────────────────────────────────────────────────────

def _status_prefix(result: CheckResult) -> str:
    return {PASS: "PASS", WARN: "WARN", FAIL: "FAIL", SKIP: "SKIP"}[result.status]


def print_results(results: list[CheckResult]) -> int:
    print("\n=== Spawn Readiness Check ===\n")
    for r in results:
        prefix = _status_prefix(r)
        print(f"{prefix:<4}  [{r.number:>2}] {r.label} — {r.message}")

    passed = sum(1 for r in results if r.status == PASS)
    warned = sum(1 for r in results if r.status == WARN)
    skipped = sum(1 for r in results if r.status == SKIP)
    failed = sum(1 for r in results if r.status == FAIL and r.critical)

    print(f"\nResult: {passed} passed, {warned} warning{'s' if warned != 1 else ''}, "
          f"{skipped} skipped, {failed} critical failure{'s' if failed != 1 else ''}")

    if failed:
        print("BLOCKED: Fix critical failures before spawning.")
        return 1
    else:
        print("Ready to spawn.")
        return 0


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Octobots spawn readiness checker. Run from the project directory.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--check",
        choices=["infra-only", "files-only"],
        help="infra-only: checks 5-7 (script consistency). files-only: checks 1-4, 8-10.",
    )
    args = parser.parse_args()

    roles = get_configured_roles()
    results: list[CheckResult] = []

    infra_checks = {5, 6, 7}
    file_checks = {1, 2, 3, 4, 8, 9, 10}

    def should_run(n: int) -> bool:
        if args.check == "infra-only":
            return n in infra_checks
        if args.check == "files-only":
            return n in file_checks
        return True

    if should_run(1):
        results.append(check_1_relay_db())
    if should_run(2):
        results.append(check_2_memory_files(roles))
    if should_run(3):
        results.append(check_3_role_files(roles))
    if should_run(4):
        results.append(check_4_agent_symlinks(roles))
    if should_run(5):
        results.append(check_5_role_aliases(roles))
    if should_run(6):
        results.append(check_6_display_vs_theme(roles))
    if should_run(7):
        results.append(check_7_telegram_team(roles))
    if should_run(8):
        results.append(check_8_env_worker(roles))
    if should_run(9):
        results.append(check_9_claude_md())
    if should_run(10):
        results.append(check_10_agents_md())

    return print_results(results)


if __name__ == "__main__":
    sys.exit(main())
