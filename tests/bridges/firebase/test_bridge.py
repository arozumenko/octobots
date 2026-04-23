"""tests/bridges/firebase/test_bridge.py — Unit tests for Bridge class.

No live Firestore — all Firebase/Storage interactions are mocked.
Uses pytest-asyncio for async test cases.

Tests ported from nutrisnap/workers/firebase_supervisor/tests/test_firebase_bridge.py,
stripped of app-specific concerns (USDA enrichment, /training writes, etc.).
"""
from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_firestore_mock() -> MagicMock:
    """Return a minimal mock that looks like the google.cloud.firestore module."""
    fs = MagicMock()
    fs.SERVER_TIMESTAMP = "SERVER_TIMESTAMP_SENTINEL"
    fs.transactional = lambda fn: fn
    return fs


def _make_job_snapshot(
    job_id: str = "job-abc",
    status: str = "pending",
    requires: list[str] | None = None,
    exists: bool = True,
) -> MagicMock:
    snap = MagicMock()
    snap.exists = exists
    snap.to_dict.return_value = {
        "id": job_id,
        "status": status,
        "requires": requires if requires is not None else ["vision"],
        "userId": "user-1",
        "imageStoragePath": f"users/user-1/meals/{job_id}.jpg",
        "mealId": "meal-1",
        "workerId": None,
        "workerCapabilities": None,
        "attempts": 0,
        "result": None,
        "errorMessage": None,
        "errorAt": None,
    }
    snap.id = job_id
    return snap


def _make_job_doc(
    job_id: str = "job-abc",
    status: str = "pending",
    requires: list[str] | None = None,
) -> MagicMock:
    doc = MagicMock()
    doc.id = job_id
    doc.to_dict.return_value = {
        "id": job_id,
        "status": status,
        "requires": requires if requires is not None else ["vision"],
        "userId": "user-1",
        "imageStoragePath": f"users/user-1/meals/{job_id}.jpg",
        "mealId": "meal-1",
        "workerId": None,
        "workerCapabilities": None,
        "attempts": 0,
        "result": None,
        "errorMessage": None,
        "errorAt": None,
    }
    return doc


def _make_bridge(
    tmp_path: Path,
    payload_builder: Any = None,
    result_enricher: Any = None,
    mcp_result_timeout_sec: int = 5,
) -> "Bridge":
    from bridges.firebase.bridge import Bridge

    if payload_builder is None:
        def payload_builder(job_id: str, claimed_doc: dict, local_image_path: Path) -> dict:
            return {
                "jobId": job_id,
                "imageStoragePath": claimed_doc.get("imageStoragePath", ""),
                "kind": "test-job",
            }

    return Bridge(
        service_account_path="/fake/sa.json",
        worker_id="test-worker",
        worker_capabilities=["vision", "claude-code"],
        octobots_db=str(tmp_path / "relay.db"),
        relay_script=tmp_path / "relay.py",
        taskbox_recipient="vision-analyst",
        mcp_results_dir=tmp_path / "mcp-results",
        mcp_images_dir=tmp_path / "mcp-images",
        mcp_jobs_dir=tmp_path / "mcp-jobs",
        payload_builder=payload_builder,
        result_enricher=result_enricher,
        mcp_result_timeout_sec=mcp_result_timeout_sec,
    )


def _make_db_and_bucket(job_id: str = "job-abc") -> tuple[MagicMock, MagicMock]:
    job_ref_mock = MagicMock()
    db = MagicMock()
    db.collection.return_value.document.return_value = job_ref_mock
    db.transaction.return_value = MagicMock()

    bucket = MagicMock()
    blob_mock = MagicMock()
    blob_mock.download_to_filename = MagicMock()
    bucket.blob.return_value = blob_mock
    return db, bucket


# ---------------------------------------------------------------------------
# Tests: claim_job (pure function)
# ---------------------------------------------------------------------------


class TestClaimJob:
    def setup_method(self) -> None:
        from bridges.firebase.bridge import claim_job
        self.claim_job = claim_job
        self.fs = _make_firestore_mock()

    def _txn_and_ref(self, snap: MagicMock) -> tuple[MagicMock, MagicMock]:
        txn = MagicMock()
        job_ref = MagicMock()
        job_ref.get.return_value = snap
        return txn, job_ref

    def test_claim_happy_path(self) -> None:
        snap = _make_job_snapshot(status="pending", requires=["vision"])
        txn, ref = self._txn_and_ref(snap)

        result = self.claim_job(txn, ref, "worker-1", ["vision", "claude-code"], self.fs)

        assert result is not None
        assert result["status"] == "pending"
        txn.update.assert_called_once()
        update_data = txn.update.call_args[0][1]
        assert update_data["status"] == "claimed"
        assert update_data["workerId"] == "worker-1"
        assert update_data["workerCapabilities"] == ["vision", "claude-code"]

    def test_claim_rejects_missing_capabilities(self) -> None:
        snap = _make_job_snapshot(status="pending", requires=["vision", "gpu"])
        txn, ref = self._txn_and_ref(snap)

        result = self.claim_job(txn, ref, "worker-1", ["vision"], self.fs)

        assert result is None
        txn.update.assert_not_called()

    def test_claim_rejects_already_claimed(self) -> None:
        snap = _make_job_snapshot(status="claimed")
        txn, ref = self._txn_and_ref(snap)

        result = self.claim_job(txn, ref, "worker-1", ["vision"], self.fs)

        assert result is None
        txn.update.assert_not_called()

    def test_claim_rejects_nonexistent_doc(self) -> None:
        snap = _make_job_snapshot(exists=False)
        txn, ref = self._txn_and_ref(snap)

        result = self.claim_job(txn, ref, "worker-1", ["vision"], self.fs)

        assert result is None
        txn.update.assert_not_called()

    def test_claim_empty_requires_succeeds(self) -> None:
        snap = _make_job_snapshot(status="pending", requires=[])
        txn, ref = self._txn_and_ref(snap)

        result = self.claim_job(txn, ref, "worker-1", ["vision"], self.fs)

        assert result is not None
        txn.update.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: heartbeat
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestHeartbeatLoop:
    async def test_heartbeat_updates_heartbeat_at(self) -> None:
        from bridges.firebase.bridge import _heartbeat_loop

        job_ref = MagicMock()
        fs = _make_firestore_mock()

        task = asyncio.create_task(
            _heartbeat_loop(job_ref, "job-hb", interval_sec=0, firestore_module=fs)
        )
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert job_ref.update.call_count >= 1
        call_kwargs = job_ref.update.call_args[0][0]
        assert "heartbeatAt" in call_kwargs


# ---------------------------------------------------------------------------
# Tests: graceful shutdown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestGracefulShutdown:
    async def test_release_job_sets_status_pending_and_clears_worker(self) -> None:
        from bridges.firebase.bridge import _release_job

        job_ref = MagicMock()
        fs = _make_firestore_mock()

        await _release_job(job_ref, "job-release", fs)

        job_ref.update.assert_called_once()
        data = job_ref.update.call_args[0][0]
        assert data["status"] == "pending"
        assert data["workerId"] is None
        assert data["workerCapabilities"] is None
        assert data["claimedAt"] is None
        assert data["heartbeatAt"] is None


# ---------------------------------------------------------------------------
# Tests: _process_job
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestProcessJob:
    async def test_process_job_writes_done_on_success(self, tmp_path: Path) -> None:
        """Happy path: claim succeeds, MCP returns result, Firestore written as done."""
        bridge = _make_bridge(tmp_path)
        db, bucket = _make_db_and_bucket("job-happy")
        doc = _make_job_doc("job-happy")
        fs = _make_firestore_mock()

        mcp_result = {
            "foodItems": [{"name": "chicken", "portionGrams": 150}],
            "needsClarification": False,
        }

        with (
            patch.object(bridge, "_enqueue_taskbox", return_value="msg-1"),
            patch.object(bridge, "_stash_job_for_mcp"),
            patch.object(bridge, "_wait_for_mcp_result", new=AsyncMock(return_value=mcp_result)),
        ):
            with patch("bridges.firebase.bridge.claim_job", return_value=doc.to_dict()):
                await bridge._process_job(
                    doc=doc,
                    db=db,
                    bucket=bucket,
                    runner_config={"heartbeatIntervalSec": 15, "mcpTimeoutSec": 5},
                    dry_run=False,
                    firestore_module=fs,
                )

        update_calls = db.collection.return_value.document.return_value.update.call_args_list
        last = update_calls[-1][0][0]
        assert last["status"] == "done"
        assert "result" in last

    async def test_process_job_calls_result_enricher(self, tmp_path: Path) -> None:
        """result_enricher is called with the raw MCP result and its output is stored."""
        enriched_flag = {"called": False}

        async def my_enricher(raw: dict) -> dict:
            enriched_flag["called"] = True
            return {**raw, "enriched": True}

        bridge = _make_bridge(tmp_path, result_enricher=my_enricher)
        db, bucket = _make_db_and_bucket("job-enrich")
        doc = _make_job_doc("job-enrich")
        fs = _make_firestore_mock()

        mcp_result = {"items": [], "needsClarification": False}

        with (
            patch.object(bridge, "_enqueue_taskbox", return_value="msg-enrich"),
            patch.object(bridge, "_stash_job_for_mcp"),
            patch.object(bridge, "_wait_for_mcp_result", new=AsyncMock(return_value=mcp_result)),
        ):
            with patch("bridges.firebase.bridge.claim_job", return_value=doc.to_dict()):
                await bridge._process_job(
                    doc=doc,
                    db=db,
                    bucket=bucket,
                    runner_config={"heartbeatIntervalSec": 15, "mcpTimeoutSec": 5},
                    dry_run=False,
                    firestore_module=fs,
                )

        assert enriched_flag["called"], "result_enricher must be called"
        update_calls = db.collection.return_value.document.return_value.update.call_args_list
        last = update_calls[-1][0][0]
        assert last["result"].get("enriched") is True

    async def test_process_job_identity_enricher_when_none(self, tmp_path: Path) -> None:
        """When result_enricher=None, the raw result is stored unchanged."""
        bridge = _make_bridge(tmp_path, result_enricher=None)
        db, bucket = _make_db_and_bucket("job-identity")
        doc = _make_job_doc("job-identity")
        fs = _make_firestore_mock()

        mcp_result = {"foodItems": [{"name": "rice"}], "usdaResolved": False}

        with (
            patch.object(bridge, "_enqueue_taskbox", return_value="msg-identity"),
            patch.object(bridge, "_stash_job_for_mcp"),
            patch.object(bridge, "_wait_for_mcp_result", new=AsyncMock(return_value=mcp_result)),
        ):
            with patch("bridges.firebase.bridge.claim_job", return_value=doc.to_dict()):
                await bridge._process_job(
                    doc=doc,
                    db=db,
                    bucket=bucket,
                    runner_config={"heartbeatIntervalSec": 15, "mcpTimeoutSec": 5},
                    dry_run=False,
                    firestore_module=fs,
                )

        update_calls = db.collection.return_value.document.return_value.update.call_args_list
        last = update_calls[-1][0][0]
        assert last["status"] == "done"
        assert last["result"] == mcp_result

    async def test_process_job_writes_error_on_mcp_timeout(self, tmp_path: Path) -> None:
        """TimeoutError from MCP polling writes status=error to Firestore."""
        bridge = _make_bridge(tmp_path, mcp_result_timeout_sec=1)
        db, bucket = _make_db_and_bucket("job-timeout")
        doc = _make_job_doc("job-timeout")
        fs = _make_firestore_mock()

        with (
            patch.object(bridge, "_enqueue_taskbox", return_value="msg-timeout"),
            patch.object(bridge, "_stash_job_for_mcp"),
            patch.object(
                bridge, "_wait_for_mcp_result",
                new=AsyncMock(side_effect=asyncio.TimeoutError("no result")),
            ),
        ):
            with patch("bridges.firebase.bridge.claim_job", return_value=doc.to_dict()):
                await bridge._process_job(
                    doc=doc,
                    db=db,
                    bucket=bucket,
                    runner_config={"heartbeatIntervalSec": 15, "mcpTimeoutSec": 1},
                    dry_run=False,
                    firestore_module=fs,
                )

        update_calls = db.collection.return_value.document.return_value.update.call_args_list
        last = update_calls[-1][0][0]
        assert last["status"] == "error"
        assert "MCP result timeout" in last.get("errorMessage", "")

    async def test_process_job_dry_run_skips_claim(self, tmp_path: Path) -> None:
        """dry_run=True must log and return without claiming."""
        bridge = _make_bridge(tmp_path)
        db, bucket = _make_db_and_bucket("job-dry")
        doc = _make_job_doc("job-dry")
        fs = _make_firestore_mock()

        with patch("bridges.firebase.bridge.claim_job") as mock_claim:
            await bridge._process_job(
                doc=doc,
                db=db,
                bucket=bucket,
                runner_config={"heartbeatIntervalSec": 15, "mcpTimeoutSec": 5},
                dry_run=True,
                firestore_module=fs,
            )

        mock_claim.assert_not_called()
        db.collection.return_value.document.return_value.update.assert_not_called()

    async def test_process_job_does_not_ack_taskbox(self, tmp_path: Path) -> None:
        """Bridge must NOT call relay with 'ack' — ack belongs to the MCP server."""
        relay_calls: list[list[str]] = []

        bridge = _make_bridge(tmp_path)

        def _capture_relay(args: list[str]) -> dict:
            relay_calls.append(list(args))
            return {"id": "msg-no-ack", "status": "done"}

        db, bucket = _make_db_and_bucket("job-no-ack")
        doc = _make_job_doc("job-no-ack")
        fs = _make_firestore_mock()

        mcp_result = {"items": [], "needsClarification": False}

        with (
            patch.object(bridge, "_relay", side_effect=_capture_relay),
            patch.object(bridge, "_wait_for_mcp_result", new=AsyncMock(return_value=mcp_result)),
            patch.object(bridge, "_stash_job_for_mcp"),
        ):
            with patch("bridges.firebase.bridge.claim_job", return_value=doc.to_dict()):
                # Patch _enqueue_taskbox to use our captured relay
                with patch.object(bridge, "_enqueue_taskbox", return_value="msg-no-ack"):
                    await bridge._process_job(
                        doc=doc,
                        db=db,
                        bucket=bucket,
                        runner_config={"heartbeatIntervalSec": 15, "mcpTimeoutSec": 5},
                        dry_run=False,
                        firestore_module=fs,
                    )

        ack_calls = [c for c in relay_calls if c and c[0] == "ack"]
        assert not ack_calls, f"Bridge must NOT ack Taskbox; got: {ack_calls}"


# ---------------------------------------------------------------------------
# Tests: stash writes
# ---------------------------------------------------------------------------


class TestStashJobForMcp:
    def test_stash_writes_image_and_metadata(self, tmp_path: Path) -> None:
        """_stash_job_for_mcp writes image bytes and JSON metadata."""
        bridge = _make_bridge(tmp_path)

        # Create a fake image file.
        image_src = tmp_path / "source.jpg"
        image_src.write_bytes(b"FAKE_IMAGE_BYTES")

        bridge._stash_job_for_mcp("job-stash", "msg-stash", image_src)

        image_dest = bridge._mcp_images_dir / "job-stash.jpg"
        meta_dest = bridge._mcp_jobs_dir / "job-stash.json"

        assert image_dest.exists(), "Image file should be written"
        assert image_dest.read_bytes() == b"FAKE_IMAGE_BYTES"

        assert meta_dest.exists(), "Metadata file should be written"
        metadata = json.loads(meta_dest.read_text())
        assert metadata["jobId"] == "job-stash"
        assert metadata["msgId"] == "msg-stash"
        assert metadata["imagePath"] == str(image_dest)
        assert metadata["mimeType"] == "image/jpeg"
        assert metadata["octobotsDb"] == str(tmp_path / "relay.db")

    def test_stash_atomic_write(self, tmp_path: Path) -> None:
        """_stash_job_for_mcp must not leave .tmp files behind."""
        bridge = _make_bridge(tmp_path)

        image_src = tmp_path / "source.jpg"
        image_src.write_bytes(b"ATOMIC_TEST")

        bridge._stash_job_for_mcp("job-atomic", "msg-atomic", image_src)

        tmp_files = list(bridge._mcp_images_dir.glob("*.tmp")) + list(bridge._mcp_jobs_dir.glob("*.tmp"))
        assert not tmp_files, f"Temporary files should be cleaned up: {tmp_files}"


# ---------------------------------------------------------------------------
# Tests: wait_for_mcp_result
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestWaitForMcpResult:
    async def test_wait_for_mcp_result_returns_result(self, tmp_path: Path) -> None:
        """Result file present before polling starts is returned immediately."""
        bridge = _make_bridge(tmp_path)
        bridge._mcp_results_dir.mkdir(parents=True, exist_ok=True)

        payload = {"foodItems": [], "needsClarification": False}
        result_file = bridge._mcp_results_dir / "job-wait.json"
        result_file.write_text(json.dumps(payload))

        result = await bridge._wait_for_mcp_result("job-wait")
        assert result == payload
        assert not result_file.exists(), "Result file should be deleted after being consumed"

    async def test_wait_for_mcp_result_cleans_up_file(self, tmp_path: Path) -> None:
        """Result file is removed after being read."""
        bridge = _make_bridge(tmp_path)
        bridge._mcp_results_dir.mkdir(parents=True, exist_ok=True)

        result_file = bridge._mcp_results_dir / "job-cleanup.json"
        result_file.write_text(json.dumps({"items": []}))

        await bridge._wait_for_mcp_result("job-cleanup")

        assert not result_file.exists()

    async def test_wait_for_mcp_result_times_out(self, tmp_path: Path) -> None:
        """TimeoutError is raised when no result file appears within timeout."""
        bridge = _make_bridge(tmp_path, mcp_result_timeout_sec=1)
        bridge._mcp_results_dir.mkdir(parents=True, exist_ok=True)

        with pytest.raises(asyncio.TimeoutError):
            await bridge._wait_for_mcp_result("job-no-file")


# ---------------------------------------------------------------------------
# Tests: payload builder hook
# ---------------------------------------------------------------------------


class TestPayloadBuilder:
    def test_render_taskbox_payload_uses_payload_builder(self, tmp_path: Path) -> None:
        """payload_builder receives (job_id, claimed_doc, local_image_path) and its
        return value is used as the Taskbox message payload."""
        captured: dict = {}

        def my_builder(job_id: str, claimed_doc: dict, local_image_path: Path) -> dict:
            captured["job_id"] = job_id
            captured["claimed_doc"] = claimed_doc
            captured["local_image_path"] = local_image_path
            return {"myCustomField": "hello", "jobId": job_id}

        relay_responses: list[str] = []

        def _fake_relay(args: list[str]) -> dict:
            if args[0] == "send":
                # The payload is the last positional argument.
                payload_json = args[-1]
                relay_responses.append(payload_json)
                return {"id": "msg-pb-1"}
            return {}

        bridge = _make_bridge(tmp_path, payload_builder=my_builder)

        with patch.object(bridge, "_relay", side_effect=_fake_relay):
            msg_id = bridge._enqueue_taskbox(
                job_id="job-pb",
                claimed_doc={"imageStoragePath": "path/to/image.jpg", "userId": "u1"},
                local_image_path=Path("/tmp/job-pb.jpg"),
            )

        assert msg_id == "msg-pb-1"
        assert captured["job_id"] == "job-pb"
        assert captured["local_image_path"] == Path("/tmp/job-pb.jpg")
        assert captured["claimed_doc"]["imageStoragePath"] == "path/to/image.jpg"

        assert relay_responses, "relay send must have been called"
        sent_payload = json.loads(relay_responses[0])
        assert sent_payload["myCustomField"] == "hello"
        assert sent_payload["jobId"] == "job-pb"


# ---------------------------------------------------------------------------
# Tests: thread-safe snapshot handoff
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestSnapshotThreadsafeHandoff:
    """Regression: _on_snapshot called from a background thread must correctly
    deliver documents to the asyncio queue consumer via call_soon_threadsafe."""

    async def _bootstrap_state(
        self, monkeypatch: Any, tmp_path: Path
    ) -> tuple[asyncio.Queue, Any]:
        """Boot Bridge.run() just far enough to capture the _on_snapshot closure
        and the pending_jobs queue, then abort before the main loop starts."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

        from bridges.firebase import bridge as bridge_mod

        sa_file = tmp_path / "sa.json"
        sa_file.write_text(json.dumps({"project_id": "test-proj"}))

        b = _make_bridge(tmp_path)
        b._sa_path = str(sa_file)

        captured: dict[str, Any] = {}

        original_state_cls = bridge_mod._BridgeState

        class _CapturingState(original_state_cls):
            def __init__(self) -> None:
                super().__init__()
                captured["state"] = self

        def _fake_on_snapshot_register(callback: Any) -> MagicMock:
            captured["on_snapshot"] = callback
            raise RuntimeError("stop-after-listener")

        firebase_admin_mock = MagicMock()
        credentials_mock = MagicMock()
        firestore_mock_mod = MagicMock()
        storage_mock_mod = MagicMock()

        query_mock = MagicMock()
        query_mock.on_snapshot.side_effect = _fake_on_snapshot_register
        db_mock = MagicMock()
        db_mock.collection.return_value.where.return_value.where.return_value = query_mock
        firestore_mock_mod.client.return_value = db_mock

        with (
            patch.object(bridge_mod, "_load_firebase", return_value=(
                firebase_admin_mock, credentials_mock, firestore_mock_mod, storage_mock_mod,
            )),
            patch.object(bridge_mod, "_BridgeState", _CapturingState),
            patch.dict("sys.modules", {"google.cloud.firestore_v1.base_query": MagicMock()}),
        ):
            try:
                await b.run(dry_run=True)
            except RuntimeError as exc:
                if "stop-after-listener" not in str(exc):
                    raise

        return captured["state"].pending_jobs, captured["on_snapshot"]

    def _make_doc_and_change(self, job_id: str) -> tuple[MagicMock, MagicMock]:
        doc = MagicMock()
        doc.id = job_id
        doc.to_dict.return_value = {"status": "pending"}
        change = MagicMock()
        change.type.name = "ADDED"
        change.document = doc
        return doc, change

    async def test_on_snapshot_threadsafe_handoff(self, monkeypatch: Any, tmp_path: Path) -> None:
        """Documents from a background thread reach the asyncio queue consumer."""
        queue, on_snapshot = await self._bootstrap_state(monkeypatch, tmp_path)

        doc, change = self._make_doc_and_change("job-threadsafe")

        def _fire() -> None:
            on_snapshot(MagicMock(), [change], MagicMock())

        t = threading.Thread(target=_fire)
        t.start()
        t.join(timeout=2.0)

        received = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert received.id == "job-threadsafe"

    async def test_non_added_changes_ignored(self, monkeypatch: Any, tmp_path: Path) -> None:
        """MODIFIED and REMOVED snapshot changes must not be enqueued."""
        queue, on_snapshot = await self._bootstrap_state(monkeypatch, tmp_path)

        for change_type in ("MODIFIED", "REMOVED"):
            doc, change = self._make_doc_and_change(f"job-{change_type}")
            change.type.name = change_type

            def _fire(ch: Any = change) -> None:
                on_snapshot(MagicMock(), [ch], MagicMock())

            t = threading.Thread(target=_fire)
            t.start()
            t.join(timeout=2.0)

        await asyncio.sleep(0.1)
        assert queue.empty(), "MODIFIED/REMOVED changes should not enqueue jobs"


# ---------------------------------------------------------------------------
# Tests: _log_task_exception
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestLogTaskException:
    async def test_exception_in_task_is_logged(self, caplog: Any) -> None:
        """_log_task_exception must emit ERROR when a task raised."""
        import logging
        from bridges.firebase.bridge import _log_task_exception

        boom = RuntimeError("simulated failure")

        async def _raising() -> None:
            raise boom

        task = asyncio.create_task(_raising())
        try:
            await task
        except RuntimeError:
            pass

        with caplog.at_level(logging.ERROR, logger="bridges.firebase.bridge"):
            _log_task_exception(task, "job-err-test")

        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert error_records, "Expected ERROR log when task raises"
        combined = " ".join(r.getMessage() for r in error_records)
        assert "job-err-test" in combined
        assert "simulated failure" in combined or "RuntimeError" in combined

    async def test_cancelled_task_does_not_log_error(self, caplog: Any) -> None:
        """Cancelled tasks must not be treated as errors."""
        import logging
        from bridges.firebase.bridge import _log_task_exception

        async def _slow() -> None:
            await asyncio.sleep(60)

        task = asyncio.create_task(_slow())
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        with caplog.at_level(logging.ERROR, logger="bridges.firebase.bridge"):
            _log_task_exception(task, "job-cancel")

        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert not error_records, f"Cancelled task must not produce ERROR: {error_records}"
