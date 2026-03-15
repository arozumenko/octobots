---
name: taskbox
description: Send and receive messages between Claude Code instances using a shared SQLite queue. Use when the user says "send to <instance>", "check inbox", "start listening", "check responses", or anything about inter-instance communication.
license: Apache-2.0
compatibility: Requires Python 3.10+ (stdlib only, no pip dependencies)
metadata:
  author: octobots
  version: "0.1.0"
---

# Taskbox

Message queue for Claude Code inter-instance communication. No MCP, no servers — just SQLite and Bash.

## Quick Start

Resolve your instance ID from env `OCTOBOTS_ID` or ask the user to pick one (e.g. "alpha", "beta").

The relay script is at `octobots/skills/taskbox/scripts/relay.py`. All output is JSON.

```bash
# Initialize (first time)
python octobots/skills/taskbox/scripts/relay.py init

# Send a message
python octobots/skills/taskbox/scripts/relay.py send --from MY_ID --to PEER "message"

# Check inbox
python octobots/skills/taskbox/scripts/relay.py inbox --id MY_ID

# Claim → process → ack
python octobots/skills/taskbox/scripts/relay.py claim --id MY_ID MSG_ID
# ... do the work ...
python octobots/skills/taskbox/scripts/relay.py ack MSG_ID "response summary"

# Check responses to messages you sent
python octobots/skills/taskbox/scripts/relay.py responses --id MY_ID
```

## Sending

Summarize the user's intent into a clear, self-contained message — the recipient has no access to your conversation. Report the returned message ID for tracking.

## Receiving

To listen for incoming tasks, use `/loop 10s` to poll the inbox. For each message:

1. **Check** inbox — if empty, skip
2. **Claim** the oldest message (atomic lock prevents double-processing)
3. **Process** it using all available tools — this is blocking
4. **Ack** with a concise response

Process one message at a time. New messages queue as `pending` while you work.

## Details

See `references/protocol.md` for message lifecycle, queue commands, and architecture.
