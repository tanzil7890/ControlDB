"""LangChain / LangGraph callback handler.

Usage::

    from controldb import ControlDB
    from controldb.integrations.langchain import ControlDBCallbackHandler

    control = ControlDB(...)
    with control.run(agent_id="aml-agent") as run:
        handler = ControlDBCallbackHandler(run)
        chain.invoke({...}, config={"callbacks": [handler]})

The handler depends on ``langchain-core``. We import lazily so the rest of
the SDK works without LangChain installed.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _require_base():
    try:
        from langchain_core.callbacks import BaseCallbackHandler  # type: ignore
    except Exception as exc:  # pragma: no cover - exercised only if langchain installed
        raise ImportError(
            "langchain-core is required for ControlDBCallbackHandler. "
            "Install: pip install langchain-core"
        ) from exc
    return BaseCallbackHandler


def build_handler(run):
    BaseCallbackHandler = _require_base()

    class ControlDBCallbackHandler(BaseCallbackHandler):  # type: ignore[misc]
        def on_llm_start(self, serialized: Dict[str, Any], prompts: List[str], **kwargs: Any) -> None:
            run.emit(
                event_type="model_call.started",
                payload={"model": serialized.get("name"), "prompts": prompts},
            )

        def on_llm_end(self, response: Any, **kwargs: Any) -> None:
            run.emit(
                event_type="model_call.completed",
                payload={"response": getattr(response, "generations", str(response))},
            )

        def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
            run.emit(
                event_type="model_call.failed",
                payload={"error": {"type": type(error).__name__, "message": str(error)}},
            )

        def on_tool_start(self, serialized: Dict[str, Any], input_str: str, **kwargs: Any) -> None:
            run.emit(
                event_type="tool_call.started",
                payload={"name": serialized.get("name"), "input": input_str},
            )

        def on_tool_end(self, output: str, **kwargs: Any) -> None:
            run.emit(
                event_type="tool_call.completed",
                payload={"output": output},
            )

        def on_tool_error(self, error: BaseException, **kwargs: Any) -> None:
            run.emit(
                event_type="tool_call.failed",
                payload={"error": {"type": type(error).__name__, "message": str(error)}},
            )

    return ControlDBCallbackHandler()
