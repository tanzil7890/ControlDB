# Threat Model

| Asset | Threat | Control |
|---|---|---|
| Audit events | Tampering | Hash chain + `/v1/runs/{run_id}/verify` |
| API keys | Leakage / replay | SHA-256 hashed at rest; bearer over TLS; rotation via `auth.api_key.revoked` audit event |
| PII payloads | Disclosure | Five payload modes (`metadata_only`/`hash_only`/`redacted_payload`/`full_payload`/`self_hosted_payload`); regex + field redaction in SDK and collector |
| Cross-tenant access | Confused deputy | Every row carries `org_id` + `project_id`; every query filters on principal |
| Evidence export | Tampering / leakage | Bundle SHA-256 stored; export itself recorded as `evidence.export.completed` event |
| Policy | Drift / unauditable | Every policy check stores `policy_id` + `policy_version` + `policy_hash` |
| Approval | Forgery | Approvals are first-class events; status transitions append new events (no in-place updates) |
