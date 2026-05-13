# Example: AML review agent

Runs an in-memory AML triage flow against the local collector. Demonstrates
tool calls, memory writes, state changes, policy checks, human approval, and
evidence export.

```bash
make dev               # start collector at http://localhost:8080
python examples/aml-agent/run.py
```
