"""OpenAI-compatible Chat Completions HTTP client."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import httpx

from .config import ModelProfile
from .errors import (
    AIServiceAuthenticationError,
    AIServiceConfigurationError,
    AIServiceInvalidResponse,
    AIServiceNetworkError,
    AIServiceProviderError,
    AIServiceRateLimitError,
)
from .models import ChatMessage, ChatResult, TokenUsage


def _invalid_response(message: str) -> AIServiceInvalidResponse:
    return AIServiceInvalidResponse(message, stage="response")


def _optional_count(value: object, path: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _invalid_response(f"invalid token count at {path}")
    return value


class OpenAICompatibleClient:
    """Send one non-streaming request using an OpenAI-compatible endpoint."""

    def __init__(
        self,
        profile: ModelProfile,
        api_key: str,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise AIServiceConfigurationError(
                "AI API key must be a non-empty string",
                stage="configuration",
            )
        self.profile = profile
        self._api_key = api_key.strip()
        self._owns_http_client = http_client is None
        self._http_client = http_client or httpx.AsyncClient(
            headers={"Accept": "application/json"}
        )
        self._closed = False

    async def __aenter__(self) -> OpenAICompatibleClient:
        if self._closed:
            raise AIServiceConfigurationError(
                "AI client is already closed",
                stage="configuration",
            )
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._owns_http_client:
            await self._http_client.aclose()

    @staticmethod
    def _raise_http_error(status_code: int) -> None:
        if status_code in {401, 403}:
            raise AIServiceAuthenticationError(
                "AI provider rejected the API key",
                stage="http",
                status_code=status_code,
            )
        if status_code == 429:
            raise AIServiceRateLimitError(
                "AI provider rate or quota limit was exceeded",
                stage="http",
                retryable=True,
                status_code=status_code,
            )
        raise AIServiceProviderError(
            f"AI provider returned HTTP {status_code}",
            stage="http",
            retryable=status_code >= 500,
            status_code=status_code,
        )

    async def chat(self, messages: Sequence[ChatMessage]) -> ChatResult:
        if self._closed:
            raise AIServiceConfigurationError(
                "AI client is closed",
                stage="configuration",
            )
        if not messages:
            raise AIServiceConfigurationError(
                "at least one chat message is required",
                stage="validation",
            )
        if any(not isinstance(message, ChatMessage) for message in messages):
            raise AIServiceConfigurationError(
                "messages must contain ChatMessage objects",
                stage="validation",
            )

        payload = {
            "model": self.profile.model,
            "messages": [
                {"role": message.role, "content": message.content}
                for message in messages
            ],
            "temperature": self.profile.temperature,
            "max_tokens": self.profile.max_tokens,
            "stream": False,
        }
        payload.update(self.profile.request_options)
        try:
            response = await self._http_client.post(
                f"{self.profile.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=payload,
                timeout=self.profile.timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise AIServiceNetworkError(
                "AI provider request timed out",
                stage="http",
                retryable=True,
            ) from exc
        except httpx.RequestError as exc:
            raise AIServiceNetworkError(
                "AI provider request failed",
                stage="http",
                retryable=True,
            ) from exc
        if not 200 <= response.status_code < 300:
            self._raise_http_error(response.status_code)
        try:
            raw = response.json()
        except (UnicodeDecodeError, ValueError) as exc:
            raise _invalid_response("AI provider returned invalid JSON") from exc
        return self._parse_result(raw)

    def _parse_result(self, raw: object) -> ChatResult:
        if not isinstance(raw, Mapping):
            raise _invalid_response("AI provider returned a non-object JSON value")
        choices = raw.get("choices")
        if (
            isinstance(choices, (str, bytes))
            or not isinstance(choices, Sequence)
            or not choices
            or not isinstance(choices[0], Mapping)
        ):
            raise _invalid_response("AI provider response has no choices")
        choice: Mapping[str, Any] = choices[0]
        message = choice.get("message")
        if not isinstance(message, Mapping):
            raise _invalid_response("AI provider response has no assistant message")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise _invalid_response("AI provider response has empty assistant content")

        finish_reason = choice.get("finish_reason")
        if finish_reason is not None and not isinstance(finish_reason, str):
            raise _invalid_response("AI provider response has invalid finish_reason")
        response_model = raw.get("model")
        if response_model is None:
            response_model = self.profile.model
        if not isinstance(response_model, str) or not response_model.strip():
            raise _invalid_response("AI provider response has invalid model")

        usage = raw.get("usage")
        if usage is None:
            token_usage = TokenUsage()
        elif isinstance(usage, Mapping):
            token_usage = TokenUsage(
                prompt_tokens=_optional_count(
                    usage.get("prompt_tokens"), "$.usage.prompt_tokens"
                ),
                completion_tokens=_optional_count(
                    usage.get("completion_tokens"), "$.usage.completion_tokens"
                ),
                total_tokens=_optional_count(
                    usage.get("total_tokens"), "$.usage.total_tokens"
                ),
            )
        else:
            raise _invalid_response("AI provider response has invalid usage")
        return ChatResult(
            content=content,
            profile=self.profile.name,
            model=response_model,
            finish_reason=finish_reason,
            usage=token_usage,
        )
