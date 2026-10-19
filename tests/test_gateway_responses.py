"""Exercise real CrewAI/OpenAI serialization without provider network access."""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
from openai import OpenAI
from openai.types.responses.response_input_item_param import ResponseInputItemParam
from pydantic import BaseModel, TypeAdapter, ValidationError
import unittest
from unittest.mock import patch

from tools.llm_gateway import GatewayResponsesLLM
from tools.responses_client import response_text


def terminal(output, *, status="completed"):
    return {
        "id": "resp_test", "object": "response", "created_at": 1,
        "model": "llm-trusted", "status": status, "error": None,
        "incomplete_details": {"reason": "max_output_tokens"} if status == "incomplete" else None,
        "output": output,
        "usage": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14,
                  "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}},
    }


def text_output(text):
    return [{"type": "message", "id": "msg_test", "role": "assistant", "status": "completed",
             "content": [{"type": "output_text", "text": text, "annotations": []}]}]


def gateway(handler):
    llm = GatewayResponsesLLM(model="llm-trusted", api_key="test-token", base_url="http://llm.test/v1")
    llm._client = OpenAI(api_key="test-token", base_url="http://llm.test/v1",
                         http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    return llm


class GatewayResponsesTests(unittest.TestCase):
    def test_tool_call_and_second_turn_use_responses_with_complete_history(self):
        sent = []
        def handler(request):
            assert request.url.path == "/v1/responses"
            assert request.headers["Authorization"] == "Bearer test-token"
            body = json.loads(request.content)
            sent.append(body)
            if len(sent) == 1:
                return httpx.Response(200, json=terminal([{
                    "type": "function_call", "id": "fc_test", "call_id": "call_search",
                    "name": "lookup", "arguments": '{"q":"InP"}', "status": "completed",
                }]))
            return httpx.Response(200, json=terminal(text_output("找到文献")))

        llm = gateway(handler)
        history = [{"role": "system", "content": "Use the search tool."}, {"role": "user", "content": "Find InP papers."}]
        tools = [{"type": "function", "function": {
            "name": "lookup", "description": "Search papers", "parameters": {
                "type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"],
            },
        }}]
        calls = llm.call(history, tools=tools)
        # Use the actual executor parser: native Responses' flat arguments were
        # previously lost here, causing tools to receive {} instead of their input.
        from crewai.agents.crew_agent_executor import CrewAgentExecutor
        call_id, name, arguments = CrewAgentExecutor._parse_native_tool_call(SimpleNamespace(), calls[0])
        assert (call_id, name, arguments) == ("call_search", "lookup", '{"q":"InP"}')
        second_history = history + [
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}},
            ]},
            {"role": "tool", "tool_call_id": call_id, "content": "Paper about InP etching"},
        ]
        assert llm.call(second_history, tools=tools) == "找到文献"
        assert len(sent) == 2
        assert all(body["store"] is False and "previous_response_id" not in body for body in sent)
        assert sent[0]["tools"][0]["strict"] is False
        assert sent[1]["input"][-2:] == [
            {"type": "function_call", "call_id": "call_search", "name": "lookup", "arguments": '{"q":"InP"}'},
            {"type": "function_call_output", "call_id": "call_search", "output": "Paper about InP etching"},
        ]
        for item in sent[1]["input"]:
            TypeAdapter(ResponseInputItemParam).validate_python(item)
        # Preparing the provider payload must not mutate CrewAI's executor history.
        assert second_history[-1]["role"] == "tool"
        assert second_history[-2]["tool_calls"][0]["id"] == "call_search"


    def test_assistant_text_history_and_structured_response_use_native_api(self):
        sent = []
        class Report(BaseModel):
            summary: str
        def handler(request):
            sent.append(json.loads(request.content))
            return httpx.Response(200, json=terminal(text_output('{"summary":"ready"}')))
        llm = gateway(handler)
        result = llm.call([
            {"role": "user", "content": "Earlier question"},
            {"role": "assistant", "content": "Earlier answer"},
            {"role": "user", "content": "Summarize as JSON"},
        ], response_model=Report)
        assert result.model_dump() == {"summary": "ready"}
        body = sent[0]
        assert body["text"]["format"]["type"] == "json_schema"
        assert body["text"]["format"]["schema"]["properties"]["summary"]["type"] == "string"
        for item in body["input"]:
            TypeAdapter(ResponseInputItemParam).validate_python(item)


    def test_nonterminal_or_failed_response_is_not_an_empty_success(self):
        for status in ("failed", "incomplete", "in_progress"):
            with self.subTest(status=status):
                llm = gateway(lambda request: httpx.Response(200, json=terminal(text_output("partial"), status=status)))
                with self.assertRaisesRegex(ValueError, "did not complete"):
                    llm.call("hello")


    def test_refusal_or_missing_output_is_not_an_empty_success(self):
        refused = text_output("")
        refused[0]["content"] = [{"type": "refusal", "refusal": "Unavailable"}]
        for output in ([], refused):
            with self.subTest(output=output):
                llm = gateway(lambda request: httpx.Response(200, json=terminal(output)))
                with self.assertRaisesRegex(ValueError, "refusal|no assistant text"):
                    llm.call("hello")


    def test_gateway_client_cannot_enable_stateful_continuations(self):
        for settings in ({"store": True}, {"auto_chain": True}, {"auto_chain_reasoning": True},
                         {"previous_response_id": "resp_old"}, {"api": "completions"}, {"stream": True}):
            with self.subTest(settings=settings), self.assertRaises(ValidationError):
                GatewayResponsesLLM(model="llm-trusted", api_key="test-token", **settings)


    def test_json_http_consumers_read_responses_and_reject_partial_or_refusal(self):
        response = terminal(text_output('{"ok":true}'))
        assert json.loads(response_text(response)) == {"ok": True}
        with self.assertRaises(ValueError):
            response_text(terminal(text_output("partial"), status="incomplete"))
        refused = text_output("")
        refused[0]["content"] = [{"type": "refusal", "refusal": "Unavailable"}]
        with self.assertRaises(ValueError):
            response_text(terminal(refused))


    def test_literature_json_client_sends_standard_responses(self):
        from literature_downloader.search_planner import LLMJsonClient
        sent = []
        def post(url, **kwargs):
            sent.append((url, kwargs))
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: terminal(text_output('{"queries":["InP"]}')))
        with patch("literature_downloader.search_planner.requests.post", side_effect=post):
            result = LLMJsonClient("llm-service", "llm-trusted", "http://llm.test/v1", "test-token").complete_json("JSON only", {"topic": "InP"})
        assert result == {"queries": ["InP"]}
        url, request = sent[0]
        assert url == "http://llm.test/v1/responses"
        assert request["json"]["store"] is False
        assert request["json"]["text"] == {"format": {"type": "json_object"}}
        assert "messages" not in request["json"]


    def test_content_direct_json_branch_sends_standard_responses(self):
        from content_agents.common.llm import generate_json
        from content_agents.common.config import AgentConfig
        config = AgentConfig.from_env("test-bot")
        from dataclasses import replace
        config = replace(config, llm_base_url="http://llm.test/v1", llm_api_key="test-token", llm_model="llm-trusted")
        sent = []
        def post(url, body, **kwargs):
            sent.append((url, body))
            return terminal(text_output('{"ok":true}'))
        with patch("content_agents.common.llm.post_json", side_effect=post):
            assert generate_json("return JSON", config) == {"ok": True}
        assert sent[0][0] == "http://llm.test/v1/responses"
        assert sent[0][1]["store"] is False
        assert sent[0][1]["text"] == {"format": {"type": "json_object"}}


if __name__ == "__main__":
    unittest.main()
