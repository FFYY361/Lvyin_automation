"""Public weather domain models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True, slots=True)
class DailyWeather:
    """One administrative region's daily forecast."""

    adcode: str
    region_name: str
    forecast_date: date
    condition: str
    low_c: int
    high_c: int
    wind_direction: str
    wind_level: str
    report_time: datetime
