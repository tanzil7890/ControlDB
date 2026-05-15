"""Insurance claims adjudication agent demonstrating ControlDB full audit surface."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sdk" / "python"))

from controldb import ControlDB
from controldb.errors import ApprovalRequiredError


# --- mock domain functions ---------------------------------------------------

def fetch_claim(claim_id: str) -> dict:
    return {
        "claim_id": claim_id,
        "policy_number": "POL-992211",
        "claimant": "Jane Smith",
        "incident_date": "2026-04-15",
        "incident_type": "vehicle_collision",
        "claimed_amount": 42_500.00,
        "status": "submitted",
    }


def verify_policy_coverage(policy_number: str, incident_type: str) -> dict:
    return {
        "policy_number": policy_number,
        "incident_type": incident_type,
        "covered": True,
        "deductible": 500.00,
        "coverage_limit": 50_000.00,
    }


def run_fraud_score(claim_id: str) -> dict:
    return {
        "claim_id": claim_id,
        "fraud_score": 0.12,
        "flags": [],
        "model_version": "fraud-v3.1",
    }


def get_repair_estimate(claim_id: str) -> dict:
    return {
        "claim_id": claim_id,
        "estimated_amount": 41_800.00,
        "vendor": "AutoRepair Pro",
        "confidence": 0.94,
    }


def approve_payment(claim_id: str, amount: float, approved_by: str) -> dict:
    return {
        "claim_id": claim_id,
        "payment_amount": amount,
        "approved_by": approved_by,
        "payment_id": f"PAY-{claim_id[-6:].upper()}",
        "status": "approved",
    }


# --- agent -------------------------------------------------------------------

def main() -> None:
    api_key = os.environ.get("CONTROLDB_API_KEY", "test-key")
    base_url = os.environ.get("CONTROLDB_URL", "http://localhost:8080")

    control = ControlDB(
        api_key=api_key,
        base_url=base_url,
        project="claims-adjudication",
        environment="dev",
        default_payload_mode="full_payload",
        redaction_rules=[
            {"field": "claimant", "mode": "redact"},
            {"field": "policy_number", "mode": "hash"},
        ],
    )

    claim_id = os.environ.get("CLAIM_ID", "CLM-20260501-0042")

    with control.run(agent_id="claims-adjudication-agent", agent_version="v2.1.0") as run:
        run.input(
            user_id="adjuster_88",
            input_hash=f"sha256:{claim_id}",
            metadata={"claim_id": claim_id, "queue": "auto_adjudication"},
        )

        # Step 1 — fetch claim
        with run.tool_call("fetch_claim") as tool:
            tool.input({"claim_id": claim_id})
            claim = fetch_claim(claim_id)
            tool.output(claim)

        claimed_amount = claim["claimed_amount"]

        # Step 2 — verify coverage
        with run.tool_call("verify_policy_coverage") as tool:
            tool.input({
                "policy_number": claim["policy_number"],
                "incident_type": claim["incident_type"],
            })
            coverage = verify_policy_coverage(claim["policy_number"], claim["incident_type"])
            tool.output(coverage)

        if not coverage["covered"]:
            run.state_change(
                entity_type="claim",
                entity_id=claim_id,
                before={"status": "submitted"},
                after={"status": "denied", "reason": "not_covered"},
            )
            run.commit()
            print(f"Claim {claim_id} denied: not covered under policy.")
            return

        # Step 3 — fraud detection
        with run.tool_call("run_fraud_score") as tool:
            tool.input({"claim_id": claim_id})
            fraud = run_fraud_score(claim_id)
            tool.output(fraud)

        if fraud["fraud_score"] > 0.70:
            run.state_change(
                entity_type="claim",
                entity_id=claim_id,
                before={"status": "submitted"},
                after={"status": "flagged_for_investigation", "fraud_score": fraud["fraud_score"]},
                reason="High fraud score — escalated to SIU",
            )
            run.commit()
            print(f"Claim {claim_id} flagged for SIU (fraud_score={fraud['fraud_score']}).")
            return

        # Step 4 — repair estimate
        with run.tool_call("get_repair_estimate") as tool:
            tool.input({"claim_id": claim_id})
            estimate = get_repair_estimate(claim_id)
            tool.output(estimate)

        run.memory_write(
            store="claim_context",
            key="estimated_amount",
            value=estimate["estimated_amount"],
        )
        run.memory_write(
            store="claim_context",
            key="fraud_cleared",
            value=True,
        )

        # Step 5 — policy check: amounts > $25,000 require manager approval
        try:
            run.enforce_policy(
                policy_id="large_claim_requires_manager_approval",
                input={
                    "claimed_amount": claimed_amount,
                    "estimated_amount": estimate["estimated_amount"],
                    "fraud_score": fraud["fraud_score"],
                    "adjuster_id": "adjuster_88",
                },
            )
        except ApprovalRequiredError as exc:
            print(f"Manager approval required for ${claimed_amount:,.2f} claim: {exc.approval_id}")
            # In production, send notification to manager and await callback.
            # Here we simulate immediate approval.
            run.human_approval(
                approval_id=exc.approval_id,
                reviewer_id="manager_12",
                decision="approved",
                reason=f"Fraud cleared, estimate within coverage limit of ${coverage['coverage_limit']:,.2f}",
            )

        # Step 6 — approve payment
        settlement_amount = min(estimate["estimated_amount"], coverage["coverage_limit"]) - coverage["deductible"]
        with run.tool_call("approve_payment") as tool:
            tool.input({
                "claim_id": claim_id,
                "amount": settlement_amount,
                "approved_by": "manager_12",
            })
            payment = approve_payment(claim_id, settlement_amount, "manager_12")
            tool.output(payment)

        # Step 7 — final state transition
        run.state_change(
            entity_type="claim",
            entity_id=claim_id,
            before={"status": "submitted"},
            after={
                "status": "approved",
                "payment_id": payment["payment_id"],
                "settlement_amount": settlement_amount,
            },
            reason=f"Adjudication complete. Settlement: ${settlement_amount:,.2f}",
        )

        run.commit()
        print(f"Claim {claim_id} approved. Payment {payment['payment_id']} of ${settlement_amount:,.2f} issued.")
        print(f"Audit run: {run.run_id}")


if __name__ == "__main__":
    main()
