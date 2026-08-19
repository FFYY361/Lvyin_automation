"""Stable, secret-safe errors for the AI dialogue service."""

from __future__ import annotations


class AIServiceError(RuntimeError):
    """Base error exposed by the AI client and service."""

    def __init__(
        self,
        message: str,
        *,
        stage: str,
        retryable: bool = False,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.retryable = retryable
        self.status_code = status_code


class AIServiceConfigurationError(AIServiceError):
    """Raised when a model profile or its credential is invalid."""


class AIServiceNetworkError(AIServiceError):
    """Raised when the provider cannot be reached."""


class AIServiceAuthenticationError(AIServiceError):
    """Raised when the provider rejects the API key."""


class AIServiceRateLimitError(AIServiceError):
    """Raised when the provider rejects a request due to a usage limit."""


class AIServiceProviderError(AIServiceError):
    """Raised when the provider returns an unsuccessful HTTP response."""


class AIServiceInvalidResponse(AIServiceError):
    """Raised when a response is not a usable Chat Completions response."""
