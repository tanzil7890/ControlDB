"""Hooks for the OpenAI Agents SDK.

The OpenAI Agents SDK exposes lifecycle hooks (``on_run_started``,
``on_tool_invocation``, etc.). This module returns a hook object that maps
those callbacks to ControlDB events.
"""

from __future__ import annotations

from typing import Any


class ControlDBOpenAIAgentsHook:
    def __init__(self, run):
        self.run = run

    def on_run_started(self, **kwargs: Any) -> None:
        self.run.emit(event_type="agent_run.started", payload=dict(kwargs))

    def on_run_completed(self, **kwargs: Any) -> None:
        self.run.emit(event_type="agent_run.committed", payload=dict(kwargs))

    def on_tool_invocation(self, name: str, args: Any, output: Any = None, error: Any = None) -> None:
        if error is not None:
            self.run.emit(
                event_type="tool_call.failed",
                payload={"name": name, "input": args, "error": str(error)},
            )
        else:
            self.run.emit(
                event_type="tool_call.completed",
                payload={"name": name, "input": args, "output": output},
            )

    def on_handoff(self, source: str, target: str, **kwargs: Any) -> None:
        self.run.emit(
            event_type="memory.write",
            payload={"store": "_handoffs", "key": f"{source}->{target}", "value": dict(kwargs)},
        )

    def on_guardrail(self, guardrail_id: str, result: Any) -> None:
        self.run.emit(
            event_type="policy.check.passed" if result else "policy.check.failed",
            payload={"policy_id": guardrail_id, "result": result},
        )
