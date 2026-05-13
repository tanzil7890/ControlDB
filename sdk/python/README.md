# controldb (Python SDK)

```bash
pip install -e .
```

```python
from controldb import ControlDB

control = ControlDB(api_key="...", project="aml", environment="prod")

with control.run(agent_id="aml-review", agent_version="v1.0.0") as run:
    with run.tool_call("query_transactions") as tool:
        tool.input({"customer_id": "cust_123"})
        tool.output({"hits": 3})
    run.state_change(
        entity_type="customer_risk_profile",
        entity_id="cust_123",
        before={"risk": 0.4},
        after={"risk": 0.81},
        reason="High-risk wire activity",
    )
    run.commit()
```
