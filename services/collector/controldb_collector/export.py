"""Evidence export: JSON bundle, CSV, Markdown, ZIP."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from sqlalchemy import select

from .auth import Principal
from .config import SETTINGS
from .ids import generate_export_id
from .ingest import _event_to_dict, _run_to_dict
from .storage import AgentRun, AuditEvent, Approval, Database, EvidenceExport
from .telemetry import METRICS


class EvidenceService:
    def __init__(self, db: Database, object_store_dir: Optional[str] = None):
        self.db = db
        self.object_store_dir = Path(object_store_dir or SETTINGS.object_store_dir)
        self.object_store_dir.mkdir(parents=True, exist_ok=True)

    def export_run(self, principal: Principal, run_id: str, generated_by: Optional[str] = None) -> Dict[str, Any]:
        with self.db.session() as session:
            run = session.get(AgentRun, run_id)
            if not run or run.org_id != principal.org_id or run.project_id != principal.project_id:
                raise LookupError("run not found")
            event_rows = list(session.scalars(
                select(AuditEvent).where(AuditEvent.run_id == run_id).order_by(AuditEvent.sequence.asc())
            ))
            approvals = list(session.scalars(
                select(Approval).where(Approval.run_id == run_id)
            ))
            events = [_event_to_dict(e) for e in event_rows]
            run_dict = _run_to_dict(run)
        from .ingest import IngestService  # avoid cycle at import time

        # Verify chain integrity (use a fresh session)
        ingest = IngestService(self.db)
        verification = ingest.verify(principal, run_id)

        state_diffs = [
            {
                "event_id": e["event_id"],
                "step_id": e["step_id"],
                "payload": e["payload"],
            }
            for e in events
            if e["event_type"].startswith("state.")
        ]
        policy_checks = [e for e in events if e["event_type"].startswith("policy.")]
        approvals_list = [_approval_to_dict(a) for a in approvals]

        export_id = generate_export_id()
        bundle_filename = f"evidence_bundle_{run_id}.zip"
        bundle_path = self.object_store_dir / bundle_filename

        manifest = {
            "bundle_id": export_id,
            "run_id": run_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "generated_by": generated_by,
            "event_count": len(events),
            "hash_chain_valid": verification["valid"],
            "broken_at_event_id": verification.get("broken_at_event_id"),
            "artifact_count": sum(1 for e in events if e["event_type"] == "evidence.artifact.created"),
            "schema_version": SETTINGS.schema_version,
        }

        summary_md = _render_summary_markdown(run_dict, events, approvals_list, verification)
        events_csv = _render_events_csv(events)

        with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", json.dumps(manifest, indent=2))
            zf.writestr("run.json", json.dumps(run_dict, indent=2))
            zf.writestr("events.jsonl", "\n".join(json.dumps(e) for e in events))
            zf.writestr("events.csv", events_csv)
            zf.writestr("state_diffs.json", json.dumps(state_diffs, indent=2))
            zf.writestr("policy_checks.json", json.dumps(policy_checks, indent=2))
            zf.writestr("approvals.json", json.dumps(approvals_list, indent=2))
            zf.writestr("run_summary.md", summary_md)

        bundle_bytes = bundle_path.read_bytes()
        bundle_hash = "sha256:" + hashlib.sha256(bundle_bytes).hexdigest()

        with self.db.session() as session:
            row = EvidenceExport(
                export_id=export_id,
                run_id=run_id,
                org_id=principal.org_id,
                project_id=principal.project_id,
                bundle_uri=str(bundle_path),
                bundle_hash=bundle_hash,
                event_count=len(events),
                hash_chain_valid=verification["valid"],
                generated_by=generated_by,
            )
            session.add(row)
            METRICS.inc("controldb.exports.completed")

        # Record an audit event for the export itself.
        from .ingest import IngestService  # local import to avoid cycle
        from .ids import generate_event_id
        from .hashing import compute_event_hash

        ingest = IngestService(self.db)
        principal_for_event = principal
        export_event = {
            "event_id": generate_event_id(),
            "event_type": "evidence.export.completed",
            "schema_version": SETTINGS.schema_version,
            "payload_mode": "metadata_only",
            "payload": {
                "export_id": export_id,
                "bundle_uri": str(bundle_path),
                "bundle_hash": bundle_hash,
                "event_count": len(events),
                "hash_chain_valid": verification["valid"],
            },
        }
        try:
            ingest.append_events(principal_for_event, run_id, [export_event])
        except Exception:
            pass

        return {
            "export_id": export_id,
            "bundle_uri": str(bundle_path),
            "bundle_hash": bundle_hash,
            "event_count": len(events),
            "hash_chain_valid": verification["valid"],
        }


def _approval_to_dict(row: Approval) -> Dict[str, Any]:
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


def _render_events_csv(events: Iterable[Mapping[str, Any]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "sequence", "event_id", "occurred_at", "event_type", "step_id", "step_index", "actor_type", "actor_id",
        "payload_mode", "payload_hash", "previous_event_hash", "event_hash",
    ])
    for e in events:
        writer.writerow([
            e.get("sequence"), e.get("event_id"), e.get("occurred_at"), e.get("event_type"),
            e.get("step_id"), e.get("step_index"),
            (e.get("actor") or {}).get("type"), (e.get("actor") or {}).get("id"),
            e.get("payload_mode"), e.get("payload_hash"), e.get("previous_event_hash"), e.get("event_hash"),
        ])
    return buf.getvalue()


def _render_summary_markdown(run: Mapping[str, Any], events: List[Mapping[str, Any]], approvals: List[Mapping[str, Any]], verification: Mapping[str, Any]) -> str:
    lines = []
    lines.append(f"# Evidence Bundle — Run {run['run_id']}")
    lines.append("")
    lines.append(f"- Agent: `{run['agent_id']}` (`{run.get('agent_version') or 'unversioned'}`)")
    lines.append(f"- Environment: `{run['environment']}`")
    lines.append(f"- Status: `{run['status']}`")
    lines.append(f"- Started: `{run.get('started_at')}`")
    lines.append(f"- Ended: `{run.get('ended_at')}`")
    lines.append(f"- Events: **{len(events)}**")
    lines.append(f"- Hash chain valid: **{verification.get('valid')}**")
    if verification.get("broken_at_event_id"):
        lines.append(f"- Broken at event: `{verification['broken_at_event_id']}`")
    lines.append("")
    lines.append("## Timeline")
    for e in events:
        lines.append(f"- `{e['sequence']}` `{e['occurred_at']}` **{e['event_type']}** step={e.get('step_id')}")
    lines.append("")
    lines.append("## Approvals")
    if not approvals:
        lines.append("- (none)")
    else:
        for a in approvals:
            lines.append(f"- `{a['approval_id']}` — {a['status']} by `{a.get('reviewer_id')}` — {a.get('reason') or ''}")
    return "\n".join(lines) + "\n"
