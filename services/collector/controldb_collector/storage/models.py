"""SQLAlchemy 2.x ORM models.

Schema mirrors section 11 of the implementation guide. JSON columns use the
portable ``JSON`` type so the same schema works on SQLite (dev) and Postgres
(prod).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Organization(Base):
    __tablename__ = "organizations"
    org_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Project(Base):
    __tablename__ = "projects"
    project_id: Mapped[str] = mapped_column(String, primary_key=True)
    org_id: Mapped[str] = mapped_column(String, ForeignKey("organizations.org_id"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ApiKey(Base):
    __tablename__ = "api_keys"
    key_id: Mapped[str] = mapped_column(String, primary_key=True)
    api_key_hash: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    org_id: Mapped[str] = mapped_column(String, nullable=False)
    project_id: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, default="default")
    scopes: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    run_id: Mapped[str] = mapped_column(String, primary_key=True)
    org_id: Mapped[str] = mapped_column(String, nullable=False)
    project_id: Mapped[str] = mapped_column(String, nullable=False)
    environment: Mapped[str] = mapped_column(String, nullable=False)
    agent_id: Mapped[str] = mapped_column(String, nullable=False)
    agent_version: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="running")
    metadata_json: Mapped[Dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_event_hash: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    event_count: Mapped[int] = mapped_column(Integer, default=0)

    events = relationship("AuditEvent", back_populates="run", cascade="all,delete-orphan", order_by="AuditEvent.sequence")


class AuditEvent(Base):
    __tablename__ = "audit_events"
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    org_id: Mapped[str] = mapped_column(String, nullable=False)
    project_id: Mapped[str] = mapped_column(String, nullable=False)
    environment: Mapped[str] = mapped_column(String, nullable=False)
    run_id: Mapped[str] = mapped_column(String, ForeignKey("agent_runs.run_id"), nullable=False)
    step_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    step_index: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    schema_version: Mapped[str] = mapped_column(String, nullable=False)
    actor_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    actor_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    trace_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    span_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    parent_span_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    payload_mode: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    payload_hash: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    previous_event_hash: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    event_hash: Mapped[str] = mapped_column(String, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String, nullable=True, unique=True)

    run = relationship("AgentRun", back_populates="events")

    __table_args__ = (
        Index("idx_audit_events_run_id", "run_id"),
        Index("idx_audit_events_event_type", "event_type"),
        Index("idx_audit_events_org_project_time", "org_id", "project_id", "occurred_at"),
    )


class Approval(Base):
    __tablename__ = "approvals"
    approval_id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(String, nullable=False)
    org_id: Mapped[str] = mapped_column(String, nullable=False)
    project_id: Mapped[str] = mapped_column(String, nullable=False)
    policy_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    requested_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    reviewer_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="requested")
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    input_json: Mapped[Optional[Dict[str, Any]]] = mapped_column("input", JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class PolicyRecord(Base):
    __tablename__ = "policies"
    policy_id: Mapped[str] = mapped_column(String, primary_key=True)
    policy_version: Mapped[str] = mapped_column(String, primary_key=True)
    policy_hash: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    body: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class EvidenceExport(Base):
    __tablename__ = "evidence_exports"
    export_id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(String, nullable=False)
    org_id: Mapped[str] = mapped_column(String, nullable=False)
    project_id: Mapped[str] = mapped_column(String, nullable=False)
    bundle_uri: Mapped[str] = mapped_column(String, nullable=False)
    bundle_hash: Mapped[str] = mapped_column(String, nullable=False)
    event_count: Mapped[int] = mapped_column(Integer, default=0)
    hash_chain_valid: Mapped[bool] = mapped_column(Boolean, default=True)
    generated_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class DashboardAuditLog(Base):
    __tablename__ = "dashboard_audit_log"
    log_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    org_id: Mapped[str] = mapped_column(String, nullable=False)
    actor_id: Mapped[str] = mapped_column(String, nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False)
    target: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    metadata_json: Mapped[Dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
