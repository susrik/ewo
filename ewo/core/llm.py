"""LLM client abstraction.

``LLMClient`` is a small protocol; production uses ``OpenAILLM`` (any
OpenAI-protocol endpoint, base_url + api_key from config). Tests inject
``FakeLLM`` — no network, deterministic, records prompts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from ewo.config import LLMConfig


@dataclass
class LLMResult:
    text: str
    tokens_used: int = 0


class LLMClient(Protocol):
    def complete(self, prompt: str, system: str | None = None, smart: bool = False) -> LLMResult:
        """Return a completion for *prompt*; ``smart=True`` uses the smart model tier."""
        ...


class OpenAILLM:
    """OpenAI-protocol client. Lazily imports the openai SDK.

    ``http_client`` allows tests to inject a mocked transport (the openai 3.x
    SDK uses its own vendored httpx, out of reach of respx).
    """

    def __init__(self, config: LLMConfig, http_client: Any | None = None) -> None:
        self._config = config
        self._http_client = http_client

    def complete(self, prompt: str, system: str | None = None, smart: bool = False) -> LLMResult:
        from openai import OpenAI

        client = OpenAI(
            base_url=self._config.base_url,
            api_key=self._config.api_key,
            http_client=self._http_client,
            timeout=self._config.request_timeout,
        )
        messages: list[dict[str, str]] = []
        if system is not None:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        response = client.chat.completions.create(
            model=self._config.smart_model if smart else self._config.model,
            messages=messages,  # type: ignore[arg-type]
        )
        text = response.choices[0].message.content or ""
        tokens = response.usage.total_tokens if response.usage else 0
        return LLMResult(text=text, tokens_used=tokens)


@dataclass
class FakeLLM:
    """Test double: canned responses, records every prompt."""

    responses: list[str] = field(default_factory=lambda: ["fake response"])
    calls: list[dict[str, object]] = field(default_factory=list)
    tokens_per_call: int = 10

    def complete(self, prompt: str, system: str | None = None, smart: bool = False) -> LLMResult:
        self.calls.append({"prompt": prompt, "system": system, "smart": smart})
        index = min(len(self.calls) - 1, len(self.responses) - 1)
        return LLMResult(text=self.responses[index], tokens_used=self.tokens_per_call)
