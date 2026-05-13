"""FastAPI application factory."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Body, Depends, FastAPI, HTTPException, Path as PathParam, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field

from .approvals import ApprovalsService
from .auth import Principal, current_principal, require_scope
from .config import SETTINGS
from .export import EvidenceService
from .ingest import EVENT_TYPES, IngestError, IngestService
from .policy import get_policy_engine
from .query import QueryService
from .storage import Database, get_db
from .telemetry import METRICS

_LOG = logging.getLogger("controldb.app")


class RunStartRequest(BaseModel):
    agent_id: str
    agent_version: Optional[str] = None
    environment: str = "dev"
    metadata: Dict[str, Any] = Field(default_factory=dict)
    run_id: Optional[str] = None
    org_id: Optional[str] = None
    project_id: Optional[str] = None


class EventInput(BaseModel):
    model_config = ConfigDict(extra="allow")
    event_id: Optional[str] = None
    event_type: str
    schema_version: Optional[str] = None
    payload_mode: str = "metadata_only"
    payload: Optional[Dict[str, Any]] = None
    payload_hash: Optional[str] = None
    previous_event_hash: Optional[str] = None
    event_hash: Optional[str] = None
    step_id: Optional[str] = None
    step_index: Optional[int] = None
    actor: Optional[Dict[str, Any]] = None
    occurred_at: Optional[str] = None
    ingested_at: Optional[str] = None
    trace_id: Optional[str] = None
    span_id: Optional[str] = None
    parent_span_id: Optional[str] = None
    idempotency_key: Optional[str] = None


class EventsRequest(BaseModel):
    events: List[EventInput]


class RollbackRequest(BaseModel):
    reason: Optional[str] = None


class PolicyCheckRequest(BaseModel):
    policy_id: str
    input: Dict[str, Any] = Field(default_factory=dict)
    run_id: Optional[str] = None


class ApprovalRequestModel(BaseModel):
    approval_id: Optional[str] = None
    run_id: str
    policy_id: Optional[str] = None
    requested_by: Optional[str] = None
    reason: Optional[str] = None
    input: Optional[Dict[str, Any]] = None


class ApprovalDecisionModel(BaseModel):
    reviewer_id: str
    reason: Optional[str] = None


class ExportRequest(BaseModel):
    run_id: str
    generated_by: Optional[str] = None


class QueryRequest(BaseModel):
    run_id: Optional[str] = None
    event_types: Optional[List[str]] = None
    limit: int = 200
    cursor: Optional[int] = None


def create_app(database: Optional[Database] = None) -> FastAPI:
    app = FastAPI(title="ControlDB Collector", version="0.1.0")
    db = database or get_db()
    ingest = IngestService(db)
    approvals = ApprovalsService(db)
    evidence = EvidenceService(db)
    queries = QueryService(db)
    policy_engine = get_policy_engine()

    @app.get("/healthz", tags=["meta"])
    async def healthz() -> Dict[str, Any]:
        return {"status": "ok", "schema_version": SETTINGS.schema_version}

    @app.get("/metrics", tags=["meta"])
    async def metrics() -> Dict[str, Any]:
        return METRICS.snapshot()

    @app.get("/v1/event-types", tags=["meta"])
    async def event_types() -> Dict[str, Any]:
        return {"schema_version": SETTINGS.schema_version, "event_types": sorted(EVENT_TYPES)}

    # ---- runs ----

    @app.post("/v1/runs/start", tags=["runs"])
    async def runs_start(
        body: RunStartRequest,
        principal: Principal = Depends(require_scope("runs", "write")),
    ) -> Dict[str, Any]:
        try:
            return ingest.start_run(
                principal,
                run_id=body.run_id,
                agent_id=body.agent_id,
                agent_version=body.agent_version,
                environment=body.environment,
                metadata=body.metadata,
            )
        except IngestError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc))

    @app.post("/v1/runs/{run_id}/events", tags=["events"])
    async def runs_events(
        run_id: str = PathParam(...),
        request: Request = None,  # type: ignore[assignment]
        body: EventsRequest = Body(...),
        principal: Principal = Depends(require_scope("events", "write")),
    ) -> Dict[str, Any]:
        try:
            ids = ingest.append_events(principal, run_id, [e.model_dump() for e in body.events])
            return {"event_ids": ids}
        except IngestError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc))

    @app.post("/v1/runs/{run_id}/commit", tags=["runs"])
    async def runs_commit(
        run_id: str = PathParam(...),
        principal: Principal = Depends(require_scope("runs", "write")),
    ) -> Dict[str, Any]:
        try:
            return ingest.commit_run(principal, run_id)
        except IngestError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc))

    @app.post("/v1/runs/{run_id}/rollback", tags=["runs"])
    async def runs_rollback(
        run_id: str = PathParam(...),
        body: RollbackRequest = Body(default=RollbackRequest()),
        principal: Principal = Depends(require_scope("runs", "write")),
    ) -> Dict[str, Any]:
        try:
            return ingest.rollback_run(principal, run_id, body.reason)
        except IngestError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc))

    @app.get("/v1/runs/{run_id}", tags=["runs"])
    async def runs_get(
        run_id: str = PathParam(...),
        principal: Principal = Depends(require_scope("runs", "read")),
    ) -> Dict[str, Any]:
        try:
            return ingest.get_run(principal, run_id)
        except IngestError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc))

    @app.get("/v1/runs", tags=["runs"])
    async def runs_list(
        limit: int = Query(50, ge=1, le=500),
        principal: Principal = Depends(require_scope("runs", "read")),
    ) -> Dict[str, Any]:
        from sqlalchemy import select

        from .storage import AgentRun

        with db.session() as session:
            stmt = (
                select(AgentRun)
                .where(AgentRun.org_id == principal.org_id)
                .where(AgentRun.project_id == principal.project_id)
                .order_by(AgentRun.started_at.desc())
                .limit(limit)
            )
            from .ingest import _run_to_dict

            return {"runs": [_run_to_dict(r) for r in session.scalars(stmt)]}

    @app.get("/v1/runs/{run_id}/timeline", tags=["events"])
    async def runs_timeline(
        run_id: str = PathParam(...),
        limit: int = Query(1000, ge=1, le=5000),
        cursor: Optional[int] = Query(None, ge=0),
        principal: Principal = Depends(require_scope("events", "read")),
    ) -> Dict[str, Any]:
        try:
            return ingest.timeline(principal, run_id, limit=limit, cursor=cursor)
        except IngestError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc))

    @app.get("/v1/runs/{run_id}/replay", tags=["events"])
    async def runs_replay(
        run_id: str = PathParam(...),
        principal: Principal = Depends(require_scope("events", "read")),
    ) -> Dict[str, Any]:
        try:
            timeline = ingest.timeline(principal, run_id)
            verification = ingest.verify(principal, run_id)
            return {"timeline": timeline, "verification": verification, "mode": "historical"}
        except IngestError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc))

    @app.get("/v1/runs/{run_id}/verify", tags=["events"])
    async def runs_verify(
        run_id: str = PathParam(...),
        principal: Principal = Depends(require_scope("events", "read")),
    ) -> Dict[str, Any]:
        try:
            return ingest.verify(principal, run_id)
        except IngestError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc))

    # ---- policy ----

    @app.post("/v1/policy/check", tags=["policy"])
    async def policy_check(
        body: PolicyCheckRequest,
        principal: Principal = Depends(require_scope("policies", "read")),
    ) -> Dict[str, Any]:
        decision = policy_engine.evaluate(body.policy_id, body.input)
        if decision.allowed and not decision.requires_approval:
            METRICS.inc("controldb.policy.allowed")
        else:
            METRICS.inc("controldb.policy.blocked")
        return {
            "policy_id": decision.policy_id,
            "policy_version": decision.policy_version,
            "policy_hash": decision.policy_hash,
            "result": {
                "allowed": decision.allowed,
                "requires_approval": decision.requires_approval,
                "reason": decision.reason,
                **decision.raw,
            },
        }

    @app.get("/v1/policies", tags=["policy"])
    async def policies_list(
        principal: Principal = Depends(require_scope("policies", "read")),
    ) -> Dict[str, Any]:
        return {"policies": policy_engine.list_policies()}

    # ---- approvals ----

    @app.post("/v1/approvals/request", tags=["approvals"])
    async def approvals_request(
        body: ApprovalRequestModel,
        principal: Principal = Depends(require_scope("approvals", "review")),
    ) -> Dict[str, Any]:
        return approvals.request(principal, body.model_dump())

    @app.post("/v1/approvals/{approval_id}/approve", tags=["approvals"])
    async def approvals_approve(
        approval_id: str = PathParam(...),
        body: ApprovalDecisionModel = Body(...),
        principal: Principal = Depends(require_scope("approvals", "review")),
    ) -> Dict[str, Any]:
        try:
            return approvals.decide(principal, approval_id, "approved", body.reviewer_id, body.reason)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc))

    @app.post("/v1/approvals/{approval_id}/reject", tags=["approvals"])
    async def approvals_reject(
        approval_id: str = PathParam(...),
        body: ApprovalDecisionModel = Body(...),
        principal: Principal = Depends(require_scope("approvals", "review")),
    ) -> Dict[str, Any]:
        try:
            return approvals.decide(principal, approval_id, "rejected", body.reviewer_id, body.reason)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @app.get("/v1/approvals/pending", tags=["approvals"])
    async def approvals_pending(
        principal: Principal = Depends(require_scope("approvals", "review")),
    ) -> Dict[str, Any]:
        return {"approvals": approvals.pending(principal)}

    # ---- evidence export ----

    @app.post("/v1/audit/export", tags=["evidence"])
    async def audit_export(
        body: ExportRequest,
        principal: Principal = Depends(require_scope("exports", "create")),
    ) -> Dict[str, Any]:
        try:
            return evidence.export_run(principal, body.run_id, generated_by=body.generated_by)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @app.get("/v1/audit/exports/{export_id}/download", tags=["evidence"])
    async def export_download(
        export_id: str = PathParam(...),
        principal: Principal = Depends(require_scope("exports", "create")),
    ) -> Any:
        from sqlalchemy import select

        from .storage import EvidenceExport

        with db.session() as session:
            row = session.scalars(select(EvidenceExport).where(EvidenceExport.export_id == export_id)).first()
            if not row:
                raise HTTPException(status_code=404, detail="export not found")
            if row.org_id != principal.org_id or row.project_id != principal.project_id:
                raise HTTPException(status_code=403, detail="forbidden")
            path = Path(row.bundle_uri)
            if not path.exists():
                raise HTTPException(status_code=410, detail="bundle missing on disk")
            return FileResponse(path, media_type="application/zip", filename=path.name)

    # ---- query ----

    @app.post("/v1/query", tags=["query"])
    async def query_events(
        body: QueryRequest,
        principal: Principal = Depends(require_scope("events", "read")),
    ) -> Dict[str, Any]:
        return queries.query(principal, body.model_dump())

    # ---- dashboard (HTML, served only when enabled) ----
    if SETTINGS.enable_dashboard:
        from .dashboard import register_dashboard

        register_dashboard(app, db, ingest, approvals, evidence)

    @app.exception_handler(IngestError)
    async def _ingest_error_handler(request: Request, exc: IngestError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    return app


app = create_app()
