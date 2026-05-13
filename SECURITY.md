# Security

If you discover a vulnerability please email security@controldb.dev (or open
a private GitHub Security Advisory). Do not file a public issue.

## Threat model summary

- Bearer API keys (SHA-256 hashed at rest) for SDK access.
- RBAC scopes per resource/action (see [services/collector/controldb_collector/auth.py](services/collector/controldb_collector/auth.py)).
- Tenant isolation: every audit event carries `org_id` + `project_id`. All
  queries filter on them.
- Append-only audit table; tampering is detected by `/v1/runs/{run_id}/verify`.
- Payload redaction runs in the SDK and the collector before persistence.
- TLS expected at the load balancer in production deployments.
