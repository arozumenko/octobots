"""bridges/firebase/bridge.py — Generic Firestore-to-Taskbox transport.

Claims jobs from a Firestore collection, downloads a referenced image from
Cloud Storage, stashes it at a predictable path for a co-located MCP server,
enqueues a Taskbox row for an agent, polls for an MCP result file, runs an
optional app-specific enrichment, and writes the final result back to Firestore.

Firebase packages (firebase-admin, google-cloud-firestore, google-cloud-storage)
are lazy-imported so that octobots imports cleanly when they are not installed.
Install separately if using this bridge:
    pip install firebase-admin>=6.5.0 google-cloud-firestore>=2.16.0 \
                google-cloud-storage>=2.17.0 httpx>=0.27.0

Usage:
    from octobots.bridges.firebase import Bridge

    bridge = Bridge(
        service_account_path="/path/to/sa.json",
        worker_id="my-worker",
        worker_capabilities=["vision"],
        octobots_db="/path/to/.octobots/relay.db",
        relay_script="/path/to/relay.py",
        taskbox_recipient="vision-analyst",
        mcp_results_dir="/tmp/app-mcp-results",
        mcp_images_dir="/tmp/app-images",
        mcp_jobs_dir="/tmp/app-jobs",
        payload_builder=my_payload_builder,
    )
    asyncio.run(bridge.run())
"""
from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
import signal
import socket
import subprocess
import sys
import tempfile
from datetime import timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Hook type aliases
# ---------------------------------------------------------------------------

PayloadBuilder = Callable[[str, dict[str, Any], Path], dict[str, Any]]
ResultEnricher = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


# ---------------------------------------------------------------------------
# Dependency loading
# ---------------------------------------------------------------------------


def _load_firebase() -> tuple[Any, Any, Any, Any]:
    """Return (firebase_admin, credentials, firestore, storage) modules."""
    import firebase_admin  # noqa: PLC0415
    from firebase_admin import credentials, firestore, storage  # noqa: PLC0415
    return firebase_admin, credentials, firestore, storage


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv  # noqa: PLC0415
        load_dotenv()
    except ImportError:
        pass  # python-dotenv optional


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------


def _log_task_exception(task: asyncio.Task, job_id: str) -> None:
    """Log an error if *task* raised an exception (not cancelled)."""
    if not task.cancelled() and task.exception() is not None:
        logger.error(
            "process_job raised for jobId=%s: %r",
            job_id, task.exception(), exc_info=task.exception(),
        )


# ---------------------------------------------------------------------------
# Internal state container
# ---------------------------------------------------------------------------


class _BridgeState:
    """Mutable state shared between the listener callback and the async loop."""

    def __init__(self) -> None:
        self.pending_jobs: asyncio.Queue[Any] = asyncio.Queue()
        self.in_flight: set[asyncio.Task[None]] = set()
        self.shutdown_event: asyncio.Event = asyncio.Event()
        self.listener_unsubscribe: Any = None


# ---------------------------------------------------------------------------
# Firestore claim transaction (pure function for testability)
# ---------------------------------------------------------------------------


def claim_job(
    transaction: Any,
    job_ref: Any,
    worker_id: str,
    worker_capabilities: list[str],
    firestore_module: Any,
) -> dict[str, Any] | None:
    """Claim a pending job via Firestore transaction.

    Returns the job document dict if claimed, or None if the job could not
    be claimed (wrong status, missing capabilities, or not found).
    """
    snapshot = job_ref.get(transaction=transaction)
    if not snapshot.exists:
        return None
    doc = snapshot.to_dict()
    if doc.get("status") != "pending":
        return None
    required = doc.get("requires", [])
    if not all(cap in worker_capabilities for cap in required):
        return None
    transaction.update(job_ref, {
        "status": "claimed",
        "workerId": worker_id,
        "workerCapabilities": worker_capabilities,
        "claimedAt": firestore_module.SERVER_TIMESTAMP,
        "updatedAt": firestore_module.SERVER_TIMESTAMP,
    })
    return doc


# ---------------------------------------------------------------------------
# Heartbeat loop
# ---------------------------------------------------------------------------


async def _heartbeat_loop(
    job_ref: Any,
    job_id: str,
    interval_sec: int,
    firestore_module: Any,
) -> None:
    """Periodically update heartbeatAt until cancelled."""
    while True:
        await asyncio.sleep(interval_sec)
        try:
            job_ref.update({
                "heartbeatAt": firestore_module.SERVER_TIMESTAMP,
                "updatedAt": firestore_module.SERVER_TIMESTAMP,
            })
            logger.debug("Heartbeat for jobId=%s", job_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Heartbeat update failed for jobId=%s: %s", job_id, exc)


# ---------------------------------------------------------------------------
# Graceful shutdown helper
# ---------------------------------------------------------------------------


async def _release_job(job_ref: Any, job_id: str, firestore_module: Any) -> None:
    """Release a claimed/running job back to pending so another worker can pick it up."""
    try:
        job_ref.update({
            "status": "pending",
            "workerId": None,
            "workerCapabilities": None,
            "claimedAt": None,
            "heartbeatAt": None,
            "updatedAt": firestore_module.SERVER_TIMESTAMP,
        })
        logger.info("Released jobId=%s back to pending", job_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not release jobId=%s: %s", job_id, exc)


# ---------------------------------------------------------------------------
# Bridge class
# ---------------------------------------------------------------------------


class Bridge:
    """Firestore-to-Taskbox bridge.

    Claims jobs from a Firestore collection, downloads a referenced image from
    Cloud Storage, stashes it at a predictable path for a co-located MCP server,
    enqueues a Taskbox row for an agent, polls for an MCP result file, runs an
    optional app-specific enrichment, and writes the final result back to Firestore.

    Hook contracts
    --------------
    payload_builder(job_id, claimed_doc, local_image_path) -> dict
        Build the Taskbox content payload. The adapter decides what the agent sees.
        MUST include enough context for the agent to function. REQUIRED.

    result_enricher(raw_result) -> Awaitable[dict]
        Transform the MCP tool's result dict before it's written to Firestore.
        Default: identity pass-through (returns raw_result unchanged).
    """

    def __init__(
        self,
        *,
        # Firebase
        service_account_path: str,
        storage_bucket: str = "",
        firebase_project_id: str = "",
        # Job queue
        jobs_collection: str = "jobs",
        worker_id: str,
        worker_capabilities: list[str],
        # Taskbox
        octobots_db: str,
        relay_script: str | Path,
        taskbox_sender: str = "firebase-bridge",
        taskbox_recipient: str,
        # MCP IPC
        mcp_results_dir: str | Path,
        mcp_images_dir: str | Path,
        mcp_jobs_dir: str | Path,
        image_mime_type: str = "image/jpeg",
        # Hooks
        payload_builder: PayloadBuilder,
        result_enricher: ResultEnricher | None = None,
        # Tuning
        heartbeat_interval_sec: int | None = None,
        stale_threshold_sec: int | None = None,
        mcp_result_timeout_sec: int = 300,
    ) -> None:
        self._sa_path = service_account_path
        self._storage_bucket = storage_bucket
        self._firebase_project_id = firebase_project_id
        self._jobs_collection = jobs_collection
        self._worker_id = worker_id
        self._worker_capabilities = worker_capabilities
        self._octobots_db = octobots_db
        self._relay_script = Path(relay_script)
        self._taskbox_sender = taskbox_sender
        self._taskbox_recipient = taskbox_recipient
        self._mcp_results_dir = Path(mcp_results_dir)
        self._mcp_images_dir = Path(mcp_images_dir)
        self._mcp_jobs_dir = Path(mcp_jobs_dir)
        self._image_mime_type = image_mime_type
        self._payload_builder = payload_builder
        self._result_enricher = result_enricher
        self._heartbeat_interval_sec = heartbeat_interval_sec
        self._stale_threshold_sec = stale_threshold_sec
        self._mcp_result_timeout_sec = mcp_result_timeout_sec

    # ------------------------------------------------------------------
    # Relay / Taskbox helpers
    # ------------------------------------------------------------------

    def _relay(self, args: list[str]) -> dict[str, Any]:
        """Call relay.py with the given args and return parsed JSON output."""
        env = os.environ.copy()
        env["OCTOBOTS_DB"] = self._octobots_db
        result = subprocess.run(
            [sys.executable, str(self._relay_script), *args],
            capture_output=True,
            text=True,
            env=env,
            timeout=10,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"relay.py {args[0]} failed (exit {result.returncode}): {result.stderr.strip()}"
            )
        return json.loads(result.stdout)

    def _enqueue_taskbox(self, job_id: str, claimed_doc: dict[str, Any], local_image_path: Path) -> str:
        """Build payload via hook and insert a job into the Taskbox.

        Returns the Taskbox message ID.
        """
        # Ensure the DB is initialised.
        self._relay(["init"])

        payload_dict = self._payload_builder(job_id, claimed_doc, local_image_path)
        payload = json.dumps(payload_dict)

        response = self._relay([
            "send",
            "--from", self._taskbox_sender,
            "--to", self._taskbox_recipient,
            payload,
        ])
        msg_id: str = response["id"]
        logger.info(
            "Taskbox enqueued jobId=%s msgId=%s workerId=%s",
            job_id, msg_id, self._worker_id,
        )
        return msg_id

    # ------------------------------------------------------------------
    # MCP IPC stash
    # ------------------------------------------------------------------

    def _stash_job_for_mcp(
        self,
        job_id: str,
        msg_id: str,
        local_image_path: Path,
    ) -> None:
        """Copy the downloaded image and write job metadata for the MCP server.

        Writes two files atomically:
            mcp_images_dir/<job_id>.jpg  — the meal image bytes
            mcp_jobs_dir/<job_id>.json   — job metadata for MCP server lookup

        Non-throwing: if anything fails, logs ERROR and returns.
        """
        try:
            self._mcp_images_dir.mkdir(parents=True, exist_ok=True)
            self._mcp_jobs_dir.mkdir(parents=True, exist_ok=True)

            # Atomic copy of image file.
            image_dest = self._mcp_images_dir / f"{job_id}.jpg"
            image_tmp = self._mcp_images_dir / f"{job_id}.jpg.tmp"
            image_bytes = local_image_path.read_bytes()
            image_tmp.write_bytes(image_bytes)
            os.replace(str(image_tmp), str(image_dest))

            # Atomic write of metadata JSON.
            metadata = {
                "jobId": job_id,
                "msgId": msg_id,
                "imagePath": str(image_dest),
                "mimeType": self._image_mime_type,
                "octobotsDb": self._octobots_db,
                "createdAt": datetime.datetime.now(timezone.utc).isoformat(),
            }
            jobs_dest = self._mcp_jobs_dir / f"{job_id}.json"
            jobs_tmp = self._mcp_jobs_dir / f"{job_id}.json.tmp"
            with open(str(jobs_tmp), "w", encoding="utf-8") as f:
                json.dump(metadata, f)
            os.replace(str(jobs_tmp), str(jobs_dest))

            logger.info(
                "MCP IPC stash written for jobId=%s msgId=%s imagePath=%s",
                job_id, msg_id, image_dest,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Failed to stash MCP IPC files for jobId=%s: %s",
                job_id, exc,
            )

    # ------------------------------------------------------------------
    # MCP result polling
    # ------------------------------------------------------------------

    async def _wait_for_mcp_result(self, job_id: str) -> dict[str, Any]:
        """Wait for an MCP result to appear in mcp_results_dir/<jobId>.json.

        Raises:
            asyncio.TimeoutError: if no result arrives within mcp_result_timeout_sec.
        """
        result_path = self._mcp_results_dir / f"{job_id}.json"
        poll_interval = 1.0
        elapsed = 0.0
        timeout_sec = self._mcp_result_timeout_sec

        logger.debug("Waiting for MCP result at %s (timeout=%ds)", result_path, timeout_sec)

        while elapsed < timeout_sec:
            if result_path.exists():
                try:
                    with open(result_path) as f:
                        data = json.load(f)
                    result_path.unlink(missing_ok=True)
                    logger.info("MCP result received for jobId=%s", job_id)
                    return data
                except (OSError, json.JSONDecodeError) as exc:
                    logger.warning("Could not read MCP result file %s: %s", result_path, exc)
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        raise asyncio.TimeoutError(
            f"MCP result not received for jobId={job_id} within {timeout_sec}s"
        )

    # ------------------------------------------------------------------
    # Config helpers
    # ------------------------------------------------------------------

    async def _read_runner_config(self, db: Any) -> dict[str, Any]:
        """Read /config/jobRunner from Firestore; fall back to hardcoded defaults."""
        defaults: dict[str, Any] = {
            "heartbeatIntervalSec": self._heartbeat_interval_sec if self._heartbeat_interval_sec is not None else 15,
            "staleThresholdSec": self._stale_threshold_sec if self._stale_threshold_sec is not None else 60,
            "maxJobsPerWorkerPerHour": 100,
            "mcpTimeoutSec": self._mcp_result_timeout_sec,
        }
        try:
            snap = db.collection("config").document("jobRunner").get()
            if snap.exists:
                data = snap.to_dict() or {}
                # Constructor params take precedence over Firestore when explicitly set.
                if self._heartbeat_interval_sec is None:
                    defaults["heartbeatIntervalSec"] = data.get("heartbeatIntervalSec", defaults["heartbeatIntervalSec"])
                if self._stale_threshold_sec is None:
                    defaults["staleThresholdSec"] = data.get("staleThresholdSec", defaults["staleThresholdSec"])
                logger.info("Loaded runner config from Firestore: %s", data)
            else:
                logger.info("No /config/jobRunner doc found; using defaults")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not read /config/jobRunner: %s — using defaults", exc)
        return defaults

    # ------------------------------------------------------------------
    # Per-job processing
    # ------------------------------------------------------------------

    async def _process_job(
        self,
        doc: Any,
        db: Any,
        bucket: Any,
        runner_config: dict[str, Any],
        dry_run: bool,
        firestore_module: Any,
    ) -> None:
        """Claim and fully process one pending job document."""
        job_id: str = doc.id
        doc_data = doc.to_dict() or {}

        logger.info(
            "Attempting claim for jobId=%s userId=%s",
            job_id, doc_data.get("userId"),
        )

        if dry_run:
            logger.info("[DRY-RUN] Would claim jobId=%s — skipping", job_id)
            return

        # --- Claim ---
        job_ref = db.collection(self._jobs_collection).document(job_id)
        transaction = db.transaction()

        @firestore_module.transactional
        def _claim(txn: Any) -> dict[str, Any] | None:
            return claim_job(txn, job_ref, self._worker_id, self._worker_capabilities, firestore_module)

        claimed_doc = _claim(transaction)
        if claimed_doc is None:
            logger.info("Could not claim jobId=%s (already taken or capabilities mismatch)", job_id)
            return

        logger.info("Claimed jobId=%s workerId=%s", job_id, self._worker_id)

        # --- Running ---
        job_ref.update({
            "status": "running",
            "heartbeatAt": firestore_module.SERVER_TIMESTAMP,
            "updatedAt": firestore_module.SERVER_TIMESTAMP,
        })

        heartbeat_interval = runner_config.get("heartbeatIntervalSec", 15)
        heartbeat_task = asyncio.create_task(
            _heartbeat_loop(job_ref, job_id, heartbeat_interval, firestore_module)
        )

        tmp_dir: str | None = None
        image_path: Path | None = None

        try:
            # --- Download image ---
            storage_path: str = claimed_doc.get("imageStoragePath", "")
            tmp_dir = tempfile.mkdtemp()
            image_path = Path(tmp_dir) / f"{job_id}.jpg"

            logger.info("Downloading image jobId=%s storagePath=%s", job_id, storage_path)
            blob = bucket.blob(storage_path)
            blob.download_to_filename(str(image_path))
            logger.info("Image downloaded to %s", image_path)

            # --- Enqueue Taskbox ---
            taskbox_msg_id = self._enqueue_taskbox(job_id, claimed_doc, image_path)

            # --- Stash image + metadata for MCP server ---
            self._stash_job_for_mcp(
                job_id=job_id,
                msg_id=taskbox_msg_id,
                local_image_path=image_path,
            )

            # --- Wait for MCP result ---
            raw_result = await self._wait_for_mcp_result(job_id)

            # --- Optional enrichment ---
            if self._result_enricher is not None:
                enriched_result = await self._result_enricher(raw_result)
            else:
                enriched_result = raw_result

            # --- Write result ---
            job_ref.update({
                "status": "done",
                "result": enriched_result,
                "updatedAt": firestore_module.SERVER_TIMESTAMP,
            })
            logger.info("Job done jobId=%s", job_id)

        except asyncio.TimeoutError as exc:
            error_msg = f"MCP result timeout: {exc}"
            logger.error("Job error jobId=%s: %s", job_id, error_msg)
            job_ref.update({
                "status": "error",
                "errorMessage": error_msg,
                "errorAt": firestore_module.SERVER_TIMESTAMP,
                "updatedAt": firestore_module.SERVER_TIMESTAMP,
            })
        except Exception as exc:  # noqa: BLE001
            error_msg = str(exc)
            logger.exception("Job error jobId=%s: %s", job_id, error_msg)
            job_ref.update({
                "status": "error",
                "errorMessage": error_msg,
                "errorAt": firestore_module.SERVER_TIMESTAMP,
                "updatedAt": firestore_module.SERVER_TIMESTAMP,
            })
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            # NOTE: Taskbox row ack ownership belongs to the MCP server's result
            # submission tool. The bridge never acks — the MCP tool acks after
            # writing the result file.
            # Clean up temp image.
            if image_path:
                image_path.unlink(missing_ok=True)
            if tmp_dir:
                try:
                    Path(tmp_dir).rmdir()
                except OSError:
                    pass
            # Clean up MCP IPC stash files.
            (self._mcp_images_dir / f"{job_id}.jpg").unlink(missing_ok=True)
            (self._mcp_jobs_dir / f"{job_id}.json").unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # Public run loop
    # ------------------------------------------------------------------

    async def run(
        self,
        *,
        dry_run: bool = False,
        once: bool = False,
    ) -> None:
        """Start the listener and process jobs until SIGINT / SIGTERM.

        dry_run: listen but do NOT claim — log only.
        once:    process at most one job and exit.
        """
        from google.cloud.firestore_v1.base_query import FieldFilter  # noqa: PLC0415

        firebase_admin, credentials_mod, firestore_mod, storage_mod = _load_firebase()

        sa_path = self._sa_path
        if not sa_path or not Path(sa_path).is_file():
            raise RuntimeError(
                "service_account_path must point to a valid service account JSON file. "
                f"Got: {sa_path!r}"
            )

        # Infer project_id from SA JSON if not explicitly provided.
        project_id = self._firebase_project_id
        if not project_id:
            try:
                with open(sa_path) as f:
                    sa_json = json.load(f)
                project_id = sa_json.get("project_id", "")
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("Could not read project_id from service account JSON: %s", exc)

        cred = credentials_mod.Certificate(sa_path)
        bucket_name = self._storage_bucket or f"{project_id}.firebasestorage.app"
        logger.info("Using Cloud Storage bucket: %s", bucket_name)
        logger.info("Using Taskbox DB: %s", self._octobots_db)

        app = firebase_admin.initialize_app(cred, {"storageBucket": bucket_name})

        db = firestore_mod.client()
        bucket = storage_mod.bucket()

        runner_config = await self._read_runner_config(db)

        state = _BridgeState()

        loop = asyncio.get_running_loop()

        def _enqueue_job(doc: Any) -> None:
            """Called on the asyncio loop thread — safe to touch the queue."""
            try:
                state.pending_jobs.put_nowait(doc)
            except asyncio.QueueFull:
                logger.warning("Pending queue full — dropping jobId=%s", doc.id)

        def _on_snapshot(
            query_snapshot: Any,
            changes: Any,
            read_time: Any,
        ) -> None:
            if state.shutdown_event.is_set():
                return
            for change in changes:
                if change.type.name == "ADDED":
                    doc = change.document
                    logger.info(
                        "Snapshot ADDED jobId=%s status=%s",
                        doc.id, (doc.to_dict() or {}).get("status"),
                    )
                    # Thread-safe handoff: schedule the put on the event loop so
                    # the asyncio consumer (pending_jobs.get()) is properly woken.
                    loop.call_soon_threadsafe(_enqueue_job, doc)

        query = (
            db.collection(self._jobs_collection)
            .where(filter=FieldFilter("status", "==", "pending"))
            .where(filter=FieldFilter("requires", "array_contains_any", self._worker_capabilities))
        )

        logger.info(
            "Starting listener workerId=%s capabilities=%s dry_run=%s",
            self._worker_id, self._worker_capabilities, dry_run,
        )
        listener = query.on_snapshot(_on_snapshot)
        state.listener_unsubscribe = listener

        def _handle_signal() -> None:
            logger.info("Shutdown signal received — stopping bridge")
            state.shutdown_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _handle_signal)

        try:
            while not state.shutdown_event.is_set():
                try:
                    doc = await asyncio.wait_for(
                        state.pending_jobs.get(),
                        timeout=1.0,
                    )
                except asyncio.TimeoutError:
                    continue

                task = asyncio.create_task(
                    self._process_job(
                        doc=doc,
                        db=db,
                        bucket=bucket,
                        runner_config=runner_config,
                        dry_run=dry_run,
                        firestore_module=firestore_mod,
                    )
                )
                state.in_flight.add(task)

                def _done(t: asyncio.Task, _jid: str = doc.id) -> None:
                    state.in_flight.discard(t)
                    _log_task_exception(t, _jid)

                task.add_done_callback(_done)

                if once:
                    logger.info("--once flag set — waiting for single job to finish")
                    await task
                    break

        finally:
            logger.info("Stopping listener and draining in-flight tasks (up to 30s)")

            if state.listener_unsubscribe is not None:
                try:
                    state.listener_unsubscribe.unsubscribe()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Error unsubscribing listener: %s", exc)

            if state.in_flight:
                done, pending = await asyncio.wait(state.in_flight, timeout=30.0)
                logger.info(
                    "Drain complete: done=%d still_pending=%d",
                    len(done), len(pending),
                )
                for t in pending:
                    t.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)

            # Release any jobs that were claimed but whose tasks didn't complete.
            logger.info("Releasing in-flight claimed/running jobs back to pending")
            try:
                claimed_query = (
                    db.collection(self._jobs_collection)
                    .where(filter=FieldFilter("workerId", "==", self._worker_id))
                    .where(filter=FieldFilter("status", "in", ["claimed", "running"]))
                )
                claimed_docs = claimed_query.stream()
                release_tasks = [
                    _release_job(
                        db.collection(self._jobs_collection).document(d.id),
                        d.id,
                        firestore_mod,
                    )
                    for d in claimed_docs
                ]
                if release_tasks:
                    await asyncio.gather(*release_tasks, return_exceptions=True)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Error releasing jobs on shutdown: %s", exc)

            firebase_admin.delete_app(app)
            logger.info("Bridge stopped cleanly workerId=%s", self._worker_id)
