#!/usr/bin/env python3
"""Generate a GitHub App installation token for octobots.

GitHub Apps authenticate in two steps:
1. Create a JWT signed with the app's private key
2. Exchange the JWT for a short-lived installation token (1 hour)

The installation token is used like a PAT — set GH_TOKEN and gh CLI uses it.

Usage:
  # Print token to stdout
  python octobots/scripts/gh-token.py

  # Use with gh CLI
  export GH_TOKEN=$(python octobots/scripts/gh-token.py)
  gh issue comment 42 --body "Hello from octobots"

Environment (.env.octobots):
  OCTOBOTS_GH_APP_ID              — GitHub App ID
  OCTOBOTS_GH_APP_PRIVATE_KEY_PATH — Path to .pem file
  OCTOBOTS_GH_INSTALLATION_ID     — Installation ID
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# ── Load .env.octobots ──────────────────────────────────────────────────────
def _load_env() -> None:
    for env_path in [Path.cwd() / ".env.octobots", Path(__file__).parent.parent / ".env.octobots"]:
        if env_path.is_file():
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip().strip("\"'")
                if key and key not in os.environ:
                    os.environ[key] = value

_load_env()

APP_ID = os.environ.get("OCTOBOTS_GH_APP_ID", "")
PRIVATE_KEY_PATH = os.environ.get("OCTOBOTS_GH_APP_PRIVATE_KEY_PATH", "")
INSTALLATION_ID = os.environ.get("OCTOBOTS_GH_INSTALLATION_ID", "")

# ── Token cache ─────────────────────────────────────────────────────────────
_CACHE_PATH = Path.cwd() / ".octobots" / ".gh-token-cache"
_TOKEN_TTL = 3500  # refresh 100s before expiry (tokens last 3600s)


def _cached_token() -> str | None:
    if _CACHE_PATH.is_file():
        try:
            data = json.loads(_CACHE_PATH.read_text())
            if data.get("expires_at", 0) > time.time():
                return data["token"]
        except (json.JSONDecodeError, KeyError):
            pass
    return None


def _save_token(token: str, expires_at: float) -> None:
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_PATH.write_text(json.dumps({"token": token, "expires_at": expires_at}))


# ── JWT creation (no external deps) ────────────────────────────────────────
def _create_jwt() -> str:
    """Create a JWT signed with RS256 using the app's private key."""
    import base64
    import hashlib
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    now = int(time.time())
    payload = {
        "iat": now - 60,  # issued at (60s in the past for clock drift)
        "exp": now + 600,  # expires in 10 minutes
        "iss": APP_ID,
    }

    # Header
    header = base64.urlsafe_b64encode(
        json.dumps({"alg": "RS256", "typ": "JWT"}).encode()
    ).rstrip(b"=")

    # Payload
    body = base64.urlsafe_b64encode(
        json.dumps(payload).encode()
    ).rstrip(b"=")

    # Sign
    message = header + b"." + body
    private_key = serialization.load_pem_private_key(
        Path(PRIVATE_KEY_PATH).read_bytes(), password=None
    )
    signature = private_key.sign(message, padding.PKCS1v15(), hashes.SHA256())
    sig_b64 = base64.urlsafe_b64encode(signature).rstrip(b"=")

    return (message + b"." + sig_b64).decode()


# ── Get installation token ──────────────────────────────────────────────────
def get_token() -> str:
    """Get a valid installation token (cached if possible)."""
    cached = _cached_token()
    if cached:
        return cached

    if not all([APP_ID, PRIVATE_KEY_PATH, INSTALLATION_ID]):
        print("Error: OCTOBOTS_GH_APP_ID, OCTOBOTS_GH_APP_PRIVATE_KEY_PATH, "
              "and OCTOBOTS_GH_INSTALLATION_ID must be set", file=sys.stderr)
        sys.exit(1)

    if not Path(PRIVATE_KEY_PATH).is_file():
        print(f"Error: Private key not found at {PRIVATE_KEY_PATH}", file=sys.stderr)
        sys.exit(1)

    import urllib.request

    jwt = _create_jwt()

    req = urllib.request.Request(
        f"https://api.github.com/app/installations/{INSTALLATION_ID}/access_tokens",
        method="POST",
        headers={
            "Authorization": f"Bearer {jwt}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )

    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
            token = data["token"]
            # Cache it
            _save_token(token, time.time() + _TOKEN_TTL)
            return token
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"Error: GitHub API {e.code}: {body}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    print(get_token())
