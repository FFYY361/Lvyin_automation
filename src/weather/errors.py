"""Stable, secret-safe errors for the weather query layer."""

from __future__ import annotations


class WeatherError(RuntimeError):
    """Base error exposed by the weather client and service."""

    def __init__(
        self,
        message: str,
        *,
        stage: str,
        retryable: bool = False,
        error_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.retryable = retryable
        self.error_code = error_code


class WeatherConfigurationError(WeatherError):
    """Raised when the AMap API key or client configuration is invalid."""


class InvalidAdcode(WeatherError):
    """Raised when a public query receives an invalid administrative code."""


class HistoricalWeatherUnsupported(WeatherError):
    """Raised before HTTP when the requested date is in the past."""


class ForecastUnavailable(WeatherError):
    """Raised when AMap does not include the requested date in its forecast."""


class WeatherAuthenticationError(WeatherError):
    """Raised when AMap rejects the Web Service API key or its permissions."""


class WeatherQuotaExceeded(WeatherError):
    """Raised when AMap reports a quota or request-frequency limit."""


class WeatherNetworkError(WeatherError):
    """Raised after a retryable HTTP operation exhausts its retry."""


class WeatherInvalidResponse(WeatherError):
    """Raised when an AMap response cannot be mapped safely."""
