import asyncio
import json
import time
from typing import Any, List, Optional, Union

from pydantic import Field

from app.agent.react import ReActAgent
from app.exceptions import TokenLimitExceeded
from app.logger import logger
from app.observability.tool_context import bind_trace_context
from app.observability.tracing import (
    get_tracer,
    hash_text,
    preview_text,
    record_exception,
    result_to_attributes,
    safe_json_dumps,
    set_span_attributes,
)
from app.prompt.toolcall import NEXT_STEP_PROMPT, SYSTEM_PROMPT
from app.schema import TOOL_CHOICE_TYPE, AgentState, Message, ToolCall, ToolChoice
from app.tool import CreateChatCompletion, Terminate, ToolCollection


TOOL_CALL_REQUIRED = "Tool calls required but none provided"
TRACER = get_tracer(__name__)


class ToolCallAgent(ReActAgent):
    """Base agent class for handling tool/function calls with enhanced abstraction"""

    name: str = "toolcall"
    description: str = "an agent that can execute tool calls."

    system_prompt: str = SYSTEM_PROMPT
    next_step_prompt: str = NEXT_STEP_PROMPT

    available_tools: ToolCollection = ToolCollection(
        CreateChatCompletion(), Terminate()
    )
    tool_choices: TOOL_CHOICE_TYPE = ToolChoice.AUTO  # type: ignore
    special_tool_names: List[str] = Field(default_factory=lambda: [Terminate().name])

    tool_calls: List[ToolCall] = Field(default_factory=list)
    _current_base64_image: Optional[str] = None

    max_steps: int = 30
    max_observe: Optional[Union[int, bool]] = None

    async def think(self) -> bool:
        """Process current state and decide next actions using tools."""
        with TRACER.start_as_current_span("agent.plan_tools") as span:
            set_span_attributes(
                span,
                {
                    "agent.name": self.name,
                    "agent.step": self.current_step,
                    "tool_choice.mode": getattr(self.tool_choices, "value", str(self.tool_choices)),
                    "tool.available_count": len(self.available_tools.tools),
                    "tool.available_names": [tool.name for tool in self.available_tools.tools],
                    "agent.next_step_prompt_preview": preview_text(self.next_step_prompt),
                },
            )
            if self.next_step_prompt:
                user_msg = Message.user_message(self.next_step_prompt)
                self.messages += [user_msg]

            try:
                response = await self.llm.ask_tool(
                    messages=self.messages,
                    system_msgs=(
                        [Message.system_message(self.system_prompt)]
                        if self.system_prompt
                        else None
                    ),
                    tools=self.available_tools.to_params(),
                    tool_choice=self.tool_choices,
                )
            except ValueError as exc:
                record_exception(span, exc)
                raise
            except Exception as e:
                if hasattr(e, "__cause__") and isinstance(e.__cause__, TokenLimitExceeded):
                    token_limit_error = e.__cause__
                    logger.error(
                        f"🚨 Token limit error (from RetryError): {token_limit_error}"
                    )
                    self.memory.add_message(
                        Message.assistant_message(
                            f"Maximum token limit reached, cannot continue execution: {str(token_limit_error)}"
                        )
                    )
                    self.state = AgentState.FINISHED
                    set_span_attributes(
                        span,
                        {
                            "agent.state_after_plan": getattr(self.state, "value", str(self.state)),
                            "error.type": type(token_limit_error).__name__,
                            "error.message": preview_text(token_limit_error),
                        },
                    )
                    return False
                record_exception(span, e)
                raise

            self.tool_calls = tool_calls = (
                response.tool_calls if response and response.tool_calls else []
            )
            content = response.content if response and response.content else ""

            logger.info(f"✨ {self.name}'s thoughts: {content}")
            logger.info(
                f"🛠️ {self.name} selected {len(tool_calls) if tool_calls else 0} tools to use"
            )
            if tool_calls:
                logger.info(
                    f"🧰 Tools being prepared: {[call.function.name for call in tool_calls]}"
                )
                logger.info(f"🔧 Tool arguments: {tool_calls[0].function.arguments}")

            set_span_attributes(
                span,
                {
                    "llm.response_present": response is not None,
                    "llm.response_content_preview": preview_text(content),
                    "llm.response_content_hash": hash_text(content) if content else None,
                    "llm.selected_tool_count": len(tool_calls),
                    "llm.selected_tool_names": [call.function.name for call in tool_calls],
                },
            )

            try:
                if response is None:
                    raise RuntimeError("No response received from the LLM")

                if self.tool_choices == ToolChoice.NONE:
                    if tool_calls:
                        logger.warning(
                            f"🤔 Hmm, {self.name} tried to use tools when they weren't available!"
                        )
                    if content:
                        self.memory.add_message(Message.assistant_message(content))
                        return True
                    return False

                assistant_msg = (
                    Message.from_tool_calls(content=content, tool_calls=self.tool_calls)
                    if self.tool_calls
                    else Message.assistant_message(content)
                )
                self.memory.add_message(assistant_msg)

                if self.tool_choices == ToolChoice.REQUIRED and not self.tool_calls:
                    return True

                if self.tool_choices == ToolChoice.AUTO and not self.tool_calls:
                    return bool(content)

                return bool(self.tool_calls)
            except Exception as e:
                record_exception(span, e)
                logger.error(f"🚨 Oops! The {self.name}'s thinking process hit a snag: {e}")
                self.memory.add_message(
                    Message.assistant_message(
                        f"Error encountered while processing: {str(e)}"
                    )
                )
                return False

    async def act(self) -> str:
        """Execute tool calls and handle their results."""
        if not self.tool_calls:
            if self.tool_choices == ToolChoice.REQUIRED:
                raise ValueError(TOOL_CALL_REQUIRED)
            return self.messages[-1].content or "No content or commands to execute"

        results = []
        for command in self.tool_calls:
            self._current_base64_image = None
            result = await self.execute_tool(command)

            if self.max_observe:
                result = result[: self.max_observe]

            logger.info(
                f"🎯 Tool '{command.function.name}' completed its mission! Result: {result}"
            )

            tool_msg = Message.tool_message(
                content=result,
                tool_call_id=command.id,
                name=command.function.name,
                base64_image=self._current_base64_image,
            )
            self.memory.add_message(tool_msg)
            results.append(result)

        return "\n\n".join(results)

    async def execute_tool(self, command: ToolCall) -> str:
        """Execute a single tool call with robust error handling and instrumentation."""
        if not command or not command.function or not command.function.name:
            return "Error: Invalid command format"

        name = command.function.name
        if name not in self.available_tools.tool_map:
            return f"Error: Unknown tool '{name}'"

        started_at = time.perf_counter()
        with bind_trace_context(
            agent_name=self.name,
            agent_step=self.current_step,
            tool_call_id=command.id,
            tool_name=name,
        ), TRACER.start_as_current_span("agent.tool_call") as span:
            raw_arguments = command.function.arguments or "{}"
            set_span_attributes(
                span,
                {
                    "agent.name": self.name,
                    "agent.step": self.current_step,
                    "tool.call_id": command.id,
                    "tool.name": name,
                    "tool.is_special": self._is_special_tool(name),
                    "tool.arguments_preview": preview_text(raw_arguments),
                    "tool.arguments_hash": hash_text(raw_arguments),
                },
            )
            try:
                args = json.loads(raw_arguments)
                set_span_attributes(
                    span,
                    {
                        "tool.arguments_json": safe_json_dumps(args),
                        "tool.arguments_key_count": len(args) if isinstance(args, dict) else 0,
                        "tool.arguments_keys": sorted(args.keys()) if isinstance(args, dict) else None,
                    },
                )

                logger.info(f"🔧 Activating tool: '{name}'...")
                result = await self.available_tools.execute(name=name, tool_input=args)
                set_span_attributes(span, result_to_attributes(result))

                await self._handle_special_tool(name=name, result=result)

                if hasattr(result, "base64_image") and result.base64_image:
                    self._current_base64_image = result.base64_image

                observation = (
                    f"Observed output of cmd `{name}` executed:\n{str(result)}"
                    if result
                    else f"Cmd `{name}` completed with no output"
                )
                set_span_attributes(
                    span,
                    {
                        "tool.observation_preview": preview_text(observation),
                        "tool.duration_ms": round((time.perf_counter() - started_at) * 1000, 3),
                    },
                )
                return observation
            except json.JSONDecodeError as exc:
                record_exception(span, exc)
                error_msg = f"Error parsing arguments for {name}: Invalid JSON format"
                logger.error(
                    f"📝 Oops! The arguments for '{name}' don't make sense - invalid JSON, arguments:{command.function.arguments}"
                )
                set_span_attributes(
                    span,
                    {"tool.duration_ms": round((time.perf_counter() - started_at) * 1000, 3)},
                )
                return f"Error: {error_msg}"
            except Exception as e:
                record_exception(span, e)
                error_msg = f"⚠️ Tool '{name}' encountered a problem: {str(e)}"
                logger.exception(error_msg)
                set_span_attributes(
                    span,
                    {"tool.duration_ms": round((time.perf_counter() - started_at) * 1000, 3)},
                )
                return f"Error: {error_msg}"

    async def _handle_special_tool(self, name: str, result: Any, **kwargs):
        """Handle special tool execution and state changes."""
        if not self._is_special_tool(name):
            return

        if self._should_finish_execution(name=name, result=result, **kwargs):
            logger.info(f"🏁 Special tool '{name}' has completed the task!")
            self.state = AgentState.FINISHED

    @staticmethod
    def _should_finish_execution(**kwargs) -> bool:
        """Determine if tool execution should finish the agent."""
        return True

    def _is_special_tool(self, name: str) -> bool:
        """Check if tool name is in special tools list."""
        return name.lower() in [n.lower() for n in self.special_tool_names]

    async def cleanup(self):
        """Clean up resources used by the agent's tools."""
        logger.info(f"🧹 Cleaning up resources for agent '{self.name}'...")
        for tool_name, tool_instance in self.available_tools.tool_map.items():
            if hasattr(tool_instance, "cleanup") and asyncio.iscoroutinefunction(
                tool_instance.cleanup
            ):
                try:
                    logger.debug(f"🧼 Cleaning up tool: {tool_name}")
                    await tool_instance.cleanup()
                except Exception as e:
                    logger.error(
                        f"🚨 Error cleaning up tool '{tool_name}': {e}", exc_info=True
                    )
        logger.info(f"✨ Cleanup complete for agent '{self.name}'.")

    async def run(self, request: Optional[str] = None) -> str:
        """Run the agent with cleanup when done."""
        try:
            return await super().run(request)
        finally:
            await self.cleanup()
