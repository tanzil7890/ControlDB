"""CrewAI and AutoGen integration for ControlDB.

Provides two adapters:

* ``ControlDBCrewAIListener`` — wraps a CrewAI ``Crew`` and captures agent
  messages, tool calls, and final decisions.
* ``ControlDBAutoGenHook`` — hooks into an AutoGen ``ConversableAgent`` via
  ``register_reply`` to capture multi-agent messages and handoffs.

Usage — CrewAI::

    from controldb.integrations.crewai import ControlDBCrewAIListener
    listener = ControlDBCrewAIListener(run=my_run)
    listener.attach(my_crew)

Usage — AutoGen::

    from controldb.integrations.crewai import ControlDBAutoGenHook
    ControlDBAutoGenHook.attach(agent=my_agent, run=my_run)
"""

from __future__ import annotations

import asyncio
import functools
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

if TYPE_CHECKING:
    from ..run import Run


# ---------------------------------------------------------------------------
# CrewAI listener
# ---------------------------------------------------------------------------

class ControlDBCrewAIListener:
    """Wraps CrewAI crew/agent callbacks to emit ControlDB audit events.

    Call ``attach(crew)`` after the Crew is configured but before ``kickoff()``.
    """

    def __init__(self, run: "Run") -> None:
        self._run = run

    def _fire(self, coro: Any) -> None:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(coro)
        except RuntimeError:
            asyncio.run(coro)

    def attach(self, crew: Any) -> None:
        """Monkey-patch the crew's task execution callbacks."""
        try:
            # CrewAI >= 0.30 exposes task.callback
            for task in getattr(crew, "tasks", []):
                original_callback = getattr(task, "callback", None)
                task.callback = self._make_task_callback(task, original_callback)
        except Exception:
            pass

        # Wrap agent tools
        for agent in getattr(crew, "agents", []):
            self._wrap_agent_tools(agent)

        # Wrap crew.kickoff to capture final output
        original_kickoff = crew.kickoff
        @functools.wraps(original_kickoff)
        def patched_kickoff(*args: Any, **kwargs: Any) -> Any:
            result = original_kickoff(*args, **kwargs)
            self._fire(
                self._run.state_change(
                    entity_type="crew_output",
                    entity_id=str(id(crew)),
                    before={"status": "running"},
                    after={"status": "completed", "output": str(result)[:2048]},
                    reason="crew.kickoff completed",
                )
            )
            return result
        crew.kickoff = patched_kickoff

    def _make_task_callback(self, task: Any, original: Optional[Callable]) -> Callable:
        task_desc = getattr(task, "description", "unknown_task")[:120]

        def callback(output: Any) -> None:
            self._fire(
                self._run.tool_call(
                    name=f"task:{task_desc}",
                    output={"result": str(output)[:2048]},
                )
            )
            if original:
                original(output)

        return callback

    def _wrap_agent_tools(self, agent: Any) -> None:
        tools = getattr(agent, "tools", []) or []
        for i, tool in enumerate(tools):
            original_run = getattr(tool, "_run", None) or getattr(tool, "run", None)
            if original_run is None:
                continue
            tool_name = getattr(tool, "name", f"tool_{i}")
            tool_description = getattr(tool, "description", "")

            def make_wrapper(orig: Callable, tname: str) -> Callable:
                @functools.wraps(orig)
                def wrapper(*args: Any, **kwargs: Any) -> Any:
                    result = orig(*args, **kwargs)
                    self._fire(
                        self._run.tool_call(
                            name=tname,
                            input={"args": list(args)[:10], "kwargs": dict(list(kwargs.items())[:10])},
                            output={"result": str(result)[:2048]},
                        )
                    )
                    return result
                return wrapper

            if hasattr(tool, "_run"):
                tool._run = make_wrapper(original_run, tool_name)
            else:
                tool.run = make_wrapper(original_run, tool_name)


# ---------------------------------------------------------------------------
# AutoGen hook
# ---------------------------------------------------------------------------

class ControlDBAutoGenHook:
    """Registers a reply hook on an AutoGen ``ConversableAgent``.

    Captures every message sent/received by the agent, including multi-agent
    handoffs and final decisions.
    """

    @staticmethod
    def attach(agent: Any, run: "Run") -> None:
        """Register ControlDB hooks on *agent*.

        Works with ``autogen.ConversableAgent`` and ``autogen_agentchat.agents``.
        """

        def _fire(coro: Any) -> None:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(coro)
            except RuntimeError:
                asyncio.run(coro)

        def _controldb_reply_hook(
            recipient: Any,
            messages: Optional[List[Dict[str, Any]]] = None,
            sender: Optional[Any] = None,
            config: Optional[Any] = None,
        ) -> Any:
            if messages:
                last = messages[-1] if isinstance(messages, list) else messages
                content = last.get("content", "") if isinstance(last, dict) else str(last)
                role = last.get("role", "agent") if isinstance(last, dict) else "agent"
                sender_name = getattr(sender, "name", "unknown")

                _fire(
                    run._emit(
                        "memory.write",
                        {
                            "store": "autogen_messages",
                            "key": f"msg_{len(messages)}",
                            "value": {
                                "role": role,
                                "sender": sender_name,
                                "content": str(content)[:2048],
                            },
                        },
                    )
                )

            # Return False so AutoGen continues processing other reply hooks
            return False, None

        # AutoGen ConversableAgent API
        register = getattr(agent, "register_reply", None)
        if register:
            try:
                from autogen import ConversableAgent  # type: ignore

                register(
                    [ConversableAgent, None],
                    _controldb_reply_hook,
                    position=0,
                )
            except ImportError:
                # autogen-agentchat or similar
                register(None, _controldb_reply_hook, position=0)
        else:
            # Fallback: wrap generate_reply directly
            original_generate = getattr(agent, "generate_reply", None)
            if original_generate:
                @functools.wraps(original_generate)
                def patched_generate(*args: Any, **kwargs: Any) -> Any:
                    result = original_generate(*args, **kwargs)
                    _fire(
                        run.tool_call(
                            name=f"autogen:{getattr(agent, 'name', 'agent')}:generate_reply",
                            output={"reply": str(result)[:2048]},
                        )
                    )
                    return result
                agent.generate_reply = patched_generate
