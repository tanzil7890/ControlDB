"""Toy refund agent: refund > $5,000 blocks on approval."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sdk" / "python"))

from controldb import ControlDB
from controldb.errors import ApprovalRequiredError


def main() -> None:
    control = ControlDB(
        api_key=os.environ.get("CONTROLDB_API_KEY", "test-key"),
        base_url=os.environ.get("CONTROLDB_URL", "http://localhost:8080"),
        project="refund-agent",
        environment="dev",
        default_payload_mode="full_payload",
    )

    with control.run(agent_id="refund-agent", agent_version="1.0.0") as run:
        run.input(user_id="customer_42", metadata={"order_id": "ord_123"})

        with run.tool_call("lookup_order") as tool:
            tool.input({"order_id": "ord_123"})
            tool.output({"status": "delivered", "amount": 7000})

        run.state_change(
            entity_type="refund_case",
            entity_id="case_123",
            before={"status": "pending"},
            after={"status": "proposed"},
            reason="Customer eligible",
            applied=False,
        )

        try:
            run.enforce_policy(
                policy_id="refund_requires_approval",
                input={"action": "approve_refund", "amount": 7000},
            )
        except ApprovalRequiredError as exc:
            run.human_approval(
                approval_id=exc.approval_id,
                reviewer_id="manager_07",
                decision="approved",
                reason="Manager override",
            )

        run.state_change(
            entity_type="refund_case",
            entity_id="case_123",
            before={"status": "proposed"},
            after={"status": "approved"},
            reason="Refund approved",
        )

        run.commit()
        print(f"Run committed: {run.run_id}")


if __name__ == "__main__":
    main()
