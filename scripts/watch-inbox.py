#!/usr/bin/env python3
"""Watchdog script: LLM-less inbox directory monitor.

Monitors a configured inbox directory for new files. When a file appears,
writes a raw signal note to the Obsidian vault and delivers a taskbox
message to the PA role. No LLM involvement.

Usage:
    python3 octobots/scripts/watch-inbox.py \\
        --inbox .octobots/pa-inbox/ \\
        --role personal-assistant \\
        --vault ~/Notes

    # Process once and exit
    python3 octobots/scripts/watch-inbox.py \\
        --inbox .octobots/pa-inbox/ --role personal-assistant --vault ~/Notes --once
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Resolve paths
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
OCTOBOTS_DIR = SCRIPT_DIR.parent
RELAY_SCRIPT = OCTOBOTS_DIR / "skills" / "taskbox" / "scripts" / "relay.py"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"


def setup_logging(log_path: Path | None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(str(log_path), encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, handlers=handlers)


# ---------------------------------------------------------------------------
# Vault writing
# ---------------------------------------------------------------------------
IGNORED_PREFIXES = (".",)
IGNORED_SUFFIXES = (".tmp", ".part", ".swp", ".swx")


def _should_ignore(name: str) -> bool:
    if any(name.startswith(p) for p in IGNORED_PREFIXES):
        return True
    if any(name.endswith(s) for s in IGNORED_SUFFIXES):
        return True
    return False


def _slug(filename: str) -> str:
    base = Path(filename).stem
    # Lowercase, replace non-alnum with hyphens, collapse runs
    slug = "".join(c if c.isalnum() else "-" for c in base.lower()).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    # Keep it short + add hash suffix for uniqueness
    short = slug[:40]
    h = hashlib.md5(filename.encode()).hexdigest()[:6]
    return f"{short}-{h}"


def write_signal_note(vault: Path, filename: str, content: str) -> Path:
    """Write a raw signal note to the Obsidian vault and return its path."""
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    slug = _slug(filename)

    signals_dir = vault / "Signals"
    signals_dir.mkdir(parents=True, exist_ok=True)

    note_name = f"{date_str}-{slug}.md"
    note_path = signals_dir / note_name

    frontmatter = (
        "---\n"
        "type: signal\n"
        "source: inbox\n"
        f"date: {date_str}\n"
        f"filename: {filename}\n"
        "processed: false\n"
        "action: pending\n"
        "tags: [inbox, unprocessed]\n"
        "---\n\n"
    )

    note_path.write_text(frontmatter + content, encoding="utf-8")
    return note_path


# ---------------------------------------------------------------------------
# Taskbox delivery
# ---------------------------------------------------------------------------
def send_taskbox(role: str, signal_path: Path) -> bool:
    """Send a taskbox message pointing to the signal note."""
    db = os.environ.get("OCTOBOTS_DB", "")
    cmd = [
        sys.executable,
        str(RELAY_SCRIPT),
        "send",
        "--from", "watchdog",
        "--to", role,
        str(signal_path),
    ]
    if db:
        cmd.insert(2, f"--db={db}")
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=10)
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        logging.error("Taskbox send failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# File processing
# ---------------------------------------------------------------------------
def process_file(filepath: Path, vault: Path, role: str, processed_dir: Path) -> None:
    """Process a single inbox file: vault note → taskbox → move to processed."""
    filename = filepath.name
    if _should_ignore(filename):
        logging.debug("Ignoring %s", filename)
        return

    logging.info("Processing: %s", filename)

    # Read content
    try:
        content = filepath.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logging.error("Cannot read %s: %s", filepath, exc)
        return

    # Write signal note
    signal_path = write_signal_note(vault, filename, content)
    logging.info("Signal note: %s", signal_path)

    # Send taskbox message
    delivered = send_taskbox(role, signal_path)
    status = "delivered" if delivered else "delivery-failed"
    logging.info("Taskbox: %s (%s)", filename, status)

    # Move to processed
    date_dir = processed_dir / datetime.now(timezone.utc).strftime("%Y-%m-%d")
    date_dir.mkdir(parents=True, exist_ok=True)
    dest = date_dir / filename
    # Handle name collisions
    if dest.exists():
        stem, suffix = dest.stem, dest.suffix
        dest = date_dir / f"{stem}-{int(time.time())}{suffix}"
    shutil.move(str(filepath), str(dest))
    logging.info("Moved to: %s", dest)


def scan_once(inbox: Path, vault: Path, role: str, processed_dir: Path) -> int:
    """Scan inbox directory once. Returns number of files processed."""
    count = 0
    for entry in sorted(inbox.iterdir()):
        if not entry.is_file():
            continue
        if _should_ignore(entry.name):
            continue
        process_file(entry, vault, role, processed_dir)
        count += 1
    return count


# ---------------------------------------------------------------------------
# Filesystem watching (watchdog library or polling fallback)
# ---------------------------------------------------------------------------
def watch_with_watchdog(inbox: Path, vault: Path, role: str, processed_dir: Path) -> None:
    """Use the watchdog library for OS-native filesystem events."""
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    class InboxHandler(FileSystemEventHandler):
        def on_created(self, event):  # type: ignore[override]
            if event.is_directory:
                return
            filepath = Path(event.src_path)
            if _should_ignore(filepath.name):
                return
            # Small delay to let writes complete
            time.sleep(0.3)
            if filepath.exists():
                process_file(filepath, vault, role, processed_dir)

    observer = Observer()
    observer.schedule(InboxHandler(), str(inbox), recursive=False)
    observer.start()
    logging.info("Watching %s (OS-native events)", inbox)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


def watch_with_polling(inbox: Path, vault: Path, role: str, processed_dir: Path,
                       interval: float = 2.0) -> None:
    """Polling fallback when watchdog library is unavailable."""
    logging.info("Watching %s (polling every %.0fs)", inbox, interval)
    seen: set[str] = {e.name for e in inbox.iterdir() if e.is_file()}

    try:
        while True:
            time.sleep(interval)
            current = set()
            for entry in inbox.iterdir():
                if not entry.is_file():
                    continue
                current.add(entry.name)
            new_files = current - seen
            for name in sorted(new_files):
                filepath = inbox / name
                if filepath.exists():
                    process_file(filepath, vault, role, processed_dir)
            seen = current
    except KeyboardInterrupt:
        pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Watch inbox directory for new files")
    parser.add_argument("--inbox", required=True, help="Path to inbox directory")
    parser.add_argument("--role", default="personal-assistant", help="Taskbox target role")
    parser.add_argument("--vault", help="Obsidian vault path (reads from TOOLS.md if omitted)")
    parser.add_argument("--once", action="store_true", help="Process existing files and exit")
    parser.add_argument("--log", help="Log file path (default: .octobots/watchdog.log)")
    args = parser.parse_args()

    inbox = Path(args.inbox).resolve()
    inbox.mkdir(parents=True, exist_ok=True)

    processed_dir = inbox / "processed"
    processed_dir.mkdir(parents=True, exist_ok=True)

    # Resolve vault path
    vault_path = args.vault
    if not vault_path:
        tools_md = Path(".octobots/persona/TOOLS.md")
        if tools_md.exists():
            for line in tools_md.read_text(encoding="utf-8").splitlines():
                if "Vault path:" in line:
                    vault_path = line.split(":", 1)[1].strip()
                    break
    if not vault_path:
        vault_path = "~/Notes"
    vault = Path(vault_path).expanduser().resolve()
    vault.mkdir(parents=True, exist_ok=True)

    # Logging
    log_path = Path(args.log) if args.log else Path(".octobots/watchdog.log")
    setup_logging(log_path)

    logging.info("Inbox: %s", inbox)
    logging.info("Vault: %s", vault)
    logging.info("Role: %s", args.role)

    if args.once:
        count = scan_once(inbox, vault, args.role, processed_dir)
        logging.info("Processed %d files", count)
        return

    # Process any existing files first
    scan_once(inbox, vault, args.role, processed_dir)

    # Start watching
    try:
        watch_with_watchdog(inbox, vault, args.role, processed_dir)
    except ImportError:
        logging.warning("watchdog library not available, falling back to polling")
        watch_with_polling(inbox, vault, args.role, processed_dir)


if __name__ == "__main__":
    main()
