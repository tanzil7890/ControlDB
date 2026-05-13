# Architecture Overview

```
Agent app  ──►  ControlDB SDK  ──►  Collector REST API  ──►  Event Store (SQLite/Postgres)
                                       │                        │
                                       ├── Redaction            └── Hash chain
                                       ├── Policy engine
                                       ├── Approvals
                                       └── Evidence export ──► Object storage (local FS / S3)
```

## Components

| Component | Path | Notes |
|---|---|---|
| Python SDK | `sdk/python/controldb/` | Run context, redaction, hash chain, idempotency, batching, retries, offline spool |
| Collector | `services/collector/controldb_collector/` | FastAPI app, RBAC, policy, approvals, replay, export, dashboard |
| Storage | `services/collector/controldb_collector/storage/` | SQLAlchemy 2.x; SQLite for dev, Postgres for prod |
| Policy | `services/collector/controldb_collector/policy/` | YAML rules; optional OPA forwarder |
| Object store | `controldb_artifacts/` (local), S3/MinIO (prod) | Evidence bundles |
| Dashboard | `services/collector/controldb_collector/dashboard/` | Minimal HTML; Next.js scaffold in `web/dashboard/` |

## Data flow for one event

1. Agent calls `run.tool_call("name")` (SDK).
2. SDK builds envelope, applies redaction, computes `payload_hash` and
   `event_hash` chained from the previous hash, attaches an idempotency key.
3. SDK batches and POSTs to `/v1/runs/{run_id}/events`.
4. Collector authenticates, validates event_type + schema_version, applies
   server-side redaction, recomputes `event_hash`, inserts into
   `audit_events` (unique idempotency_key).
5. Collector updates `agent_runs.last_event_hash` and `event_count`.
6. OpenTelemetry-compatible metrics incremented.
