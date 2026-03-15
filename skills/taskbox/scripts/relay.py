#!/usr/bin/env python3
"""Octobots message relay — SQLite queue for inter-instance communication.

No MCP, no servers — just a CLI that Claude Code calls via Bash.

Usage:
    python relay.py send   --from alpha --to beta "message body"
    python relay.py inbox  --id beta [--limit 5]
    python relay.py claim  --id beta MSG_ID
    python relay.py ack    MSG_ID ["optional response"]
    python relay.py responses --id alpha [--limit 10]
    python relay.py stats
    python relay.py peers
    python relay.py init               # create DB if not exists
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import uuid
from pathlib import Path

DB_PATH = os.environ.get(
    "OCTOBOTS_DB",
    os.path.join(os.path.dirname(__file__), "..", "relay.db"),
)


def _db() -> sqlite3.Connection:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id          TEXT PRIMARY KEY,
            sender      TEXT NOT NULL,
            recipient   TEXT NOT NULL,
            content     TEXT NOT NULL,
            response    TEXT DEFAULT '',
            status      TEXT NOT NULL DEFAULT 'pending',
            created_at  REAL NOT NULL,
            updated_at  REAL NOT NULL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_inbox
        ON messages(recipient, status)
    """)
    conn.commit()
    return conn


def cmd_init(_args: argparse.Namespace) -> None:
    _db().close()
    print(json.dumps({"ok": True, "db": str(Path(DB_PATH).resolve())}))


def cmd_send(args: argparse.Namespace) -> None:
    msg_id = uuid.uuid4().hex[:12]
    now = time.time()
    conn = _db()
    conn.execute(
        "INSERT INTO messages (id, sender, recipient, content, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 'pending', ?, ?)",
        (msg_id, args.sender, args.to, args.message, now, now),
    )
    conn.commit()
    conn.close()
    print(json.dumps({"id": msg_id, "status": "sent", "to": args.to}))


def cmd_inbox(args: argparse.Namespace) -> None:
    conn = _db()
    rows = conn.execute(
        "SELECT id, sender, content, created_at FROM messages "
        "WHERE recipient = ? AND status = 'pending' "
        "ORDER BY created_at ASC LIMIT ?",
        (args.id, args.limit),
    ).fetchall()
    conn.close()
    print(json.dumps([dict(r) for r in rows], indent=2))


def cmd_claim(args: argparse.Namespace) -> None:
    conn = _db()
    cur = conn.execute(
        "UPDATE messages SET status = 'processing', updated_at = ? "
        "WHERE id = ? AND status = 'pending'",
        (time.time(), args.msg_id),
    )
    conn.commit()
    if cur.rowcount == 0:
        conn.close()
        print(json.dumps({"error": "not found or already claimed"}))
        sys.exit(1)
    row = conn.execute(
        "SELECT id, sender, content, created_at FROM messages WHERE id = ?",
        (args.msg_id,),
    ).fetchone()
    conn.close()
    print(json.dumps(dict(row)))


def cmd_ack(args: argparse.Namespace) -> None:
    conn = _db()
    conn.execute(
        "UPDATE messages SET status = 'done', response = ?, updated_at = ? WHERE id = ?",
        (args.response or "", time.time(), args.msg_id),
    )
    conn.commit()
    conn.close()
    print(json.dumps({"id": args.msg_id, "status": "done"}))


def cmd_responses(args: argparse.Namespace) -> None:
    conn = _db()
    rows = conn.execute(
        "SELECT id, recipient, content, response, updated_at FROM messages "
        "WHERE sender = ? AND status = 'done' AND response != '' "
        "ORDER BY updated_at DESC LIMIT ?",
        (args.id, args.limit),
    ).fetchall()
    conn.close()
    print(json.dumps([dict(r) for r in rows], indent=2))


def cmd_stats(_args: argparse.Namespace) -> None:
    conn = _db()
    rows = conn.execute(
        "SELECT recipient, status, COUNT(*) as count FROM messages "
        "GROUP BY recipient, status ORDER BY recipient"
    ).fetchall()
    conn.close()
    stats: dict[str, dict[str, int]] = {}
    for r in rows:
        stats.setdefault(r["recipient"], {})[r["status"]] = r["count"]
    print(json.dumps(stats, indent=2))


def cmd_peers(_args: argparse.Namespace) -> None:
    conn = _db()
    rows = conn.execute(
        "SELECT DISTINCT sender AS peer FROM messages "
        "UNION SELECT DISTINCT recipient FROM messages"
    ).fetchall()
    conn.close()
    print(json.dumps([r["peer"] for r in rows]))


def main() -> None:
    p = argparse.ArgumentParser(prog="relay", description="Octobots message relay")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="Initialize DB")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("send", help="Send a message")
    s.add_argument("--from", dest="sender", required=True)
    s.add_argument("--to", required=True)
    s.add_argument("message")
    s.set_defaults(func=cmd_send)

    s = sub.add_parser("inbox", help="Check inbox")
    s.add_argument("--id", required=True, help="Your instance ID")
    s.add_argument("--limit", type=int, default=5)
    s.set_defaults(func=cmd_inbox)

    s = sub.add_parser("claim", help="Claim a message")
    s.add_argument("--id", required=True, help="Your instance ID")
    s.add_argument("msg_id")
    s.set_defaults(func=cmd_claim)

    s = sub.add_parser("ack", help="Acknowledge + respond")
    s.add_argument("msg_id")
    s.add_argument("response", nargs="?", default="")
    s.set_defaults(func=cmd_ack)

    s = sub.add_parser("responses", help="Check responses to your sent messages")
    s.add_argument("--id", required=True, help="Your instance ID")
    s.add_argument("--limit", type=int, default=10)
    s.set_defaults(func=cmd_responses)

    s = sub.add_parser("stats", help="Queue statistics")
    s.set_defaults(func=cmd_stats)

    s = sub.add_parser("peers", help="List known peers")
    s.set_defaults(func=cmd_peers)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
