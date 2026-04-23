"""Collection classes for managing multiple tools."""
from typing import Any, Dict, List

from app.exceptions import ToolError
from app.logger import logger
from app.observability.tracing import get_tracer, record_exception, result_to_attributes, safe_json_dumps, set_span_attributes
from app.tool.base import BaseTool, ToolFailure, ToolResult


TRACER = get_tracer(__name__)


class ToolCollection:
    """A collection of defined tools."""

    class Config:
        arbitrary_types_allowed = True

    def __init__(self, *tools: BaseTool):
        self.tools = tools
        self.tool_map = {tool.name: tool for tool in tools}

    def __iter__(self):
        return iter(self.tools)

    def to_params(self) -> List[Dict[str, Any]]:
        return [tool.to_param() for tool in self.tools]

    async def execute(
        self, *, name: str, tool_input: Dict[str, Any] = None
    ) -> ToolResult:
        tool_input = tool_input or {}
        with TRACER.start_as_current_span("tool.dispatch") as span:
            set_span_attributes(
                span,
                {
                    "tool.name": name,
                    "tool.dispatch.input": safe_json_dumps(tool_input),
                    "tool.dispatch.input_keys": sorted(tool_input.keys()),
                    "tool.dispatch.tool_exists": name in self.tool_map,
                },
            )
            tool = self.tool_map.get(name)
            if not tool:
                result = ToolFailure(error=f"Tool {name} is invalid")
                set_span_attributes(span, result_to_attributes(result, prefix="tool.dispatch.result"))
                return result
            try:
                result = await tool(**tool_input)
                set_span_attributes(span, result_to_attributes(result, prefix="tool.dispatch.result"))
                return result
            except ToolError as e:
                result = ToolFailure(error=e.message)
                set_span_attributes(span, result_to_attributes(result, prefix="tool.dispatch.result"))
                return result
            except Exception as exc:
                record_exception(span, exc)
                raise

    async def execute_all(self) -> List[ToolResult]:
        """Execute all tools in the collection sequentially."""
        results = []
        for tool in self.tools:
            try:
                result = await tool()
                results.append(result)
            except ToolError as e:
                results.append(ToolFailure(error=e.message))
        return results

    def get_tool(self, name: str) -> BaseTool:
        return self.tool_map.get(name)

    def add_tool(self, tool: BaseTool):
        """Add a single tool to the collection.

        If a tool with the same name already exists, it will be skipped and a warning will be logged.
        """
        if tool.name in self.tool_map:
            logger.warning(f"Tool {tool.name} already exists in collection, skipping")
            return self

        self.tools += (tool,)
        self.tool_map[tool.name] = tool
        return self

    def add_tools(self, *tools: BaseTool):
        """Add multiple tools to the collection.

        If any tool has a name conflict with an existing tool, it will be skipped and a warning will be logged.
        """
        for tool in tools:
            self.add_tool(tool)
        return self
