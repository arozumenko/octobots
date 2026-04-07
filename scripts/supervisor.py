#!/usr/bin/env python3
"""Octobots Supervisor — manages Claude Code workers in tmux with a Rich TUI.

Replaces supervisor.sh with a proper Python implementation.

Usage:
  python octobots/scripts/supervisor.py
  python octobots/scripts/supervisor.py --interval 10
  python octobots/scripts/supervisor.py --workers python-dev js-dev
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text
from rich import box

from scheduler import JobStore, Scheduler, JobType, JobAction, parse_interval, format_interval
from roles import ROLE_ALIASES, ROLE_DISPLAY, resolve_alias

# ── Paths ───────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent
OCTOBOTS_DIR = SCRIPT_DIR.parent
PROJECT_DIR = Path.cwd()
RUNTIME_DIR = PROJECT_DIR / ".octobots"
RELAY_SCRIPT = OCTOBOTS_DIR / "skills" / "taskbox" / "scripts" / "relay.py"
BASE_ROLES = OCTOBOTS_DIR / "roles"
LOCAL_ROLES = RUNTIME_DIR / "roles"

TMUX_SESSION = "octobots"
EXCLUDED_ROLES = {"scout"}
WORKTREE_ROLES = {"python-dev", "js-dev"}

# Role theming — colors and display names for tmux panes
ROLE_THEME: dict[str, dict[str, str]] = {
    "project-manager": {"color": "colour213", "icon": "📋", "name": "pm"},
    "python-dev":      {"color": "colour117", "icon": "🐍", "name": "py"},
    "js-dev":          {"color": "colour220", "icon": "⚡", "name": "js"},
    "qa-engineer":     {"color": "colour156", "icon": "🧪", "name": "qa"},
    "ba":              {"color": "colour183", "icon": "📝", "name": "ba"},
    "tech-lead":       {"color": "colour209", "icon": "🏗️", "name": "tl"},
    "scout":           {"color": "colour252", "icon": "🔍", "name": "scout"},
}

console = Console()


# ── .env.octobots loader ────────────────────────────────────────────────────

def load_env() -> None:
    # Search project root first, then octobots repo (fallback)
    for env_path in [PROJECT_DIR / ".env.octobots", OCTOBOTS_DIR / ".env.octobots"]:
        if env_path.is_file():
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip().strip("\"'")
                if key and key not in os.environ:
                    os.environ[key] = value


# ── Taskbox ─────────────────────────────────────────────────────────────────

class Taskbox:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    def _db(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY, sender TEXT NOT NULL, recipient TEXT NOT NULL,
                content TEXT NOT NULL, response TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                created_at REAL NOT NULL, updated_at REAL NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_inbox ON messages(recipient, status)")
        conn.commit()
        return conn

    def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._db()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY, sender TEXT NOT NULL, recipient TEXT NOT NULL,
                content TEXT NOT NULL, response TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                created_at REAL NOT NULL, updated_at REAL NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_inbox ON messages(recipient, status)")
        conn.commit()
        conn.close()

    def inbox(self, role: str, limit: int = 1) -> list[dict]:
        conn = self._db()
        rows = conn.execute(
            "SELECT id, sender, content, created_at FROM messages "
            "WHERE recipient = ? AND status = 'pending' ORDER BY created_at ASC LIMIT ?",
            (role, limit),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def claim(self, msg_id: str) -> bool:
        conn = self._db()
        cur = conn.execute(
            "UPDATE messages SET status='processing', updated_at=? WHERE id=? AND status='pending'",
            (time.time(), msg_id),
        )
        conn.commit()
        conn.close()
        return cur.rowcount > 0

    def stats(self) -> dict[str, dict[str, int]]:
        conn = self._db()
        rows = conn.execute(
            "SELECT recipient, status, COUNT(*) as count FROM messages GROUP BY recipient, status"
        ).fetchall()
        conn.close()
        result: dict[str, dict[str, int]] = {}
        for r in rows:
            result.setdefault(r["recipient"], {})[r["status"]] = r["count"]
        return result

    def requeue_processing(self, role: str) -> int:
        """Move processing messages for a role back to pending so they get re-delivered."""
        conn = self._db()
        cur = conn.execute(
            "UPDATE messages SET status='pending', updated_at=? "
            "WHERE recipient=? AND status='processing'",
            (time.time(), role),
        )
        conn.commit()
        count = cur.rowcount
        conn.close()
        return count

    def requeue_all_processing(self) -> int:
        """Move all processing messages back to pending (used on startup)."""
        conn = self._db()
        cur = conn.execute(
            "UPDATE messages SET status='pending', updated_at=? WHERE status='processing'",
            (time.time(),),
        )
        conn.commit()
        count = cur.rowcount
        conn.close()
        return count

    def active_tasks(self) -> list[dict]:
        """Return all pending and processing messages."""
        conn = self._db()
        rows = conn.execute(
            "SELECT id, sender, recipient, status, content, created_at "
            "FROM messages WHERE status IN ('pending', 'processing') "
            "ORDER BY created_at ASC"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def abandon_all(self) -> int:
        """Mark all pending and processing messages as done (hard reset)."""
        conn = self._db()
        cur = conn.execute(
            "UPDATE messages SET status='done', response='abandoned', updated_at=? "
            "WHERE status IN ('pending', 'processing')",
            (time.time(),),
        )
        conn.commit()
        count = cur.rowcount
        conn.close()
        return count

    def pending_count(self) -> int:
        conn = self._db()
        row = conn.execute("SELECT COUNT(*) FROM messages WHERE status='pending'").fetchone()
        conn.close()
        return row[0] if row else 0

    def counts_for(self, role: str) -> dict[str, int]:
        """Return pending and processing counts for a specific role."""
        conn = self._db()
        rows = conn.execute(
            "SELECT status, COUNT(*) as n FROM messages "
            "WHERE recipient = ? AND status IN ('pending', 'processing') GROUP BY status",
            (role,),
        ).fetchall()
        conn.close()
        result = {"pending": 0, "processing": 0}
        for r in rows:
            result[r["status"]] = r["n"]
        return result

    def undelivered_responses(self, limit: int = 10) -> list[dict]:
        """Fetch ack responses that haven't been delivered back to the sender."""
        conn = self._db()
        rows = conn.execute(
            "SELECT id, sender, recipient, content, response, updated_at FROM messages "
            "WHERE status = 'done' AND response != '' AND response_delivered = 0 "
            "ORDER BY updated_at ASC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def mark_response_delivered(self, msg_id: str) -> None:
        conn = self._db()
        conn.execute(
            "UPDATE messages SET response_delivered = 1, updated_at = ? WHERE id = ?",
            (time.time(), msg_id),
        )
        conn.commit()
        conn.close()

    def _ensure_schema(self) -> None:
        """Add response_delivered column if it doesn't exist (migration)."""
        conn = self._db()
        try:
            conn.execute("SELECT response_delivered FROM messages LIMIT 1")
        except sqlite3.OperationalError:
            conn.execute("ALTER TABLE messages ADD COLUMN response_delivered INTEGER DEFAULT 0")
            # Mark all existing messages as delivered so we don't replay history
            conn.execute("UPDATE messages SET response_delivered = 1 WHERE status = 'done'")
            conn.commit()
        conn.close()

    def mark_all_responses_delivered(self) -> None:
        """Mark all current responses as delivered (used on startup to avoid replay)."""
        conn = self._db()
        conn.execute(
            "UPDATE messages SET response_delivered = 1 WHERE status = 'done' AND response != '' AND response_delivered = 0"
        )
        conn.commit()
        conn.close()


# ── Tmux ────────────────────────────────────────────────────────────────────

class TmuxManager:
    def __init__(self, session: str = TMUX_SESSION):
        self.session = session
        self.panes: dict[str, str] = {}   # role → pane target
        self._placeholder: str | None = None  # ghost pane keeping layout even

    def exists(self) -> bool:
        r = subprocess.run(["tmux", "has-session", "-t", self.session], capture_output=True)
        return r.returncode == 0

    def kill(self) -> None:
        subprocess.run(["tmux", "kill-session", "-t", self.session], capture_output=True)

    def send_keys(self, pane: str, text: str, confirm_paste: bool = False) -> bool:
        single = text.replace("\n", " ").strip()
        try:
            subprocess.run(
                ["tmux", "send-keys", "-t", pane, single, "Enter"],
                check=True, capture_output=True,
            )
            if confirm_paste:
                # Claude Code shows "[Pasted text #N +N lines]" for long input
                # and waits for Enter to submit. Send a second Enter after a
                # short delay to confirm the paste.
                time.sleep(1)
                subprocess.run(
                    ["tmux", "send-keys", "-t", pane, "Enter"],
                    check=True, capture_output=True,
                )
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    def capture_pane(self, pane: str, lines: int = 20) -> str:
        try:
            r = subprocess.run(
                ["tmux", "capture-pane", "-t", pane, "-p", "-S", f"-{lines}"],
                capture_output=True, text=True,
            )
            return r.stdout
        except (subprocess.CalledProcessError, FileNotFoundError):
            return ""

    def create_session(self, workers: list[str]) -> None:
        if self.exists():
            console.print("[yellow]tmux session already exists. Killing it.[/yellow]")
            self.kill()
            time.sleep(1)

        # Create session with tiled dashboard
        subprocess.run(
            ["tmux", "new-session", "-d", "-s", self.session, "-n", "dashboard"],
            check=True,
        )

        # Create panes
        for i in range(1, len(workers)):
            subprocess.run(
                ["tmux", "split-window", "-t", f"{self.session}:dashboard", "-h"],
                check=True, capture_output=True,
            )
            subprocess.run(
                ["tmux", "select-layout", "-t", f"{self.session}:dashboard", "tiled"],
                capture_output=True,
            )
        subprocess.run(
            ["tmux", "select-layout", "-t", f"{self.session}:dashboard", "tiled"],
            capture_output=True,
        )

        # Map roles to panes and apply theming
        for i, worker in enumerate(workers):
            pane_target = f"{self.session}:dashboard.{i}"
            self.panes[worker] = pane_target

            theme = ROLE_THEME.get(worker, {"color": "colour250", "icon": "🤖", "name": worker})

            # Set pane border color and title
            subprocess.run([
                "tmux", "select-pane", "-t", pane_target,
                "-T", f"{theme['icon']} {theme['name']}",
                "-P", f"fg={theme['color']}",
            ], capture_output=True)

        # Enable pane titles and borders
        subprocess.run([
            "tmux", "set-option", "-t", self.session, "pane-border-status", "top",
        ], capture_output=True)
        subprocess.run([
            "tmux", "set-option", "-t", self.session, "pane-border-format",
            " #{pane_title} ",
        ], capture_output=True)
        subprocess.run([
            "tmux", "set-option", "-t", self.session, "pane-border-style", "fg=colour240",
        ], capture_output=True)
        subprocess.run([
            "tmux", "set-option", "-t", self.session, "pane-active-border-style", "fg=colour75,bold",
        ], capture_output=True)

        # Ensure even pane count from the start
        self._sync_placeholder()

    def _alloc_pane(self) -> str:
        """Split a new pane, re-tile, return its target string."""
        subprocess.run(
            ["tmux", "split-window", "-t", f"{self.session}:dashboard", "-h"],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["tmux", "select-layout", "-t", f"{self.session}:dashboard", "tiled"],
            capture_output=True,
        )
        # Identify the new index — not yet tracked in self.panes or _placeholder
        r = subprocess.run(
            ["tmux", "list-panes", "-t", f"{self.session}:dashboard", "-F", "#{pane_index}"],
            capture_output=True, text=True,
        )
        known = {p.split(".")[-1] for p in self.panes.values()}
        if self._placeholder:
            known.add(self._placeholder.split(".")[-1])
        indices = [i.strip() for i in r.stdout.strip().splitlines() if i.strip()]
        new_idx = next((i for i in indices if i not in known), indices[-1])
        return f"{self.session}:dashboard.{new_idx}"

    def _sync_placeholder(self) -> None:
        """Keep pane count even: add a ghost pane when odd, remove it when even."""
        needs = (len(self.panes) % 2 == 1)

        if needs and self._placeholder is None:
            pane_target = self._alloc_pane()
            self._placeholder = pane_target
            subprocess.run([
                "tmux", "select-pane", "-t", pane_target,
                "-T", "·",
                "-P", "fg=colour237",
            ], capture_output=True)

        elif not needs and self._placeholder is not None:
            subprocess.run(["tmux", "kill-pane", "-t", self._placeholder], capture_output=True)
            self._placeholder = None
            subprocess.run(
                ["tmux", "select-layout", "-t", f"{self.session}:dashboard", "tiled"],
                capture_output=True,
            )

    def add_pane(self, role: str) -> str:
        """Add a role pane, keep layout even with placeholder logic."""
        # Kill placeholder first so _alloc_pane finds the right new index
        if self._placeholder:
            subprocess.run(["tmux", "kill-pane", "-t", self._placeholder], capture_output=True)
            self._placeholder = None

        pane_target = self._alloc_pane()
        self.panes[role] = pane_target

        theme = ROLE_THEME.get(role, {"color": "colour250", "icon": "🤖", "name": role})
        subprocess.run([
            "tmux", "select-pane", "-t", pane_target,
            "-T", f"{theme['icon']} {theme['name']}",
            "-P", f"fg={theme['color']}",
        ], capture_output=True)

        self._sync_placeholder()
        return pane_target

    def kill_pane(self, pane: str) -> None:
        """Kill a role pane, keep layout even with placeholder logic."""
        # Kill placeholder first — it will be re-evaluated after
        if self._placeholder:
            subprocess.run(["tmux", "kill-pane", "-t", self._placeholder], capture_output=True)
            self._placeholder = None

        subprocess.run(["tmux", "kill-pane", "-t", pane], capture_output=True)
        subprocess.run(
            ["tmux", "select-layout", "-t", f"{self.session}:dashboard", "tiled"],
            capture_output=True,
        )
        self._sync_placeholder()

    def save_pane_map(self) -> None:
        pane_map = RUNTIME_DIR / ".pane-map"
        pane_map.write_text(
            "\n".join(f"{role}={target}" for role, target in self.panes.items())
        )


# ── Role Resolution ─────────────────────────────────────────────────────────

def resolve_role(role: str) -> Path | None:
    local = LOCAL_ROLES / role / "AGENT.md"
    base = BASE_ROLES / role / "AGENT.md"
    if local.is_file():
        return local.parent
    if base.is_file():
        return base.parent
    return None


def discover_workers() -> list[str]:
    env_workers = os.environ.get("OCTOBOTS_WORKERS", "")
    if env_workers:
        return env_workers.split()

    excluded = set(os.environ.get("OCTOBOTS_EXCLUDED_ROLES", "scout").split())
    seen: set[str] = set()
    workers: list[str] = []

    for roles_dir in [LOCAL_ROLES, BASE_ROLES]:
        if not roles_dir.is_dir():
            continue
        for role_dir in sorted(roles_dir.iterdir()):
            if not role_dir.is_dir():
                continue
            role = role_dir.name
            if role in seen or role in excluded:
                continue
            if (role_dir / "AGENT.md").is_file():
                seen.add(role)
                workers.append(role)

    return workers


# ── Supervisor ──────────────────────────────────────────────────────────────

class Supervisor:
    def __init__(self, workers: list[str], interval: int = 15):
        self.workers = workers
        self.interval = interval
        self.tmux = TmuxManager()
        self.taskbox = Taskbox(RUNTIME_DIR / "relay.db")
        self.launched: set[str] = set()
        self._role_source: dict[str, str] = {}  # worker_id → source role (for clones)
        self._running = True
        # Per-role recycle state for Ollama-backed workers. Auto-compact is
        # disabled for them; supervisor recycles the session when context
        # usage crosses a threshold. Three-stage state machine per role:
        #   stage 0 (idle)         → entry not in dict
        #   stage 1 (checkpointed) → {"checkpoint_at": epoch}
        #   stage 2 (cleared)      → {"checkpoint_at": ..., "cleared_at": epoch}
        self._ollama_recycle: dict[str, dict] = {}
        # One-shot warning so we don't spam logs when jsonl can't be located.
        self._ollama_jsonl_warned: set[str] = set()

        # Scheduler
        self.job_store = JobStore(RUNTIME_DIR / "schedule.json")
        self.scheduler = Scheduler(
            store=self.job_store,
            taskbox=self.taskbox,
            tmux=self.tmux,
            relay_script=RELAY_SCRIPT,
            octobots_dir=OCTOBOTS_DIR,
            runtime_dir=RUNTIME_DIR,
            on_event=self._on_scheduled_event,
        )

    def preflight(self) -> bool:
        ok = True
        # `claude` and `copilot` are checked lazily per-role in spawn(), since
        # mixed teams may use either or both runtimes.
        for cmd in ["tmux", "python3", "gh", "git"]:
            if not shutil.which(cmd):
                console.print(f"[red]✗ {cmd} not found[/red]")
                ok = False
        return ok

    def _get_gh_app_token(self) -> str:
        """Get GitHub App installation token if configured."""
        if os.environ.get("OCTOBOTS_GH_APP_ID"):
            try:
                gh_token_script = SCRIPT_DIR / "gh-token.py"
                r = subprocess.run(
                    ["python3", str(gh_token_script)],
                    capture_output=True, text=True, timeout=15,
                    cwd=str(PROJECT_DIR),
                )
                if r.returncode == 0 and r.stdout.strip():
                    return r.stdout.strip()
                if r.stderr:
                    console.print(f"[yellow]GitHub App token: {r.stderr.strip()}[/yellow]")
            except Exception as e:
                console.print(f"[yellow]GitHub App token failed: {e}[/yellow]")
        return ""

    def _resolve_gh_token(self, role: str) -> str:
        """Resolve the GitHub token for a specific role.

        Resolution order:
        1. OCTOBOTS_GH_TOKEN_<ROLE> — per-role token (role name uppercased, dashes → underscores)
           e.g. OCTOBOTS_GH_TOKEN_PROJECT_MANAGER, OCTOBOTS_GH_TOKEN_PYTHON_DEV
        2. OCTOBOTS_GH_TOKEN — shared token for all roles
        3. GitHub App installation token (if configured)
        4. Empty string — worker falls back to personal gh auth
        """
        # Per-role token
        role_key = f"OCTOBOTS_GH_TOKEN_{role.upper().replace('-', '_')}"
        per_role = os.environ.get(role_key, "")
        if per_role:
            return per_role

        # Shared token
        shared = os.environ.get("OCTOBOTS_GH_TOKEN", "")
        if shared:
            return shared

        # GitHub App token (shared across roles)
        return self._gh_app_token

    def setup(self) -> None:
        # Init taskbox
        self.taskbox.init()
        self.taskbox._ensure_schema()
        self.taskbox.mark_all_responses_delivered()  # don't replay old responses

        # Get GitHub App token (used as fallback if no per-role tokens)
        self._gh_app_token = self._get_gh_app_token()

        # Show token configuration
        has_per_role = any(
            os.environ.get(f"OCTOBOTS_GH_TOKEN_{r.upper().replace('-', '_')}")
            for r in self.workers
        )
        has_shared = bool(os.environ.get("OCTOBOTS_GH_TOKEN"))

        if has_per_role:
            configured = [
                r for r in self.workers
                if os.environ.get(f"OCTOBOTS_GH_TOKEN_{r.upper().replace('-', '_')}")
            ]
            console.print(f"[green]✓ Per-role GH tokens:[/green] {', '.join(configured)}")
            unconfigured = [r for r in self.workers if r not in configured]
            if unconfigured:
                fallback = "shared token" if has_shared else ("GitHub App" if self._gh_app_token else "personal gh auth")
                console.print(f"[dim]  Others use {fallback}: {', '.join(unconfigured)}[/dim]")
        elif has_shared:
            console.print("[green]✓ Shared GH token for all roles[/green]")
        elif self._gh_app_token:
            console.print("[green]✓ GitHub App authenticated (all roles)[/green]")
        else:
            console.print("[dim]No GH tokens configured — using personal gh auth[/dim]")

        # Requeue any interrupted tasks from previous run
        requeued = self.taskbox.requeue_all_processing()
        if requeued:
            console.print(f"[yellow]↩ Requeued {requeued} interrupted task(s) → pending[/yellow]")

        # Show active task summary before launching workers
        active = self.taskbox.active_tasks()
        if active:
            console.print(f"[cyan]📋 {len(active)} task(s) queued for delivery:[/cyan]")
            for t in active:
                preview = t["content"].replace("\n", " ")[:70]
                console.print(f"  [dim]{t['recipient']:15} ← {t['sender']:15} {preview}[/dim]")

        # Create tmux session
        self.tmux.create_session(self.workers)

        # Launch Claude in each pane
        for role in self.workers:
            self._launch_worker(role)

        self.tmux.save_pane_map()
        self._ensure_board()
        self._write_roster()

    def _launch_worker(self, role: str) -> None:
        # For clones, source_role is the definition; role is the instance id
        source_role = self._role_source.get(role, role)
        role_dir = resolve_role(source_role)
        if not role_dir:
            console.print(f"[red]✗ {role}: AGENT.md not found (source: {source_role})[/red]")
            return

        pane = self.tmux.panes.get(role, "")
        if not pane:
            return

        worker_dir = RUNTIME_DIR / "workers" / role
        # Determine launch directory by AGENT.md frontmatter `workspace:` field:
        #   clone   → isolated worker_dir (own repo clone)
        #   shared  → project root (default)
        #   <unset> → project root
        # Legacy `.workspace-root` marker file is still honored as an override
        # for roles that haven't migrated to the frontmatter convention.
        workspace_kind = "shared"
        if role_dir:
            try:
                fm_text = (role_dir / "AGENT.md").read_text(encoding="utf-8", errors="replace")
                import re as _re_ws
                m = _re_ws.search(r'^workspace:\s*(\w+)', fm_text, _re_ws.MULTILINE)
                if m:
                    workspace_kind = m.group(1).strip().lower()
            except OSError:
                pass
        forces_root = role_dir and (role_dir / ".workspace-root").is_file()
        if forces_root or workspace_kind != "clone":
            launch_dir = PROJECT_DIR
            env_label = "root" if forces_root else "shared"
        else:
            launch_dir = worker_dir if worker_dir.is_dir() else PROJECT_DIR
            env_label = "isolated" if worker_dir.is_dir() else "shared"
        label = f"{role} [{source_role}]" if source_role != role else role
        console.print(f"[cyan]◆[/cyan] {label} → {launch_dir} [{env_label}]")

        db_path = RUNTIME_DIR / "relay.db"
        gh_token = self._resolve_gh_token(source_role)
        gh_env = f"GH_TOKEN={gh_token} " if gh_token else ""
        # NOTE: Do NOT pass OCTOBOTS_TG_TOKEN/OCTOBOTS_TG_OWNER here.
        # Shell scripts (notify-user.sh, send-file.sh) read .env.octobots
        # fresh on every invocation, so edits take effect immediately
        # without restarting workers.

        # Register source role as a named agent in the launch dir.
        # Be defensive: a stale or self-looping symlink left over from a
        # crashed earlier run will make Path.resolve() raise RuntimeError
        # ("Symlink loop") or OSError ("Too many levels of symbolic links").
        # In either case, just unlink and recreate.
        agents_dir = launch_dir / ".claude" / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True)
        agent_link = agents_dir / source_role
        if agent_link.is_symlink():
            try:
                stale = agent_link.resolve() != role_dir.resolve()
            except (OSError, RuntimeError):
                stale = True  # broken or looped — treat as stale
            if stale:
                agent_link.unlink()
        if not agent_link.exists():
            agent_link.symlink_to(role_dir)

        # Inject CLAUDE.md into isolated worker dirs so Claude auto-loads context.
        # workspace:clone workers get a generated file with their exact workspace path —
        # a symlink would give every worker the same file with no path info.
        # Other isolated workers still get a symlink to the project CLAUDE.md.
        if launch_dir != PROJECT_DIR:
            import re as _re
            agent_md_text = (role_dir / "AGENT.md").read_text() if role_dir else ""
            is_clone_workspace = bool(_re.search(r'^workspace:\s*clone', agent_md_text, _re.MULTILINE))
            worker_claude = launch_dir / "CLAUDE.md"

            if is_clone_workspace:
                # Remove stale symlink if present; regenerate real file each launch
                # so the path stays accurate if worker_dir moves.
                if worker_claude.is_symlink():
                    worker_claude.unlink()
                cloned_repos = [p.name for p in launch_dir.iterdir() if (p / ".git").is_dir()]
                repo_lines = "\n".join(f"- `{launch_dir / r}/`" for r in sorted(cloned_repos))
                project_claude = PROJECT_DIR / "CLAUDE.md"
                project_content = project_claude.read_text() if project_claude.is_file() else ""
                worker_claude.write_text(
                    f"# YOUR WORKSPACE — CRITICAL\n\n"
                    f"You are running from an isolated workspace, NOT the project root.\n\n"
                    f"**Your workspace root:** `{launch_dir}/`\n\n"
                    f"**Your cloned repos:**\n{repo_lines or '(none yet — check worker dir)'}\n\n"
                    f"All file paths, git commands, and tool calls must use these paths.\n"
                    f"Do NOT assume you are at `{PROJECT_DIR}/`.\n\n"
                    f"---\n\n"
                    + project_content
                )
            else:
                project_claude = PROJECT_DIR / "CLAUDE.md"
                if project_claude.is_file():
                    if worker_claude.is_symlink() and worker_claude.resolve() != project_claude.resolve():
                        worker_claude.unlink()
                    if not worker_claude.exists():
                        worker_claude.symlink_to(project_claude)

        # ── Per-role runtime dispatch (claude | copilot) ─────────────────
        # Read `runtime:` from AGENT.md frontmatter; default = claude.
        runtime = "claude"
        if role_dir and (role_dir / "AGENT.md").is_file():
            import re as _re_rt
            _txt = (role_dir / "AGENT.md").read_text()
            _m = _re_rt.search(r"^---\s*\n(.*?)\n---", _txt, _re_rt.DOTALL)
            if _m:
                _rt = _re_rt.search(r"^runtime:\s*(\S+)", _m.group(1), _re_rt.MULTILINE)
                if _rt:
                    runtime = _rt.group(1).strip()

        # Local-model opt-in via .env.octobots (no AGENT.md edits needed):
        #   OCTOBOTS_OLLAMA_ROLES="personal-assistant ba"
        #   OCTOBOTS_OLLAMA_MODEL=gemma4:26b
        #   OCTOBOTS_OLLAMA_MODEL_PERSONAL_ASSISTANT=...   (optional override)
        ollama_model = ""
        ollama_roles = os.environ.get("OCTOBOTS_OLLAMA_ROLES", "").split()
        if role in ollama_roles:
            role_var = "OCTOBOTS_OLLAMA_MODEL_" + role.upper().replace("-", "_")
            ollama_model = os.environ.get(role_var) or os.environ.get("OCTOBOTS_OLLAMA_MODEL", "")

        if runtime == "claude":
            if ollama_model:
                # Per-role local model: wrap with `ollama launch claude`,
                # which exec's real Claude Code with the right env vars set.
                if not shutil.which("ollama"):
                    console.print(f"[red]✗ {role}: ollama binary not found (role is in OCTOBOTS_OLLAMA_ROLES with model {ollama_model})[/red]")
                    return
                # Local models choke on Claude Code's auto-compact path
                # (gemma-via-ollama returns malformed tool-use and the session
                # hangs). Disable it; we instead recycle the pane periodically
                # via _recycle_ollama_workers().
                agent_cmd = (
                    f"{gh_env}OCTOBOTS_ID={role} OCTOBOTS_DB={db_path} "
                    f"DISABLE_AUTO_COMPACT=1 CLAUDE_CODE_DISABLE_AUTO_COMPACT=1 "
                    f"ollama launch claude --model {ollama_model} --yes -- "
                    f"--agent '{source_role}' --dangerously-skip-permissions"
                )
            else:
                if not shutil.which("claude"):
                    console.print(f"[red]✗ {role}: claude binary not found[/red]")
                    return
                agent_cmd = (
                    f"{gh_env}OCTOBOTS_ID={role} OCTOBOTS_DB={db_path} "
                    f"CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD=1 "
                    f"claude --agent '{source_role}' --dangerously-skip-permissions"
                )
        elif runtime == "copilot":
            if not shutil.which("copilot"):
                console.print(f"[red]✗ {role}: copilot binary not found (https://gh.io/copilot-install)[/red]")
                return
            # Materialize translated .agent.md into $COPILOT_HOME/agents/.
            try:
                subprocess.run(
                    ["python3", str(OCTOBOTS_DIR / "scripts" / "sync-copilot-agents.py"), str(role_dir)],
                    check=True, capture_output=True,
                )
            except subprocess.CalledProcessError as e:
                console.print(f"[red]✗ {role}: copilot agent sync failed: {e.stderr.decode().strip()}[/red]")
                return
            agent_cmd = (
                f"{gh_env}OCTOBOTS_ID={role} OCTOBOTS_DB={db_path} "
                f"copilot --agent '{source_role}' --allow-all"
            )
        else:
            console.print(f"[red]✗ {role}: unknown runtime '{runtime}' (expected claude|copilot)[/red]")
            return

        # cd + launch in one atomic command so the agent starts from launch_dir.
        cmd = f"cd '{launch_dir}' && {agent_cmd}"
        self.tmux.send_keys(pane, cmd, confirm_paste=True)
        self.launched.add(role)
        time.sleep(3)

    # ── Board ────────────────────────────────────────────────────────────────

    def _ensure_board(self) -> None:
        """Create a default board.md if it doesn't exist."""
        board_path = RUNTIME_DIR / "board.md"
        if board_path.is_file():
            return
        board_path.parent.mkdir(parents=True, exist_ok=True)
        board_path.write_text(
            "# Team Board\n\n"
            "## Team\n\n"
            "_Supervisor-maintained. Route taskbox messages to the Worker ID, not the role name._\n\n"
            "## Active Work\n\n"
            "_No active tasks._\n"
        )

    def _update_board_section(self, section: str, content: str) -> None:
        """Replace a named ## section in board.md, preserving all other sections."""
        import re as _re
        board_path = RUNTIME_DIR / "board.md"
        self._ensure_board()
        text = board_path.read_text()
        pattern = rf'(## {_re.escape(section)}\n).*?(?=\n## |\Z)'
        replacement = rf'\g<1>{content}'
        new_text = _re.sub(pattern, replacement, text, flags=_re.DOTALL)
        if new_text == text and f'## {section}' not in text:
            new_text = text.rstrip() + f'\n\n## {section}\n\n{content}'
        board_path.write_text(new_text)

    def _write_roster(self) -> None:
        """Update the Team section of board.md with current worker roster."""
        import re as _re

        lines = [
            "_Supervisor-maintained. Route taskbox messages to the Worker ID, not the role name._\n\n",
            "| Worker ID | Role | Workspace | Skills |\n",
            "|-----------|------|-----------|--------|\n",
        ]

        for worker in self.workers:
            source = self._role_source.get(worker, worker)
            role_dir = resolve_role(source)

            worker_dir = RUNTIME_DIR / "workers" / worker
            if role_dir and (role_dir / ".workspace-root").is_file():
                workspace = "root"
            elif worker_dir.is_dir():
                agent_md_text = (role_dir / "AGENT.md").read_text() if role_dir else ""
                workspace = "clone" if _re.search(r'^workspace:\s*clone', agent_md_text, _re.MULTILINE) else "isolated"
            else:
                workspace = "shared"

            allowed = self._role_skills(source)
            skills_str = ", ".join(sorted(allowed)) if allowed else "all"
            role_label = f"{source} *(clone)*" if source != worker else source
            lines.append(f"| `{worker}` | {role_label} | {workspace} | {skills_str} |\n")

        self._update_board_section("Team", "".join(lines))

    def _write_active_work(self) -> None:
        """Update the Active Work section of board.md from live taskbox state."""
        active = self.taskbox.active_tasks()
        if not active:
            content = "_No active tasks._\n"
        else:
            rows = [
                "| Worker | Task | Status |\n",
                "|--------|------|--------|\n",
            ]
            for t in active:
                preview = t["content"].replace("\n", " ")[:70]
                status_icon = "⚙" if t["status"] == "processing" else "⏳"
                rows.append(f"| `{t['recipient']}` | {preview} | {status_icon} {t['status']} |\n")
            content = "".join(rows)
        self._update_board_section("Active Work", content)

    # ── Role management ──────────────────────────────────────────────────────

    def _role_skills(self, role: str) -> set[str] | None:
        """Return declared skills from AGENT.md frontmatter, or None (no filter)."""
        import re
        role_dir = resolve_role(role)
        if not role_dir:
            return None
        agent_md = role_dir / "AGENT.md"
        if not agent_md.is_file():
            return None
        m = re.search(r'^skills:\s*\[([^\]]*)\]', agent_md.read_text(), re.MULTILINE)
        if not m:
            return None
        return {s.strip() for s in m.group(1).split(",") if s.strip()}

    def _setup_worker_env(self, role: str, source_role: str | None = None) -> None:
        """Create .octobots/workers/<role>/ with symlinks, env, .claude/ seeding.

        source_role: the role definition to use (defaults to role). Used for clones
                     where the worker id differs from the role definition name.
        """
        source = source_role or role
        worker_dir = RUNTIME_DIR / "workers" / role
        if worker_dir.is_dir():
            return  # already set up

        worker_dir.mkdir(parents=True)

        # Standard symlinks into the project
        for src, name in [
            (OCTOBOTS_DIR, "octobots"),
            (RUNTIME_DIR, ".octobots"),
        ]:
            link = worker_dir / name
            if src.exists() and not link.exists():
                link.symlink_to(src)

        for fname in ["AGENTS.md", ".env", ".env.octobots"]:
            src = PROJECT_DIR / fname
            link = worker_dir / fname
            if src.is_file() and not link.exists():
                link.symlink_to(src)

        for dname in ["venv", "node_modules"]:
            src = PROJECT_DIR / dname
            link = worker_dir / dname
            if src.is_dir() and not link.exists():
                link.symlink_to(src)

        # Worker env file
        db_path = RUNTIME_DIR / "relay.db"
        (worker_dir / ".env.worker").write_text(
            f"WORKER_ID={role}\nOCTOBOTS_ID={role}\nOCTOBOTS_DB={db_path}\n"
        )

        # Seed .claude/agents/ — link source role definition, not worker id
        agents_dir = worker_dir / ".claude" / "agents"
        skills_dir = worker_dir / ".claude" / "skills"
        agents_dir.mkdir(parents=True, exist_ok=True)
        skills_dir.mkdir(parents=True, exist_ok=True)

        role_dir = resolve_role(source)
        if role_dir:
            link = agents_dir / source  # always link under the source role name
            if not link.exists():
                link.symlink_to(role_dir)

        shared_agents = OCTOBOTS_DIR / "shared" / "agents"
        if shared_agents.is_dir():
            for agent_dir in shared_agents.iterdir():
                if agent_dir.is_dir():
                    link = agents_dir / agent_dir.name
                    if not link.exists():
                        link.symlink_to(agent_dir)

        allowed = self._role_skills(source)
        skills_base = OCTOBOTS_DIR / "skills"
        if skills_base.is_dir():
            for skill_dir in skills_base.iterdir():
                if skill_dir.is_dir():
                    if allowed is not None and skill_dir.name not in allowed:
                        continue
                    link = skills_dir / skill_dir.name
                    if not link.exists():
                        link.symlink_to(skill_dir)

        # Memory file — clones get their own memory, seeded from source if exists
        memory_file = RUNTIME_DIR / "memory" / f"{role}.md"
        if not memory_file.is_file():
            source_memory = RUNTIME_DIR / "memory" / f"{source}.md"
            if source_role and source_memory.is_file():
                # Clone inherits source memory as starting point
                import shutil as _sh
                _sh.copy2(source_memory, memory_file)
            else:
                memory_file.write_text(
                    f"# Memory — {role}\n\nPersistent learnings from past conversations. "
                    "Read this before starting work.\n\n"
                    "## Project Knowledge\n\n## Lessons Learned\n\n## Notes\n"
                )

    def _clone_repos_for_worker(self, worker_dir: Path) -> int:
        """Clone all project git repos into the worker dir. Returns count cloned."""
        cloned = 0
        for git_dir in PROJECT_DIR.glob("*/.git"):
            repo_path = git_dir.parent
            repo_name = repo_path.name
            if repo_name == "octobots":
                continue
            dest = worker_dir / repo_name
            if dest.exists():
                continue
            try:
                origin = subprocess.run(
                    ["git", "-C", str(repo_path), "remote", "get-url", "origin"],
                    capture_output=True, text=True, check=True,
                ).stdout.strip()
                subprocess.run(
                    ["git", "clone", "--quiet", origin, str(dest)],
                    capture_output=True, check=True,
                )
                cloned += 1
            except subprocess.CalledProcessError:
                console.print(f"[yellow]  ✗ could not clone {repo_name}[/yellow]")
        return cloned

    def _role_clone(self, source_role: str, alias: str | None = None) -> None:
        """Spawn a clone of source_role with its own isolated workspace."""
        if resolve_role(source_role) is None:
            console.print(f"[red]Unknown role: {source_role}[/red]")
            return

        # Auto-generate alias if not provided
        if alias is None:
            n = 2
            while f"{source_role}-{n}" in self.workers:
                n += 1
            alias = f"{source_role}-{n}"

        if alias in self.workers:
            console.print(f"[yellow]{alias} is already running.[/yellow]")
            return

        # Record source mapping before setup
        self._role_source[alias] = source_role

        self._setup_worker_env(alias, source_role=source_role)

        # Clone repos if workspace: clone is declared on the source role
        import re as _re
        role_dir = resolve_role(source_role)
        agent_md = (role_dir / "AGENT.md").read_text() if role_dir else ""
        if _re.search(r'^workspace:\s*clone', agent_md, _re.MULTILINE):
            worker_dir = RUNTIME_DIR / "workers" / alias
            cloned = self._clone_repos_for_worker(worker_dir)
            if cloned:
                console.print(f"[dim]  Cloned {cloned} repo(s) into {alias} workspace[/dim]")

        pane = self.tmux.add_pane(alias)
        self.workers.append(alias)
        self.tmux.save_pane_map()
        self._write_roster()
        self._launch_worker(alias)
        console.print(f"[green]✓ {alias} (clone of {source_role}) launched[/green]")

    def _teardown_worker_env(self, role: str) -> None:
        """Remove worker dir and undo promotion if the role was promoted from Claude."""
        worker_dir = RUNTIME_DIR / "workers" / role
        promoted = (worker_dir / ".promoted").is_file() if worker_dir.is_dir() else False

        # Remove worker dir
        if worker_dir.is_dir():
            shutil.rmtree(worker_dir)

        # If promoted: restore role dir back to .claude/agents/<role>/
        role_dir = BASE_ROLES / role
        agent_link = PROJECT_DIR / ".claude" / "agents" / role
        if promoted and role_dir.is_dir():
            if agent_link.is_symlink():
                agent_link.unlink()
            shutil.move(str(role_dir), str(agent_link))
            console.print(f"[dim]  Restored {role} → .claude/agents/{role}/[/dim]")

    def _role_add(self, role: str) -> None:
        # Detect GitHub repo format: owner/repo or owner/repo@ref
        if "/" in role:
            ref = "main"
            repo = role
            if "@" in repo:
                repo, ref = repo.rsplit("@", 1)
            console.print(f"[dim]Fetching role {repo}@{ref}...[/dim]")
            installed_name = self._fetch_component("agent", repo, ref)
            if not installed_name:
                return
            role = installed_name

        if role in self.workers:
            console.print(f"[yellow]{role} is already running.[/yellow]")
            return

        # Resolve source: octobots/roles/ or .claude/agents/ (real dir = Claude-created)
        role_dir = resolve_role(role)
        promoted = False

        if role_dir is None:
            agent_candidate = PROJECT_DIR / ".claude" / "agents" / role
            if agent_candidate.is_dir() and not agent_candidate.is_symlink():
                # Promote: move into octobots/roles/, replace with symlink
                dest = BASE_ROLES / role
                shutil.move(str(agent_candidate), str(dest))
                agent_candidate.symlink_to(dest)
                role_dir = dest
                promoted = True
                console.print(f"[cyan]↑ Promoted .claude/agents/{role}/ → octobots/roles/{role}/[/cyan]")
            else:
                console.print(f"[red]Role '{role}' not found in octobots/roles/ or .claude/agents/[/red]")
                return

        # Warn if AGENT.md is missing octobots conventions
        agent_md = role_dir / "AGENT.md"
        if agent_md.is_file():
            content = agent_md.read_text()
            missing = []
            if "taskbox" not in content:
                missing.append("taskbox")
            if "Task complete." not in content and "Session complete." not in content:
                missing.append("session signal")
            if missing:
                console.print(f"[yellow]⚠  {role}/AGENT.md missing octobots conventions: {', '.join(missing)}[/yellow]")
                console.print(f"[dim]   Worker will run but may not integrate cleanly with the team.[/dim]")

        # Set up worker environment
        self._setup_worker_env(role)

        # Mark as promoted so teardown can undo it
        if promoted:
            (RUNTIME_DIR / "workers" / role / ".promoted").touch()

        # Add live tmux pane
        pane = self.tmux.add_pane(role)
        self.workers.append(role)
        self.tmux.save_pane_map()
        self._write_roster()

        # Launch
        self._launch_worker(role)
        console.print(f"[green]✓ {role} added and launched[/green]")

    def _role_remove(self, role: str) -> None:
        if role not in self.workers:
            console.print(f"[red]{role} is not an active worker.[/red]")
            return

        pane = self.tmux.panes.get(role)

        # Graceful exit
        if pane:
            self.tmux.send_keys(pane, "/exit")
            time.sleep(2)
            self.tmux.kill_pane(pane)
            del self.tmux.panes[role]

        self.workers.remove(role)
        self.launched.discard(role)
        self._role_source.pop(role, None)
        self.tmux.save_pane_map()
        self._write_roster()

        # Tear down workspace
        self._teardown_worker_env(role)
        console.print(f"[green]✓ {role} removed[/green]")

    def cmd_role(self, args: list[str]) -> None:
        sub = args[0] if args else "list"

        if sub == "list":
            table = Table(title="Roles", box=box.ROUNDED)
            table.add_column("Role", style="cyan")
            table.add_column("Source")
            table.add_column("Active", justify="center")

            seen: set[str] = set()
            rows: list[tuple[str, str, bool]] = []

            # octobots/roles/
            for d in sorted(BASE_ROLES.iterdir()) if BASE_ROLES.is_dir() else []:
                if d.is_dir() and (d / "AGENT.md").is_file():
                    rows.append((d.name, "octobots/roles/", d.name in self.workers))
                    seen.add(d.name)

            # .octobots/roles/ (local overrides)
            for d in sorted(LOCAL_ROLES.iterdir()) if LOCAL_ROLES.is_dir() else []:
                if d.is_dir() and (d / "AGENT.md").is_file() and d.name not in seen:
                    rows.append((d.name, ".octobots/roles/", d.name in self.workers))
                    seen.add(d.name)

            # .claude/agents/ — real dirs (Claude-created, not yet promoted)
            agents_dir = PROJECT_DIR / ".claude" / "agents"
            if agents_dir.is_dir():
                for d in sorted(agents_dir.iterdir()):
                    if d.is_dir() and not d.is_symlink() and d.name not in seen:
                        rows.append((d.name, ".claude/agents/ (promotable)", d.name in self.workers))
                        seen.add(d.name)

            for name, source, active in rows:
                clone_of = self._role_source.get(name)
                display = f"{name} [dim](clone of {clone_of})[/dim]" if clone_of else name
                table.add_row(
                    display,
                    source,
                    "[green]●[/green]" if active else "[dim]○[/dim]",
                )
            console.print(table)

        elif sub == "add":
            if len(args) < 2:
                console.print("[red]Usage: /role add <name>[/red]")
                return
            self._role_add(args[1])

        elif sub == "remove":
            if len(args) < 2:
                console.print("[red]Usage: /role remove <name>[/red]")
                return
            self._role_remove(args[1])

        elif sub == "clone":
            if len(args) < 2:
                console.print("[red]Usage: /role clone <source> [alias][/red]")
                return
            self._role_clone(args[1], args[2] if len(args) > 2 else None)

        else:
            console.print(f"[red]Unknown subcommand: {sub}[/red]. Usage: /role [list|add|remove|clone]")

    def process_message(self, role: str, msg: dict) -> None:
        pane = self.tmux.panes.get(role, "")
        if not pane:
            return

        msg_id = msg["id"]
        sender = msg["sender"]
        content = msg["content"]

        if not self.taskbox.claim(msg_id):
            return

        # Build single-line task prompt
        notify_cmd = f"octobots/scripts/notify-user.sh"
        relay_cmd = f"python3 {RELAY_SCRIPT}"

        prompt = (
            f"Message from {sender}: {content} "
            f"-- RULES: You MUST respond to this message. "
            f"If it is a task: do the work, then 1) comment on the GitHub issue, "
            f"2) run: {relay_cmd} ack {msg_id} \"your summary\", "
            f"3) run: {notify_cmd} \"Done: summary\". "
            f"If it is a question: answer via {relay_cmd} ack {msg_id} \"your answer\". "
            f"NEVER ignore a message. Silence breaks the pipeline."
        )
        self.tmux.send_keys(pane, prompt, confirm_paste=True)
        console.print(f"[green]→[/green] {role}: task from {sender} ({msg_id[:8]})")

        # Auto-resume healthcheck — worker has real work now
        if hasattr(self, "_health_state") and role in self._health_state:
            state = self._health_state[role]
            if state.get("healthcheck_paused"):
                state["healthcheck_paused"] = False
                state["nudge_count"] = 0
                state["last_active_at"] = time.time()
                console.print(f"[dim]▶ {role}: healthcheck resumed (new message delivered)[/dim]")

    def _on_scheduled_event(self, job: Any, result: str) -> None:
        """Called when a scheduled job executes."""
        type_label = job.type.value
        console.print(
            f"[magenta]⏰[/magenta] [{type_label}] {job.action.value} → "
            f"{job.target}: {result}"
        )

    def poll_once(self) -> None:
        # Check scheduled jobs
        try:
            self.scheduler.check()
        except Exception as e:
            console.print(f"[red]Scheduler error: {e}[/red]")

        # Check for restart requests from workers
        self._poll_restart_requests()

        # Monitor worker health (context pressure, API errors)
        self._check_worker_health()

        # Recycle Ollama-backed panes (auto-compact is disabled for them)
        self._recycle_ollama_workers()

        # Poll taskbox — deliver pending messages, but only if worker is free
        for role in self.workers:
            counts = self.taskbox.counts_for(role)
            if counts["processing"] > 0:
                continue  # worker is busy — hold until current task is acked
            msgs = self.taskbox.inbox(role, limit=1)
            if msgs:
                self.process_message(role, msgs[0])

        # Deliver ack responses back to senders
        self._deliver_responses()

        # Poll GitHub for issues assigned to the bot
        self._poll_github_issues()

        # Keep board Active Work section current
        self._write_active_work()

    def _deliver_responses(self) -> None:
        """Deliver ack responses back to the original sender."""
        try:
            responses = self.taskbox.undelivered_responses(limit=5)
        except Exception:
            return  # schema migration may not have run yet

        for resp in responses:
            sender = resp["sender"]
            recipient = resp["recipient"]
            response_text = resp["response"]
            msg_id = resp["id"]

            # The sender is who should receive the response
            pane = self.tmux.panes.get(sender, "")
            if not pane:
                # Sender not a running worker (e.g. "github", "telegram")
                self.taskbox.mark_response_delivered(msg_id)
                continue

            prompt = (
                f"Response from {recipient} to your earlier message: {response_text}"
            )
            self.tmux.send_keys(pane, prompt, confirm_paste=True)
            self.taskbox.mark_response_delivered(msg_id)
            console.print(f"[blue]←[/blue] {sender}: response from {recipient} ({msg_id[:8]})")

    def _ollama_role_model(self, role: str) -> str:
        """Return the Ollama model for a role, or '' if it's not Ollama-backed."""
        ollama_roles = os.environ.get("OCTOBOTS_OLLAMA_ROLES", "").split()
        if role not in ollama_roles:
            return ""
        role_var = "OCTOBOTS_OLLAMA_MODEL_" + role.upper().replace("-", "_")
        return os.environ.get(role_var) or os.environ.get("OCTOBOTS_OLLAMA_MODEL", "")

    def _ollama_context_usage(self, role: str) -> tuple[int, int] | None:
        """Return (used_tokens, limit) for a role, or None if unknown.

        Reads the most recently modified jsonl transcript under
        ~/.claude/projects/<encoded-cwd>/, takes the last assistant turn's
        usage.input_tokens (which is what Claude Code will send on the
        next request — the relevant number for compaction risk).
        """
        # Resolve the role's launch dir the same way spawn() does.
        source_role = self._role_source.get(role, role)
        role_dir = resolve_role(source_role)
        worker_dir = RUNTIME_DIR / "workers" / role
        workspace_kind = "shared"
        if role_dir and (role_dir / "AGENT.md").is_file():
            try:
                fm_text = (role_dir / "AGENT.md").read_text(encoding="utf-8", errors="replace")
                import re as _re_ws
                m = _re_ws.search(r'^workspace:\s*(\w+)', fm_text, _re_ws.MULTILINE)
                if m:
                    workspace_kind = m.group(1).strip().lower()
            except OSError:
                pass
        forces_root = role_dir and (role_dir / ".workspace-root").is_file()
        if forces_root or workspace_kind != "clone":
            launch_dir = PROJECT_DIR
        else:
            launch_dir = worker_dir if worker_dir.is_dir() else PROJECT_DIR

        # Claude Code encodes the cwd by replacing path separators with '-'.
        # /Users/foo/bar → -Users-foo-bar
        encoded = str(launch_dir).replace("/", "-")
        # Honor CLAUDE_CONFIG_DIR (Claude Code's config root override),
        # falling back to ~/.claude.
        config_dir_env = os.environ.get("CLAUDE_CONFIG_DIR")
        config_root = Path(config_dir_env) if config_dir_env else (Path.home() / ".claude")
        projects_root = config_root / "projects" / encoded
        if not projects_root.is_dir():
            if role not in self._ollama_jsonl_warned:
                console.print(f"[dim yellow]recycle: no transcript dir for {role} ({projects_root})[/dim yellow]")
                self._ollama_jsonl_warned.add(role)
            return None

        try:
            jsonls = sorted(projects_root.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            return None
        if not jsonls:
            return None

        # Walk the most recent jsonl backwards for the last usage.input_tokens.
        try:
            with jsonls[0].open("rb") as f:
                f.seek(0, 2)
                size = f.tell()
                # Read the tail (last ~64KB) — usage lives on assistant turns.
                read = min(size, 65536)
                f.seek(size - read)
                tail = f.read().decode("utf-8", errors="replace")
        except OSError:
            return None

        used = 0
        for line in reversed(tail.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            usage = (obj.get("message") or {}).get("usage") or obj.get("usage")
            if isinstance(usage, dict) and "input_tokens" in usage:
                used = (
                    int(usage.get("input_tokens", 0))
                    + int(usage.get("cache_read_input_tokens", 0))
                    + int(usage.get("cache_creation_input_tokens", 0))
                )
                break

        if used <= 0:
            return None

        # Per-role limit override; default 128k (most modern Ollama models).
        role_limit_var = "OCTOBOTS_OLLAMA_CONTEXT_LIMIT_" + role.upper().replace("-", "_")
        try:
            limit = int(
                os.environ.get(role_limit_var)
                or os.environ.get("OCTOBOTS_OLLAMA_CONTEXT_LIMIT", "128000")
            )
        except ValueError:
            limit = 128000
        return used, limit

    def _recycle_ollama_workers(self) -> None:
        """Three-stage context-aware recycle for Ollama-backed panes.

        Local models choke on Claude Code's auto-compact path; we disable
        it at launch and recycle the session here when context usage
        crosses a threshold. Per-role state machine:

        1. Checkpoint — ask the role to flush state to MEMORY.md
        2. /clear     — wipe session after grace period
        3. Re-init    — tell the cleared role to re-read its persona files
                        and memory so it isn't a blank slate for the user

        Tunables (.env.octobots):
          OCTOBOTS_OLLAMA_RECYCLE_AT      — usage % that triggers (default 70)
          OCTOBOTS_OLLAMA_RECYCLE_GRACE   — checkpoint→/clear seconds (default 60)
          OCTOBOTS_OLLAMA_REINIT_DELAY    — /clear→re-init seconds (default 5)
          OCTOBOTS_OLLAMA_CONTEXT_LIMIT   — model context window (default 128000)
        """
        try:
            threshold_pct = float(os.environ.get("OCTOBOTS_OLLAMA_RECYCLE_AT", "70"))
        except ValueError:
            threshold_pct = 70.0
        try:
            grace_s = float(os.environ.get("OCTOBOTS_OLLAMA_RECYCLE_GRACE", "60"))
        except ValueError:
            grace_s = 60.0
        try:
            reinit_delay_s = float(os.environ.get("OCTOBOTS_OLLAMA_REINIT_DELAY", "5"))
        except ValueError:
            reinit_delay_s = 5.0

        now = time.time()
        for role in self.workers:
            if not self._ollama_role_model(role):
                continue
            if role not in self.launched:
                continue
            pane = self.tmux.panes.get(role, "")
            if not pane:
                continue

            state = self._ollama_recycle.get(role, {})

            # Stage 0 → 1: idle, check usage and trigger if over threshold.
            if not state:
                usage = self._ollama_context_usage(role)
                if usage is None:
                    continue
                used, limit = usage
                pct = (used / limit) * 100.0 if limit else 0.0
                if pct < threshold_pct:
                    continue
                checkpoint_prompt = (
                    f"[supervisor] Your context is at {used:,}/{limit:,} tokens "
                    f"({pct:.0f}%). Flush any in-progress state, open loops, and "
                    f"important context to your MEMORY.md NOW using the Write tool. "
                    f"Do NOT reply to the user via notify-user.sh — this is "
                    f"internal housekeeping. Your session will be cleared in "
                    f"{int(grace_s)}s and you'll be told to re-read your persona "
                    f"files afterward."
                )
                self.tmux.send_keys(pane, checkpoint_prompt, confirm_paste=True)
                self._ollama_recycle[role] = {"checkpoint_at": now}
                console.print(f"[yellow]↻[/yellow] {role}: checkpoint @ {pct:.0f}% ({used:,}/{limit:,})")
                continue

            # Stage 1 → 2: grace period elapsed, send /clear.
            if "cleared_at" not in state:
                if now - state["checkpoint_at"] < grace_s:
                    continue
                self.tmux.send_keys(pane, "/clear", confirm_paste=True)
                state["cleared_at"] = now
                console.print(f"[yellow]↻[/yellow] {role}: /clear sent")
                continue

            # Stage 2 → 0: send re-init prompt and reset state.
            if now - state["cleared_at"] < reinit_delay_s:
                continue
            reinit_prompt = (
                "[supervisor] Your session was just cleared to recover from "
                "context pressure. Before doing anything else, re-read these "
                "files to restore your identity and working state:\n"
                "  1. Your AGENT.md and SOUL.md (in .claude/agents/<your-role>/)\n"
                "  2. .octobots/persona/USER.md\n"
                "  3. .octobots/persona/TOOLS.md\n"
                "  4. .octobots/persona/access-control.yaml\n"
                "  5. Your MEMORY.md (in .octobots/memory/)\n"
                "  6. Open Loops.md in the Obsidian vault if you have one\n\n"
                "Then sit idle and wait for the user. Do NOT reply via "
                "notify-user.sh — this is internal housekeeping, not a user "
                "message."
            )
            self.tmux.send_keys(pane, reinit_prompt, confirm_paste=True)
            self._ollama_recycle.pop(role, None)
            console.print(f"[green]✓[/green] {role}: re-init sent, recycle complete")

    def _check_worker_health(self) -> None:
        """Monitor worker panes for context pressure and auto-recover.

        Detects:
        - API 500 errors (context too large or transient failures)
        - Long churn times (sign of retries / context pressure)
        - Idle after error (worker gave up)

        Actions:
        - Send /compact on first signs of pressure
        - Send /clear + restart if worker is stuck after multiple failures
        """
        now = time.time()
        # Only check every 30 seconds
        if now - getattr(self, "_last_health_check", 0) < 30:
            return
        self._last_health_check = now

        if not hasattr(self, "_health_state"):
            self._health_state: dict[str, dict] = {}

        import re as _re

        for role in self.workers:
            pane = self.tmux.panes.get(role, "")
            if not pane:
                continue

            output = self.tmux.capture_pane(pane, 15)
            if not output:
                continue

            state = self._health_state.setdefault(role, {
                "error_count": 0,
                "last_compact": 0,
                "last_restart": 0,
                "last_clear": 0,
                "last_pane_hash": "",
                "last_active_at": now,
                "nudge_count": 0,
                "last_nudge_at": 0,
                "healthcheck_paused": False,
            })

            # Detect API errors
            has_500 = "API Error: 500" in output or "Internal server error" in output
            has_overloaded = "overloaded_error" in output
            has_context_error = "prompt is too long" in output.lower() or "context window" in output.lower()

            # Detect if worker is idle (at prompt) after errors
            lines = output.strip().split("\n")
            last_lines = [l.strip() for l in lines[-3:] if l.strip()]
            is_idle = any(
                "bypass permissions" in l.lower() or l.startswith("❯") or l.startswith(">")
                for l in last_lines
            )

            # Worker requested /clear (e.g. "Task complete. /clear recommended before next task.")
            # Also detect legacy "Standing by." — worker done but using old signal pattern
            output_lower = output.lower()
            requests_clear = is_idle and (
                "/clear recommended" in output_lower
                or "standing by" in output_lower
            )
            if requests_clear and now - state.get("last_clear", 0) > 60:
                requeued = self.taskbox.requeue_processing(role)
                if requeued:
                    console.print(f"[yellow]↩ {role}: requeued {requeued} processing task(s) before /clear[/yellow]")
                console.print(f"[cyan]🧹 {role}: requested /clear — sending it[/cyan]")
                self.tmux.send_keys(pane, "/clear")
                state["last_clear"] = now
                state["error_count"] = 0
                continue

            # Worker requested /compact (e.g. "Epic X complete. /compact recommended.")
            requests_compact = is_idle and "/compact recommended" in output_lower
            if requests_compact and now - state.get("last_compact", 0) > 120:
                console.print(f"[cyan]📦 {role}: requested /compact — sending it[/cyan]")
                self.tmux.send_keys(pane, "/compact")
                state["last_compact"] = now
                continue

            if has_500 or has_overloaded or has_context_error:
                state["error_count"] += 1

                # First occurrence: try /compact
                if state["error_count"] <= 2 and now - state["last_compact"] > 120:
                    console.print(f"[yellow]⚠ {role}: API error detected, sending /compact[/yellow]")
                    self.tmux.send_keys(pane, "/compact")
                    state["last_compact"] = now

                # Repeated errors + idle: worker is stuck, restart it
                elif state["error_count"] >= 3 and is_idle and now - state["last_restart"] > 300:
                    console.print(f"[red]⚠ {role}: stuck after {state['error_count']} errors, restarting[/red]")
                    requeued = self.taskbox.requeue_processing(role)
                    if requeued:
                        console.print(f"[yellow]↩ {role}: requeued {requeued} interrupted task(s)[/yellow]")
                    self.cmd_restart(role)
                    state["last_restart"] = now
                    state["error_count"] = 0
            else:
                # No errors visible — reset counter
                if is_idle or "Cooked" in output or "Done" in output:
                    state["error_count"] = 0

            # ── Silence / stuck detection ────────────────────────────────────
            import hashlib as _hashlib
            pane_hash = _hashlib.md5(output.encode()).hexdigest()
            if pane_hash != state["last_pane_hash"]:
                # Pane changed — worker is alive, reset silence tracking
                state["last_pane_hash"] = pane_hash
                state["last_active_at"] = now
                state["nudge_count"] = 0
                continue

            silence_min = (now - state["last_active_at"]) / 60
            if silence_min < 30:
                continue  # Too early to worry

            counts = self.taskbox.counts_for(role)

            # A stuck processing task always overrides the pause
            if state["healthcheck_paused"] and counts["processing"] == 0:
                continue  # User explicitly paused healthcheck for this role

            board = self._board_assignments()
            on_board = bool(board.get(role))

            if counts["processing"] == 0 and not on_board:
                # Genuinely idle — nothing in relay.db, nothing on board
                if not state["healthcheck_paused"]:
                    state["healthcheck_paused"] = True
                    console.print(f"[dim]⏸ {role}: silent {silence_min:.0f}min, board empty — auto-paused healthcheck[/dim]")
                continue

            # Worker should be active but has been silent
            if now - state["last_nudge_at"] < 900:  # 15 min between nudges
                continue

            state["nudge_count"] += 1
            state["last_nudge_at"] = now

            if counts["processing"] > 0:
                requeued = self.taskbox.requeue_processing(role)
                console.print(f"[yellow]🔔 {role}: silent {silence_min:.0f}min with {counts['processing']} stuck message(s) — requeued[/yellow]")
            elif on_board:
                console.print(f"[yellow]🔔 {role}: silent {silence_min:.0f}min, board has tasks but no relay messages[/yellow]")

            if state["nudge_count"] >= 2:
                # Second nudge — escalate to user
                try:
                    notify_cmd = PROJECT_DIR / "octobots/scripts/notify-user.sh"
                    if notify_cmd.is_file():
                        import subprocess as _sub
                        _sub.Popen(
                            ["bash", str(notify_cmd),
                             f"⚠ {role} has been silent for {silence_min:.0f} minutes and may be stuck. Check /logs {role}"],
                            cwd=str(PROJECT_DIR),
                        )
                except Exception:
                    pass
                console.print(f"[red]⚠ {role}: still silent after requeue — user notified[/red]")

    def _board_assignments(self) -> dict[str, list[str]]:
        """Parse .octobots/board.md Active Work table → {role: [task, ...]}."""
        board_path = RUNTIME_DIR / "board.md"
        if not board_path.is_file():
            return {}
        result: dict[str, list[str]] = {}
        in_table = False
        for line in board_path.read_text().splitlines():
            if line.startswith("## Active Work"):
                in_table = True
                continue
            if in_table and line.startswith("##"):
                break
            if in_table and "|" in line and not line.startswith("|---"):
                cols = [c.strip() for c in line.split("|") if c.strip()]
                if len(cols) >= 2 and cols[0] not in ("Role", "—", ""):
                    role = cols[0].lower().replace(" ", "-")
                    task = cols[1] if len(cols) > 1 else ""
                    if task and task != "—":
                        result.setdefault(role, []).append(task)
        return result

    def _poll_restart_requests(self) -> None:
        """Check for restart requests via taskbox (from workers or telegram)."""
        msgs = self.taskbox.inbox("supervisor", limit=5)
        for msg in msgs:
            if not self.taskbox.claim(msg["id"]):
                continue
            sender = msg["sender"]
            content = msg["content"].strip().lower()

            # "restart" (self-restart) or "restart <role>" (from telegram/other)
            if content in ("restart", "restart me", "reload"):
                target = sender
            elif content.startswith("restart "):
                target = resolve_alias(content.split(" ", 1)[1].strip())
            else:
                continue

            if target in self.workers or target == "all":
                console.print(f"[yellow]🔄 {target} restart requested by {sender}[/yellow]")
                self.cmd_restart(target)

            # Ack the message
            conn = self.taskbox._db()
            conn.execute(
                "UPDATE messages SET status='done', response='restarted', updated_at=? WHERE id=?",
                (time.time(), msg["id"]),
            )
            conn.commit()
            conn.close()

    def _poll_github_issues(self) -> None:
        """Check for GitHub issues assigned to the bot and route to PM."""
        gh_token = getattr(self, "_gh_app_token", "")
        issue_repo = os.environ.get("OCTOBOTS_ISSUE_REPO", "")
        if not gh_token or not issue_repo:
            return

        # Only check every 60 seconds (not every poll cycle)
        now = time.time()
        if now - getattr(self, "_last_gh_poll", 0) < 60:
            return
        self._last_gh_poll = now

        try:
            import urllib.request
            owner, repo = issue_repo.split("/", 1)
            url = (
                f"https://api.github.com/repos/{owner}/{repo}/issues"
                f"?assignee=octobotsai[bot]&state=open&per_page=10"
            )
            req = urllib.request.Request(url, headers={
                "Authorization": f"token {gh_token}",
                "Accept": "application/vnd.github+json",
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                issues = json.loads(resp.read())

            if not issues:
                return

            # Track which issues we've already routed
            seen = getattr(self, "_routed_issues", set())

            for issue in issues:
                issue_num = issue["number"]
                if issue_num in seen:
                    continue

                seen.add(issue_num)
                title = issue["title"]
                labels = [l["name"] for l in issue.get("labels", [])]
                url = issue["html_url"]

                # Route to PM via taskbox
                import uuid
                msg_id = uuid.uuid4().hex[:12]
                self.taskbox.init()  # ensure table exists
                conn = self.taskbox._db()
                conn.execute(
                    "INSERT INTO messages (id, sender, recipient, content, status, created_at, updated_at) "
                    "VALUES (?, 'github', 'project-manager', ?, 'pending', ?, ?)",
                    (msg_id, f"New issue assigned to octobots — #{issue_num}: {title}. Labels: {', '.join(labels)}. URL: {url}", now, now),
                )
                conn.commit()
                conn.close()

                console.print(f"[blue]📥[/blue] Issue #{issue_num} assigned to bot → routed to pm")

            self._routed_issues = seen

        except Exception as e:
            pass  # silent — don't spam logs on network failures

    # ── Slash Commands ──────────────────────────────────────────────────────

    def cmd_status(self) -> None:
        table = Table(title="Worker Status", box=box.ROUNDED)
        table.add_column("Role", style="cyan")
        table.add_column("Pane", style="dim")
        table.add_column("State", style="green")
        table.add_column("Last Output", style="white", max_width=60)

        for role in self.workers:
            pane = self.tmux.panes.get(role, "?")
            output = self.tmux.capture_pane(pane, 5).strip().split("\n")
            last_line = output[-1] if output else ""
            # Detect state from output
            if "bypass permissions" in last_line.lower():
                state = "[green]idle[/green]"
            elif ">" in last_line or "❯" in last_line:
                state = "[green]idle[/green]"
            else:
                state = "[yellow]working[/yellow]"

            # Clean ANSI codes
            import re
            last_line = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', last_line)[:60]

            table.add_row(role, pane.split(".")[-1], state, last_line)

        console.print(table)

    def cmd_tasks(self) -> None:
        stats = self.taskbox.stats()
        if not stats:
            console.print("[dim]No taskbox activity.[/dim]")
            return

        table = Table(title="Taskbox", box=box.ROUNDED)
        table.add_column("Role", style="cyan")
        table.add_column("Pending", style="yellow")
        table.add_column("Processing", style="blue")
        table.add_column("Done", style="green")

        for role, counts in sorted(stats.items()):
            table.add_row(
                role,
                str(counts.get("pending", 0)),
                str(counts.get("processing", 0)),
                str(counts.get("done", 0)),
            )
        console.print(table)

    def cmd_workers(self) -> None:
        table = Table(title="Workers", box=box.ROUNDED)
        table.add_column("Role", style="cyan")
        table.add_column("Pane", style="dim")
        table.add_column("Source", style="blue")
        table.add_column("Environment", style="green")

        for role in self.workers:
            pane = self.tmux.panes.get(role, "?")
            role_dir = resolve_role(role)
            source = "project" if role_dir and str(LOCAL_ROLES) in str(role_dir) else "base"
            worker_dir = RUNTIME_DIR / "workers" / role
            if role_dir and (role_dir / ".workspace-root").is_file():
                env = "root"
            elif worker_dir.is_dir():
                env = "isolated"
            else:
                env = "shared"
            table.add_row(role, pane, source, env)

        console.print(table)

    def cmd_logs(self, role: str, lines: int = 30) -> None:
        pane = self.tmux.panes.get(role)
        if not pane:
            console.print(f"[red]Unknown role: {role}[/red]")
            return
        output = self.tmux.capture_pane(pane, lines)
        console.print(Panel(output.strip(), title=f"[cyan]{role}[/cyan]", box=box.ROUNDED))

    def cmd_send(self, role: str, message: str) -> None:
        pane = self.tmux.panes.get(role)
        if not pane:
            console.print(f"[red]Unknown role: {role}[/red]")
            return
        self.tmux.send_keys(pane, message, confirm_paste=True)
        console.print(f"[green]→[/green] Sent to {role}")

    def cmd_restart(self, role: str) -> None:
        if role == "all":
            for r in self.workers:
                self.cmd_restart(r)
            return

        pane = self.tmux.panes.get(role)
        if not pane:
            console.print(f"[red]Unknown role: {role}[/red]")
            return

        console.print(f"[yellow]Restarting {role}...[/yellow]")
        self.tmux.send_keys(pane, "/exit")
        time.sleep(3)
        self._launch_worker(role)
        console.print(f"[green]✓ {role} restarted[/green]")

    def _fetch_component(self, component_type: str, repo: str, ref: str = "main") -> str | None:
        """Fetch a role or skill from GitHub via registry-fetch.sh.

        Returns the installed name on success, None on failure.
        component_type: 'agent' or 'skill'
        repo: 'owner/repo'
        ref: branch/tag/SHA
        """
        fetch_script = OCTOBOTS_DIR / "scripts" / "registry-fetch.sh"
        if not fetch_script.is_file():
            console.print(f"[red]registry-fetch.sh not found at {fetch_script}[/red]")
            return None
        try:
            r = subprocess.run(
                ["bash", str(fetch_script), component_type, repo, ref],
                capture_output=True, text=True, cwd=str(PROJECT_DIR),
            )
            if r.returncode != 0:
                console.print(f"[red]Fetch failed: {r.stderr.strip() or repo}[/red]")
                return None
            # Last non-empty line of stdout is the installed name
            name = next((l.strip() for l in reversed(r.stdout.splitlines()) if l.strip()), None)
            if r.stderr.strip():
                for line in r.stderr.strip().splitlines():
                    console.print(f"[dim]{line}[/dim]")
            return name
        except Exception as e:
            console.print(f"[red]Fetch error: {e}[/red]")
            return None

    def cmd_skill_add(self, repo: str) -> None:
        """Fetch a skill from GitHub and symlink into all active workers.

        repo: 'owner/repo' or 'owner/repo@ref'
        """
        ref = "main"
        if "@" in repo:
            repo, ref = repo.rsplit("@", 1)

        console.print(f"[dim]Fetching skill {repo}@{ref}...[/dim]")
        skill_name = self._fetch_component("skill", repo, ref)
        if not skill_name:
            return

        # Run setup-skill.sh to install dependencies
        setup_script = OCTOBOTS_DIR / "scripts" / "setup-skill.sh"
        if setup_script.is_file():
            subprocess.run(["bash", str(setup_script), skill_name],
                           capture_output=True, cwd=str(PROJECT_DIR))

        # Symlink into all active workers' .claude/skills/
        skill_src = PROJECT_DIR / ".claude" / "skills" / skill_name
        if not skill_src.exists():
            console.print(f"[yellow]⚠  Skill installed but .claude/skills/{skill_name} not found[/yellow]")
            return

        for worker in self.workers:
            worker_skills = RUNTIME_DIR / "workers" / worker / ".claude" / "skills"
            worker_skills.mkdir(parents=True, exist_ok=True)
            link = worker_skills / skill_name
            if link.exists() or link.is_symlink():
                console.print(f"[dim]{worker}: {skill_name} already linked[/dim]")
            else:
                link.symlink_to(skill_src)
                console.print(f"[green]✓ {worker}: linked .claude/skills/{skill_name}[/green]")

        console.print(f"[green]✓ {skill_name} installed from {repo}[/green]")

    def cmd_skill(self, role: str, skill: str) -> None:
        """Add a skill to a worker: creates the symlink + updates AGENT.md skills list."""
        # Resolve skill source: bundled skills first, then installed (.claude/skills/)
        skill_src = OCTOBOTS_DIR / "skills" / skill
        if not skill_src.is_dir():
            skill_src = PROJECT_DIR / ".claude" / "skills" / skill
        if not skill_src.is_dir():
            bundled = [d.name for d in (OCTOBOTS_DIR / "skills").iterdir() if d.is_dir()]
            installed = [d.name for d in (PROJECT_DIR / ".claude" / "skills").iterdir()
                         if d.is_dir()] if (PROJECT_DIR / ".claude" / "skills").is_dir() else []
            console.print(f"[red]Unknown skill: {skill}[/red]")
            console.print(f"[dim]Bundled: {', '.join(sorted(bundled))}[/dim]")
            if installed:
                console.print(f"[dim]Installed: {', '.join(sorted(installed))}[/dim]")
            return

        if role not in self.workers and role != "all":
            console.print(f"[red]Unknown role: {role}[/red]")
            return

        roles = list(self.workers) if role == "all" else [role]

        for r in roles:
            # 1. Symlink into worker's .claude/skills/
            skills_dir = RUNTIME_DIR / "workers" / r / ".claude" / "skills"
            skills_dir.mkdir(parents=True, exist_ok=True)
            link = skills_dir / skill
            if link.exists() or link.is_symlink():
                console.print(f"[dim]{r}: {skill} already linked[/dim]")
            else:
                link.symlink_to(skill_src)
                console.print(f"[green]✓ {r}: linked .claude/skills/{skill}[/green]")

            # 2. Update skills: list in AGENT.md frontmatter
            for base in (BASE_ROLES, LOCAL_ROLES):
                agent_md = base / r / "AGENT.md"
                if not agent_md.is_file():
                    continue
                text = agent_md.read_text()
                import re
                m = re.search(r'^skills:\s*\[([^\]]*)\]', text, re.MULTILINE)
                if m:
                    current = [s.strip() for s in m.group(1).split(",") if s.strip()]
                    if skill not in current:
                        current.append(skill)
                        new_line = f"skills: [{', '.join(current)}]"
                        text = text[:m.start()] + new_line + text[m.end():]
                        agent_md.write_text(text)
                        console.print(f"[dim]  Updated {agent_md.relative_to(OCTOBOTS_DIR.parent)} skills list[/dim]")
                else:
                    # Insert skills: line before closing ---
                    text = re.sub(r'^(---\s*\n)', r'skills: [' + skill + r']\n\1',
                                  text[::-1], count=1, flags=re.MULTILINE)[::-1]
                    agent_md.write_text(text)
                    console.print(f"[dim]  Added skills: [{skill}] to {agent_md.relative_to(OCTOBOTS_DIR.parent)}[/dim]")
                break

        console.print(f"[yellow]Note: restart the worker(s) to load the new skill.[/yellow]")

    def cmd_tasks(self, args: list[str]) -> None:
        sub = args[0] if args else "list"

        if sub == "clean":
            # Requeue all processing → pending
            requeued = self.taskbox.requeue_all_processing()
            console.print(f"[yellow]↩ Requeued {requeued} processing task(s) → pending[/yellow]")
        elif sub == "abandon":
            count = self.taskbox.abandon_all()
            console.print(f"[yellow]🗑 Abandoned {count} task(s)[/yellow]")
        else:
            active = self.taskbox.active_tasks()
            if not active:
                console.print("[dim]No pending or processing tasks.[/dim]")
                return
            table = Table(title="Active Tasks", box=box.ROUNDED)
            table.add_column("ID", style="dim", width=14)
            table.add_column("Status", width=10)
            table.add_column("From", width=18)
            table.add_column("To", width=18)
            table.add_column("Content")
            for t in active:
                status_color = "yellow" if t["status"] == "processing" else "green"
                preview = t["content"].replace("\n", " ")[:60]
                table.add_row(
                    t["id"][:12],
                    f"[{status_color}]{t['status']}[/{status_color}]",
                    t["sender"],
                    t["recipient"],
                    preview,
                )
            console.print(table)

    def cmd_clear(self, role: str) -> None:
        pane = self.tmux.panes.get(role)
        if not pane:
            console.print(f"[red]Unknown role: {role}[/red]")
            return
        requeued = self.taskbox.requeue_processing(role)
        if requeued:
            console.print(f"[yellow]↩ {role}: requeued {requeued} interrupted task(s)[/yellow]")
        self.tmux.send_keys(pane, "/clear")
        console.print(f"[green]✓ {role} cleared[/green]")

    def cmd_board(self) -> None:
        board_path = RUNTIME_DIR / "board.md"
        if board_path.is_file():
            from rich.markdown import Markdown
            console.print(Panel(Markdown(board_path.read_text()), title="Team Board", box=box.ROUNDED))
        else:
            console.print("[dim]No board.md found.[/dim]")

    def cmd_health(self) -> None:
        table = Table(title="Health Check", box=box.ROUNDED)
        table.add_column("Check", style="cyan")
        table.add_column("Status")

        # tmux
        table.add_row("tmux session", "[green]✓[/green]" if self.tmux.exists() else "[red]✗[/red]")

        # relay DB
        db_ok = (RUNTIME_DIR / "relay.db").is_file()
        table.add_row("taskbox DB", "[green]✓[/green]" if db_ok else "[red]✗[/red]")

        # panes alive
        for role in self.workers:
            pane = self.tmux.panes.get(role, "")
            output = self.tmux.capture_pane(pane, 1)
            alive = bool(output.strip())
            table.add_row(f"  {role}", "[green]alive[/green]" if alive else "[red]dead[/red]")

        # board
        table.add_row("board.md", "[green]✓[/green]" if (RUNTIME_DIR / "board.md").is_file() else "[dim]missing[/dim]")

        # bridge
        bridge_alive = hasattr(self, "_bridge_proc") and self._bridge_proc and self._bridge_proc.poll() is None
        table.add_row("telegram bridge", "[green]running[/green]" if bridge_alive else "[dim]not started (/bridge)[/dim]")

        # pending messages
        pending = self.taskbox.pending_count()
        table.add_row("pending tasks", f"[yellow]{pending}[/yellow]" if pending else "[green]0[/green]")

        # scheduled jobs
        jobs = self.job_store.load()
        active_jobs = sum(1 for j in jobs if not j.paused)
        paused_jobs = sum(1 for j in jobs if j.paused)
        job_status = f"[green]{active_jobs} active[/green]"
        if paused_jobs:
            job_status += f", [yellow]{paused_jobs} paused[/yellow]"
        table.add_row("scheduled jobs", job_status if jobs else "[dim]none[/dim]")

        console.print(table)

    def cmd_bridge(self, restart: bool = False) -> None:
        """Start or restart the Telegram bridge as a background process."""
        if hasattr(self, "_bridge_proc") and self._bridge_proc and self._bridge_proc.poll() is None:
            if not restart:
                console.print(f"[yellow]Bridge already running (PID: {self._bridge_proc.pid}). Use /bridge restart[/yellow]")
                return
            self._bridge_proc.terminate()
            self._bridge_proc.wait(timeout=5)
            console.print("[yellow]Bridge stopped.[/yellow]")

        bridge_script = SCRIPT_DIR / "telegram-bridge.py"
        if not bridge_script.is_file():
            console.print("[red]telegram-bridge.py not found[/red]")
            return

        # Check for Telegram config
        token = os.environ.get("OCTOBOTS_TG_TOKEN", "")
        if not token:
            console.print("[red]OCTOBOTS_TG_TOKEN not set. Add it to .env.octobots[/red]")
            return

        # Find Python
        for py in [PROJECT_DIR / "venv" / "bin" / "python", PROJECT_DIR / ".venv" / "bin" / "python"]:
            if py.is_file():
                python = str(py)
                break
        else:
            python = "python3"

        self._bridge_proc = subprocess.Popen(
            [python, str(bridge_script)],
            cwd=str(PROJECT_DIR),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        console.print(f"[green]✓ Telegram bridge started (PID: {self._bridge_proc.pid})[/green]")

    def _parse_schedule_target(self, rest: list[str]) -> tuple[str, str, str] | None:
        """Parse the target portion of a schedule/loop command.

        Supports:
            @role <message>          → send to role via taskbox
            run <command>            → shell command
            agent <name> <prompt>    → invoke Claude Code agent

        Returns (action, target, content) or None on error.
        """
        if not rest:
            self._print_schedule_help()
            return None

        first = rest[0].lower()

        # @role shorthand — send taskbox message (same as Telegram @pm, @qa, etc.)
        if first.startswith("@"):
            role_alias = first[1:]
            role = resolve_alias(role_alias)
            if role not in self.workers and role != "all":
                available = sorted(set(a for a, r in ROLE_ALIASES.items() if r in self.workers and len(a) <= 3))
                console.print(f"[red]Unknown role: @{role_alias}. Available: {', '.join(f'@{a}' for a in available)}[/red]")
                return None
            content = " ".join(rest[1:])
            if not content:
                console.print("[red]Missing message after @role[/red]")
                return None
            return ("send", role, content)

        # run <command>
        if first == "run":
            target = " ".join(rest[1:])
            if not target:
                console.print("[red]Missing command to run[/red]")
                return None
            return ("run", target, "")

        # agent <name> <prompt>
        if first == "agent":
            if len(rest) < 3:
                console.print("[red]Usage: agent <agent-name> <prompt>[/red]")
                return None
            target = rest[1]
            content = " ".join(rest[2:])
            from scheduler import resolve_agent
            if not resolve_agent(target, OCTOBOTS_DIR, RUNTIME_DIR):
                agents = []
                for base in [RUNTIME_DIR / "agents", OCTOBOTS_DIR / "shared" / "agents"]:
                    if base.is_dir():
                        for d in sorted(base.iterdir()):
                            if (d / "AGENT.md").is_file() and d.name not in agents:
                                agents.append(d.name)
                console.print(f"[red]Agent not found: {target}. Available: {', '.join(agents) or 'none'}[/red]")
                return None
            return ("agent", target, content)

        console.print(f"[red]Expected @role, run, or agent — got: {first}[/red]")
        self._print_schedule_help()
        return None

    def _print_schedule_help(self) -> None:
        console.print(
            "[dim]Usage:\n"
            "  /schedule <at|every|cron> <spec> @<role> <message>\n"
            "  /schedule <at|every|cron> <spec> run <command>\n"
            "  /schedule <at|every|cron> <spec> agent <name> <prompt>\n\n"
            "Examples:\n"
            "  /schedule every 30m @pm Check status of all tasks\n"
            "  /schedule at 15:00 @py Review PR #42\n"
            "  /schedule every 1h run git fetch --all\n"
            "  /schedule every 10m agent taskbox-listener Check inbox\n"
            "  /schedule cron 0 9 * * MON-FRI @ba Daily standup report\n\n"
            "  /loop 30m @pm Check task progress\n"
            "  /loop 5m run ./scripts/health-check.sh\n"
            "  /loop 10m agent rca-investigator Check for flaky tests\n\n"
            "Template variables (resolved at execution time):\n"
            "  {time}     current time (14:35)   {date}     today's date (2026-04-04)\n"
            "  {datetime} date + time             {weekday}  day name (Friday)\n"
            "  {week}     ISO week number         {month}    month name (April)\n"
            "  {role}     target role name        {schedule} job spec (every 30m)\n"
            "  Unknown {variables} are passed through unchanged.[/dim]"
        )

    def cmd_schedule(self, args: list[str]) -> None:
        """Handle /schedule command.

        Usage:
            /schedule <at|every|cron> <spec> @<role> <message>
            /schedule <at|every|cron> <spec> run <command>
            /schedule <at|every|cron> <spec> agent <name> <prompt>
        """
        if len(args) < 3:
            self._print_schedule_help()
            return

        job_type = args[0].lower()
        if job_type not in ("at", "every", "cron"):
            console.print(f"[red]Invalid type: {job_type}. Use: at, every, cron[/red]")
            return

        # Parse spec — for cron expressions, the spec is 5 fields
        if job_type == "cron":
            if len(args) < 8:  # cron + 5 fields + target + message
                console.print("[red]Cron needs 5 fields: /schedule cron <min> <hour> <dom> <month> <dow> @<role> <message>[/red]")
                return
            spec = " ".join(args[1:6])
            rest = args[6:]
        else:
            spec = args[1]
            rest = args[2:]

        parsed = self._parse_schedule_target(rest)
        if not parsed:
            return
        action, target, content = parsed

        try:
            job = self.scheduler.create_job(job_type, spec, action, target, content)
            next_dt = datetime.fromisoformat(job.next_run)
            console.print(
                f"[green]✓ Scheduled[/green] [{job.id}] {job.type.value} {job.spec} "
                f"→ {action} {target}\n"
                f"  Next run: [yellow]{next_dt.strftime('%Y-%m-%d %H:%M UTC')}[/yellow]"
            )
        except ValueError as e:
            console.print(f"[red]Error: {e}[/red]")

    def cmd_loop(self, args: list[str]) -> None:
        """Handle /loop — shortcut for /schedule every.

        Usage:
            /loop <interval> @<role> <message>
            /loop <interval> run <command>
            /loop <interval> agent <name> <prompt>
        """
        if len(args) < 3:
            self._print_schedule_help()
            return

        self.cmd_schedule(["every"] + args)

    def cmd_jobs(self, args: list[str]) -> None:
        """Handle /jobs — list, cancel, pause, resume scheduled jobs.

        Usage:
            /jobs                  — list all
            /jobs cancel <id>      — remove a job
            /jobs pause <id>       — pause a job
            /jobs resume <id>      — resume a paused job
        """
        if not args:
            # List all jobs
            jobs = self.job_store.load()
            if not jobs:
                console.print("[dim]No scheduled jobs.[/dim]")
                return

            table = Table(title="Scheduled Jobs", box=box.ROUNDED)
            table.add_column("ID", style="cyan")
            table.add_column("Type", style="blue")
            table.add_column("Spec", style="white")
            table.add_column("Action", style="green")
            table.add_column("Target", style="yellow")
            table.add_column("Content", style="white", max_width=30)
            table.add_column("Next Run", style="magenta")
            table.add_column("Runs", style="dim")
            table.add_column("Status")

            for j in jobs:
                try:
                    next_dt = datetime.fromisoformat(j.next_run)
                    next_str = next_dt.strftime("%m-%d %H:%M")
                except (ValueError, TypeError):
                    next_str = "?"

                status = "[yellow]paused[/yellow]" if j.paused else "[green]active[/green]"
                content_short = (j.content[:27] + "...") if len(j.content) > 30 else j.content

                table.add_row(
                    j.id, j.type.value, j.spec, j.action.value,
                    j.target, content_short, next_str,
                    str(j.run_count), status,
                )

            console.print(table)
            return

        action = args[0].lower()
        if action == "cancel":
            if len(args) < 2:
                console.print("[red]Usage: /jobs cancel <id>[/red]")
                return
            if self.job_store.remove(args[1]):
                console.print(f"[green]✓ Cancelled job {args[1]}[/green]")
            else:
                console.print(f"[red]Job {args[1]} not found[/red]")

        elif action in ("pause", "resume"):
            if len(args) < 2:
                console.print(f"[red]Usage: /jobs {action} <id>[/red]")
                return
            result = self.job_store.toggle_pause(args[1])
            if result is None:
                console.print(f"[red]Job {args[1]} not found[/red]")
            else:
                state = "paused" if result else "active"
                console.print(f"[green]✓ Job {args[1]} is now {state}[/green]")

        else:
            console.print(f"[red]Unknown action: {action}. Use: cancel, pause, resume[/red]")

    def cmd_pause(self, role: str) -> None:
        if not hasattr(self, "_health_state"):
            self._health_state = {}
        state = self._health_state.setdefault(role, {})
        state["healthcheck_paused"] = True
        console.print(f"[yellow]⏸ {role}: healthcheck paused until next message[/yellow]")

    def cmd_resume(self, role: str) -> None:
        if not hasattr(self, "_health_state"):
            self._health_state = {}
        state = self._health_state.setdefault(role, {})
        state["healthcheck_paused"] = False
        state["nudge_count"] = 0
        state["last_active_at"] = time.time()
        console.print(f"[green]▶ {role}: healthcheck resumed[/green]")

    def cmd_help(self) -> None:
        table = Table(title="Commands", box=box.ROUNDED, show_header=False)
        table.add_column("Command", style="cyan")
        table.add_column("Description")

        cmds = [
            ("/status", "Worker states and last output"),
            ("/workers", "List panes, sources, environments"),
            ("/tasks", "Taskbox stats"),
            ("/logs <role> [N]", "Last N lines from a worker"),
            ("/send <role> <msg>", "Send a message to a worker's pane"),
            ("/restart <role|all>", "Restart a worker (exit + relaunch)"),
            ("/skill add <owner/repo[@ref]>", "Fetch skill from GitHub + link into all workers"),
            ("/skill <role|all> <skill>", "Link installed skill to a worker (update AGENT.md)"),
            ("/role list", "Show all available roles and which are active"),
            ("/role add <name|owner/repo[@ref]>", "Add role live; fetches from GitHub if repo given"),
            ("/role remove <name>", "Stop role, remove workspace, demote if promoted"),
            ("/role clone <source> [alias]", "Spawn a clone with its own isolated workspace"),
            ("/clear <role>", "Send /clear to a worker"),
            ("/tasks [clean|abandon]", "List active tasks; clean requeues processing; abandon drops all"),
            ("/pause <role>", "Pause silence healthcheck (worker intentionally idle)"),
            ("/resume <role>", "Resume silence healthcheck manually"),
            ("/board", "Show team board"),
            ("/bridge", "Start Telegram bridge (background)"),
            ("/health", "System health check"),
            ("/schedule <type> <spec> @role msg", "Schedule a job (at/every/cron)"),
            ("/loop <interval> @role msg", "Shortcut for /schedule every"),
            ("/jobs [cancel|pause|resume <id>]", "List or manage scheduled jobs"),
            ("/stop", "Graceful shutdown"),
            ("/help", "This help"),
        ]
        for cmd, desc in cmds:
            table.add_row(cmd, desc)

        console.print(table)

    def handle_command(self, line: str) -> bool:
        """Handle a slash command. Returns False to exit."""
        parts = line.strip().split()
        if not parts:
            return True

        cmd = parts[0].lower()
        args = parts[1:]

        if cmd == "/status":
            self.cmd_status()
        elif cmd == "/workers":
            self.cmd_workers()
        elif cmd == "/tasks":
            self.cmd_tasks()
        elif cmd == "/logs":
            role = args[0] if args else ""
            lines = int(args[1]) if len(args) > 1 else 30
            self.cmd_logs(role, lines)
        elif cmd == "/send":
            if len(args) >= 2:
                self.cmd_send(args[0], " ".join(args[1:]))
            else:
                console.print("[red]Usage: /send <role> <message>[/red]")
        elif cmd == "/restart":
            self.cmd_restart(args[0] if args else "all")
        elif cmd == "/skill":
            if args and args[0] == "add":
                if len(args) >= 2:
                    self.cmd_skill_add(args[1])
                else:
                    console.print("[red]Usage: /skill add <owner/repo[@ref]>[/red]")
            elif len(args) >= 2:
                self.cmd_skill(args[0], args[1])
            else:
                console.print("[red]Usage: /skill add <owner/repo[@ref]>  |  /skill <role|all> <skill>[/red]")
        elif cmd == "/role":
            self.cmd_role(args)
        elif cmd == "/tasks":
            self.cmd_tasks(args)
        elif cmd == "/clear":
            if args:
                self.cmd_clear(args[0])
            else:
                console.print("[red]Usage: /clear <role>[/red]")
        elif cmd == "/pause":
            if args:
                self.cmd_pause(args[0])
            else:
                console.print("[red]Usage: /pause <role>[/red]")
        elif cmd == "/resume":
            if args:
                self.cmd_resume(args[0])
            else:
                console.print("[red]Usage: /resume <role>[/red]")
        elif cmd == "/board":
            self.cmd_board()
        elif cmd == "/bridge":
            self.cmd_bridge(restart="restart" in args)
        elif cmd == "/health":
            self.cmd_health()
        elif cmd == "/schedule":
            self.cmd_schedule(args)
        elif cmd == "/loop":
            self.cmd_loop(args)
        elif cmd == "/jobs":
            self.cmd_jobs(args)
        elif cmd in ("/stop", "/quit", "/exit"):
            return False
        elif cmd == "/help":
            self.cmd_help()
        else:
            console.print(f"[red]Unknown command: {cmd}[/red]. Type /help for commands.")

        return True

    # ── Main Loop ───────────────────────────────────────────────────────────

    def run(self) -> None:
        # Banner
        console.print()
        console.print(Panel(
            "[bold cyan]Octobots Supervisor[/bold cyan]\n\n"
            f"Workers: [green]{', '.join(self.workers)}[/green]\n"
            f"Poll: [yellow]{self.interval}s[/yellow] │ tmux: [blue]{TMUX_SESSION}[/blue]\n"
            f"DB: [dim]{RUNTIME_DIR / 'relay.db'}[/dim]\n\n"
            f"View all: [bold]tmux attach -t {TMUX_SESSION}[/bold]\n"
            "Type [cyan]/help[/cyan] for commands.",
            box=box.DOUBLE,
            title="[bold white]🤖[/bold white]",
        ))
        console.print()

        # Background polling
        import threading

        def poll_loop():
            while self._running:
                try:
                    self.poll_once()
                except Exception as e:
                    console.print(f"[red]Poll error: {e}[/red]")
                time.sleep(self.interval)

        poller = threading.Thread(target=poll_loop, daemon=True)
        poller.start()

        # Interactive command loop
        try:
            while self._running:
                try:
                    line = Prompt.ask("[bold cyan]octobots[/bold cyan]")
                    if not line.strip():
                        continue
                    if not line.startswith("/"):
                        console.print("[dim]Type /help for commands, or prefix with / to run a command.[/dim]")
                        continue
                    if not self.handle_command(line):
                        break
                except (KeyboardInterrupt, EOFError):
                    break
        finally:
            self._running = False
            console.print("\n[yellow]Supervisor stopped. Workers still running in tmux.[/yellow]")
            console.print(f"Reattach: [bold]tmux attach -t {TMUX_SESSION}[/bold]")
            console.print(f"Kill all:  [bold]tmux kill-session -t {TMUX_SESSION}[/bold]")


# ── Main ────────────────────────────────────────────────────────────────────

def main() -> None:
    load_env()

    parser = argparse.ArgumentParser(description="Octobots Supervisor")
    parser.add_argument("--interval", type=int, default=15, help="Poll interval in seconds")
    parser.add_argument("--workers", nargs="*", help="Specific workers to launch")
    args = parser.parse_args()

    # Ensure runtime dir
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    (RUNTIME_DIR / "memory").mkdir(exist_ok=True)

    # `--workers` not passed → auto-discover.
    # `--workers` passed with zero args → start empty, build the team interactively
    # via /role add. This is the "form your team from the REPL" mode.
    if args.workers is None:
        workers = discover_workers()
        if not workers:
            console.print("[red]No workers found. Check octobots/roles/ or .octobots/roles/[/red]")
            console.print("[dim]Tip: re-run with --workers (no args) to start empty and add roles via /role add.[/dim]")
            sys.exit(1)
    else:
        workers = args.workers
        if not workers:
            console.print("[yellow]Starting with no workers. Use /role add <name> to spawn roles.[/yellow]")

    supervisor = Supervisor(workers, args.interval)

    if not supervisor.preflight():
        console.print("\n[red]Install missing tools and try again.[/red]")
        sys.exit(1)

    supervisor.setup()
    supervisor.run()


if __name__ == "__main__":
    main()
