from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Dict, Iterator, Optional


_current_agent_name: ContextVar[Optional[str]] = ContextVar("agent_name", default=None)
_current_agent_step: ContextVar[Optional[int]] = ContextVar("agent_step", default=None)
_current_tool_call_id: ContextVar[Optional[str]] = ContextVar("tool_call_id", default=None)
_current_tool_name: ContextVar[Optional[str]] = ContextVar("tool_name", default=None)

_CONTEXT_MAP = {
    "agent_name": _current_agent_name,
    "agent_step": _current_agent_step,
    "tool_call_id": _current_tool_call_id,
    "tool_name": _current_tool_name,
}


def get_trace_context() -> Dict[str, object]:
    context: Dict[str, object] = {}
    if (agent_name := _current_agent_name.get()) is not None:
        context["agent.name"] = agent_name
    if (agent_step := _current_agent_step.get()) is not None:
        context["agent.step"] = agent_step
    if (tool_call_id := _current_tool_call_id.get()) is not None:
        context["tool.call_id"] = tool_call_id
    if (tool_name := _current_tool_name.get()) is not None:
        context["tool.name"] = tool_name
    return context


@contextmanager
def bind_trace_context(
    *,
    agent_name: Optional[str] = None,
    agent_step: Optional[int] = None,
    tool_call_id: Optional[str] = None,
    tool_name: Optional[str] = None,
) -> Iterator[None]:
    tokens: list[tuple[ContextVar[object], Token[object]]] = []
    values = {
        "agent_name": agent_name,
        "agent_step": agent_step,
        "tool_call_id": tool_call_id,
        "tool_name": tool_name,
    }
    try:
        for key, value in values.items():
            if value is None:
                continue
            var = _CONTEXT_MAP[key]
            token = var.set(value)
            tokens.append((var, token))
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)
