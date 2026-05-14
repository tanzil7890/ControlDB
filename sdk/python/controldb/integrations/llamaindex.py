"""LlamaIndex integration for ControlDB.

Provides a ``ControlDBCallbackHandler`` that can be added to a LlamaIndex
``Settings.callback_manager`` to automatically capture query engine calls,
retriever calls, tool calls, and model calls.

Usage::

    from llama_index.core import Settings
    from llama_index.core.callbacks import CallbackManager
    from controldb.integrations.llamaindex import ControlDBCallbackHandler

    handler = ControlDBCallbackHandler(run=my_run)
    Settings.callback_manager = CallbackManager([handler])
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from ..run import Run


def build_handler(run: "Run") -> Any:
    """Return a LlamaIndex-compatible BaseCallbackHandler bound to *run*.

    The returned object maps LlamaIndex callback events to ControlDB event
    types. All event helpers are fire-and-forget (queued into the run buffer).
    """
    try:
        from llama_index.core.callbacks import CBEventType  # type: ignore
        from llama_index.core.callbacks.base_handler import BaseCallbackHandler  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "llama-index is required for this integration: pip install llama-index-core"
        ) from exc

    import threading

    class _Handler(BaseCallbackHandler):
        def __init__(self) -> None:
            super().__init__(event_starts_to_ignore=[], event_ends_to_ignore=[])
            self._run = run
            self._pending: Dict[str, Dict[str, Any]] = {}
            self._lock = threading.Lock()

        # ------------------------------------------------------------------
        def _fire(self, coro: Any) -> None:
            import asyncio

            try:
                loop = asyncio.get_running_loop()
                loop.create_task(coro)
            except RuntimeError:
                asyncio.run(coro)

        # ------------------------------------------------------------------
        def on_event_start(
            self,
            event_type: "CBEventType",
            payload: Optional[Dict[str, Any]] = None,
            event_id: str = "",
            parent_id: str = "",
            **kwargs: Any,
        ) -> str:
            with self._lock:
                self._pending[event_id] = {
                    "event_type": event_type,
                    "payload": payload or {},
                }
            return event_id

        def on_event_end(
            self,
            event_type: "CBEventType",
            payload: Optional[Dict[str, Any]] = None,
            event_id: str = "",
            **kwargs: Any,
        ) -> None:
            with self._lock:
                start_data = self._pending.pop(event_id, {})

            payload = payload or {}
            start_payload = start_data.get("payload", {})

            if event_type == CBEventType.LLM:
                response = payload.get("response") or payload.get("output", {})
                self._fire(
                    self._run.model_call(
                        model=payload.get("serialized", {}).get("model", "unknown"),
                        input={"messages": start_payload.get("messages", [])},
                        output={"response": str(response)[:2048]},
                        input_tokens=payload.get("usage", {}).get("prompt_tokens"),
                        output_tokens=payload.get("usage", {}).get("completion_tokens"),
                    )
                )

            elif event_type == CBEventType.FUNCTION_CALL:
                func_name = payload.get("tool", {}).get("name") or start_payload.get("tool", {}).get("name", "unknown_function")
                self._fire(
                    self._run.tool_call(
                        name=func_name,
                        input=start_payload.get("function_call", {}),
                        output=payload.get("function_call_response", {}),
                    )
                )

            elif event_type == CBEventType.QUERY:
                query_str = start_payload.get("query_str", "")
                self._fire(
                    self._run.tool_call(
                        name="query_engine",
                        input={"query": query_str},
                        output={"response": str(payload.get("response", ""))[:2048]},
                    )
                )

            elif event_type == CBEventType.RETRIEVE:
                self._fire(
                    self._run.tool_call(
                        name="retriever",
                        input={"query": start_payload.get("query_str", "")},
                        output={
                            "node_count": len(payload.get("nodes", [])),
                            "node_ids": [
                                n.get("node", {}).get("id_", "")
                                for n in payload.get("nodes", [])[:10]
                            ],
                        },
                    )
                )

            elif event_type == CBEventType.SYNTHESIZE:
                self._fire(
                    self._run.tool_call(
                        name="response_synthesizer",
                        input={"query": start_payload.get("query_str", "")},
                        output={"response": str(payload.get("response", ""))[:2048]},
                    )
                )

        def start_trace(self, trace_id: Optional[str] = None) -> None:
            pass

        def end_trace(
            self,
            trace_id: Optional[str] = None,
            trace_map: Optional[Dict[str, List[str]]] = None,
        ) -> None:
            pass

    return _Handler()


class ControlDBCallbackHandler:
    """Convenience class that wraps ``build_handler``.

    Instantiate with a ``Run`` and pass to
    ``llama_index.core.Settings.callback_manager``.
    """

    def __new__(cls, run: "Run") -> Any:  # type: ignore[override]
        return build_handler(run)
