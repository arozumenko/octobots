#!/usr/bin/env python3
"""Telegram bridge for Octobots — user interface via Telegram.

Architecture:
  User (Telegram) → bridge → tmux send-keys to worker panes
  Any role → notify MCP tool → notify_lib → Telegram Bot API → User
  Slash commands → read taskbox/schedule/tmux directly

No taskbox for user ↔ PM. Taskbox is only for inter-role communication.
Notifications from roles go directly via Telegram Bot API (the `notify` MCP
tool, backed by octobots/scripts/notify_lib.py).

Usage:
  python octobots/scripts/telegram-bridge.py

Environment (.env.octobots):
  OCTOBOTS_TG_TOKEN  — Telegram bot token (required)
  OCTOBOTS_TG_OWNER  — Telegram user ID for auth (required)
  OCTOBOTS_TMUX          — tmux session name (default: octobots)
  OCTOBOTS_DEFAULT_ROLE  — role to route to when no @prefix/reply (default: project-manager)
                           Falls back to legacy OCTOBOTS_PM_PANE for back-compat.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

# Allow importing sibling modules (roles.py)
sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── Load .env ───────────────────────────────────────────────────────────────
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env.octobots")
load_dotenv(Path.cwd() / ".env.octobots")

# ── Config ──────────────────────────────────────────────────────────────────

TG_TOKEN = os.environ.get("OCTOBOTS_TG_TOKEN", "")
TG_OWNER = os.environ.get("OCTOBOTS_TG_OWNER", "")
TMUX_SESSION = os.environ.get("OCTOBOTS_TMUX", "octobots")
DEFAULT_ROLE = (
    os.environ.get("OCTOBOTS_DEFAULT_ROLE")
    or os.environ.get("OCTOBOTS_PM_PANE")  # legacy alias
    or "project-manager"
)

SCRIPT_DIR = Path(__file__).parent
OCTOBOTS_DIR = SCRIPT_DIR.parent
PROJECT_DIR = Path.cwd()
RUNTIME_DIR = PROJECT_DIR / ".octobots"
RELAY_SCRIPT = OCTOBOTS_DIR / "skills" / "taskbox" / "scripts" / "relay.py"


def _check_env() -> None:
    if not TG_TOKEN:
        print("Error: OCTOBOTS_TG_TOKEN not set", file=sys.stderr)
        sys.exit(1)
    if not TG_OWNER:
        print("Error: OCTOBOTS_TG_OWNER not set", file=sys.stderr)
        sys.exit(1)


# ── Markdown → Telegram HTML ───────────────────────────────────────────────

def markdown_to_telegram_html(text: str) -> str:
    """Convert Markdown to Telegram-compatible HTML.

    Supports: <b>, <i>, <s>, <u>, <code>, <pre>, <a>, <blockquote>.
    """
    # Escape HTML first
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # Code blocks (``` ... ```)
    text = re.sub(
        r"```\w*\n(.*?)```",
        r"<pre>\1</pre>",
        text,
        flags=re.DOTALL,
    )

    # Inline code
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)

    # Bold (**text** or __text__)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text)

    # Italic (*text* or _text_)
    text = re.sub(r"(?<!\w)\*([^\*\n]+?)\*(?!\w)", r"<i>\1</i>", text)
    text = re.sub(r"(?<!\w)_([^_\n]+?)_(?!\w)", r"<i>\1</i>", text)

    # Strikethrough
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)

    # Headers → bold
    text = re.sub(r"^#{1,6}\s+(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)

    # Links [text](url)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)

    # Horizontal rules
    text = re.sub(r"^---+$", "———", text, flags=re.MULTILINE)

    # Blockquotes
    lines = text.split("\n")
    result = []
    in_quote = False
    quote_lines: list[str] = []
    for line in lines:
        if line.startswith("&gt; "):
            if not in_quote:
                in_quote = True
                quote_lines = []
            quote_lines.append(line[5:])
        else:
            if in_quote:
                result.append("<blockquote>" + "\n".join(quote_lines) + "</blockquote>")
                in_quote = False
                quote_lines = []
            result.append(line)
    if in_quote:
        result.append("<blockquote>" + "\n".join(quote_lines) + "</blockquote>")
    text = "\n".join(result)

    # Unordered lists → bullet points
    text = re.sub(r"^[\-\*] (.+)$", r"• \1", text, flags=re.MULTILINE)

    # Ordered lists → numbered
    _counter = [0]
    def _number_item(match: re.Match) -> str:
        _counter[0] += 1
        return f"{_counter[0]}. {match.group(1)}"
    text = re.sub(r"^\d+\. (.+)$", _number_item, text, flags=re.MULTILINE)

    # Wrap markdown tables in <pre>
    table_lines: list[str] = []
    final_lines: list[str] = []
    in_table = False
    for line in text.split("\n"):
        stripped = line.strip()
        is_table = stripped.startswith("|") and stripped.endswith("|")
        if is_table:
            if not in_table:
                in_table = True
                table_lines = []
            table_lines.append(line)
        else:
            if in_table:
                final_lines.append("<pre>" + "\n".join(table_lines) + "</pre>")
                in_table = False
            final_lines.append(line)
    if in_table:
        final_lines.append("<pre>" + "\n".join(table_lines) + "</pre>")
    text = "\n".join(final_lines)

    # Clean up excessive newlines
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    return text


# ── tmux helpers ────────────────────────────────────────────────────────────

def _load_pane_map() -> dict[str, str]:
    """Load role → tmux pane target from supervisor's .pane-map file."""
    pane_map_path = Path.cwd() / ".octobots" / ".pane-map"
    if not pane_map_path.is_file():
        return {}
    result = {}
    for line in pane_map_path.read_text().splitlines():
        if "=" in line:
            role, target = line.strip().split("=", 1)
            result[role] = target
    return result


def resolve_pane(role: str) -> str:
    """Resolve a role name to its tmux pane target."""
    pane_map = _load_pane_map()
    if role in pane_map:
        return pane_map[role]
    # Fallback: try as window name
    return f"{TMUX_SESSION}:{role}"


def tmux_send(role: str, text: str) -> bool:
    """Send a single-line message to a role's tmux pane.

    Sends a second Enter after 1s to confirm Claude Code's paste prompt
    which appears for long input.
    """
    target = resolve_pane(role)
    single = text.replace("\n", " ").strip()
    try:
        subprocess.run(
            ["tmux", "send-keys", "-t", target, single, "Enter"],
            check=True, capture_output=True,
        )
        import time
        time.sleep(1)
        subprocess.run(
            ["tmux", "send-keys", "-t", target, "Enter"],
            check=True, capture_output=True,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def tmux_session_exists() -> bool:
    result = subprocess.run(
        ["tmux", "has-session", "-t", TMUX_SESSION],
        capture_output=True,
    )
    return result.returncode == 0


def tmux_capture(pane: str, lines: int = 20) -> str:
    """Capture last N lines from a tmux pane."""
    try:
        r = subprocess.run(
            ["tmux", "capture-pane", "-t", pane, "-p", "-S", f"-{lines}"],
            capture_output=True, text=True,
        )
        return r.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape codes."""
    return re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', text)


# ── Telegram send with HTML formatting ──────────────────────────────────────

async def send_telegram(bot, chat_id: int, text: str, role: str = "") -> None:
    """Send a formatted message to Telegram, splitting if needed."""
    if role:
        text = f"<b>[{role}]</b>\n{text}"

    # If text already contains HTML tags, send as-is. Otherwise convert markdown.
    html = text if re.search(r"<[a-z]+[ >]", text) else markdown_to_telegram_html(text)

    # Split long messages (Telegram limit: 4096 chars)
    chunks = [html[i:i + 4000] for i in range(0, len(html), 4000)]

    for chunk in chunks:
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=chunk,
                parse_mode="HTML",
            )
        except Exception:
            # If HTML parsing fails, fall back to plain text
            try:
                plain = re.sub(r"<[^>]+>", "", chunk)
                await bot.send_message(chat_id=chat_id, text=plain)
            except Exception as e:
                logger.error("Failed to send to Telegram: %s", e)


# ── Telegram bot ────────────────────────────────────────────────────────────

async def run_bot() -> None:
    from telegram import Update, BotCommand
    from telegram.ext import (
        Application,
        CommandHandler,
        MessageHandler,
        filters,
        ContextTypes,
    )
    from roles import ROLE_ALIASES as ALIASES, ROLE_DISPLAY as DISPLAY_NAMES, resolve_alias

    def label_for(role: str) -> str:
        """Display label for a role; falls back to '🤖 <role>' for unknown roles."""
        return DISPLAY_NAMES.get(role, f"🤖 {role}")

    def _auth(update: Update) -> bool:
        return str(update.effective_user.id) == TG_OWNER

    # ── /start ─────────────────────────────────────────────────────────

    async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _auth(update):
            await update.message.reply_text("Unauthorized.")
            return
        await send_telegram(
            context.bot,
            update.effective_chat.id,
            "<b>Octobots</b> connected.\n\n"
            f"Messages go to <b>{label_for(DEFAULT_ROLE)}</b> by default.\n"
            "Use <code>@role message</code> to reach a specific team member.\n\n"
            "<b>Commands:</b>\n"
            "• /status — worker states\n"
            "• /tasks — taskbox queue stats\n"
            "• /team — list roles and aliases\n"
            "• /logs <i>role</i> — last output from a worker\n"
            "• /board — team whiteboard\n"
            "• /health — system health check\n"
            "• /jobs — scheduled jobs\n"
            "• /schedule — create a scheduled job\n"
            "• /loop — shortcut for recurring schedule\n"
            "• /restart <i>role</i> — restart a worker\n"
            "• /help — full command reference\n",
        )

    # ── /status — worker states ────────────────────────────────────────

    async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _auth(update):
            return
        pane_map = _load_pane_map()
        if not pane_map:
            await send_telegram(context.bot, update.effective_chat.id, "<i>No workers running.</i>")
            return

        lines = ["<b>Worker Status</b>\n"]
        for role, pane in sorted(pane_map.items()):
            output = tmux_capture(pane, 3)
            last_line = _strip_ansi(output.split("\n")[-1]) if output else ""
            display = DISPLAY_NAMES.get(role, role)
            # Detect idle vs working
            if not last_line or ">" in last_line or "❯" in last_line or "bypass" in last_line.lower():
                state = "💤"
            else:
                state = "⚙️"
            lines.append(f"{display} {state}  <code>{last_line[:50]}</code>")

        await send_telegram(context.bot, update.effective_chat.id, "\n".join(lines))

    # ── /tasks — taskbox stats ─────────────────────────────────────────

    async def cmd_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _auth(update):
            return
        try:
            result = subprocess.run(
                ["python3", str(RELAY_SCRIPT), "stats"],
                capture_output=True, text=True, timeout=10,
            )
            stats = result.stdout.strip()
            if not stats or stats == "{}":
                await send_telegram(context.bot, update.effective_chat.id, "<i>No taskbox activity.</i>")
            else:
                await send_telegram(
                    context.bot, update.effective_chat.id,
                    f"<b>Taskbox</b>\n<pre>{stats}</pre>",
                )
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    # ── /team — role listing ───────────────────────────────────────────

    async def cmd_team(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _auth(update):
            return
        # Build the roster from actually-running panes so PA-only / custom
        # team compositions don't show ghost teammates.
        pane_map = _load_pane_map()
        # Reverse ROLE_ALIASES to find a short alias for each running role.
        short_alias: dict[str, str] = {}
        for alias, full in ALIASES.items():
            if full in pane_map and full != alias and len(alias) <= len(short_alias.get(full, "x" * 99)):
                short_alias[full] = alias

        lines = ["<b>Team</b>\n"]
        if not pane_map:
            lines.append("<i>No workers running.</i>")
        else:
            for role in sorted(pane_map.keys()):
                display = label_for(role)
                alias = short_alias.get(role, role)
                marker = "  ← default" if role == DEFAULT_ROLE else ""
                lines.append(f"{display} <code>@{alias}</code>{marker}")
        await send_telegram(context.bot, update.effective_chat.id, "\n".join(lines))

    # ── /logs <role> — last output from a worker ───────────────────────

    async def cmd_logs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _auth(update):
            return
        args = context.args or []
        if not args:
            await send_telegram(context.bot, update.effective_chat.id, "Usage: <code>/logs role [lines]</code>")
            return

        role = resolve_alias(args[0])
        lines = int(args[1]) if len(args) > 1 else 20
        pane_map = _load_pane_map()
        pane = pane_map.get(role)
        if not pane:
            await send_telegram(context.bot, update.effective_chat.id, f"Unknown role: <code>{args[0]}</code>")
            return

        output = _strip_ansi(tmux_capture(pane, lines))
        display = DISPLAY_NAMES.get(role, role)
        if output:
            await send_telegram(context.bot, update.effective_chat.id, f"<b>{display}</b>\n<pre>{output[:3500]}</pre>")
        else:
            await send_telegram(context.bot, update.effective_chat.id, f"<b>{display}</b> — <i>no output</i>")

    # ── /board — team whiteboard ───────────────────────────────────────

    async def cmd_board(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _auth(update):
            return
        board_path = RUNTIME_DIR / "board.md"
        if board_path.is_file():
            content = board_path.read_text(encoding="utf-8").strip()
            if content:
                await send_telegram(context.bot, update.effective_chat.id, content[:3500])
            else:
                await send_telegram(context.bot, update.effective_chat.id, "<i>Board is empty.</i>")
        else:
            await send_telegram(context.bot, update.effective_chat.id, "<i>No board.md found.</i>")

    # ── /health — system health check ──────────────────────────────────

    async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _auth(update):
            return
        lines = ["<b>Health Check</b>\n"]

        # tmux
        lines.append(f"tmux: {'✅' if tmux_session_exists() else '❌'}")

        # relay DB
        db_ok = (RUNTIME_DIR / "relay.db").is_file()
        lines.append(f"taskbox DB: {'✅' if db_ok else '❌'}")

        # workers
        pane_map = _load_pane_map()
        for role, pane in sorted(pane_map.items()):
            output = tmux_capture(pane, 1)
            alive = bool(output.strip())
            display = DISPLAY_NAMES.get(role, role)
            lines.append(f"  {display}: {'✅' if alive else '❌'}")

        # pending tasks
        try:
            result = subprocess.run(
                ["python3", str(RELAY_SCRIPT), "stats"],
                capture_output=True, text=True, timeout=5,
            )
            stats = json.loads(result.stdout.strip()) if result.stdout.strip() else {}
            pending = sum(v.get("pending", 0) for v in stats.values()) if isinstance(stats, dict) else 0
            lines.append(f"pending tasks: {pending}")
        except Exception:
            lines.append("pending tasks: ?")

        # scheduled jobs
        schedule_path = RUNTIME_DIR / "schedule.json"
        if schedule_path.is_file():
            try:
                jobs = json.loads(schedule_path.read_text())
                active = sum(1 for j in jobs if not j.get("paused"))
                paused = sum(1 for j in jobs if j.get("paused"))
                job_str = f"{active} active"
                if paused:
                    job_str += f", {paused} paused"
                lines.append(f"scheduled jobs: {job_str}")
            except Exception:
                lines.append("scheduled jobs: ?")
        else:
            lines.append("scheduled jobs: none")

        await send_telegram(context.bot, update.effective_chat.id, "\n".join(lines))

    # ── /jobs — list scheduled jobs ────────────────────────────────────

    async def cmd_jobs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _auth(update):
            return
        args = context.args or []

        schedule_path = RUNTIME_DIR / "schedule.json"

        # Sub-commands: cancel, pause, resume
        if args and args[0] in ("cancel", "pause", "resume"):
            from scheduler import JobStore
            store = JobStore(schedule_path)

            if len(args) < 2:
                await send_telegram(context.bot, update.effective_chat.id, f"Usage: <code>/jobs {args[0]} id</code>")
                return

            job_id = args[1]
            if args[0] == "cancel":
                if store.remove(job_id):
                    await send_telegram(context.bot, update.effective_chat.id, f"✅ Cancelled <code>{job_id}</code>")
                else:
                    await send_telegram(context.bot, update.effective_chat.id, f"Job <code>{job_id}</code> not found")
            else:
                result = store.toggle_pause(job_id)
                if result is None:
                    await send_telegram(context.bot, update.effective_chat.id, f"Job <code>{job_id}</code> not found")
                else:
                    state = "paused" if result else "active"
                    await send_telegram(context.bot, update.effective_chat.id, f"✅ <code>{job_id}</code> → {state}")
            return

        # List jobs
        if not schedule_path.is_file():
            await send_telegram(context.bot, update.effective_chat.id, "<i>No scheduled jobs.</i>")
            return

        try:
            jobs = json.loads(schedule_path.read_text())
        except Exception:
            await send_telegram(context.bot, update.effective_chat.id, "<i>No scheduled jobs.</i>")
            return

        if not jobs:
            await send_telegram(context.bot, update.effective_chat.id, "<i>No scheduled jobs.</i>")
            return

        lines = ["<b>Scheduled Jobs</b>\n"]
        for j in jobs:
            status = "⏸" if j.get("paused") else "▶️"
            jtype = j.get("type", "?")
            spec = j.get("spec", "?")
            action = j.get("action", "?")
            target = j.get("target", "?")
            content = j.get("content", "")
            runs = j.get("run_count", 0)
            jid = j.get("id", "?")

            content_short = (content[:30] + "…") if len(content) > 30 else content
            lines.append(
                f"{status} <code>{jid}</code> {jtype} {spec} → {action} {target}\n"
                f"    {content_short} (×{runs})"
            )

        await send_telegram(context.bot, update.effective_chat.id, "\n".join(lines))

    # ── /schedule — create a scheduled job ─────────────────────────────

    async def cmd_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _auth(update):
            return
        args = context.args or []

        if len(args) < 3:
            await send_telegram(
                context.bot, update.effective_chat.id,
                "<b>Schedule</b>\n\n"
                "<code>/schedule every 30m @pm Check tasks</code>\n"
                "<code>/schedule at 15:00 @py Review PR</code>\n"
                "<code>/schedule every 1h run git fetch</code>\n"
                "<code>/schedule every 10m agent taskbox-listener Check inbox</code>\n"
                "<code>/schedule cron 0 9 * * MON-FRI @ba Standup</code>\n",
            )
            return

        from scheduler import JobStore, Scheduler, resolve_agent

        job_type = args[0].lower()
        if job_type not in ("at", "every", "cron"):
            await send_telegram(context.bot, update.effective_chat.id, f"Invalid type: <code>{job_type}</code>. Use: at, every, cron")
            return

        # Parse spec
        if job_type == "cron":
            if len(args) < 8:
                await send_telegram(context.bot, update.effective_chat.id, "Cron needs 5 fields + target + message")
                return
            spec = " ".join(args[1:6])
            rest = args[6:]
        else:
            spec = args[1]
            rest = args[2:]

        if not rest:
            await send_telegram(context.bot, update.effective_chat.id, "Missing target (@role, run, or agent)")
            return

        first = rest[0].lower()

        if first.startswith("@"):
            action = "send"
            role = resolve_alias(first[1:])
            target = role
            content = " ".join(rest[1:])
        elif first == "run":
            action = "run"
            target = " ".join(rest[1:])
            content = ""
        elif first == "agent":
            action = "agent"
            if len(rest) < 3:
                await send_telegram(context.bot, update.effective_chat.id, "Usage: agent <i>name</i> <i>prompt</i>")
                return
            target = rest[1]
            content = " ".join(rest[2:])
            if not resolve_agent(target, OCTOBOTS_DIR, RUNTIME_DIR):
                await send_telegram(context.bot, update.effective_chat.id, f"Agent not found: <code>{target}</code>")
                return
        else:
            await send_telegram(context.bot, update.effective_chat.id, f"Expected @role, run, or agent — got: <code>{first}</code>")
            return

        store = JobStore(RUNTIME_DIR / "schedule.json")
        scheduler = Scheduler(store=store, taskbox=None, tmux=None, relay_script=RELAY_SCRIPT,
                              octobots_dir=OCTOBOTS_DIR, runtime_dir=RUNTIME_DIR)
        try:
            job = scheduler.create_job(job_type, spec, action, target, content)
            from datetime import datetime
            next_dt = datetime.fromisoformat(job.next_run)
            await send_telegram(
                context.bot, update.effective_chat.id,
                f"✅ Scheduled <code>{job.id}</code> {job_type} {spec} → {action} {target}\n"
                f"Next: {next_dt.strftime('%Y-%m-%d %H:%M UTC')}",
            )
        except ValueError as e:
            await send_telegram(context.bot, update.effective_chat.id, f"Error: {e}")

    # ── /loop — shortcut for /schedule every ───────────────────────────

    async def cmd_loop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _auth(update):
            return
        args = context.args or []
        if len(args) < 3:
            await send_telegram(
                context.bot, update.effective_chat.id,
                "<b>Loop</b> (shortcut for <code>/schedule every</code>)\n\n"
                "<code>/loop 30m @pm Check tasks</code>\n"
                "<code>/loop 5m run ./health-check.sh</code>\n"
                "<code>/loop 10m agent taskbox-listener Check inbox</code>\n",
            )
            return
        # Reuse schedule handler with "every" prepended
        context.args = ["every"] + list(args)
        await cmd_schedule(update, context)

    # ── /restart <role> — restart a worker ─────────────────────────────

    async def cmd_restart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _auth(update):
            return
        args = context.args or []
        if not args:
            await send_telegram(context.bot, update.effective_chat.id, "Usage: <code>/restart role</code>")
            return

        role = resolve_alias(args[0])
        pane_map = _load_pane_map()

        if role == "all":
            targets = list(pane_map.keys())
        elif role in pane_map:
            targets = [role]
        else:
            await send_telegram(context.bot, update.effective_chat.id, f"Unknown role: <code>{args[0]}</code>")
            return

        # Send restart request via taskbox (supervisor picks it up)
        for r in targets:
            try:
                subprocess.run(
                    ["python3", str(RELAY_SCRIPT), "send", "--from", "telegram", "--to", "supervisor", f"restart {r}"],
                    capture_output=True, text=True, timeout=10,
                )
            except Exception:
                pass

        display = ", ".join(DISPLAY_NAMES.get(r, r) for r in targets)
        await send_telegram(context.bot, update.effective_chat.id, f"🔄 Restart requested: {display}")

    # Claude Code slash commands forwarded verbatim (no "[User via Telegram]:" prefix).
    # Registered as CommandHandlers because unregistered /commands are swallowed by filters.COMMAND.
    _CC_PASSTHROUGH_COMMANDS = (
        "model",
        "effort",
        "clear",
        "compact",
        "review",
        "memory",
    )

    async def cmd_claude_passthrough(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _auth(update):
            return

        # Strip @BotName suffix that Telegram appends in groups
        command = update.message.text.split()[0].split("@")[0]

        target_pane = DEFAULT_ROLE
        target_label = label_for(DEFAULT_ROLE)
        args = list(context.args or [])
        if args and args[0].startswith("@"):
            target_pane = resolve_alias(args[0][1:])
            target_label = label_for(target_pane)
            args = args[1:]

        raw = f"{command} {' '.join(args)}".strip() if args else command

        if not tmux_session_exists():
            await send_telegram(
                context.bot, update.effective_chat.id,
                "Supervisor not running. Start with: <code>octobots/supervisor.sh</code>",
            )
            return

        success = tmux_send(target_pane, raw)
        if success:
            logger.info("Telegram CC passthrough → %s: %s", target_pane, raw)
            await send_telegram(
                context.bot, update.effective_chat.id,
                f"→ <b>{target_label}</b>  <code>{raw}</code>",
            )
        else:
            await send_telegram(
                context.bot, update.effective_chat.id,
                f"Failed to reach <b>{target_label}</b> — pane not found.",
            )

    # ── /help — full command reference ─────────────────────────────────

    async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _auth(update):
            return
        await send_telegram(
            context.bot, update.effective_chat.id,
            "<b>Commands</b>\n\n"
            "<b>Team</b>\n"
            "<code>@role message</code> — send to a worker\n"
            "/status — worker states\n"
            "/tasks — taskbox queue stats\n"
            "/team — list roles and aliases\n"
            "/logs <i>role</i> — last output from a worker\n"
            "/board — team whiteboard\n"
            "/health — system health check\n"
            "/restart <i>role|all</i> — restart a worker\n\n"
            "<b>Scheduling</b>\n"
            "/jobs — list scheduled jobs\n"
            "/jobs cancel|pause|resume <i>id</i>\n"
            "/schedule <i>type spec @role msg</i>\n"
            "/loop <i>interval @role msg</i>\n\n"
            "<b>Claude Code passthrough</b>\n"
            "These are forwarded verbatim to the worker's Claude Code session:\n"
            "/model <i>model-name</i> — switch model (e.g. claude-opus-4-7)\n"
            "/effort <i>level</i> — set effort (default/low/medium/high/max)\n"
            "/clear — clear Claude Code context\n"
            "/compact — compact context\n"
            "/review — trigger code review\n"
            "/memory — invoke memory skill\n"
            "Prefix with <code>@role</code> to target a specific worker.\n\n"
            "<b>Examples</b>\n"
            "<code>/schedule every 30m @pm Check tasks</code>\n"
            "<code>/loop 5m run ./health-check.sh</code>\n"
            "<code>/jobs cancel abc123</code>\n"
            "<code>/model claude-opus-4-7</code>\n"
            "<code>/effort max</code>\n",
        )

    # ── Message handler — @role routing ────────────────────────────────

    async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _auth(update):
            await update.message.reply_text("Unauthorized.")
            return

        text = update.message.text or ""
        if not text.strip():
            return

        # Route by: 1) reply to a role's message, 2) @role prefix, 3) default role
        target_pane = DEFAULT_ROLE
        target_label = label_for(DEFAULT_ROLE)

        # Check if replying to a message from a specific role: [role-name] ...
        # Role badge can be in .text (regular message) or .caption (photo/document)
        reply = update.message.reply_to_message
        reply_text = (reply.text or reply.caption or "") if reply else ""
        if reply_text:
            match = re.match(r"\[([a-z][\w-]*)\]", reply_text)
            if match:
                role = match.group(1)
                target_pane = ALIASES.get(role, role)
                target_label = DISPLAY_NAMES.get(target_pane, target_pane)

        # Explicit @role overrides reply routing
        if text.startswith("@"):
            parts = text.split(" ", 1)
            role = parts[0][1:].lower()
            target_pane = ALIASES.get(role, role)
            target_label = DISPLAY_NAMES.get(target_pane, target_pane)
            text = parts[1] if len(parts) > 1 else ""
            if not text:
                await send_telegram(
                    context.bot, update.effective_chat.id,
                    f"Usage: <code>@{target_pane} your message</code>",
                )
                return

        if not tmux_session_exists():
            await send_telegram(
                context.bot, update.effective_chat.id,
                "Supervisor not running.\nStart with: <code>octobots/supervisor.sh</code>",
            )
            return

        # Broadcast to all workers
        if target_pane == "all":
            pane_map = _load_pane_map()
            sent = []
            failed = []
            for role_name, _ in sorted(pane_map.items()):
                if tmux_send(role_name, f"[User via Telegram to everyone]: {text}"):
                    display = DISPLAY_NAMES.get(role_name, role_name)
                    sent.append(display)
                else:
                    failed.append(role_name)

            parts = []
            if sent:
                parts.append(f"→ <b>{', '.join(sent)}</b>")
            if failed:
                parts.append(f"Failed: {', '.join(failed)}")
            await send_telegram(context.bot, update.effective_chat.id, "\n".join(parts) if parts else "No workers running.")
            return

        # If text is a Claude Code slash command (e.g. @role /model ...), forward verbatim
        cc_cmd = text.split()[0].lstrip("/") if text.startswith("/") else None
        if cc_cmd and cc_cmd in _CC_PASSTHROUGH_COMMANDS:
            success = tmux_send(target_pane, text)
            if success:
                logger.info("Telegram CC passthrough → %s: %s", target_pane, text[:80])
                await send_telegram(
                    context.bot, update.effective_chat.id,
                    f"→ <b>{target_label}</b>  <code>{text}</code>",
                )
            else:
                await send_telegram(
                    context.bot, update.effective_chat.id,
                    f"Failed to reach <b>{target_label}</b> — pane <code>{target_pane}</code> not found in tmux.",
                )
            return

        # Send directly to the role's tmux pane
        success = tmux_send(target_pane, f"[User via Telegram]: {text}")

        if success:
            logger.info("Telegram → %s: %s", target_pane, text[:80])
            await send_telegram(
                context.bot, update.effective_chat.id,
                f"→ <b>{target_label}</b>",
            )
        else:
            await send_telegram(
                context.bot, update.effective_chat.id,
                f"Failed to reach <b>{target_label}</b> — pane <code>{target_pane}</code> not found in tmux.",
            )

    # ── Attachment handler — documents, photos, etc. ─────────────────────

    async def handle_attachment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _auth(update):
            await update.message.reply_text("Unauthorized.")
            return

        msg = update.message
        caption = msg.caption or ""

        # Determine target role from caption (@role prefix) or reply context
        target_pane = DEFAULT_ROLE
        target_label = label_for(DEFAULT_ROLE)

        # Check reply context (role badge can be in .text or .caption)
        reply = msg.reply_to_message
        reply_text = (reply.text or reply.caption or "") if reply else ""
        if reply_text:
            match = re.match(r"\[([a-z][\w-]*)\]", reply_text)
            if match:
                role = match.group(1)
                target_pane = ALIASES.get(role, role)
                target_label = DISPLAY_NAMES.get(target_pane, target_pane)

        # Explicit @role in caption overrides
        if caption.startswith("@"):
            parts = caption.split(" ", 1)
            role = parts[0][1:].lower()
            target_pane = ALIASES.get(role, role)
            target_label = DISPLAY_NAMES.get(target_pane, target_pane)
            caption = parts[1] if len(parts) > 1 else ""

        # Download the file
        file_obj = None
        filename = "attachment"

        if msg.document:
            file_obj = await context.bot.get_file(msg.document.file_id)
            filename = msg.document.file_name or f"document_{msg.document.file_unique_id}"
        elif msg.photo:
            # Get the largest photo
            photo = msg.photo[-1]
            file_obj = await context.bot.get_file(photo.file_id)
            filename = f"photo_{photo.file_unique_id}.jpg"
        elif msg.audio:
            file_obj = await context.bot.get_file(msg.audio.file_id)
            filename = msg.audio.file_name or f"audio_{msg.audio.file_unique_id}"
        elif msg.video:
            file_obj = await context.bot.get_file(msg.video.file_id)
            filename = msg.video.file_name or f"video_{msg.video.file_unique_id}"
        elif msg.voice:
            file_obj = await context.bot.get_file(msg.voice.file_id)
            filename = f"voice_{msg.voice.file_unique_id}.ogg"

        if not file_obj:
            await send_telegram(context.bot, update.effective_chat.id, "<i>Unsupported attachment type.</i>")
            return

        # Save to .octobots/inbox/
        inbox_dir = RUNTIME_DIR / "inbox"
        inbox_dir.mkdir(parents=True, exist_ok=True)
        local_path = inbox_dir / filename

        await file_obj.download_to_drive(str(local_path))
        logger.info("Downloaded attachment: %s → %s", filename, local_path)

        if not tmux_session_exists():
            await send_telegram(
                context.bot, update.effective_chat.id,
                "Supervisor not running.\nStart with: <code>octobots/supervisor.sh</code>",
            )
            return

        # For text-based files, include content inline in the prompt
        text_extensions = {".md", ".txt", ".json", ".yaml", ".yml", ".csv", ".py", ".js", ".ts", ".html", ".css", ".sh", ".toml", ".cfg", ".ini", ".xml", ".sql"}
        ext = Path(filename).suffix.lower()
        file_ref = f"(file saved to {local_path})"

        # Always tell the role where the file is saved so it can Read it.
        # For short text files, include a preview; for everything else, just the path.
        if ext in text_extensions and local_path.stat().st_size < 2000:
            content = local_path.read_text(encoding="utf-8", errors="replace")
            prompt = f"[User via Telegram] sent file '{filename}' (saved to {local_path}): {caption} -- Content: {content}"
        elif ext in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg"}:
            prompt = f"[User via Telegram] sent image '{filename}' (saved to {local_path}): {caption} -- Use the Read tool on {local_path} to view it."
        else:
            prompt = f"[User via Telegram] sent file '{filename}' (saved to {local_path}): {caption} -- Use the Read tool on {local_path} to view it."

        success = tmux_send(target_pane, prompt)

        if success:
            await send_telegram(
                context.bot, update.effective_chat.id,
                f"📎 <b>{filename}</b> → <b>{target_label}</b>",
            )
        else:
            await send_telegram(
                context.bot, update.effective_chat.id,
                f"Failed to reach <b>{target_label}</b> — pane not found.",
            )

    # ── Build app and register handlers ────────────────────────────────

    app = Application.builder().token(TG_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("tasks", cmd_tasks))
    app.add_handler(CommandHandler("team", cmd_team))
    app.add_handler(CommandHandler("logs", cmd_logs))
    app.add_handler(CommandHandler("board", cmd_board))
    app.add_handler(CommandHandler("health", cmd_health))
    app.add_handler(CommandHandler("jobs", cmd_jobs))
    app.add_handler(CommandHandler("schedule", cmd_schedule))
    app.add_handler(CommandHandler("loop", cmd_loop))
    app.add_handler(CommandHandler("restart", cmd_restart))
    app.add_handler(CommandHandler("help", cmd_help))
    # Claude Code native slash commands — forwarded verbatim to the worker pane.
    for _cc_cmd in _CC_PASSTHROUGH_COMMANDS:
        app.add_handler(CommandHandler(_cc_cmd, cmd_claude_passthrough))
    # Attachment handler MUST be registered before text handler.
    # Photo-with-caption messages can match both filters.TEXT (via caption)
    # and filters.PHOTO. By registering attachments first, they get priority.
    app.add_handler(MessageHandler(
        (filters.Document.ALL | filters.PHOTO | filters.AUDIO | filters.VIDEO | filters.VOICE) & ~filters.COMMAND,
        handle_attachment,
    ))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Set bot command menu (shows in Telegram UI)
    await app.bot.set_my_commands([
        BotCommand("status", "Worker states"),
        BotCommand("tasks", "Taskbox queue stats"),
        BotCommand("team", "List roles and aliases"),
        BotCommand("logs", "Last output from a worker"),
        BotCommand("board", "Team whiteboard"),
        BotCommand("health", "System health check"),
        BotCommand("jobs", "Scheduled jobs"),
        BotCommand("schedule", "Create a scheduled job"),
        BotCommand("loop", "Recurring schedule shortcut"),
        BotCommand("restart", "Restart a worker"),
        BotCommand("help", "Command reference"),
    ])

    logger.info("Telegram bridge started — tmux %s, default role %s", TMUX_SESSION, DEFAULT_ROLE)
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


def main() -> None:
    _check_env()
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
