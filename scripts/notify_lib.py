"""Octobots Telegram notification transport (single source of truth).

Used by:
  - supervisor/mcp/notify/server.py (the `notify` MCP tool exposed to roles)
  - supervisor/scripts/supervisor.py (internal "stuck role" warnings)
  - any future Python caller that needs to message the user

Pure stdlib (urllib + mimetypes), no third-party deps. Reads
OCTOBOTS_TG_TOKEN / OCTOBOTS_TG_OWNER from .env.octobots fresh on every call,
so credential edits take effect without restart.
"""

from __future__ import annotations

import html
import json
import mimetypes
import os
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Optional
from urllib import request as urlrequest

TG_API = "https://api.telegram.org/bot{token}/{method}"
TEXT_LIMIT = 4000  # Telegram hard limit is 4096; leave headroom for the role badge

PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
VOICE_EXTS = {".ogg", ".oga", ".opus"}
AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".flac", ".wav"}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _project_root() -> Optional[Path]:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            stderr=subprocess.DEVNULL,
        )
        return Path(out.decode().strip())
    except Exception:
        return None


def _load_env() -> None:
    here = Path(__file__).resolve().parent
    candidates = [
        (_project_root() / ".env.octobots") if _project_root() else None,
        Path.cwd() / ".env.octobots",
        here.parent / ".env.octobots",
        here.parent.parent / ".env.octobots",
    ]
    for path in candidates:
        if path and path.is_file():
            for raw in path.read_text().splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
            return


def credentials() -> Optional[tuple[str, str]]:
    _load_env()
    token = os.environ.get("OCTOBOTS_TG_TOKEN", "").strip()
    chat = os.environ.get("OCTOBOTS_TG_OWNER", "").strip()
    if not token or not chat:
        return None
    return token, chat


def _from_role(explicit: Optional[str]) -> str:
    return explicit or os.environ.get("OCTOBOTS_ID") or "unknown"


# ---------------------------------------------------------------------------
# Telegram transport
# ---------------------------------------------------------------------------

def _post_json(token: str, method: str, payload: dict) -> dict:
    url = TG_API.format(token=token, method=method)
    body = json.dumps(payload).encode("utf-8")
    req = urlrequest.Request(
        url, data=body, headers={"Content-Type": "application/json"}
    )
    with urlrequest.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _post_multipart(
    token: str, method: str, fields: dict, file_field: str, file_path: Path
) -> dict:
    boundary = f"----octobots{uuid.uuid4().hex}"
    crlf = b"\r\n"
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(f"--{boundary}".encode())
        parts.append(
            f'Content-Disposition: form-data; name="{name}"'.encode()
        )
        parts.append(b"")
        parts.append(str(value).encode("utf-8"))
    mime, _ = mimetypes.guess_type(file_path.name)
    mime = mime or "application/octet-stream"
    parts.append(f"--{boundary}".encode())
    parts.append(
        f'Content-Disposition: form-data; name="{file_field}"; '
        f'filename="{file_path.name}"'.encode()
    )
    parts.append(f"Content-Type: {mime}".encode())
    parts.append(b"")
    parts.append(file_path.read_bytes())
    parts.append(f"--{boundary}--".encode())
    parts.append(b"")
    body = crlf.join(parts)

    url = TG_API.format(token=token, method=method)
    req = urlrequest.Request(
        url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urlrequest.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _route_for(file_path: Path) -> tuple[str, str]:
    """(method, multipart_field) for a file based on its extension."""
    ext = file_path.suffix.lower()
    if ext in PHOTO_EXTS:
        return "sendPhoto", "photo"
    if ext in VOICE_EXTS:
        return "sendVoice", "voice"
    if ext in AUDIO_EXTS:
        return "sendAudio", "audio"
    return "sendDocument", "document"


def _ok(response: dict) -> dict:
    if response.get("ok"):
        return {"status": "sent"}
    return {"status": "error", "telegram": response}


def _preview(text: str, role: str) -> str:
    head = " ".join(text.splitlines()[:5])[:150]
    return f"[{role}] {head}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def send_notification(
    message: str,
    file: Optional[str] = None,
    from_role: Optional[str] = None,
) -> dict:
    """Send a Telegram notification to the user.

    Args:
        message: The text body. Used as the message body when no file is
            attached, or as the caption when one is.
        file: Optional path to a file on disk. When provided, the file is
            uploaded and the transport is chosen automatically from its
            extension (photo / voice / audio / document).
        from_role: Optional role badge override (defaults to $OCTOBOTS_ID).

    Returns:
        dict with at least a "status" key: "sent", "skipped", or "error".
    """
    creds = credentials()
    if not creds:
        return {"status": "skipped", "reason": "Telegram not configured"}
    token, chat = creds
    role = _from_role(from_role)

    # File attachment path
    if file:
        file_path = Path(file).expanduser()
        if not file_path.is_file():
            return {"status": "error", "reason": f"file not found: {file_path}"}
        method, field = _route_for(file_path)
        caption = (
            f"[{role}] {message}" if message else f"[{role}] {file_path.name}"
        )
        return _ok(
            _post_multipart(
                token, method, {"chat_id": chat, "caption": caption}, field, file_path
            )
        )

    # Plain text path — build full HTML first, then check length
    safe_role = html.escape(role)
    safe_msg = html.escape(message)
    text = f"<b>[{safe_role}]</b> {safe_msg}"
    if len(text) <= TEXT_LIMIT:
        payload = {
            "chat_id": chat,
            "parse_mode": "HTML",
            "text": text,
        }
        return _ok(_post_json(token, "sendMessage", payload))

    # Oversized text → stage as .md and upload as document
    name = f"{role}-{time.strftime('%H%M%S')}.md"
    tmp_fd, tmp_name = tempfile.mkstemp(prefix="octobots-notify-", suffix=".md")
    os.close(tmp_fd)
    tmp = Path(tmp_name)
    target = tmp.with_name(name)
    try:
        tmp.write_text(message, encoding="utf-8")
        tmp.replace(target)
        response = _post_multipart(
            token,
            "sendDocument",
            {"chat_id": chat, "caption": _preview(message, role) + "..."},
            "document",
            target,
        )
        return _ok(response)
    finally:
        for p in (tmp, target):
            try:
                p.unlink()
            except FileNotFoundError:
                pass
