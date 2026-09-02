"""Configurable OpenAI-compatible AI dialogue service."""

from .client import OpenAICompatibleClient
from .config import AIServiceConfig, ModelProfile, load_ai_service_config
from .errors import (
    AIServiceAuthenticationError,
    AIServiceConfigurationError,
    AIServiceError,
    AIServiceInvalidResponse,
    AIServiceNetworkError,
    AIServiceProviderError,
    AIServiceRateLimitError,
)
from .models import ChatMessage, ChatResult, TokenUsage
from .service import AIChatService

__all__ = [
    "AIChatService",
    "AIServiceAuthenticationError",
    "AIServiceConfig",
    "AIServiceConfigurationError",
    "AIServiceError",
    "AIServiceInvalidResponse",
    "AIServiceNetworkError",
    "AIServiceProviderError",
    "AIServiceRateLimitError",
    "ChatMessage",
    "ChatResult",
    "ModelProfile",
    "OpenAICompatibleClient",
    "TokenUsage",
    "load_ai_service_config",
]
