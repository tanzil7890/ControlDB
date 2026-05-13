"""Event envelope model and validated constants."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional


SCHEMA_VERSION = "2026-05-01"


class PayloadMode(str, Enum):
    METADATA_ONLY = "metadata_only"
    HASH_ONLY = "hash_only"
    REDACTED_PAYLOAD = "redacted_payload"
    FULL_PAYLOAD = "full_payload"
    SELF_HOSTED_PAYLOAD = "self_hosted_payload"


# Canonical event type catalog. Mirrored from specs/event-schema/v1/event_types.json.
EVENT_TYPES = frozenset(
    [
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
    ]
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass
class Actor:
    type: str
    id: str

    def to_dict(self) -> Dict[str, Any]:
        return {"type": self.type, "id": self.id}


@dataclass
class EventEnvelope:
    event_id: str
    schema_version: str
    event_type: str
    org_id: str
    project_id: str
    environment: str
    agent_id: str
    run_id: str
    actor: Actor
    payload_mode: str
    event_hash: str
    occurred_at: str
    ingested_at: str
    agent_version: Optional[str] = None
    step_id: Optional[str] = None
    step_index: Optional[int] = None
    trace_id: Optional[str] = None
    span_id: Optional[str] = None
    parent_span_id: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None
    payload_hash: Optional[str] = None
    previous_event_hash: Optional[str] = None
    idempotency_key: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["actor"] = self.actor.to_dict()
        return d
