"""Public AI dialogue service."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import httpx

from .client import OpenAICompatibleClient
from .config import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_ENV_FILE,
    load_ai_service_config,
    load_api_key,
)
from .models import ChatMessage, ChatResult


class AIChatService:
    """Run dialogue against one configured model profile."""

    def __init__(
        self,
        client: OpenAICompatibleClient,
        *,
        close_client: bool = False,
    ) -> None:
        self._client = client
        self._close_client = close_client

    @classmethod
    def from_config(
        cls,
        profile: str | None = None,
        *,
        config_path: str | Path = DEFAULT_CONFIG_PATH,
        env_path: str | Path = DEFAULT_ENV_FILE,
        http_client: httpx.AsyncClient | None = None,
    ) -> AIChatService:
        selected = load_ai_service_config(config_path).get_profile(profile)
        client = OpenAICompatibleClient(
            selected,
            load_api_key(selected, env_path),
            http_client=http_client,
        )
        return cls(client, close_client=http_client is None)

    async def __aenter__(self) -> AIChatService:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._close_client:
            await self._client.aclose()

    async def chat(self, messages: Sequence[ChatMessage]) -> ChatResult:
        return await self._client.chat(messages)
