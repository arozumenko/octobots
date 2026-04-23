"""Octobots Firebase bridge entry point.

Reads env vars, instantiates Bridge with defaults, runs it. App-specific
adapters can subclass Bridge or provide custom payload_builder / result_enricher
callables. The generic version ships here so the bridge works standalone for
smoke tests without any app-specific integration.

Usage:
    python octobots/scripts/firebase_bridge.py [--dry-run] [--once] [--log-level LEVEL]

Environment variables:
    FIREBASE_SERVICE_ACCOUNT_PATH  — path to service account JSON (required)
    FIREBASE_STORAGE_BUCKET        — Cloud Storage bucket name
                                     (default: {project_id}.firebasestorage.app)
    FIREBASE_PROJECT_ID            — Firebase project ID
                                     (inferred from SA JSON when empty)
    WORKER_ID                      — unique worker identifier
                                     (default: dev-<hostname>)
    WORKER_CAPABILITIES            — comma-separated capability list
                                     (default: vision,claude-code)
    BRIDGE_JOBS_COLLECTION         — Firestore collection name (default: jobs)
    BRIDGE_TASKBOX_RECIPIENT       — Taskbox recipient role (default: vision-analyst)
    BRIDGE_TASKBOX_SENDER          — Taskbox sender identity (default: firebase-bridge)
    BRIDGE_MCP_RESULTS_DIR         — directory for MCP result files
                                     (default: /tmp/octobots-mcp-results)
    BRIDGE_MCP_IMAGES_DIR          — directory for MCP image stash
                                     (default: /tmp/octobots-images)
    BRIDGE_MCP_JOBS_DIR            — directory for MCP job metadata
                                     (default: /tmp/octobots-jobs)
    OCTOBOTS_DB                    — path to Taskbox SQLite DB
                                     (default: <octobots_dir>/.octobots/relay.db)
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import logging
import os
import socket
import sys
from pathlib import Path
from typing import Any

# Allow running as a script from the repo root:
#   python octobots/scripts/firebase_bridge.py
# or as a module:
#   python -m octobots.scripts.firebase_bridge
_SCRIPTS_DIR = Path(__file__).parent
_OCTOBOTS_DIR = _SCRIPTS_DIR.parent
sys.path.insert(0, str(_OCTOBOTS_DIR))

from bridges.firebase.bridge import Bridge  # noqa: E402

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Relay script path
# ---------------------------------------------------------------------------

RELAY_SCRIPT = _OCTOBOTS_DIR / "skills" / "taskbox" / "scripts" / "relay.py"

# ---------------------------------------------------------------------------
# Dotenv support (optional)
# ---------------------------------------------------------------------------


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv  # noqa: PLC0415
        load_dotenv()
    except ImportError:
        pass


# ---------------------------------------------------------------------------
# Default payload builder
# ---------------------------------------------------------------------------


def _default_payload_builder(
    job_id: str,
    claimed_doc: dict[str, Any],
    local_image_path: Path,
) -> dict[str, Any]:
    """Generic default payload: embeds jobId + imageStoragePath + kind.

    App adapters should replace this with a function that provides the
    agent with everything it needs to process the job.
    """
    from datetime import timezone  # noqa: PLC0415
    return {
        "jobId": job_id,
        "imageStoragePath": claimed_doc.get("imageStoragePath", ""),
        "kind": claimed_doc.get("kind", "generic-job"),
        "submittedAt": datetime.datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def _load_config() -> dict[str, Any]:
    _load_dotenv()

    sa_path = os.environ.get("FIREBASE_SERVICE_ACCOUNT_PATH", "")
    project_id = os.environ.get("FIREBASE_PROJECT_ID", "")

    if not project_id and sa_path and Path(sa_path).is_file():
        try:
            with open(sa_path) as f:
                sa_json = json.load(f)
            project_id = sa_json.get("project_id", "")
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not read project_id from SA JSON: %s", exc)

    worker_id = os.environ.get("WORKER_ID") or f"dev-{socket.gethostname()}"
    raw_caps = os.environ.get("WORKER_CAPABILITIES", "vision,claude-code")
    worker_capabilities = [c.strip() for c in raw_caps.split(",") if c.strip()]

    octobots_db = os.environ.get(
        "OCTOBOTS_DB",
        str(_OCTOBOTS_DIR / ".octobots" / "relay.db"),
    )

    return {
        "sa_path": sa_path,
        "storage_bucket": os.environ.get("FIREBASE_STORAGE_BUCKET", ""),
        "firebase_project_id": project_id,
        "jobs_collection": os.environ.get("BRIDGE_JOBS_COLLECTION", "jobs"),
        "worker_id": worker_id,
        "worker_capabilities": worker_capabilities,
        "octobots_db": octobots_db,
        "taskbox_recipient": os.environ.get("BRIDGE_TASKBOX_RECIPIENT", "vision-analyst"),
        "taskbox_sender": os.environ.get("BRIDGE_TASKBOX_SENDER", "firebase-bridge"),
        "mcp_results_dir": os.environ.get("BRIDGE_MCP_RESULTS_DIR", "/tmp/octobots-mcp-results"),
        "mcp_images_dir": os.environ.get("BRIDGE_MCP_IMAGES_DIR", "/tmp/octobots-images"),
        "mcp_jobs_dir": os.environ.get("BRIDGE_MCP_JOBS_DIR", "/tmp/octobots-jobs"),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="octobots-firebase-bridge",
        description="Octobots Firebase bridge — generic Firestore → Taskbox transport",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Subscribe and log pending jobs WITHOUT claiming them",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process one job then exit — useful for smoke tests",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO)",
    )
    return parser.parse_args(argv)


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        level=getattr(logging, level.upper(), logging.INFO),
    )


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    _configure_logging(args.log_level)

    cfg = _load_config()

    bridge = Bridge(
        service_account_path=cfg["sa_path"],
        storage_bucket=cfg["storage_bucket"],
        firebase_project_id=cfg["firebase_project_id"],
        jobs_collection=cfg["jobs_collection"],
        worker_id=cfg["worker_id"],
        worker_capabilities=cfg["worker_capabilities"],
        octobots_db=cfg["octobots_db"],
        relay_script=RELAY_SCRIPT,
        taskbox_sender=cfg["taskbox_sender"],
        taskbox_recipient=cfg["taskbox_recipient"],
        mcp_results_dir=cfg["mcp_results_dir"],
        mcp_images_dir=cfg["mcp_images_dir"],
        mcp_jobs_dir=cfg["mcp_jobs_dir"],
        payload_builder=_default_payload_builder,
        # No result_enricher by default — identity pass-through in Bridge
    )

    asyncio.run(bridge.run(dry_run=args.dry_run, once=args.once))


if __name__ == "__main__":
    main()
