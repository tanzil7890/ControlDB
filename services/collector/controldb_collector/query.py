"""Structured query API.

Implements a small whitelist of read-only filters over audit events. The full
SQL console comes later; this is enough for dashboards and pilots.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy import select

from .auth import Principal
from .ingest import _event_to_dict
from .storage import AuditEvent, Database


class QueryService:
    def __init__(self, db: Database):
        self.db = db

    def query(self, principal: Principal, body: Dict[str, Any]) -> Dict[str, Any]:
        event_types: Optional[List[str]] = body.get("event_types")
        run_id: Optional[str] = body.get("run_id")
        limit: int = int(body.get("limit", 200))
        cursor: Optional[int] = body.get("cursor")

        stmt = (
            select(AuditEvent)
            .where(AuditEvent.org_id == principal.org_id)
            .where(AuditEvent.project_id == principal.project_id)
            .order_by(AuditEvent.sequence.asc())
        )
        if event_types:
            stmt = stmt.where(AuditEvent.event_type.in_(event_types))
        if run_id:
            stmt = stmt.where(AuditEvent.run_id == run_id)
        if cursor:
            stmt = stmt.where(AuditEvent.sequence > int(cursor))
        stmt = stmt.limit(min(max(limit, 1), 1000))

        with self.db.session() as session:
            rows = list(session.scalars(stmt))
            events = [_event_to_dict(r) for r in rows]
            next_cursor = rows[-1].sequence if rows and len(rows) == limit else None
            return {"events": events, "next_cursor": next_cursor}
