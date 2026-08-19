"""Public request and response models for AI dialogue."""

from __future__ import annotations

from dataclasses import dataclass

from .errors import AIServiceConfigurationError

_ROLES = frozenset({"system", "user", "assistant"})


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str

    def __post_init__(self) -> None:
        if self.role not in _ROLES:
            raise AIServiceConfigurationError(
                "chat message role must be system, user, or assistant",
                stage="validation",
            )
        if not isinstance(self.content, str) or not self.content.strip():
            raise AIServiceConfigurationError(
                "chat message content must be a non-empty string",
                stage="validation",
            )


@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True)
class ChatResult:
    content: str
    profile: str
    model: str
    finish_reason: str | None
    usage: TokenUsage
