"""Cross-org / cross-project isolation and auth scopes."""

from __future__ import annotations

import httpx
import hashlib

HEADERS = {"Authorization": "Bearer test-key"}


def _create_other_org_key(monkeypatch, collector_env):
    """Insert a separate org/project + api key into the bootstrap database."""

    from controldb_collector.storage import ApiKey, Organization, Project
    from controldb_collector.storage.db import Database

    db = Database.instance()
    with db.session() as session:
        session.add(Organization(org_id="org_other", name="Other"))
        session.add(Project(project_id="proj_other", org_id="org_other", name="Other Project"))
        session.add(
            ApiKey(
                key_id="key_other",
                api_key_hash=hashlib.sha256(b"other-key").hexdigest(),
                org_id="org_other",
                project_id="proj_other",
                name="other",
                scopes={"runs": "write", "events": "write", "policies": "read", "approvals": "review", "exports": "create"},
            )
        )


def test_other_org_cannot_read_run(http_client, monkeypatch, collector_env):
    start = http_client.post("/v1/runs/start", json={"agent_id": "agent-iso"}, headers=HEADERS).json()
    run_id = start["run_id"]
    _create_other_org_key(monkeypatch, collector_env)
    other_headers = {"Authorization": "Bearer other-key"}
    resp = http_client.get(f"/v1/runs/{run_id}/timeline", headers=other_headers)
    assert resp.status_code in (403, 404)


def test_invalid_api_key_unauthorized(http_client):
    resp = http_client.post("/v1/runs/start", json={"agent_id": "x"}, headers={"Authorization": "Bearer nope"})
    assert resp.status_code == 401


def test_unknown_event_type_rejected(http_client):
    start = http_client.post("/v1/runs/start", json={"agent_id": "agent-x"}, headers=HEADERS).json()
    run_id = start["run_id"]
    bad = http_client.post(
        f"/v1/runs/{run_id}/events",
        json={"events": [{"event_type": "evil.event", "payload_mode": "metadata_only", "step_index": 0, "occurred_at": "2026-05-13T12:00:00Z"}]},
        headers=HEADERS,
    )
    assert bad.status_code == 422
