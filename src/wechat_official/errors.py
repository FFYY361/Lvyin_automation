"""Stable, secret-safe errors for the WeChat Official Account adapter."""

from __future__ import annotations


class WechatArticleError(RuntimeError):
    """Base error exposed by the public article and draft adapters."""

    def __init__(
        self,
        message: str,
        *,
        stage: str,
        retryable: bool = False,
        error_code: int | None = None,
        observed_ip: str | None = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.retryable = retryable
        self.error_code = error_code
        self.observed_ip = observed_ip


class WechatConfigurationError(WechatArticleError):
    """Raised when official-account configuration is missing or invalid."""


class WechatAuthenticationError(WechatArticleError):
    """Raised when WeChat rejects the AppID/AppSecret or access token."""


class WechatPermissionError(WechatArticleError):
    """Raised when the official account lacks an API permission."""


class WechatRateLimited(WechatArticleError):
    """Raised when an official-account API quota or frequency limit is hit."""


class MediaUploadError(WechatArticleError):
    """Raised when a body image or cover cannot be uploaded."""


class DraftValidationError(WechatArticleError):
    """Raised before a malformed draft request reaches WeChat."""


class DraftWriteError(WechatArticleError):
    """Raised when WeChat rejects a draft operation."""


class WechatTimeout(WechatArticleError):
    """Raised when an external HTTP operation times out."""
