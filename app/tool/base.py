import json
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Union

from pydantic import BaseModel, Field

from app.observability.tool_context import bind_trace_context, get_trace_context
from app.observability.tracing import (
    get_tracer,
    preview_text,
    record_exception,
    result_to_attributes,
    safe_json_dumps,
    set_span_attributes,
)
from app.utils.logger import logger


TRACER = get_tracer(__name__)


class ToolResult(BaseModel):
    """Represents the result of a tool execution."""

    output: Any = Field(default=None)
    error: Optional[str] = Field(default=None)
    base64_image: Optional[str] = Field(default=None)
    system: Optional[str] = Field(default=None)

    class Config:
        arbitrary_types_allowed = True

    def __bool__(self):
        return any(getattr(self, field) for field in self.__fields__)

    def __add__(self, other: "ToolResult"):
        def combine_fields(
            field: Optional[str], other_field: Optional[str], concatenate: bool = True
        ):
            if field and other_field:
                if concatenate:
                    return field + other_field
                raise ValueError("Cannot combine tool results")
            return field or other_field

        return ToolResult(
            output=combine_fields(self.output, other.output),
            error=combine_fields(self.error, other.error),
            base64_image=combine_fields(self.base64_image, other.base64_image, False),
            system=combine_fields(self.system, other.system),
        )

    def __str__(self):
        return f"Error: {self.error}" if self.error else self.output

    def replace(self, **kwargs):
        """Returns a new ToolResult with the given fields replaced."""
        return type(self)(**{**self.dict(), **kwargs})


class BaseTool(ABC, BaseModel):
    """Consolidated base class for all tools combining BaseModel and Tool functionality."""

    name: str
    description: str
    parameters: Optional[dict] = None

    class Config:
        arbitrary_types_allowed = True
        underscore_attrs_are_private = False

    async def __call__(self, **kwargs) -> Any:
        """Execute the tool with given parameters and emit a unified execution span."""
        started_at = time.perf_counter()
        with bind_trace_context(tool_name=self.name), TRACER.start_as_current_span("tool.execute") as span:
            set_span_attributes(
                span,
                {
                    **get_trace_context(),
                    "tool.class": self.__class__.__name__,
                    "tool.parameters_defined": bool(self.parameters),
                    "tool.kwargs_preview": safe_json_dumps(kwargs),
                    "tool.kwargs_keys": sorted(kwargs.keys()),
                    "tool.kwargs_count": len(kwargs),
                },
            )
            try:
                result = await self.execute(**kwargs)
            except Exception as exc:
                record_exception(span, exc)
                set_span_attributes(
                    span,
                    {"tool.duration_ms": round((time.perf_counter() - started_at) * 1000, 3)},
                )
                raise
            set_span_attributes(
                span,
                {
                    "tool.duration_ms": round((time.perf_counter() - started_at) * 1000, 3),
                    **result_to_attributes(result),
                },
            )
            return result

    @abstractmethod
    async def execute(self, **kwargs) -> Any:
        """Execute the tool with given parameters."""

    def to_param(self) -> Dict:
        """Convert tool to function call format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def success_response(self, data: Union[Dict[str, Any], str]) -> ToolResult:
        """Create a successful tool result."""
        if isinstance(data, str):
            text = data
        else:
            text = json.dumps(data, indent=2)
        logger.debug(f"Created success response for {self.__class__.__name__}")
        return ToolResult(output=text)

    def fail_response(self, msg: str) -> ToolResult:
        """Create a failed tool result."""
        logger.debug(f"Tool {self.__class__.__name__} returned failed result: {msg}")
        return ToolResult(error=msg)


class CLIResult(ToolResult):
    """A ToolResult that can be rendered as a CLI output."""


class ToolFailure(ToolResult):
    """A ToolResult that represents a failure."""
