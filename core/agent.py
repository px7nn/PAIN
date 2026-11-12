import inspect
import json

from dataclasses import dataclass
from typing      import Any


class AgentError(Exception):
    """Raised when agent execution fails"""
    ...


def format_tool_content(result: Any, max_chars: int = 2400) -> str:
    """Format and govern tool output length to prevent context explosion"""
    serialized = json.dumps(result, ensure_ascii=False, default=str)
    if len(serialized) <= max_chars:
        return serialized

    if isinstance(result, dict) and "result" in result:
        inner = result["result"]
        if isinstance(inner, str):
            truncated = inner[:max_chars - 120] + "\n... [Truncated to fit context budget] ..."
            return json.dumps({
                "ok": result.get("ok", True),
                "tool": result.get("tool"),
                "result": truncated,
                "_truncated": True
            }, ensure_ascii=False)
        elif isinstance(inner, list):
            return json.dumps({
                "ok": result.get("ok", True),
                "tool": result.get("tool"),
                "result": inner[:8],
                "_truncated": True,
                "_original_count": len(inner)
            }, ensure_ascii=False)

    return serialized[:max_chars - 60] + "... [Truncated to fit context budget] ..."


@dataclass
class AgentResponse:
    content: str
    tool_calls: int
    finish_reason: str | None
    usage: dict


class Agent:
    def __init__(
        self,
        tool_manager,
        generate,
        max_tool_rounds: int = 5,
        max_tool_calls: int = 8,
        generate_stream=None
    ):
        self.tool_manager = tool_manager
        self.generate = generate
        self.generate_stream = generate_stream

        self.max_tool_rounds = max_tool_rounds
        self.max_tool_calls = max_tool_calls

    # --------------------------------------------------
    # TOOL EXECUTION
    # --------------------------------------------------

    def _execute_tool(self, tool_call: dict, event_cb=None) -> tuple[str, dict]:
        call_id = tool_call.get("id")

        try:
            function = tool_call["function"]
            name = function["name"]
            arguments = function["arguments"]

            if isinstance(arguments, str):
                arguments = json.loads(arguments)

            if not isinstance(arguments, dict):
                raise ValueError("Arguments must be a JSON object.")

        except (KeyError, TypeError, ValueError) as exc:
            return call_id, {
                "ok": False,
                "error": f"Invalid tool call: {exc}"
            }
            
        if event_cb:
            event_cb({
                "type": "tool_start",
                "call_id": call_id,
                "name": name,
                "arguments": arguments
            })
        
        result = self.tool_manager.execute(name, arguments)
        
        if event_cb:
            event_cb({
                "type": "tool_result",
                "call_id": call_id,
                "result": result
            })

        return call_id, result

    # --------------------------------------------------
    # EXECUTION LOOP
    # --------------------------------------------------

    def run(
        self,
        messages: list[dict],
        model_name: str,
        event_cb = None 
    ) -> AgentResponse:
        if not isinstance(messages, list) or not messages:
            raise AgentError("Messages must be a non-empty list.")

        # Copy conversation context
        messages = json.loads(json.dumps(messages))

        available_tools = self.tool_manager.get_tools()

        total_calls = 0

        for _ in range(self.max_tool_rounds + 1):
            response = self.generate(
                messages=messages,
                model_name=model_name,
                tools=available_tools
            )

            assistant = response["message"]

            if not isinstance(assistant, dict):
                raise AgentError("Invalid assistant message.")

            tool_calls = assistant.get("tool_calls") or []

            # No tool calls: final response
            if not tool_calls:
                content = assistant.get("content") or assistant.get("reasoning_content") or ""

                return AgentResponse(
                    content=content,
                    tool_calls=total_calls,
                    finish_reason=response.get("finish_reason"),
                    usage=response.get("usage", {})
                )

            # Preserve assistant tool-call message
            messages.append({
                "role": "assistant",
                "content": assistant.get("content"),
                "tool_calls": tool_calls
            })

            # Execute sequentially
            for tool_call in tool_calls:
                call_id = tool_call.get("id")

                if total_calls >= self.max_tool_calls:
                    if not isinstance(call_id, str) or not call_id:
                        raise AgentError("Tool call has no valid ID.")
                    result = {
                        "ok": False,
                        "error": "Maximum tool-call limit reached."
                    }
                else:
                    call_id, result = self._execute_tool(tool_call, event_cb=event_cb)
                    total_calls += 1
                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": format_tool_content(result)
                })

        raise AgentError("Maximum tool execution rounds reached.")

    # --------------------------------------------------
    # STREAMING EXECUTION
    # --------------------------------------------------

    def stream(
        self,
        messages: list[dict],
        model_name: str,
        cancel_event=None
    ):
        if not isinstance(messages, list) or not messages:
            raise AgentError("Messages must be a non-empty list.")

        if not callable(getattr(self, "generate_stream", None)):
            raise AgentError("Streaming generation is not configured.")

        messages = json.loads(json.dumps(messages))
        available_tools = self.tool_manager.get_tools()
        total_calls = 0

        supports_cancel = False
        try:
            sig = inspect.signature(self.generate_stream)
            supports_cancel = (
                "cancel_event" in sig.parameters
                or any(
                    p.kind == inspect.Parameter.VAR_KEYWORD
                    for p in sig.parameters.values()
                )
            )
        except (TypeError, ValueError):
            supports_cancel = False

        for _ in range(self.max_tool_rounds + 1):
            if cancel_event is not None and cancel_event.is_set():
                return

            round_content = ""
            round_reasoning = ""
            last_finish_reason = None
            tool_calls = {}

            stream_kwargs = {
                "messages": messages,
                "model_name": model_name,
                "tools": available_tools,
            }
            if supports_cancel and cancel_event is not None:
                stream_kwargs["cancel_event"] = cancel_event

            for chunk in self.generate_stream(**stream_kwargs):
                if cancel_event is not None and cancel_event.is_set():
                    return

                if chunk.get("finish_reason"):
                    last_finish_reason = chunk.get("finish_reason")

                delta = chunk.get("delta") or {}
                content = delta.get("content")
                reasoning = delta.get("reasoning_content")

                if reasoning:
                    round_reasoning += reasoning

                if content:
                    round_content += content
                    yield {"type": "token", "content": content}

                for item in delta.get("tool_calls") or []:
                    index = item.get("index", 0)
                    current = tool_calls.setdefault(index, {
                        "id": None,
                        "type": "function",
                        "function": {"name": "", "arguments": ""}
                    })

                    if item.get("id"):
                        current["id"] = item["id"]

                    function = item.get("function") or {}
                    if function.get("name"):
                        current["function"]["name"] += function["name"]
                    if function.get("arguments"):
                        current["function"]["arguments"] += function["arguments"]

            if cancel_event is not None and cancel_event.is_set():
                return

            if not tool_calls:
                if not round_content and round_reasoning:
                    yield {"type": "token", "content": round_reasoning}
                if last_finish_reason == "length":
                    yield {"type": "truncated", "finish_reason": "length"}
                return

            assembled_calls = [tool_calls[index] for index in sorted(tool_calls)]
            messages.append({
                "role": "assistant",
                "content": round_content or None,
                "tool_calls": assembled_calls
            })

            for tool_call in assembled_calls:
                if cancel_event is not None and cancel_event.is_set():
                    return

                call_id = tool_call.get("id")
                if not isinstance(call_id, str) or not call_id:
                    raise AgentError("Streamed tool call has no valid ID.")

                if total_calls >= self.max_tool_calls:
                    result = {
                        "ok": False,
                        "error": "Maximum tool-call limit reached."
                    }
                else:
                    try:
                        function = tool_call["function"]
                        name = function["name"]
                        arguments = function["arguments"]
                        if isinstance(arguments, str):
                            arguments = json.loads(arguments)
                        if not isinstance(name, str) or not name:
                            raise ValueError("Invalid tool name.")
                        if not isinstance(arguments, dict):
                            raise ValueError("Arguments must be a JSON object.")
                    except (KeyError, TypeError, ValueError) as exc:
                        result = {"ok": False, "error": f"Invalid tool call: {exc}"}
                    else:
                        yield {
                            "type": "tool_start",
                            "call_id": call_id,
                            "name": name,
                            "arguments": arguments
                        }
                        result = self.tool_manager.execute(name, arguments)

                    total_calls += 1

                if cancel_event is not None and cancel_event.is_set():
                    return

                yield {
                    "type": "tool_result",
                    "call_id": call_id,
                    "result": result
                }

                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": format_tool_content(result)
                })

        raise AgentError("Maximum tool execution rounds reached.")