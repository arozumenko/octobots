#!/usr/bin/env python3
"""Octobots notify MCP server (stdio).

Exposes a single `notify` tool that delivers messages and files to the user
via Telegram. Each capability uses structured (typed) args, so even small
local models reliably emit valid tool calls — no fragile shell-quoting
through Bash, no payloads embedded in command strings.

Tool: `notify(message, file?, from_role?)`
  - No file → sendMessage (auto-promotes to .md document if message > 4000 chars).
  - With file → routes to sendPhoto / sendVoice / sendAudio / sendDocument
    by extension; `message` is used as the caption.

All transport logic lives in `octobots/scripts/notify_lib.py`, which is also
imported by `supervisor.py` for internal "stuck role" warnings — single
source of truth for Telegram delivery.

Register in .mcp.json:

  {
    "mcpServers": {
      "notify": {
        "command": "python3",
        "args": ["octobots/mcp/notify/server.py"]
      }
    }
  }
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

# Make the shared notify_lib importable regardless of cwd
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent.parent / "scripts"))

from notify_lib import send_notification  # noqa: E402

from mcp.server.fastmcp import FastMCP  # noqa: E402

mcp = FastMCP("octobots-notify")


@mcp.tool()
def notify(
    message: str,
    file: Optional[str] = None,
    from_role: Optional[str] = None,
    pre_formatted: bool = True,
) -> dict:
    """Send a notification to the user via Telegram.

    Args:
        message: The text body. Always required. Used as the message body
            when no file is attached, or as the caption when one is.
            HTML tags are supported: <b>bold</b>, <i>italic</i>,
            <code>monospace</code>, <a href="...">link</a>.
        file: Optional path to a file on disk. When provided, the file is
            uploaded and the transport is chosen automatically from its
            extension:
              - .jpg/.jpeg/.png/.webp/.gif → photo (may be compressed)
              - .ogg/.oga/.opus            → voice note (voice-bubble UI)
              - .mp3/.m4a/.aac/.flac/.wav  → audio player
              - anything else              → document (raw bytes preserved)
        from_role: Optional role badge override (defaults to $OCTOBOTS_ID).
        pre_formatted: If True (default), treat message as already-valid HTML
            and send as-is. Set to False to auto-escape raw plain text that
            contains < > & characters not intended as HTML tags.

    Behavior:
        - No file, message ≤ 4000 chars → sendMessage (HTML-formatted).
        - No file, message > 4000 chars → message is written to a temp .md
          file and sent as a document with a short preview as the caption.
        - With file → message is sent as the caption alongside the upload.
    """
    return send_notification(
        message=message,
        file=file,
        from_role=from_role,
        pre_formatted=pre_formatted,
    )


if __name__ == "__main__":
    mcp.run()
