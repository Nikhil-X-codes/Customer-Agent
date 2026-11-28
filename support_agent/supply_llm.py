"""OpenAI-compatible provider calls with conservative Groq -> OpenRouter failover."""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


class ProviderError(Exception):
    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


class ChatProvider:
    def __init__(self, name: str, url: str, api_key: str, model: str,
                 timeout: float = 8.0) -> None:
        if not api_key:
            raise ValueError(f"{name} API key is required")
        if not url.startswith("https://"):
            raise ValueError("Model endpoint must use HTTPS")
        self.name, self.url, self.api_key, self.model, self.timeout = (
            name, url, api_key, model, timeout)

    def complete(self, messages: list[dict], *, tools: list[dict] | None = None,
                 json_mode: bool = False) -> dict:
        payload = {"model": self.model, "messages": messages,
                   "max_completion_tokens": 700}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "required"
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if self.name == "groq":
            payload["reasoning_effort"] = "low"
            payload["include_reasoning"] = False
        request = Request(self.url, data=json.dumps(payload).encode(), method="POST",
                          headers={"Authorization": f"Bearer {self.api_key}",
                                   "Content-Type": "application/json"})
        try:
            with build_opener(_NoRedirect()).open(request, timeout=self.timeout) as response:
                raw = response.read(65537)
            if len(raw) > 65536:
                raise ProviderError("Model response too large")
            message = json.loads(raw)["choices"][0]["message"]
            if not isinstance(message, dict):
                raise ValueError("Invalid model message")
            return message
        except HTTPError as exc:
            raise ProviderError("Model provider HTTP error",
                                retryable=exc.code in (408, 429) or exc.code >= 500) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ProviderError("Model provider unavailable", retryable=True) from exc
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError("Model provider returned invalid data") from exc


class ProviderChain:
    def __init__(self, primary: ChatProvider, fallback: ChatProvider | None = None) -> None:
        self.primary, self.fallback = primary, fallback

    def complete(self, messages: list[dict], *, tools: list[dict] | None = None,
                 json_mode: bool = False) -> tuple[dict, str]:
        try:
            return self.primary.complete(messages, tools=tools, json_mode=json_mode), self.primary.name
        except ProviderError as exc:
            if not exc.retryable or self.fallback is None:
                raise
        return self.fallback.complete(messages, tools=tools, json_mode=json_mode), self.fallback.name


def configured_chain(groq_key: str, groq_model: str,
                     openrouter_key: str, openrouter_model: str) -> ProviderChain:
    primary = ChatProvider("groq", "https://api.groq.com/openai/v1/chat/completions",
                           groq_key, groq_model)
    fallback = (ChatProvider("openrouter", "https://openrouter.ai/api/v1/chat/completions",
                             openrouter_key, openrouter_model) if openrouter_key else None)
    return ProviderChain(primary, fallback)
