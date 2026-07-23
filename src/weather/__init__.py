"""AMap-backed daily weather queries."""

from .client import AmapWeatherClient
from .errors import (
    ForecastUnavailable,
    HistoricalWeatherUnsupported,
    InvalidAdcode,
    WeatherAuthenticationError,
    WeatherConfigurationError,
    WeatherError,
    WeatherInvalidResponse,
    WeatherNetworkError,
    WeatherQuotaExceeded,
)
from .models import DailyWeather
from .service import WeatherQueryService

__all__ = [
    "AmapWeatherClient",
    "DailyWeather",
    "ForecastUnavailable",
    "HistoricalWeatherUnsupported",
    "InvalidAdcode",
    "WeatherAuthenticationError",
    "WeatherConfigurationError",
    "WeatherError",
    "WeatherInvalidResponse",
    "WeatherNetworkError",
    "WeatherQueryService",
    "WeatherQuotaExceeded",
]
