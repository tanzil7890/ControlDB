# ControlDB

> **The system of record for regulated AI agent actions.**
> Replayable, queryable, tamper-evident audit trail — without replacing your stack.

ControlDB is an SDK + collector + dashboard that records every important
action an AI agent performs (prompts, model calls, tool calls, memory writes,
state changes, policy checks, human approvals, evidence exports). Events go
into an append-only hash-chained store with replay, policy enforcement,
human-in-the-loop approvals, and one-click evidence bundles.

## Quickstart (local, no Docker)

```bash
make install
make dev            # collector on http://localhost:8080
# in another shell
make run-example    # runs the AML agent through the SDK
```

Open `http://localhost:8080/?api_key=dev-key` for the minimal dashboard.

## Code paths

| Layer | Where |
|---|---|
| Python SDK | [sdk/python/controldb/](sdk/python/controldb/) |
| Event envelope schema | [specs/event-schema/v1/event_envelope.schema.json](specs/event-schema/v1/event_envelope.schema.json) |
| OpenAPI | [specs/openapi/controldb.openapi.yaml](specs/openapi/controldb.openapi.yaml) |
| FastAPI collector | [services/collector/controldb_collector/](services/collector/controldb_collector/) |
| Policy YAMLs | [deploy/policies/](deploy/policies/) |
| Example agents | [examples/](examples/) |
| Tests | [tests/](tests/) |

## Hash chain

Every event is appended with `event_hash = SHA256(canonical_json(envelope))`
that includes the `previous_event_hash`. Verify any run:

```bash
curl -H 'Authorization: Bearer dev-key' \
  http://localhost:8080/v1/runs/<run_id>/verify
```

## Evidence export

```bash
curl -X POST -H 'Authorization: Bearer dev-key' -H 'Content-Type: application/json' \
  -d '{"run_id": "<run_id>"}' http://localhost:8080/v1/audit/export
```

Bundle contains: `manifest.json`, `events.jsonl`, `events.csv`,
`state_diffs.json`, `policy_checks.json`, `approvals.json`, `run.json`,
`run_summary.md`, and a stable SHA-256 of the ZIP.

## Run the tests

```bash
make test
```

## Production deployment

`docker-compose up --build` boots Postgres + collector + MinIO + OpenTelemetry
collector. Helm + Terraform modules are tracked in
[deploy/](deploy/) (Phase 7 of the implementation guide).
