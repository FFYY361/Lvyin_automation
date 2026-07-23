"""Minimal `.env` loading limited to the AMap weather API key."""

from __future__ import annotations

import os
from pathlib import Path

from .errors import WeatherConfigurationError

DEFAULT_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
AMAP_API_KEY_ENV = "AMAP_WEATHER_API_KEY"


def load_weather_env(path: str | Path = DEFAULT_ENV_FILE) -> None:
    env_path = Path(path)
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        if name.strip() != AMAP_API_KEY_ENV:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(AMAP_API_KEY_ENV, value)


def load_api_key(path: str | Path = DEFAULT_ENV_FILE) -> str:
    load_weather_env(path)
    api_key = os.environ.get(AMAP_API_KEY_ENV, "").strip()
    if not api_key:
        raise WeatherConfigurationError(
            f"{AMAP_API_KEY_ENV} is required",
            stage="configuration",
        )
    return api_key
