"""CrewAI's native Responses provider, adapted to its tool executor's history.

Requires CrewAI >= 1.15.1,<1.16. HTTP, structured outputs, callbacks, and token tracking
stay with CrewAI. The gateway owns routing and provider credentials; this client
only uses a model alias and a caller token. Existing agent factories are nonstreaming.
"""
from __future__ import annotations

import copy
from typing import Any, Literal

from crewai.llms.providers.openai.completion import OpenAICompletion


def _input_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert executor Chat history into valid stateless Responses items."""
    result: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if role == "tool":
            call_id = message.get("tool_call_id")
            if not isinstance(call_id, str) or not call_id or not isinstance(content, str):
                raise ValueError("Invalid tool result in model history")
            result.append({"type": "function_call_output", "call_id": call_id, "output": content})
            continue
        if role not in ("system", "developer", "user", "assistant"):
            raise ValueError("Unsupported message role in model history")
        if content is not None:
            if isinstance(content, str):
                converted: str | list[dict[str, Any]] = content
            elif isinstance(content, list):
                converted = []
                for part in content:
                    if not isinstance(part, dict) or part.get("type") != "text" or not isinstance(part.get("text"), str):
                        raise ValueError("Unsupported content in model history")
                    converted.append({"type": "input_text", "text": part["text"]})
            else:
                raise ValueError("Unsupported content in model history")
            result.append({"role": role, "content": converted})
        calls = message.get("tool_calls") or []
        if calls and role != "assistant":
            raise ValueError("Only assistant messages can contain tool calls")
        if not isinstance(calls, list):
            raise ValueError("Invalid tool calls in model history")
        for call in calls:
            if not isinstance(call, dict) or call.get("type") != "function":
                raise ValueError("Unsupported tool call in model history")
            function = call.get("function")
            if not isinstance(function, dict):
                raise ValueError("Invalid function call in model history")
            call_id, name, arguments = call.get("id"), function.get("name"), function.get("arguments")
            if not isinstance(call_id, str) or not call_id or not isinstance(name, str) or not name:
                raise ValueError("Invalid function identity in model history")
            if not isinstance(arguments, str):
                raise ValueError("Invalid function arguments in model history")
            result.append({"type": "function_call", "call_id": call_id, "name": name, "arguments": arguments})
        if content is None and not calls:
            raise ValueError("Message has no supported model input")
    return result


class GatewayResponsesLLM(OpenAICompletion):
    """The nonstreaming, stateless Responses client used by our CrewAI agents."""

    api: Literal["responses"] = "responses"
    store: Literal[False] = False
    auto_chain: Literal[False] = False
    auto_chain_reasoning: Literal[False] = False
    previous_response_id: None = None
    # All current CrewAI services use nonstreaming calls. Their UI task polling
    # is unrelated to provider SSE. Do not inherit CrewAI's unverified SSE tool path.
    stream: Literal[False] = False

    def _prepare_responses_params(self, messages, tools=None, response_model=None) -> dict[str, Any]:
        params = super()._prepare_responses_params(messages, tools, response_model)
        if params.get("previous_response_id") or params.get("conversation"):
            raise ValueError("llm-service requires the full stateless message history")
        params["input"] = _input_messages(params["input"])
        params["store"] = False
        return params

    def _convert_tools_for_responses(self, tools) -> list[dict[str, Any]]:
        converted = copy.deepcopy(super()._convert_tools_for_responses(tools))
        for tool in converted:
            # Existing CrewAI schemas can contain optional fields. Responses'
            # strict default must not make them all required implicitly.
            tool["strict"] = False
            tool.setdefault("parameters", {"type": "object", "properties": {}, "additionalProperties": False})
        return converted

    def _extract_function_calls_from_response(self, response) -> list[dict[str, Any]]:
        calls = super()._extract_function_calls_from_response(response)
        for call in calls:
            # Native Responses handling reads name/arguments, whereas CrewAI's
            # executor reads function.name/function.arguments. Supply both views.
            call["type"] = "function"
            call["function"] = {"name": call["name"], "arguments": call["arguments"]}
        return calls

    def _extract_responses_finish_reason_and_id(self, response):
        if response.status != "completed" or response.error is not None:
            raise ValueError("llm-service response did not complete successfully")
        if any(
            item.type == "message" and any(part.type == "refusal" for part in item.content)
            for item in response.output
        ):
            raise ValueError("llm-service response contains a refusal")
        if not response.output_text and not any(item.type == "function_call" for item in response.output):
            raise ValueError("llm-service response has no assistant text or function calls")
        return super()._extract_responses_finish_reason_and_id(response)
