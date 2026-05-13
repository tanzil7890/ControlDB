"""Toy AML review agent that exercises the full ControlDB SDK surface."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Allow running from a repo checkout without installing the SDK.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sdk" / "python"))

from controldb import ControlDB
from controldb.errors import ApprovalRequiredError


def query_customer_profile(customer_id: str) -> dict:
    return {"customer_id": customer_id, "tier": "standard", "country": "US"}


def query_transactions(customer_id: str) -> list:
    return [
        {"id": "tx_1", "amount": 25000, "currency": "USD", "type": "wire"},
        {"id": "tx_2", "amount": 800, "currency": "USD", "type": "ach"},
    ]


def check_pep_list(customer_id: str) -> dict:
    return {"customer_id": customer_id, "hits": 0}


def main() -> None:
    api_key = os.environ.get("CONTROLDB_API_KEY", "test-key")
    base_url = os.environ.get("CONTROLDB_URL", "http://localhost:8080")
    control = ControlDB(
        api_key=api_key,
        base_url=base_url,
        project="aml-agent",
        environment="dev",
        default_payload_mode="full_payload",
        redaction_rules=[{"field": "customer_id", "mode": "hash"}],
    )

    with control.run(agent_id="aml-review-agent", agent_version="v1.0.0") as run:
        run.input(user_id="analyst_42", input_hash="sha256:case_123", metadata={"case_id": "case_123"})

        with run.tool_call("query_customer_profile") as tool:
            tool.input({"customer_id": "cust_123"})
            tool.output(query_customer_profile("cust_123"))

        with run.tool_call("query_transactions") as tool:
            tool.input({"customer_id": "cust_123"})
            tool.output(query_transactions("cust_123"))

        with run.tool_call("check_pep_list") as tool:
            tool.input({"customer_id": "cust_123"})
            tool.output(check_pep_list("cust_123"))

        run.memory_write(store="case", key="risk_summary", value="High-risk wire activity detected")

        run.state_change(
            entity_type="customer_risk_profile",
            entity_id="cust_123",
            before={"risk_score": 0.42},
            after={"risk_score": 0.81},
            reason="High-risk wire activity",
        )

        try:
            run.enforce_policy(
                policy_id="high_risk_wire_requires_approval",
                input={"risk_score": 0.81, "amount": 25000},
            )
        except ApprovalRequiredError as exc:
            print(f"Approval required: {exc.approval_id}")
            run.human_approval(
                approval_id=exc.approval_id,
                reviewer_id="analyst_42",
                decision="approved",
                reason="Escalation criteria met",
            )

        run.state_change(
            entity_type="case",
            entity_id="case_123",
            before={"status": "pending"},
            after={"status": "escalated"},
            reason="Approved escalation",
        )

        run.commit()
        print(f"Run committed: {run.run_id}")


if __name__ == "__main__":
    main()
