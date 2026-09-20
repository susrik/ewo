"""LLM client: FakeLLM behaviour + OpenAI wire format via mocked transport.

Note: the openai 3.x SDK uses a vendored httpx (``httpx2``), so respx cannot
intercept it; we inject an ``httpx2.MockTransport`` via the ``http_client``
parameter instead. Still zero network access.
"""

from __future__ import annotations

import json
from typing import Any

import httpx2

from ewo.config import LLMConfig
from ewo.core.llm import FakeLLM, OpenAILLM


def test_fake_llm_records_calls() -> None:
    fake = FakeLLM(responses=["first", "second"])
    r1 = fake.complete("p1", system="sys")
    r2 = fake.complete("p2", smart=True)
    r3 = fake.complete("p3")  # sticks at the last canned response
    assert (r1.text, r2.text, r3.text) == ("first", "second", "second")
    assert r1.tokens_used == 10
    assert fake.calls[0] == {"prompt": "p1", "system": "sys", "smart": False}
    assert fake.calls[1]["smart"] is True


def _mocked_llm(response_json: dict[str, Any], requests: list[httpx2.Request]) -> OpenAILLM:
    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json=response_json)

    http_client = httpx2.Client(transport=httpx2.MockTransport(handler))
    return OpenAILLM(
        LLMConfig(
            base_url="https://llm.example/v1",
            api_key="k",
            model="cheap-model",
            smart_model="smart-model",
        ),
        http_client=http_client,
    )


def test_openai_llm_wire_format() -> None:
    requests: list[httpx2.Request] = []
    llm = _mocked_llm(
        {
            "id": "x",
            "object": "chat.completion",
            "created": 0,
            "model": "cheap-model",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "hello"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12},
        },
        requests,
    )
    result = llm.complete("hi", system="be brief")
    assert result.text == "hello"
    assert result.tokens_used == 12

    assert str(requests[0].url) == "https://llm.example/v1/chat/completions"
    assert requests[0].headers["authorization"] == "Bearer k"
    payload = json.loads(requests[0].content)
    assert payload["model"] == "cheap-model"
    assert payload["messages"] == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "hi"},
    ]


def test_openai_llm_smart_model_no_system() -> None:
    requests: list[httpx2.Request] = []
    llm = _mocked_llm(
        {
            "id": "x",
            "object": "chat.completion",
            "created": 0,
            "model": "smart-model",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": None}}],
        },
        requests,
    )
    result = llm.complete("hi", smart=True)
    assert result.text == ""
    assert result.tokens_used == 0
    payload = json.loads(requests[0].content)
    assert payload["model"] == "smart-model"
    assert payload["messages"] == [{"role": "user", "content": "hi"}]
