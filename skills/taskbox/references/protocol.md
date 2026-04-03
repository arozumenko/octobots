# Taskbox Protocol Reference

## Message Lifecycle

```
pending ──→ processing ──→ done
   ↑              │               │
   │  reset       │  claim        │  ack (response attached)
   │  (Stop hook) │  (atomic)     │
   └──────────────┘               └── visible via `responses`
```

- **pending** — waiting in recipient's inbox
- **processing** — claimed by one consumer, work in progress
- **done** — acknowledged, response available to sender

If a worker crashes or resets mid-task (e.g. `/clear`, compaction), its `processing` message is automatically returned to `pending` by the Stop hook.

## CLI Commands

All commands output JSON. The script is at `scripts/relay.py` relative to this skill.

| Command | Description |
|---------|-------------|
| `init` | Create/verify the SQLite database |
| `send --from ID --to ID "msg"` | Queue a message for another instance |
| `inbox --id ID [--limit N]` | List pending messages (oldest first) |
| `claim --id ID MSG_ID` | Atomically lock a message for processing |
| `ack MSG_ID ["response"]` | Mark done, attach optional response |
| `responses --id ID [--limit N]` | Check responses to messages you sent |
| `reset --id ID` | Return this worker's `processing` messages to `pending` (used by Stop hook) |
| `stats` | Queue counts grouped by recipient and status |
| `peers` | List all known instance IDs |

## Architecture

- **Storage**: Single SQLite file with WAL mode (safe for concurrent access)
- **Location**: `octobots/relay.db` (override with `OCTOBOTS_DB` env var)
- **Atomicity**: `claim` uses `UPDATE ... WHERE status='pending'` — only one consumer wins
- **No dependencies**: Python stdlib only (sqlite3, argparse, json, uuid)
- **No daemon**: No background process — each CLI invocation opens/closes the DB

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `OCTOBOTS_ID` | _(ask user)_ | This instance's identifier |
| `OCTOBOTS_DB` | `octobots/relay.db` | SQLite database path |

## Multi-Instance Setup

Each Claude Code process needs a unique instance ID. Two approaches:

**Env var per terminal:**
```bash
OCTOBOTS_ID=alpha claude    # Terminal 1
OCTOBOTS_ID=beta claude     # Terminal 2
```

**Separate project directories** (if env vars aren't practical):
```bash
mkdir /tmp/alpha && cd /tmp/alpha && OCTOBOTS_ID=alpha claude
mkdir /tmp/beta  && cd /tmp/beta  && OCTOBOTS_ID=beta claude
```

Both share the same SQLite DB file — WAL mode handles concurrent reads/writes safely.
