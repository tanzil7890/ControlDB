"""Direct HTTP tests for the collector REST API."""

from __future__ import annotations


HEADERS = {"Authorization": "Bearer test-key"}


def test_healthz(http_client):
    resp = http_client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_unauthenticated_request_rejected(http_client):
    resp = http_client.post("/v1/runs/start", json={"agent_id": "x"})
    assert resp.status_code == 401


def test_run_lifecycle_via_http(http_client):
    start = http_client.post("/v1/runs/start", json={"agent_id": "agent-x"}, headers=HEADERS)
    assert start.status_code == 200, start.text
    run_id = start.json()["run_id"]

    events = [
        {
            "event_type": "agent_run.started",
            "payload_mode": "full_payload",
            "payload": {"hello": "world"},
            "step_index": 0,
            "occurred_at": "2026-05-13T12:00:00Z",
        },
        {
            "event_type": "tool_call.completed",
            "payload_mode": "full_payload",
            "payload": {"name": "lookup", "input": {"id": 1}, "output": {"ok": True}},
            "step_index": 1,
            "step_id": "step_a",
            "occurred_at": "2026-05-13T12:00:01Z",
        },
    ]
    append = http_client.post(f"/v1/runs/{run_id}/events", json={"events": events}, headers=HEADERS)
    assert append.status_code == 200, append.text
    assert len(append.json()["event_ids"]) == 2

    timeline = http_client.get(f"/v1/runs/{run_id}/timeline", headers=HEADERS).json()
    assert len(timeline["events"]) == 2
    assert timeline["events"][0]["event_type"] == "agent_run.started"

    verify = http_client.get(f"/v1/runs/{run_id}/verify", headers=HEADERS).json()
    assert verify["valid"] is True
    assert verify["event_count"] == 2

    commit = http_client.post(f"/v1/runs/{run_id}/commit", headers=HEADERS)
    assert commit.status_code == 200
    assert commit.json()["status"] == "committed"


def test_idempotency_on_events(http_client):
    start = http_client.post("/v1/runs/start", json={"agent_id": "agent-y"}, headers=HEADERS).json()
    run_id = start["run_id"]
    event = {
        "event_type": "tool_call.completed",
        "payload_mode": "metadata_only",
        "payload": {"name": "lookup"},
        "step_index": 0,
        "idempotency_key": "k1",
        "occurred_at": "2026-05-13T12:00:00Z",
    }
    r1 = http_client.post(f"/v1/runs/{run_id}/events", json={"events": [event]}, headers=HEADERS).json()
    r2 = http_client.post(f"/v1/runs/{run_id}/events", json={"events": [event]}, headers=HEADERS).json()
    assert r1["event_ids"] == r2["event_ids"]
    timeline = http_client.get(f"/v1/runs/{run_id}/timeline", headers=HEADERS).json()
    assert len(timeline["events"]) == 1


def test_policy_check_refund(http_client):
    body = {"policy_id": "refund_requires_approval", "input": {"action": "approve_refund", "amount": 7000}}
    resp = http_client.post("/v1/policy/check", json=body, headers=HEADERS).json()
    assert resp["result"]["allowed"] is False
    assert resp["result"]["requires_approval"] is True


def test_policy_check_under_threshold(http_client):
    body = {"policy_id": "refund_requires_approval", "input": {"action": "approve_refund", "amount": 1000}}
    resp = http_client.post("/v1/policy/check", json=body, headers=HEADERS).json()
    assert resp["result"]["allowed"] is True
    assert resp["result"]["requires_approval"] is False


def test_query_endpoint(http_client):
    start = http_client.post("/v1/runs/start", json={"agent_id": "agent-q"}, headers=HEADERS).json()
    run_id = start["run_id"]
    events = [
        {"event_type": "tool_call.completed", "payload_mode": "metadata_only", "payload": {}, "step_index": 0, "occurred_at": "2026-05-13T12:00:00Z"},
        {"event_type": "policy.check.failed", "payload_mode": "metadata_only", "payload": {}, "step_index": 1, "occurred_at": "2026-05-13T12:00:01Z"},
    ]
    http_client.post(f"/v1/runs/{run_id}/events", json={"events": events}, headers=HEADERS)
    resp = http_client.post("/v1/query", json={"event_types": ["policy.check.failed"], "limit": 50}, headers=HEADERS).json()
    assert any(e["run_id"] == run_id for e in resp["events"])


def test_evidence_export(http_client):
    start = http_client.post("/v1/runs/start", json={"agent_id": "agent-export"}, headers=HEADERS).json()
    run_id = start["run_id"]
    http_client.post(
        f"/v1/runs/{run_id}/events",
        json={"events": [{
            "event_type": "tool_call.completed",
            "payload_mode": "metadata_only",
            "payload": {},
            "step_index": 0,
            "occurred_at": "2026-05-13T12:00:00Z",
        }]},
        headers=HEADERS,
    )
    http_client.post(f"/v1/runs/{run_id}/commit", headers=HEADERS)
    resp = http_client.post(
        "/v1/audit/export",
        json={"run_id": run_id, "generated_by": "tester"},
        headers=HEADERS,
    ).json()
    assert resp["hash_chain_valid"] is True
    assert resp["event_count"] >= 1
    assert resp["bundle_hash"].startswith("sha256:")
