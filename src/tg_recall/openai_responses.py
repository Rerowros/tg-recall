"""Optional, no-tools OpenAI Responses adapter for bounded cited synthesis.

The adapter has no archive, filesystem, Telegram, or tool capability.  It only
receives :class:`ProviderRequest`, whose evidence has already passed the local
policy gate.  The optional ``openai`` import happens on the first request so a
core installation remains fully offline and dependency-free.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from .llm_answers import ProviderFailure, ProviderRequest, ProviderResponse, ProviderUsage, serialize_provider_request


_ANSWER_SCHEMA: dict[str, Any] = {
    "type": "json_schema",
    "name": "tg_recall_cited_answer",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "answer": {"type": "string"},
            "evidence_ids": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["answer", "evidence_ids"],
        "additionalProperties": False,
    },
}
_INSTRUCTIONS = (
    "Answer only from the supplied evidence. Return JSON matching the schema. "
    "evidence_ids must contain only supplied IDs supporting the answer. "
    "Do not request, invoke, or describe tools or actions."
)


class OpenAIResponsesProvider:
    """Structured Responses API adapter with local fallback-friendly failures.

    ``api_key`` is passed only to the lazily created SDK client.  It is never
    placed in a provider request, structured response, audit object, exception
    message, or this class's representation.  Tests may inject a minimal
    client with a ``responses.create`` method; no network is used by default.
    """

    def __init__(
        self,
        *,
        api_key: str,
        timeout_seconds: float = 30.0,
        max_output_tokens: int = 800,
        client: Any | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("OpenAI API key is required")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if isinstance(max_output_tokens, bool) or not isinstance(max_output_tokens, int) or max_output_tokens < 1:
            raise ValueError("max_output_tokens must be a positive integer")
        # An injected test/client implementation owns credentials itself, so
        # retaining an unused key here would only enlarge its lifetime.
        self._api_key: str | None = api_key if client is None else None
        self._timeout_seconds = timeout_seconds
        self._max_output_tokens = max_output_tokens
        self._client = client

    def __repr__(self) -> str:
        return (
            "OpenAIResponsesProvider("
            f"timeout_seconds={self._timeout_seconds!r}, "
            f"max_output_tokens={self._max_output_tokens!r}, "
            f"client_injected={self._client is not None})"
        )

    def answer(self, request: ProviderRequest) -> ProviderResponse:
        """Send one stateless structured request without any tools/functions."""

        try:
            response = self._get_client().responses.create(
                model=request.model,
                instructions=_INSTRUCTIONS,
                input=serialize_provider_request(request),
                text={"format": _ANSWER_SCHEMA},
                store=False,
                timeout=self._timeout_seconds,
                max_output_tokens=self._max_output_tokens,
            )
        except ProviderFailure:
            raise
        except Exception as exc:
            raise ProviderFailure(_failure_class(exc)) from None
        try:
            payload = _parse_structured_output(response)
            return ProviderResponse(
                text=payload["answer"],
                evidence_ids=tuple(payload["evidence_ids"]),
                usage=_provider_usage(response),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            # Let the vendor-neutral core classify a malformed answer as
            # ``invalid_response`` (rather than a transport outage).
            raise ProviderFailure("invalid_response") from None

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from openai import OpenAI
        except ImportError:
            raise ProviderFailure("unavailable", "OpenAI Responses SDK is not installed") from None
        # Keep the raw key only until the SDK accepts it.  It is then managed
        # inside the SDK client and no longer exists in this adapter instance.
        api_key = self._api_key
        if api_key is None:
            raise ProviderFailure("unavailable", "OpenAI client credentials are unavailable")
        self._client = OpenAI(api_key=api_key, timeout=self._timeout_seconds)
        self._api_key = None
        return self._client


def _parse_structured_output(response: Any) -> dict[str, Any]:
    output_text = getattr(response, "output_text", None)
    if not isinstance(output_text, str):
        raise ValueError("response has no structured output text")
    payload = json.loads(output_text)
    if not isinstance(payload, dict) or set(payload) != {"answer", "evidence_ids"}:
        raise ValueError("response does not match cited answer schema")
    if not isinstance(payload["answer"], str) or not isinstance(payload["evidence_ids"], list):
        raise ValueError("response fields have invalid types")
    if not all(isinstance(item, str) for item in payload["evidence_ids"]):
        raise ValueError("response evidence identifiers have invalid types")
    return payload


def _provider_usage(response: Any) -> ProviderUsage:
    usage = getattr(response, "usage", None)
    if usage is None:
        return ProviderUsage()
    return ProviderUsage(
        input_tokens=_usage_value(usage, "input_tokens"),
        output_tokens=_usage_value(usage, "output_tokens"),
    )


def _usage_value(usage: Any, key: str) -> int | None:
    value = usage.get(key) if isinstance(usage, Mapping) else getattr(usage, key, None)
    return value if isinstance(value, int) and value >= 0 else None


def _failure_class(exc: Exception) -> str:
    name = type(exc).__name__.lower()
    if isinstance(exc, TimeoutError) or "timeout" in name:
        return "timeout"
    if "rate" in name and "limit" in name:
        return "rate_limit"
    if "authentication" in name or "permission" in name or "auth" in name:
        return "authentication"
    if isinstance(exc, OSError) or "connection" in name or "network" in name:
        return "network"
    return "unavailable"
