"""Approval workflow service layer."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from .auth import Principal
from .ids import generate_approval_id
from .storage import Approval, Database
from .telemetry import METRICS


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ApprovalsService:
    def __init__(self, db: Database):
        self.db = db

    def request(self, principal: Principal, body: Dict[str, Any]) -> Dict[str, Any]:
        approval_id = body.get("approval_id") or generate_approval_id()
        with self.db.session() as session:
            existing = session.get(Approval, approval_id)
            if existing:
                return _to_dict(existing)
            row = Approval(
                approval_id=approval_id,
                run_id=body.get("run_id", ""),
                org_id=principal.org_id,
                project_id=principal.project_id,
                policy_id=body.get("policy_id"),
                requested_by=body.get("requested_by"),
                reason=body.get("reason"),
                input_json=body.get("input"),
                status="requested",
            )
            session.add(row)
            METRICS.inc("controldb.approvals.requested")
            return _to_dict(row)

    def decide(self, principal: Principal, approval_id: str, decision: str, reviewer_id: str, reason: Optional[str] = None) -> Dict[str, Any]:
        if decision not in {"approved", "rejected", "expired", "overridden"}:
            raise ValueError(f"invalid decision: {decision}")
        with self.db.session() as session:
            row = session.get(Approval, approval_id)
            if not row:
                raise LookupError("approval not found")
            if row.org_id != principal.org_id or row.project_id != principal.project_id:
                raise PermissionError("forbidden")
            row.status = decision
            row.reviewer_id = reviewer_id
            row.decided_at = _now()
            if reason:
                row.reason = reason
            return _to_dict(row)

    def pending(self, principal: Principal) -> List[Dict[str, Any]]:
        with self.db.session() as session:
            rows = list(session.scalars(
                select(Approval)
                .where(Approval.org_id == principal.org_id)
                .where(Approval.project_id == principal.project_id)
                .where(Approval.status == "requested")
                .order_by(Approval.created_at.asc())
            ))
            return [_to_dict(r) for r in rows]


def _to_dict(row: Approval) -> Dict[str, Any]:
    return {
        "approval_id": row.approval_id,
        "run_id": row.run_id,
        "status": row.status,
        "policy_id": row.policy_id,
        "requested_by": row.requested_by,
        "reviewer_id": row.reviewer_id,
        "reason": row.reason,
        "input": row.input_json,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
    }
