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
WORKTREE_ROLES = {"python-dev", "js-dev", "qa-engineer"}

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
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row
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
        self.panes: dict[str, str] = {}  # role → pane target

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

    def save_pane_map(self) -> None:
        pane_map = RUNTIME_DIR / ".pane-map"
        pane_map.write_text(
            "\n".join(f"{role}={target}" for role, target in self.panes.items())
        )


# ── Role Resolution ─────────────────────────────────────────────────────────

def resolve_role(role: str) -> Path | None:
    local = LOCAL_ROLES / role / "CLAUDE.md"
    base = BASE_ROLES / role / "CLAUDE.md"
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
            if (role_dir / "CLAUDE.md").is_file():
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
        self._running = True

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
        for cmd in ["tmux", "claude", "python3", "gh", "git"]:
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

    def _launch_worker(self, role: str) -> None:
        role_dir = resolve_role(role)
        if not role_dir:
            console.print(f"[red]✗ {role}: CLAUDE.md not found[/red]")
            return

        pane = self.tmux.panes.get(role, "")
        if not pane:
            return

        worker_dir = RUNTIME_DIR / "workers" / role
        # Roles with .workspace-root always launch from project root — they read
        # the whole codebase (e.g. qa-engineer) and must not be confined to a
        # subdirectory even when a workers/ folder exists for them.
        uses_project_root = role_dir and (role_dir / ".workspace-root").is_file()
        launch_dir = PROJECT_DIR if uses_project_root else (worker_dir if worker_dir.is_dir() else PROJECT_DIR)
        env_label = "root" if uses_project_root else ("isolated" if worker_dir.is_dir() else "shared")
        console.print(f"[cyan]◆[/cyan] {role} → {launch_dir} [{env_label}]")

        db_path = RUNTIME_DIR / "relay.db"
        gh_token = self._resolve_gh_token(role)
        gh_env = f"GH_TOKEN={gh_token} " if gh_token else ""
        # NOTE: Do NOT pass OCTOBOTS_TG_TOKEN/OCTOBOTS_TG_OWNER here.
        # Shell scripts (notify-user.sh, send-file.sh) read .env.octobots
        # fresh on every invocation, so edits take effect immediately
        # without restarting workers.
        claude_cmd = (
            f"{gh_env}OCTOBOTS_ID={role} OCTOBOTS_DB={db_path} "
            f"CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD=1 "
            f"claude --add-dir '{role_dir}' --dangerously-skip-permissions"
        )
        # cd + launch in one atomic command so Claude starts from launch_dir.
        cmd = f"cd '{launch_dir}' && {claude_cmd}"
        self.tmux.send_keys(pane, cmd, confirm_paste=True)
        self.launched.add(role)
        time.sleep(3)

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

        # Poll taskbox — deliver pending messages
        for role in self.workers:
            msgs = self.taskbox.inbox(role, limit=1)
            if msgs:
                self.process_message(role, msgs[0])

        # Deliver ack responses back to senders
        self._deliver_responses()

        # Poll GitHub for issues assigned to the bot
        self._poll_github_issues()

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
            requests_clear = is_idle and "/clear" in output and "recommended" in output.lower()
            if requests_clear and now - state.get("last_clear", 0) > 60:
                console.print(f"[cyan]🧹 {role}: requested /clear — sending it[/cyan]")
                self.tmux.send_keys(pane, "/clear")
                state["last_clear"] = now
                state["error_count"] = 0
                continue

            # Worker requested /compact (e.g. "Epic X complete. /compact recommended.")
            requests_compact = is_idle and "/compact" in output and "recommended" in output.lower()
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
            "  /loop 10m agent rca-investigator Check for flaky tests[/dim]"
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
            ("/clear <role>", "Send /clear to a worker"),
            ("/tasks [clean|abandon]", "List active tasks; clean requeues processing; abandon drops all"),
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
        elif cmd == "/tasks":
            self.cmd_tasks(args)
        elif cmd == "/clear":
            if args:
                self.cmd_clear(args[0])
            else:
                console.print("[red]Usage: /clear <role>[/red]")
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

    workers = args.workers or discover_workers()
    if not workers:
        console.print("[red]No workers found. Check octobots/roles/ or .octobots/roles/[/red]")
        sys.exit(1)

    supervisor = Supervisor(workers, args.interval)

    if not supervisor.preflight():
        console.print("\n[red]Install missing tools and try again.[/red]")
        sys.exit(1)

    supervisor.setup()
    supervisor.run()


if __name__ == "__main__":
    main()
