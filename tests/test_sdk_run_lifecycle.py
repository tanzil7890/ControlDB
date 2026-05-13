"""End-to-end SDK -> collector lifecycle, including hash chain + approvals."""

from __future__ import annotations

import pytest

from controldb.errors import ApprovalRequiredError, PolicyDeniedError


def test_full_run_with_tools_and_state(sdk):
    with sdk.run(agent_id="aml-review", agent_version="v1") as run:
        with run.tool_call("query_transactions") as tool:
            tool.input({"customer_id": "cust_1"})
            tool.output({"hits": 3})
        run.memory_write(store="case", key="risk_summary", value="High risk")
        run.state_change(
            entity_type="customer_risk_profile",
            entity_id="cust_1",
            before={"score": 0.4},
            after={"score": 0.81},
            reason="wire activity",
        )
        run.commit()

    timeline = sdk.timeline(run.run_id)
    types = [e["event_type"] for e in timeline["events"]]
    assert "tool_call.started" in types
    assert "tool_call.completed" in types
    assert "memory.write" in types
    assert "state.change.applied" in types
    assert "agent_run.committed" in types

    verify = sdk.verify(run.run_id)
    assert verify["valid"] is True
    assert verify["event_count"] == len(timeline["events"])


def test_enforce_policy_requires_approval(sdk):
    with sdk.run(agent_id="refund-agent") as run:
        with pytest.raises(ApprovalRequiredError) as excinfo:
            run.enforce_policy(
                policy_id="refund_requires_approval",
                input={"action": "approve_refund", "amount": 7000},
            )
        assert excinfo.value.approval_id
        run.human_approval(
            approval_id=excinfo.value.approval_id,
            reviewer_id="manager_07",
            decision="approved",
            reason="Override",
        )
        run.commit()

    timeline = sdk.timeline(run.run_id)
    types = [e["event_type"] for e in timeline["events"]]
    assert "approval.requested" in types
    assert "approval.approved" in types
    assert "agent_run.committed" in types


def test_run_failure_emits_failed_event(sdk):
    class Boom(Exception):
        pass

    with pytest.raises(Boom):
        with sdk.run(agent_id="failing-agent") as run:
            run.memory_write(key="x", value=1)
            raise Boom("kaboom")

    timeline = sdk.timeline(run.run_id)
    types = [e["event_type"] for e in timeline["events"]]
    assert "agent_run.failed" in types


def test_hash_chain_remains_intact_with_idempotency_replay(sdk):
    with sdk.run(agent_id="repeat-agent") as run:
        run.memory_write(key="k", value="v")
        run.memory_write(key="k", value="v")  # exact retry
        run.commit()
    verify = sdk.verify(run.run_id)
    assert verify["valid"] is True
