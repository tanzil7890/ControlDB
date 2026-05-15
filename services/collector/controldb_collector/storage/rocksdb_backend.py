"""
RocksDB-backed storage backend for ControlDB.

Drop-in replacement for the SQLAlchemy Database class.
Activated by setting CONTROLDB_ENGINE_BACKEND=rocksdb.

Uses the same session() context-manager interface so all services
(IngestService, ApprovalsService, EvidenceService, QueryService) work
unchanged. Internally stores all data in RocksDB column families.

Requires: pip install rocksdb (python-rocksdb, wraps RocksDB C library).
"""

from __future__ import annotations

import hashlib
import json
import struct
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence

try:
    import rocksdb
    _ROCKSDB_AVAILABLE = True
except ImportError:
    try:
        from . import _rocksdict_compat as rocksdb  # type: ignore
        _ROCKSDB_AVAILABLE = True
    except ImportError:
        _ROCKSDB_AVAILABLE = False

from ..config import SETTINGS
from ..hashing import compute_event_hash, compute_payload_hash

# ---------------------------------------------------------------------------
# Column family names (mirrors C++ engine layout)
# ---------------------------------------------------------------------------
_CF_META       = b"meta"
_CF_RUNS       = b"runs"
_CF_RUNS_IDX   = b"runs_by_org"
_CF_EVENTS     = b"events"
_CF_EV_RUN     = b"ev_by_run"
_CF_EV_TYPE    = b"ev_by_type"
_CF_IDEM       = b"idempotency"
_CF_APPROVALS  = b"approvals"
_CF_EXPORTS    = b"exports"
_CF_API_KEYS   = b"api_keys"
_CF_ORGS       = b"orgs"
_CF_PROJECTS   = b"projects"

_ALL_CFS = [
    _CF_META, _CF_RUNS, _CF_RUNS_IDX, _CF_EVENTS,
    _CF_EV_RUN, _CF_EV_TYPE, _CF_IDEM, _CF_APPROVALS,
    _CF_EXPORTS, _CF_API_KEYS, _CF_ORGS, _CF_PROJECTS,
]

# ---------------------------------------------------------------------------
# Big-endian 8-byte sequence key encoding
# ---------------------------------------------------------------------------

def _seq_key(n: int) -> bytes:
    return struct.pack(">q", n)

def _key_seq(b: bytes) -> int:
    return struct.unpack(">q", b)[0]

def _now_ms() -> int:
    return int(time.time() * 1000)

# ---------------------------------------------------------------------------
# WAL (pure-Python, mirrors C++ Wal)
# ---------------------------------------------------------------------------

import os

class _PythonWAL:
    """Append-only WAL using a simple binary file."""

    def __init__(self, wal_dir: str) -> None:
        Path(wal_dir).mkdir(parents=True, exist_ok=True)
        self._path         = os.path.join(wal_dir, "wal.log")
        self._applied_path = os.path.join(wal_dir, "applied.seq")
        self._mu           = threading.Lock()
        self._last_seq     = 0
        self._applied_seq  = 0
        self._load()
        self._fd = open(self._path, "ab")

    def _load(self) -> None:
        if os.path.exists(self._applied_path):
            with open(self._applied_path) as f:
                self._applied_seq = int(f.read().strip() or "0")
        if os.path.exists(self._path):
            with open(self._path, "rb") as f:
                while True:
                    hdr = f.read(21)
                    if len(hdr) < 21:
                        break
                    seq  = _key_seq(hdr[:8])
                    plen = struct.unpack(">I", hdr[17:21])[0]
                    f.read(plen + 4)
                    if seq > self._last_seq:
                        self._last_seq = seq

    def append(self, op_type: int, payload_json: str) -> int:
        with self._mu:
            self._last_seq += 1
            seq    = self._last_seq
            ts_ms  = _now_ms()
            raw    = payload_json.encode()
            plen   = len(raw)
            hdr    = _seq_key(seq) + bytes([op_type]) + _seq_key(ts_ms) + \
                     struct.pack(">I", plen)
            buf    = hdr + raw
            crc    = struct.pack(">I",
                                 int(hashlib.md5(buf).hexdigest()[:8], 16))
            self._fd.write(buf + crc)
            self._fd.flush()
            os.fsync(self._fd.fileno())
            return seq

    def mark_applied(self, wal_seq: int) -> None:
        with self._mu:
            if wal_seq > self._applied_seq:
                self._applied_seq = wal_seq
                with open(self._applied_path, "w") as f:
                    f.write(str(wal_seq))

    def close(self) -> None:
        self._fd.close()

# ---------------------------------------------------------------------------
# RocksDB Session (mirrors SQLAlchemy session context manager)
# ---------------------------------------------------------------------------

class _RocksSession:
    """
    Pseudo-session that buffers writes and commits atomically via WriteBatch.
    Provides the same attribute access pattern as SQLAlchemy Session.
    """

    def __init__(self, db: "RocksDBDatabase") -> None:
        self._db      = db
        self._batch   = rocksdb.WriteBatch() if _ROCKSDB_AVAILABLE else None
        self._pending: List[Any] = []  # objects added via session.add()

    def add(self, obj: Any) -> None:
        self._pending.append(obj)

    def flush(self) -> None:
        """Materialise pending objects into the write batch."""
        for obj in self._pending:
            self._db._write_obj(self._batch, obj)
        self._pending.clear()

    def commit(self) -> None:
        self.flush()
        if self._batch:
            opts = rocksdb.WriteOptions()
            opts.sync = True
            self._db._rocksdb.write(opts, self._batch)

    def rollback(self) -> None:
        self._pending.clear()
        self._batch = rocksdb.WriteBatch() if _ROCKSDB_AVAILABLE else None

    def close(self) -> None:
        pass

    # --- Query helpers -------------------------------------------------------

    def scalars(self, stmt: "_RocksStmt") -> "_ScalarsResult":
        return _ScalarsResult(stmt.execute(self._db))

    def get(self, model_class: type, pk: Any) -> Optional[Any]:
        return self._db._get_by_pk(model_class, pk)


class _ScalarsResult:
    def __init__(self, rows: List[Any]) -> None:
        self._rows = rows

    def first(self) -> Optional[Any]:
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


class _RocksStmt:
    """Thin query builder. Not a full ORM; covers only patterns used by services."""
    def __init__(self, model_class: type) -> None:
        self._model = model_class
        self._filters: List[tuple] = []
        self._order_by: List[tuple] = []
        self._limit_: Optional[int] = None

    def where(self, condition: "_Condition") -> "_RocksStmt":
        self._filters.append(condition)
        return self

    def order_by(self, order: "_Order") -> "_RocksStmt":
        self._order_by.append(order)
        return self

    def limit(self, n: int) -> "_RocksStmt":
        self._limit_ = n
        return self

    def execute(self, db: "RocksDBDatabase") -> List[Any]:
        return db._execute_query(self._model, self._filters,
                                  self._order_by, self._limit_)


# ---------------------------------------------------------------------------
# ORM-like model shims — match attribute names used by services
# ---------------------------------------------------------------------------

class _ORMBase:
    """Base for lightweight RocksDB-backed model objects."""
    pass


class AgentRunRow(_ORMBase):
    __tablename__ = "runs"
    __pk__        = "run_id"

    def __init__(self, **kw: Any) -> None:
        self.run_id          = kw.get("run_id", "")
        self.org_id          = kw.get("org_id", "")
        self.project_id      = kw.get("project_id", "")
        self.environment     = kw.get("environment", "dev")
        self.agent_id        = kw.get("agent_id", "")
        self.agent_version   = kw.get("agent_version")
        self.status          = kw.get("status", "running")
        self.metadata_json   = kw.get("metadata_json", {})
        self.started_at      = kw.get("started_at")
        self.ended_at        = kw.get("ended_at")
        self.last_event_hash = kw.get("last_event_hash")
        self.event_count     = kw.get("event_count", 0)

    def to_dict(self) -> dict:
        import datetime
        def _iso(v: Any) -> Optional[str]:
            if v is None: return None
            if isinstance(v, datetime.datetime): return v.isoformat()
            return str(v)
        return {
            "run_id": self.run_id, "org_id": self.org_id,
            "project_id": self.project_id, "environment": self.environment,
            "agent_id": self.agent_id, "agent_version": self.agent_version,
            "status": self.status, "metadata_json": self.metadata_json,
            "started_at": _iso(self.started_at), "ended_at": _iso(self.ended_at),
            "last_event_hash": self.last_event_hash, "event_count": self.event_count,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AgentRunRow":
        import datetime
        def _dt(v: Any) -> Optional[datetime.datetime]:
            if v is None: return None
            if isinstance(v, datetime.datetime): return v
            return datetime.datetime.fromisoformat(str(v))
        obj = cls(**{k: v for k, v in d.items()
                     if k not in ("started_at", "ended_at")})
        obj.started_at = _dt(d.get("started_at"))
        obj.ended_at   = _dt(d.get("ended_at"))
        return obj


class AuditEventRow(_ORMBase):
    __tablename__ = "events"
    __pk__        = "sequence"

    def __init__(self, **kw: Any) -> None:
        self.sequence             = kw.get("sequence", 0)
        self.event_id             = kw.get("event_id", "")
        self.org_id               = kw.get("org_id", "")
        self.project_id           = kw.get("project_id", "")
        self.environment          = kw.get("environment", "")
        self.run_id               = kw.get("run_id", "")
        self.step_id              = kw.get("step_id")
        self.step_index           = kw.get("step_index")
        self.event_type           = kw.get("event_type", "")
        self.schema_version       = kw.get("schema_version", "2026-05-01")
        self.actor_type           = kw.get("actor_type")
        self.actor_id             = kw.get("actor_id")
        self.trace_id             = kw.get("trace_id")
        self.span_id              = kw.get("span_id")
        self.parent_span_id       = kw.get("parent_span_id")
        self.payload_mode         = kw.get("payload_mode", "metadata_only")
        self.payload              = kw.get("payload")
        self.payload_hash         = kw.get("payload_hash")
        self.previous_event_hash  = kw.get("previous_event_hash")
        self.event_hash           = kw.get("event_hash", "")
        self.occurred_at          = kw.get("occurred_at")
        self.ingested_at          = kw.get("ingested_at")
        self.idempotency_key      = kw.get("idempotency_key")

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: dict) -> "AuditEventRow":
        return cls(**d)


class ApprovalRow(_ORMBase):
    __tablename__ = "approvals"
    __pk__        = "approval_id"

    def __init__(self, **kw: Any) -> None:
        self.approval_id   = kw.get("approval_id", "")
        self.run_id        = kw.get("run_id", "")
        self.org_id        = kw.get("org_id", "")
        self.project_id    = kw.get("project_id", "")
        self.policy_id     = kw.get("policy_id")
        self.requested_by  = kw.get("requested_by")
        self.reviewer_id   = kw.get("reviewer_id")
        self.status        = kw.get("status", "requested")
        self.reason        = kw.get("reason")
        self.input_json    = kw.get("input_json")
        self.created_at    = kw.get("created_at")
        self.decided_at    = kw.get("decided_at")

    def to_dict(self) -> dict: return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: dict) -> "ApprovalRow": return cls(**d)


# ---------------------------------------------------------------------------
# RocksDBDatabase — main class, mirrors Database from db.py
# ---------------------------------------------------------------------------

class RocksDBDatabase:
    """
    RocksDB-backed storage database.
    Exposes the same interface as the SQLAlchemy Database class:
      - session() context manager
    """

    _instance: Optional["RocksDBDatabase"] = None
    _lock = threading.Lock()

    def __init__(self, db_path: str) -> None:
        if not _ROCKSDB_AVAILABLE:
            raise ImportError(
                "python-rocksdb not installed. "
                "Run: pip install rocksdb  (requires RocksDB C library)"
            )
        self._db_path = db_path
        self._mu      = threading.Lock()
        self._seq     = 0
        self._wal     = _PythonWAL(db_path + "/wal")
        self._open()
        self._bootstrap()

    # -------------------------------------------------------------------------
    # Singleton (mirrors Database.instance())
    # -------------------------------------------------------------------------

    @classmethod
    def instance(cls, path: Optional[str] = None) -> "RocksDBDatabase":
        with cls._lock:
            if cls._instance is None:
                p = path or (SETTINGS.database_url.replace("rocksdb://", "")
                             .replace("rocksdb:///", ""))
                cls._instance = cls(p)
            return cls._instance

    @classmethod
    def reset(cls, path: Optional[str] = None) -> "RocksDBDatabase":
        with cls._lock:
            if cls._instance:
                cls._instance._wal.close()
            p = path or "./controldb_rocksdb"
            cls._instance = cls(p)
            return cls._instance

    # -------------------------------------------------------------------------
    # Open RocksDB
    # -------------------------------------------------------------------------

    def _open(self) -> None:
        Path(self._db_path).mkdir(parents=True, exist_ok=True)
        opts = rocksdb.Options()
        opts.create_if_missing              = True
        opts.create_missing_column_families = True

        cf_opts = rocksdb.ColumnFamilyOptions()
        cf_descs = [(rocksdb.default_column_family_name, cf_opts)] + \
                   [(cf, cf_opts) for cf in [
                       _CF_META, _CF_RUNS, _CF_RUNS_IDX, _CF_EVENTS,
                       _CF_EV_RUN, _CF_EV_TYPE, _CF_IDEM, _CF_APPROVALS,
                       _CF_EXPORTS, _CF_API_KEYS, _CF_ORGS, _CF_PROJECTS,
                   ]]

        self._rocksdb, cf_handles = rocksdb.DB.open_for_readonly(
            opts, self._db_path, cf_descs) if False else \
            rocksdb.DB(self._db_path, opts, column_families=cf_descs)

        self._cf: Dict[bytes, Any] = {}
        cf_names = [rocksdb.default_column_family_name,
                    _CF_META, _CF_RUNS, _CF_RUNS_IDX, _CF_EVENTS,
                    _CF_EV_RUN, _CF_EV_TYPE, _CF_IDEM, _CF_APPROVALS,
                    _CF_EXPORTS, _CF_API_KEYS, _CF_ORGS, _CF_PROJECTS]
        for name, handle in zip(cf_names, cf_handles):
            self._cf[name] = handle

        # Load global sequence
        v = self._get_raw(_CF_META, b"global_seq")
        if v:
            self._seq = struct.unpack(">q", v)[0]

    def _get_raw(self, cf_name: bytes, key: bytes) -> Optional[bytes]:
        return self._rocksdb.get((self._cf[cf_name], key))

    def _put_raw(self, batch: Any, cf_name: bytes, key: bytes, value: bytes) -> None:
        batch.put((self._cf[cf_name], key), value)

    # -------------------------------------------------------------------------
    # Bootstrap (org/project/api-key)
    # -------------------------------------------------------------------------

    def _bootstrap(self) -> None:
        org_id  = SETTINGS.bootstrap_org_id
        proj_id = SETTINGS.bootstrap_project_id
        if self._get_raw(_CF_ORGS, org_id.encode()):
            return

        batch = rocksdb.WriteBatch()
        self._put_raw(batch, _CF_ORGS, org_id.encode(),
                      json.dumps({"org_id": org_id, "name": "default",
                                  "created_at_ms": _now_ms()}).encode())
        self._put_raw(batch, _CF_PROJECTS, proj_id.encode(),
                      json.dumps({"project_id": proj_id, "org_id": org_id,
                                  "name": "default",
                                  "created_at_ms": _now_ms()}).encode())
        for raw_key in SETTINGS.bootstrap_api_keys:
            key_hash = "sha256:" + hashlib.sha256(raw_key.encode()).hexdigest()
            scopes   = {"runs": "write", "events": "write",
                        "policies": "read", "approvals": "review",
                        "exports": "create"}
            self._put_raw(batch, _CF_API_KEYS, key_hash.encode(),
                          json.dumps({"key_id": key_hash,
                                      "api_key_hash": key_hash,
                                      "org_id": org_id, "project_id": proj_id,
                                      "name": "bootstrap", "scopes": scopes,
                                      "created_at_ms": _now_ms(),
                                      "revoked_at_ms": 0}).encode())
        wo = rocksdb.WriteOptions(); wo.sync = True
        self._rocksdb.write(wo, batch)

    # -------------------------------------------------------------------------
    # Close
    # -------------------------------------------------------------------------

    def close(self) -> None:
        self._wal.close()
        try:
            self._rocksdb.close()
        except Exception:
            pass
        self._cf.clear()
        try:
            del self._rocksdb
        except Exception:
            pass
        import gc
        gc.collect()

    # Session context manager
    # -------------------------------------------------------------------------

    @contextmanager
    def session(self) -> Iterator[_RocksSession]:
        sess = _RocksSession(self)
        try:
            yield sess
            sess.commit()
        except Exception:
            sess.rollback()
            raise
        finally:
            sess.close()

    # -------------------------------------------------------------------------
    # Sequence
    # -------------------------------------------------------------------------

    def _next_seq(self) -> int:
        with self._mu:
            self._seq += 1
            return self._seq

    def _persist_seq(self, batch: Any) -> None:
        self._put_raw(batch, _CF_META, b"global_seq",
                      struct.pack(">q", self._seq))

    # -------------------------------------------------------------------------
    # Write objects
    # -------------------------------------------------------------------------

    def _write_obj(self, batch: Any, obj: _ORMBase) -> None:
        if isinstance(obj, AgentRunRow):
            self._write_run(batch, obj)
        elif isinstance(obj, AuditEventRow):
            self._write_event(batch, obj)
        elif isinstance(obj, ApprovalRow):
            self._write_approval(batch, obj)

    def _write_run(self, batch: Any, row: AgentRunRow) -> None:
        d = row.to_dict()
        self._put_raw(batch, _CF_RUNS, row.run_id.encode(),
                      json.dumps(d).encode())
        # Secondary index: org:proj:started_at_ms:run_id
        ts_ms = int(row.started_at.timestamp() * 1000) \
                if row.started_at else _now_ms()
        idx_key = (f"{row.org_id}:{row.project_id}:").encode() + \
                  _seq_key(ts_ms) + b":" + row.run_id.encode()
        self._put_raw(batch, _CF_RUNS_IDX, idx_key, row.run_id.encode())

    def _write_event(self, batch: Any, row: AuditEventRow) -> None:
        if row.sequence == 0:
            row.sequence = self._next_seq()
        d = row.to_dict()
        seq_b = _seq_key(row.sequence)
        self._put_raw(batch, _CF_EVENTS, seq_b,
                      json.dumps(d, default=str).encode())
        # Index: ev_by_run
        run_key = row.run_id.encode() + b":" + seq_b
        self._put_raw(batch, _CF_EV_RUN, run_key, b"")
        # Index: ev_by_type
        type_key = f"{row.org_id}:{row.project_id}:{row.event_type}:".encode() + seq_b
        self._put_raw(batch, _CF_EV_TYPE, type_key, b"")
        # Idempotency
        if row.idempotency_key:
            self._put_raw(batch, _CF_IDEM,
                          row.idempotency_key.encode(), row.event_id.encode())
            self._put_raw(batch, _CF_IDEM,
                          (row.idempotency_key + ":hash").encode(),
                          row.event_hash.encode())
        self._persist_seq(batch)

    def _write_approval(self, batch: Any, row: ApprovalRow) -> None:
        self._put_raw(batch, _CF_APPROVALS, row.approval_id.encode(),
                      json.dumps(row.to_dict(), default=str).encode())

    # -------------------------------------------------------------------------
    # Get by primary key
    # -------------------------------------------------------------------------

    def _get_by_pk(self, model_class: type, pk: Any) -> Optional[Any]:
        if model_class.__name__ == "AgentRun":
            v = self._get_raw(_CF_RUNS, str(pk).encode())
            if not v: return None
            return AgentRunRow.from_dict(json.loads(v))
        if model_class.__name__ == "Approval":
            v = self._get_raw(_CF_APPROVALS, str(pk).encode())
            if not v: return None
            return ApprovalRow.from_dict(json.loads(v))
        if model_class.__name__ == "ApiKey":
            v = self._get_raw(_CF_API_KEYS, str(pk).encode())
            if not v: return None
            return json.loads(v)
        return None

    # -------------------------------------------------------------------------
    # Query executor
    # -------------------------------------------------------------------------

    def _execute_query(self,
                       model_class: type,
                       filters: List[Any],
                       order_by: List[Any],
                       limit_: Optional[int]) -> List[Any]:
        # Build filter dict
        fmap: Dict[str, Any] = {}
        for f in filters:
            fmap.update(f.as_dict())

        result: List[Any] = []

        if model_class.__name__ == "AuditEvent":
            result = self._query_events(fmap, limit_ or 1000)
        elif model_class.__name__ == "AgentRun":
            result = self._query_runs(fmap, limit_ or 50)
        elif model_class.__name__ == "Approval":
            result = self._query_approvals(fmap, limit_ or 500)
        elif model_class.__name__ == "EvidenceExport":
            result = self._query_exports(fmap, limit_ or 50)

        # Apply order_by (simple: by attribute name)
        for o in reversed(order_by):
            attr, asc = o
            result.sort(key=lambda x: getattr(x, attr, 0) or 0,
                        reverse=(not asc))

        return result[:limit_] if limit_ else result

    def _query_events(self, fmap: dict, limit: int) -> List[AuditEventRow]:
        run_id      = fmap.get("run_id")
        org_id      = fmap.get("org_id")
        project_id  = fmap.get("project_id")
        event_types = fmap.get("event_type_in")
        cursor      = fmap.get("sequence_gt", 0)

        rows: List[AuditEventRow] = []

        if run_id:
            # Fast path: use ev_by_run index
            prefix = run_id.encode() + b":"
            it = self._rocksdb.iteritems(self._cf[_CF_EV_RUN])
            if cursor:
                it.seek(prefix + _seq_key(cursor + 1))
            else:
                it.seek(prefix)
            for k, _ in it:
                if not k.startswith(prefix):
                    break
                seq = _key_seq(k[len(prefix):len(prefix)+8])
                v = self._get_raw(_CF_EVENTS, _seq_key(seq))
                if not v:
                    continue
                ev = AuditEventRow.from_dict(json.loads(v))
                if event_types and ev.event_type not in event_types:
                    continue
                rows.append(ev)
                if len(rows) >= limit:
                    break
        else:
            # Scan ev_by_type or full events
            prefix = f"{org_id}:{project_id}:".encode()
            it = self._rocksdb.iteritems(self._cf[_CF_EV_TYPE])
            it.seek(prefix)
            for k, _ in it:
                if not k.startswith(prefix):
                    break
                parts = k.split(b":")
                if len(parts) < 4:
                    continue
                seq = _key_seq(k[-8:])
                if cursor and seq <= cursor:
                    continue
                v = self._get_raw(_CF_EVENTS, _seq_key(seq))
                if not v:
                    continue
                ev = AuditEventRow.from_dict(json.loads(v))
                if event_types and ev.event_type not in event_types:
                    continue
                rows.append(ev)
                if len(rows) >= limit:
                    break

        return rows

    def _query_runs(self, fmap: dict, limit: int) -> List[AgentRunRow]:
        org_id     = fmap.get("org_id")
        project_id = fmap.get("project_id")
        prefix = f"{org_id}:{project_id}:".encode()

        it = self._rocksdb.itervalues(self._cf[_CF_RUNS_IDX])
        it.seek_to_last()

        rows: List[AgentRunRow] = []
        # Reverse iteration for newest-first
        for run_id_b in it:
            if len(rows) >= limit:
                break
            v = self._get_raw(_CF_RUNS, run_id_b)
            if not v:
                continue
            row = AgentRunRow.from_dict(json.loads(v))
            if row.org_id == org_id and row.project_id == project_id:
                rows.append(row)
        return rows

    def _query_approvals(self, fmap: dict, limit: int) -> List[ApprovalRow]:
        org_id     = fmap.get("org_id")
        project_id = fmap.get("project_id")
        status     = fmap.get("status")

        it = self._rocksdb.itervalues(self._cf[_CF_APPROVALS])
        it.seek_to_first()
        rows: List[ApprovalRow] = []
        for v in it:
            if len(rows) >= limit:
                break
            row = ApprovalRow.from_dict(json.loads(v))
            if org_id    and row.org_id    != org_id:    continue
            if project_id and row.project_id != project_id: continue
            if status     and row.status     != status:   continue
            rows.append(row)
        return rows

    def _query_exports(self, fmap: dict, limit: int) -> List[Any]:
        org_id     = fmap.get("org_id")
        project_id = fmap.get("project_id")
        it = self._rocksdb.itervalues(self._cf[_CF_EXPORTS])
        it.seek_to_first()
        rows = []
        for v in it:
            if len(rows) >= limit:
                break
            d = json.loads(v)
            if d.get("org_id") == org_id and d.get("project_id") == project_id:
                rows.append(d)
        return rows

    # -------------------------------------------------------------------------
    # Idempotency check (used by IngestService)
    # -------------------------------------------------------------------------

    def get_event_by_idempotency_key(self, key: str) -> Optional[AuditEventRow]:
        event_id_b = self._get_raw(_CF_IDEM, key.encode())
        if not event_id_b:
            return None
        # Find by event_id via full scan (small volume in practice)
        # In production, add event_id → sequence index
        it = self._rocksdb.itervalues(self._cf[_CF_EVENTS])
        it.seek_to_first()
        event_id = event_id_b.decode()
        for v in it:
            d = json.loads(v)
            if d.get("event_id") == event_id:
                return AuditEventRow.from_dict(d)
        return None


# ---------------------------------------------------------------------------
# Query condition helpers (mimic SQLAlchemy column operators)
# ---------------------------------------------------------------------------

class _Condition:
    def __init__(self, key: str, value: Any) -> None:
        self._key   = key
        self._value = value

    def as_dict(self) -> dict:
        return {self._key: self._value}


def _make_select():
    """Factory for select() compatible with RocksDB session.scalars()."""
    def select(model_class: type) -> _RocksStmt:
        return _RocksStmt(model_class)
    return select
