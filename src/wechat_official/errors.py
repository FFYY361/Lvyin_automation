"""Stable, secret-safe errors for the WeChat article pipeline."""

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


class SourceValidationError(WechatArticleError):
    """Raised before an unsafe or unsupported source request is sent."""


class SourceAccessBlocked(WechatArticleError):
    """Raised when a public article returns a verification or block page."""


class SourceInvalidResponse(WechatArticleError):
    """Raised when an article response does not contain a usable article."""


class UnsafeHtml(WechatArticleError):
    """Raised when HTML cannot be normalised safely."""


class TemplateContractError(WechatArticleError):
    """Raised when an explicit template contract is incomplete or ambiguous."""


class PreviewValidationError(WechatArticleError):
    """Raised when preview source data or column configuration is invalid."""


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
