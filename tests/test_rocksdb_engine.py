"""
Integration tests for the RocksDB storage backend.

Tests run against the Python-level RocksDBDatabase class which mirrors the
SQLAlchemy Database interface. The full suite is skipped when python-rocksdb
is not installed; the WAL sub-suite runs unconditionally since _PythonWAL
has no native dependency.
"""

from __future__ import annotations

import datetime
import os
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "collector"))

# ---------------------------------------------------------------------------
# Availability probe — determines which test classes are skipped
# ---------------------------------------------------------------------------

try:
    from controldb_collector.storage.rocksdb_backend import _ROCKSDB_AVAILABLE
except Exception:
    _ROCKSDB_AVAILABLE = False

_skip_rocksdb = pytest.mark.skipif(
    not _ROCKSDB_AVAILABLE,
    reason="RocksDB backend not available (install rocksdb or rocksdict)",
)

# ---------------------------------------------------------------------------
# _PythonWAL tests — no rocksdb dependency
# ---------------------------------------------------------------------------

from controldb_collector.storage.rocksdb_backend import _PythonWAL  # noqa: E402

WAL_OP_INSERT_RUN   = 1
WAL_OP_INSERT_EVENT = 2


class TestPythonWAL:
    def test_append_returns_monotonic_seq(self, tmp_path):
        wal = _PythonWAL(str(tmp_path / "wal"))
        s1 = wal.append(WAL_OP_INSERT_RUN, '{"run_id":"r1"}')
        s2 = wal.append(WAL_OP_INSERT_EVENT, '{"event_id":"e1"}')
        assert s1 == 1
        assert s2 == 2
        assert wal._last_seq == 2

    def test_mark_applied_persists(self, tmp_path):
        path = str(tmp_path / "wal")
        wal = _PythonWAL(path)
        wal.append(WAL_OP_INSERT_RUN, '{"run_id":"r1"}')
        wal.append(WAL_OP_INSERT_RUN, '{"run_id":"r2"}')
        wal.mark_applied(1)
        assert wal._applied_seq == 1
        wal.close()

        wal2 = _PythonWAL(path)
        assert wal2._applied_seq == 1
        assert wal2._last_seq == 2
        wal2.close()

    def test_persistence_recovers_seq(self, tmp_path):
        path = str(tmp_path / "wal")
        wal = _PythonWAL(path)
        for i in range(5):
            wal.append(WAL_OP_INSERT_EVENT, f'{{"i":{i}}}')
        wal.close()

        wal2 = _PythonWAL(path)
        assert wal2._last_seq == 5
        wal2.close()

    def test_concurrent_appends_no_collision(self, tmp_path):
        wal = _PythonWAL(str(tmp_path / "wal"))
        seqs: list[int] = []
        mu = threading.Lock()

        def worker():
            for _ in range(20):
                s = wal.append(WAL_OP_INSERT_EVENT, '{"x":1}')
                with mu:
                    seqs.append(s)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(seqs) == 100
        assert len(set(seqs)) == 100, "duplicate sequence numbers detected"
        wal.close()


# ---------------------------------------------------------------------------
# RocksDBDatabase tests — skipped if python-rocksdb not installed
# ---------------------------------------------------------------------------

from controldb_collector.storage.rocksdb_backend import (  # noqa: E402
    RocksDBDatabase,
    AgentRunRow,
    AuditEventRow,
    ApprovalRow,
)
from controldb_collector.config import SETTINGS  # noqa: E402


@pytest.fixture()
def rdb(tmp_path, monkeypatch):
    """Fresh RocksDBDatabase instance per test."""
    monkeypatch.setenv("CONTROLDB_BOOTSTRAP_ORG_ID",     "org_test")
    monkeypatch.setenv("CONTROLDB_BOOTSTRAP_PROJECT_ID", "proj_test")
    monkeypatch.setenv("CONTROLDB_BOOTSTRAP_API_KEYS",   "test-key")

    # Refresh SETTINGS
    from controldb_collector.config import Settings
    fresh = Settings()
    for k, v in fresh.__dict__.items():
        setattr(SETTINGS, k, v)

    db = RocksDBDatabase(str(tmp_path / "rdb"))
    yield db
    db.close()
    RocksDBDatabase._instance = None


def _make_run(run_id: str = "run_001") -> AgentRunRow:
    return AgentRunRow(
        run_id=run_id,
        org_id="org_test",
        project_id="proj_test",
        environment="test",
        agent_id="test-agent",
        status="running",
        started_at=datetime.datetime.utcnow(),
    )


def _make_event(run_id: str = "run_001",
                event_id: str = "evt_001",
                idempotency_key: str | None = None) -> AuditEventRow:
    from controldb_collector.hashing import compute_event_hash

    ev = AuditEventRow(
        event_id=event_id,
        run_id=run_id,
        org_id="org_test",
        project_id="proj_test",
        environment="test",
        event_type="tool_call.completed",
        schema_version="2026-05-01",
        payload_mode="metadata_only",
        occurred_at=datetime.datetime.utcnow(),
        ingested_at=datetime.datetime.utcnow(),
        idempotency_key=idempotency_key,
    )
    ev.event_hash = compute_event_hash(ev.__dict__)
    return ev


# --- Run lifecycle -----------------------------------------------------------

@_skip_rocksdb
class TestRunLifecycle:
    def test_write_and_read_run(self, rdb):
        run = _make_run()
        with rdb.session() as s:
            s.add(run)

        from controldb_collector.storage.models import AgentRun
        with rdb.session() as s:
            got = s.get(AgentRun, "run_001")
        assert got is not None
        assert got.run_id == "run_001"
        assert got.agent_id == "test-agent"

    def test_write_run_idempotent(self, rdb):
        run = _make_run()
        with rdb.session() as s:
            s.add(run)
        # Overwrite same run_id — should not raise
        with rdb.session() as s:
            s.add(run)

        from controldb_collector.storage.models import AgentRun
        with rdb.session() as s:
            got = s.get(AgentRun, "run_001")
        assert got.run_id == "run_001"

    def test_run_not_found_returns_none(self, rdb):
        from controldb_collector.storage.models import AgentRun
        with rdb.session() as s:
            got = s.get(AgentRun, "nonexistent")
        assert got is None

    def test_list_runs_via_index(self, rdb):
        for i in range(3):
            with rdb.session() as s:
                s.add(_make_run(f"run_{i:03d}"))
        rows = rdb._query_runs({"org_id": "org_test", "project_id": "proj_test"}, 50)
        assert len(rows) == 3
        assert all(r.org_id == "org_test" for r in rows)

    def test_run_status_update(self, rdb):
        run = _make_run()
        with rdb.session() as s:
            s.add(run)

        run.status = "committed"
        run.ended_at = datetime.datetime.utcnow()
        with rdb.session() as s:
            s.add(run)

        from controldb_collector.storage.models import AgentRun
        with rdb.session() as s:
            got = s.get(AgentRun, "run_001")
        assert got.status == "committed"


# --- Event ingestion ---------------------------------------------------------

@_skip_rocksdb
class TestEventIngestion:
    def test_append_single_event(self, rdb):
        with rdb.session() as s:
            s.add(_make_run())
        ev = _make_event()
        with rdb.session() as s:
            s.add(ev)
        assert ev.sequence > 0

    def test_sequence_monotonic(self, rdb):
        with rdb.session() as s:
            s.add(_make_run())
        seqs = []
        for i in range(5):
            ev = _make_event(event_id=f"evt_{i:03d}")
            with rdb.session() as s:
                s.add(ev)
            seqs.append(ev.sequence)
        assert seqs == sorted(seqs)
        assert len(set(seqs)) == 5

    def test_timeline_ordering(self, rdb):
        with rdb.session() as s:
            s.add(_make_run())
        for i in range(5):
            ev = _make_event(event_id=f"evt_{i:03d}")
            with rdb.session() as s:
                s.add(ev)

        rows = rdb._query_events({"run_id": "run_001"}, 100)
        assert len(rows) == 5
        seqs = [r.sequence for r in rows]
        assert seqs == sorted(seqs)

    def test_idempotency_key_dedup(self, rdb):
        with rdb.session() as s:
            s.add(_make_run())

        ev1 = _make_event(idempotency_key="idem_001")
        with rdb.session() as s:
            s.add(ev1)

        existing = rdb.get_event_by_idempotency_key("idem_001")
        assert existing is not None
        assert existing.event_id == ev1.event_id

    def test_idempotency_key_missing_returns_none(self, rdb):
        result = rdb.get_event_by_idempotency_key("no_such_key")
        assert result is None

    def test_batch_events_same_run(self, rdb):
        with rdb.session() as s:
            s.add(_make_run())
        for i in range(10):
            ev = _make_event(event_id=f"batch_{i:03d}")
            with rdb.session() as s:
                s.add(ev)

        rows = rdb._query_events({"run_id": "run_001"}, 100)
        assert len(rows) == 10

    def test_events_filtered_by_run(self, rdb):
        with rdb.session() as s:
            s.add(_make_run("run_a"))
        with rdb.session() as s:
            s.add(_make_run("run_b"))

        for i in range(3):
            with rdb.session() as s:
                s.add(_make_event("run_a", f"a_{i}"))
        for i in range(2):
            with rdb.session() as s:
                s.add(_make_event("run_b", f"b_{i}"))

        rows_a = rdb._query_events({"run_id": "run_a"}, 100)
        rows_b = rdb._query_events({"run_id": "run_b"}, 100)
        assert len(rows_a) == 3
        assert len(rows_b) == 2


# --- Approvals ---------------------------------------------------------------

@_skip_rocksdb
class TestApprovals:
    def test_write_and_query_approval(self, rdb):
        appr = ApprovalRow(
            approval_id="appr_001",
            run_id="run_001",
            org_id="org_test",
            project_id="proj_test",
            status="requested",
            requested_by="agent",
        )
        with rdb.session() as s:
            s.add(appr)

        rows = rdb._query_approvals(
            {"org_id": "org_test", "project_id": "proj_test", "status": "requested"}, 50)
        assert len(rows) == 1
        assert rows[0].approval_id == "appr_001"

    def test_approval_status_update(self, rdb):
        appr = ApprovalRow(
            approval_id="appr_002",
            org_id="org_test",
            project_id="proj_test",
            status="requested",
        )
        with rdb.session() as s:
            s.add(appr)

        appr.status = "approved"
        appr.reviewer_id = "manager_1"
        with rdb.session() as s:
            s.add(appr)

        rows = rdb._query_approvals(
            {"org_id": "org_test", "project_id": "proj_test", "status": "requested"}, 50)
        assert len(rows) == 0

        rows_approved = rdb._query_approvals(
            {"org_id": "org_test", "project_id": "proj_test", "status": "approved"}, 50)
        assert len(rows_approved) == 1
        assert rows_approved[0].reviewer_id == "manager_1"

    def test_approval_filter_by_org(self, rdb):
        for org in ("org_a", "org_b"):
            appr = ApprovalRow(
                approval_id=f"appr_{org}",
                org_id=org,
                project_id="proj_test",
                status="requested",
            )
            with rdb.session() as s:
                s.add(appr)

        rows = rdb._query_approvals({"org_id": "org_a", "project_id": "proj_test"}, 50)
        assert len(rows) == 1
        assert rows[0].org_id == "org_a"


# --- Sequence persistence ----------------------------------------------------

@_skip_rocksdb
class TestSequencePersistence:
    def test_seq_survives_reopen(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CONTROLDB_BOOTSTRAP_ORG_ID",     "org_test")
        monkeypatch.setenv("CONTROLDB_BOOTSTRAP_PROJECT_ID", "proj_test")
        monkeypatch.setenv("CONTROLDB_BOOTSTRAP_API_KEYS",   "test-key")
        from controldb_collector.config import Settings
        fresh = Settings()
        for k, v in fresh.__dict__.items():
            setattr(SETTINGS, k, v)

        db_path = str(tmp_path / "rdb2")
        db = RocksDBDatabase(db_path)
        with db.session() as s:
            s.add(_make_run())
        for i in range(3):
            ev = _make_event(event_id=f"ev_{i}")
            with db.session() as s:
                s.add(ev)
        seq_before = db._seq
        db.close()

        db2 = RocksDBDatabase(db_path)
        assert db2._seq == seq_before
        db2.close()
        RocksDBDatabase._instance = None


# --- Concurrency -------------------------------------------------------------

@_skip_rocksdb
class TestConcurrency:
    def test_concurrent_event_writes_no_seq_collision(self, rdb):
        with rdb.session() as s:
            s.add(_make_run())

        seqs: list[int] = []
        mu = threading.Lock()
        errors: list[Exception] = []

        def worker(i: int):
            try:
                ev = _make_event(event_id=f"conc_{i:04d}")
                with rdb.session() as s:
                    s.add(ev)
                with mu:
                    seqs.append(ev.sequence)
            except Exception as exc:
                with mu:
                    errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"worker errors: {errors}"
        assert len(set(seqs)) == 20, "duplicate sequences in concurrent writes"
