#!/usr/bin/env python3
"""Telegram bridge for Octobots — sends user messages to PM's tmux pane.

Architecture:
  User (Telegram) → bridge → tmux send-keys to PM pane
  Any role → notify-user.sh → Telegram Bot API → User

No taskbox for user ↔ PM. Taskbox is only for inter-role communication.
Notifications from roles go directly via Telegram Bot API (notify-user.sh).

Usage:
  python octobots/scripts/telegram-bridge.py

Environment (.env.octobots):
  OCTOBOTS_TG_TOKEN  — Telegram bot token (required)
  OCTOBOTS_TG_OWNER  — Telegram user ID for auth (required)
  OCTOBOTS_TMUX      — tmux session name (default: octobots)
  OCTOBOTS_PM_PANE   — PM's tmux pane name (default: project-manager)
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
PM_PANE = os.environ.get("OCTOBOTS_PM_PANE", "project-manager")


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
    """Send a single-line message to a role's tmux pane."""
    target = resolve_pane(role)
    single = text.replace("\n", " ").strip()
    try:
        subprocess.run(
            ["tmux", "send-keys", "-t", target, single, "Enter"],
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
    from telegram import Update
    from telegram.ext import (
        Application,
        CommandHandler,
        MessageHandler,
        filters,
        ContextTypes,
    )

    async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if str(update.effective_user.id) != TG_OWNER:
            await update.message.reply_text("Unauthorized.")
            return
        await send_telegram(
            context.bot,
            update.effective_chat.id,
            "<b>Octobots</b> connected.\n\n"
            "Messages go directly to <b>Max</b> (PM).\n"
            "Use <code>@role message</code> to reach a specific team member.\n\n"
            "<b>Commands:</b>\n"
            "• /status — team queue stats\n"
            "• /team — list active roles\n",
        )

    async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if str(update.effective_user.id) != TG_OWNER:
            return
        try:
            relay = str(Path(__file__).parent.parent / "skills" / "taskbox" / "scripts" / "relay.py")
            result = subprocess.run(
                ["python3", relay, "stats"],
                capture_output=True, text=True, timeout=10,
            )
            stats = result.stdout.strip()
            if not stats or stats == "{}":
                await send_telegram(context.bot, update.effective_chat.id, "<i>No activity yet.</i>")
            else:
                await send_telegram(
                    context.bot, update.effective_chat.id,
                    f"<b>Queue Status</b>\n<pre>{stats}</pre>",
                )
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def cmd_team(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if str(update.effective_user.id) != TG_OWNER:
            return
        roles = [
            ("📋", "pm",    "Coordination, status"),
            ("📝", "ba",    "Goals → user stories"),
            ("🏗️", "tl",    "Stories → tasks"),
            ("🐍", "py",    "Python / backend"),
            ("⚡", "js",    "JS/TS / frontend"),
            ("🧪", "qa",    "Testing & verification"),
            ("🔍", "scout", "Codebase exploration"),
        ]
        lines = ["<b>Team</b>\n"]
        for icon, name, desc in roles:
            lines.append(f"{icon} <code>@{name}</code> — {desc}")
        await send_telegram(context.bot, update.effective_chat.id, "\n".join(lines))

    async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if str(update.effective_user.id) != TG_OWNER:
            await update.message.reply_text("Unauthorized.")
            return

        text = update.message.text or ""
        if not text.strip():
            return

        # Shorthand aliases for role names
        ALIASES = {
            "pm": "project-manager",
            "max": "project-manager",
            "ba": "ba",
            "alex": "ba",
            "tl": "tech-lead",
            "rio": "tech-lead",
            "py": "python-dev",
            "js": "js-dev",
            "jay": "js-dev",
            "qa": "qa-engineer",
            "sage": "qa-engineer",
            "kit": "scout",
        }

        DISPLAY_NAMES = {
            "project-manager": "📋 pm",
            "ba": "📝 ba",
            "tech-lead": "🏗️ tl",
            "python-dev": "🐍 py",
            "js-dev": "⚡ js",
            "qa-engineer": "🧪 qa",
            "scout": "🔍 scout",
        }

        # Route by: 1) reply to a role's message, 2) @role prefix, 3) default to PM
        target_pane = PM_PANE
        target_label = "📋 Max"

        # Check if replying to a message from a specific role: [role-name] ...
        reply = update.message.reply_to_message
        if reply and reply.text:
            match = re.match(r"\[([a-z][\w-]*)\]", reply.text)
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

    # Build app
    app = Application.builder().token(TG_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("team", cmd_team))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Telegram bridge started — tmux %s:%s", TMUX_SESSION, PM_PANE)
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
