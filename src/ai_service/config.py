"""Model-profile and credential loading for the AI dialogue service."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .errors import AIServiceConfigurationError

DEFAULT_CONFIG_PATH = Path(__file__).with_name("config.json")
DEFAULT_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
_PROFILE_FIELDS = {
    "base_url",
    "model",
    "api_key_env",
    "temperature",
    "max_tokens",
    "timeout_seconds",
}


def _configuration_error(message: str) -> AIServiceConfigurationError:
    return AIServiceConfigurationError(message, stage="configuration")


@dataclass(frozen=True)
class ModelProfile:
    name: str
    base_url: str
    model: str
    api_key_env: str
    temperature: float
    max_tokens: int
    timeout_seconds: float


@dataclass(frozen=True)
class AIServiceConfig:
    default_profile: str
    profiles: dict[str, ModelProfile]

    def get_profile(self, name: str | None = None) -> ModelProfile:
        selected = name or self.default_profile
        try:
            return self.profiles[selected]
        except KeyError as exc:
            available = ", ".join(sorted(self.profiles))
            raise _configuration_error(
                f"unknown AI model profile {selected!r}; available: {available}"
            ) from exc


def _required_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _configuration_error(f"{field} must be a non-empty string")
    return value.strip()


def _profile(name: str, raw: object) -> ModelProfile:
    if not isinstance(raw, dict):
        raise _configuration_error(f"profile {name!r} must be a JSON object")
    if set(raw) != _PROFILE_FIELDS:
        missing = sorted(_PROFILE_FIELDS - set(raw))
        extra = sorted(set(raw) - _PROFILE_FIELDS)
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if extra:
            details.append("unknown " + ", ".join(extra))
        raise _configuration_error(
            f"profile {name!r} fields are invalid: {'; '.join(details)}"
        )

    base_url = _required_string(raw["base_url"], f"profiles.{name}.base_url")
    parsed_url = urlparse(base_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise _configuration_error(
            f"profiles.{name}.base_url must be an HTTP(S) URL"
        )
    model = _required_string(raw["model"], f"profiles.{name}.model")
    api_key_env = _required_string(
        raw["api_key_env"], f"profiles.{name}.api_key_env"
    )
    if _ENV_NAME.fullmatch(api_key_env) is None:
        raise _configuration_error(
            f"profiles.{name}.api_key_env must be an uppercase environment name"
        )

    temperature = raw["temperature"]
    if (
        isinstance(temperature, bool)
        or not isinstance(temperature, (int, float))
        or not 0 <= temperature <= 2
    ):
        raise _configuration_error(
            f"profiles.{name}.temperature must be between 0 and 2"
        )
    max_tokens = raw["max_tokens"]
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens < 1:
        raise _configuration_error(
            f"profiles.{name}.max_tokens must be a positive integer"
        )
    timeout_seconds = raw["timeout_seconds"]
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or timeout_seconds <= 0
    ):
        raise _configuration_error(
            f"profiles.{name}.timeout_seconds must be positive"
        )
    return ModelProfile(
        name=name,
        base_url=base_url.rstrip("/"),
        model=model,
        api_key_env=api_key_env,
        temperature=float(temperature),
        max_tokens=max_tokens,
        timeout_seconds=float(timeout_seconds),
    )


def load_ai_service_config(path: str | Path = DEFAULT_CONFIG_PATH) -> AIServiceConfig:
    config_path = Path(path)
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise _configuration_error(f"cannot read AI service config: {config_path}") from exc
    if not isinstance(raw, dict) or set(raw) != {"default_profile", "profiles"}:
        raise _configuration_error(
            "AI service config must contain only default_profile and profiles"
        )
    default_profile = _required_string(raw["default_profile"], "default_profile")
    raw_profiles = raw["profiles"]
    if not isinstance(raw_profiles, dict) or not raw_profiles:
        raise _configuration_error("profiles must be a non-empty JSON object")

    profiles: dict[str, ModelProfile] = {}
    for raw_name, raw_profile in raw_profiles.items():
        name = _required_string(raw_name, "profile name")
        profiles[name] = _profile(name, raw_profile)
    if default_profile not in profiles:
        raise _configuration_error("default_profile does not exist in profiles")
    return AIServiceConfig(default_profile=default_profile, profiles=profiles)


def load_api_key(
    profile: ModelProfile,
    env_path: str | Path = DEFAULT_ENV_FILE,
) -> str:
    process_value = os.environ.get(profile.api_key_env, "").strip()
    if process_value:
        return process_value

    path = Path(env_path)
    if path.is_file():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            name, value = stripped.split("=", 1)
            if name.strip() != profile.api_key_env:
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            if value:
                return value
            break
    raise _configuration_error(f"{profile.api_key_env} is required")
