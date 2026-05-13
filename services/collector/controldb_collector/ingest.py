"""Event ingestion + run lifecycle service layer."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .auth import Principal
from .config import SETTINGS
from .hashing import compute_event_hash, compute_payload_hash
from .redaction import CollectorRedactionRule, apply_collector_redaction
from .storage import AgentRun, AuditEvent, Database
from .telemetry import METRICS


_LOG = logging.getLogger("controldb.ingest")

EVENT_TYPES = {
    "agent_run.started",
    "agent_run.committed",
    "agent_run.rolled_back",
    "agent_run.failed",
    "agent_run.requires_approval",
    "model_call.started",
    "model_call.completed",
    "model_call.failed",
    "tool_call.started",
    "tool_call.completed",
    "tool_call.failed",
    "memory.read",
    "memory.write",
    "memory.delete_requested",
    "memory.redacted",
    "state.snapshot.created",
    "state.change.proposed",
    "state.change.applied",
    "state.change.reverted",
    "policy.check.started",
    "policy.check.passed",
    "policy.check.failed",
    "policy.enforcement.blocked",
    "policy.enforcement.allowed",
    "approval.requested",
    "approval.approved",
    "approval.rejected",
    "approval.expired",
    "approval.overridden",
    "evidence.artifact.created",
    "evidence.export.started",
    "evidence.export.completed",
    "evidence.export.failed",
    "auth.api_key.created",
    "auth.api_key.revoked",
    "user.login",
    "user.permission.changed",
    "audit_log.exported",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utc_iso(value: Optional[datetime]) -> Optional[str]:
    """Return an ISO 8601 string with UTC offset, attaching tz when SQLite drops it."""

    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _parse_iso(value: Optional[str]) -> datetime:
    if not value:
        return _utcnow()
    cleaned = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(cleaned)
    except ValueError:
        return _utcnow()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class IngestError(Exception):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


class IngestService:
    """Validation + storage + hashing for incoming events."""

    def __init__(self, db: Database, redaction_rules: Optional[Iterable[CollectorRedactionRule]] = None):
        self.db = db
        self.redaction_rules = list(redaction_rules or [])

    # ---- runs ----

    def start_run(
        self,
        principal: Principal,
        *,
        run_id: Optional[str],
        agent_id: str,
        agent_version: Optional[str],
        environment: str,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not agent_id:
            raise IngestError("agent_id is required")
        from .ids import generate_run_id

        resolved_run_id = run_id or generate_run_id()
        with self.db.session() as session:
            existing = session.get(AgentRun, resolved_run_id)
            if existing:
                return {
                    "run_id": existing.run_id,
                    "status": existing.status,
                    "already_started": True,
                }
            row = AgentRun(
                run_id=resolved_run_id,
                org_id=principal.org_id,
                project_id=principal.project_id,
                environment=environment,
                agent_id=agent_id,
                agent_version=agent_version,
                status="running",
                metadata_json=dict(metadata or {}),
            )
            session.add(row)
            METRICS.inc("controldb.runs.started")
        return {"run_id": resolved_run_id, "status": "running"}

    def commit_run(self, principal: Principal, run_id: str) -> Dict[str, Any]:
        with self.db.session() as session:
            run = self._authorize_run(session, principal, run_id)
            run.status = "committed"
            run.ended_at = _utcnow()
            METRICS.inc("controldb.runs.committed")
            return {"run_id": run.run_id, "status": run.status}

    def rollback_run(self, principal: Principal, run_id: str, reason: Optional[str] = None) -> Dict[str, Any]:
        with self.db.session() as session:
            run = self._authorize_run(session, principal, run_id)
            run.status = "rolled_back"
            run.ended_at = _utcnow()
            if reason:
                run.metadata_json = {**(run.metadata_json or {}), "rollback_reason": reason}
        return {"run_id": run_id, "status": "rolled_back", "reason": reason}

    def get_run(self, principal: Principal, run_id: str) -> Dict[str, Any]:
        with self.db.session() as session:
            run = self._authorize_run(session, principal, run_id)
            return _run_to_dict(run)

    def _authorize_run(self, session, principal: Principal, run_id: str) -> AgentRun:
        run = session.get(AgentRun, run_id)
        if not run:
            raise IngestError("run not found", status_code=404)
        if run.org_id != principal.org_id or run.project_id != principal.project_id:
            raise IngestError("forbidden", status_code=403)
        return run

    # ---- events ----

    def append_events(
        self,
        principal: Principal,
        run_id: str,
        events: Sequence[Mapping[str, Any]],
        batch_idempotency_key: Optional[str] = None,
    ) -> List[str]:
        if not events:
            return []
        start = time.perf_counter()
        accepted_ids: List[str] = []
        with self.db.session() as session:
            run = self._authorize_run(session, principal, run_id)
            previous_hash = run.last_event_hash
            for event in events:
                stored_id, previous_hash = self._persist_event(
                    session,
                    principal,
                    run,
                    event,
                    previous_hash=previous_hash,
                )
                accepted_ids.append(stored_id)
            run.last_event_hash = previous_hash
            run.event_count += len(accepted_ids)
        METRICS.inc("controldb.events.ingested", by=float(len(accepted_ids)))
        METRICS.observe("controldb.ingestion.latency_ms", (time.perf_counter() - start) * 1000.0)
        return accepted_ids

    def _persist_event(
        self,
        session,
        principal: Principal,
        run: AgentRun,
        event: Mapping[str, Any],
        previous_hash: Optional[str],
    ) -> Tuple[str, str]:
        event_type = event.get("event_type")
        if event_type not in EVENT_TYPES:
            raise IngestError(f"unknown event_type: {event_type}")
        schema_version = event.get("schema_version") or SETTINGS.schema_version
        idempotency_key = event.get("idempotency_key")

        if idempotency_key:
            existing = session.scalars(
                select(AuditEvent).where(AuditEvent.idempotency_key == idempotency_key)
            ).first()
            if existing:
                return existing.event_id, existing.event_hash

        payload = event.get("payload")
        payload_mode = event.get("payload_mode", "metadata_only")
        if payload is not None and self.redaction_rules:
            payload = apply_collector_redaction(payload, self.redaction_rules)
        payload_hash = event.get("payload_hash") or (compute_payload_hash(payload) if payload is not None else None)

        actor = event.get("actor") or {"type": "agent", "id": run.agent_id}

        occurred_at = _parse_iso(event.get("occurred_at"))
        ingested_at = _utcnow()

        envelope = {
            "event_id": event.get("event_id"),
            "schema_version": schema_version,
            "event_type": event_type,
            "org_id": principal.org_id,
            "project_id": principal.project_id,
            "environment": run.environment,
            "agent_id": run.agent_id,
            "agent_version": run.agent_version,
            "run_id": run.run_id,
            "step_id": event.get("step_id"),
            "step_index": event.get("step_index"),
            "trace_id": event.get("trace_id"),
            "span_id": event.get("span_id"),
            "parent_span_id": event.get("parent_span_id"),
            "actor": actor,
            "payload_mode": payload_mode,
            "payload": payload,
            "payload_hash": payload_hash,
            "previous_event_hash": previous_hash,
            "occurred_at": occurred_at.isoformat(),
            "ingested_at": ingested_at.isoformat(),
            "idempotency_key": idempotency_key,
        }
        if not envelope["event_id"]:
            from .ids import generate_event_id

            envelope["event_id"] = generate_event_id()

        # Recompute event_hash server-side to bind to the canonical envelope.
        event_hash = compute_event_hash(envelope)

        row = AuditEvent(
            event_id=envelope["event_id"],
            org_id=principal.org_id,
            project_id=principal.project_id,
            environment=run.environment,
            run_id=run.run_id,
            step_id=envelope["step_id"],
            step_index=envelope["step_index"],
            event_type=event_type,
            schema_version=schema_version,
            actor_type=actor.get("type"),
            actor_id=actor.get("id"),
            trace_id=envelope["trace_id"],
            span_id=envelope["span_id"],
            parent_span_id=envelope["parent_span_id"],
            payload_mode=payload_mode,
            payload=payload,
            payload_hash=payload_hash,
            previous_event_hash=previous_hash,
            event_hash=event_hash,
            occurred_at=occurred_at,
            ingested_at=ingested_at,
            idempotency_key=idempotency_key,
        )
        try:
            session.add(row)
            session.flush()
        except IntegrityError:
            session.rollback()
            existing = session.scalars(
                select(AuditEvent).where(AuditEvent.event_id == envelope["event_id"])
            ).first()
            if existing:
                return existing.event_id, existing.event_hash
            raise

        if event_type == "agent_run.started":
            run.status = "running"
        elif event_type == "agent_run.requires_approval":
            run.status = "requires_approval"
        elif event_type == "agent_run.committed":
            run.status = "committed"
            run.ended_at = ingested_at
        elif event_type == "agent_run.rolled_back":
            run.status = "rolled_back"
            run.ended_at = ingested_at
        elif event_type == "agent_run.failed":
            run.status = "failed"
            run.ended_at = ingested_at

        return row.event_id, event_hash

    # ---- queries ----

    def timeline(self, principal: Principal, run_id: str, limit: int = 1000, cursor: Optional[int] = None) -> Dict[str, Any]:
        with self.db.session() as session:
            run = self._authorize_run(session, principal, run_id)
            stmt = (
                select(AuditEvent)
                .where(AuditEvent.run_id == run_id)
                .order_by(AuditEvent.sequence.asc())
            )
            if cursor:
                stmt = stmt.where(AuditEvent.sequence > cursor)
            stmt = stmt.limit(min(limit, 5000))
            rows = list(session.scalars(stmt))
            events = [_event_to_dict(row) for row in rows]
            next_cursor = rows[-1].sequence if rows and len(rows) == limit else None
            return {
                "run": _run_to_dict(run),
                "events": events,
                "next_cursor": next_cursor,
            }

    def verify(self, principal: Principal, run_id: str) -> Dict[str, Any]:
        with self.db.session() as session:
            run = self._authorize_run(session, principal, run_id)
            rows = list(session.scalars(
                select(AuditEvent).where(AuditEvent.run_id == run_id).order_by(AuditEvent.sequence.asc())
            ))
            previous: Optional[str] = None
            broken_at = None
            for row in rows:
                if row.previous_event_hash != previous:
                    broken_at = row.event_id
                    break
                envelope = {
                    "event_id": row.event_id,
                    "schema_version": row.schema_version,
                    "event_type": row.event_type,
                    "org_id": row.org_id,
                    "project_id": row.project_id,
                    "environment": row.environment,
                    "agent_id": run.agent_id,
                    "agent_version": run.agent_version,
                    "run_id": row.run_id,
                    "step_id": row.step_id,
                    "step_index": row.step_index,
                    "trace_id": row.trace_id,
                    "span_id": row.span_id,
                    "parent_span_id": row.parent_span_id,
                    "actor": {"type": row.actor_type, "id": row.actor_id},
                    "payload_mode": row.payload_mode,
                    "payload": row.payload,
                    "payload_hash": row.payload_hash,
                    "previous_event_hash": row.previous_event_hash,
                    "occurred_at": _utc_iso(row.occurred_at),
                    "ingested_at": _utc_iso(row.ingested_at),
                    "idempotency_key": row.idempotency_key,
                }
                expected = compute_event_hash(envelope)
                if expected != row.event_hash:
                    broken_at = row.event_id
                    break
                previous = row.event_hash
            return {
                "run_id": run_id,
                "valid": broken_at is None,
                "event_count": len(rows),
                "broken_at_event_id": broken_at,
            }


def _run_to_dict(run: AgentRun) -> Dict[str, Any]:
    return {
        "run_id": run.run_id,
        "org_id": run.org_id,
        "project_id": run.project_id,
        "environment": run.environment,
        "agent_id": run.agent_id,
        "agent_version": run.agent_version,
        "status": run.status,
        "started_at": _utc_iso(run.started_at),
        "ended_at": _utc_iso(run.ended_at),
        "event_count": run.event_count,
        "last_event_hash": run.last_event_hash,
        "metadata": run.metadata_json or {},
    }


def _event_to_dict(row: AuditEvent) -> Dict[str, Any]:
    return {
        "event_id": row.event_id,
        "sequence": row.sequence,
        "run_id": row.run_id,
        "step_id": row.step_id,
        "step_index": row.step_index,
        "event_type": row.event_type,
        "schema_version": row.schema_version,
        "actor": {"type": row.actor_type, "id": row.actor_id},
        "trace_id": row.trace_id,
        "span_id": row.span_id,
        "parent_span_id": row.parent_span_id,
        "payload_mode": row.payload_mode,
        "payload": row.payload,
        "payload_hash": row.payload_hash,
        "previous_event_hash": row.previous_event_hash,
        "event_hash": row.event_hash,
        "occurred_at": _utc_iso(row.occurred_at),
        "ingested_at": _utc_iso(row.ingested_at),
        "idempotency_key": row.idempotency_key,
    }
