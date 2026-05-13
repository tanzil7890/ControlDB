"""Verification API should detect tampering."""

from __future__ import annotations

import sqlite3

from sqlalchemy import select


HEADERS = {"Authorization": "Bearer test-key"}


def test_tampered_event_detected(http_client, collector_env):
    start = http_client.post("/v1/runs/start", json={"agent_id": "agent-tamper"}, headers=HEADERS).json()
    run_id = start["run_id"]
    events = [
        {"event_type": "agent_run.started", "payload_mode": "full_payload", "payload": {"v": 1}, "step_index": 0, "occurred_at": "2026-05-13T12:00:00Z"},
        {"event_type": "tool_call.completed", "payload_mode": "full_payload", "payload": {"v": 2}, "step_index": 1, "occurred_at": "2026-05-13T12:00:01Z"},
    ]
    http_client.post(f"/v1/runs/{run_id}/events", json={"events": events}, headers=HEADERS)

    db_url = collector_env["CONTROLDB_DATABASE_URL"]
    assert db_url.startswith("sqlite:///")
    path = db_url[len("sqlite:///"):]
    conn = sqlite3.connect(path)
    conn.execute(
        "UPDATE audit_events SET payload = ? WHERE run_id = ? AND event_type = 'tool_call.completed'",
        ('{"v": 999}', run_id),
    )
    conn.commit()
    conn.close()

    verify = http_client.get(f"/v1/runs/{run_id}/verify", headers=HEADERS).json()
    assert verify["valid"] is False
    assert verify["broken_at_event_id"]
