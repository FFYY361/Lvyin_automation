"""Website runtime configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"


def load_env_file(path: str | Path = DEFAULT_ENV_FILE) -> None:
    """Load unset values from a simple dotenv file."""

    env_path = Path(path)
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        name = name.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if not os.environ.get(name, "").strip():
            os.environ[name] = value


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _boolean(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be true or false")


@dataclass(frozen=True, slots=True)
class WebsiteSettings:
    database_url: str
    artifact_root: Path
    default_cover_media_id: str
    cookie_name: str
    cookie_secret: str
    cookie_secure: bool
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    log_level: str = "INFO"

    @classmethod
    def from_environment(
        cls,
        *,
        env_path: str | Path = DEFAULT_ENV_FILE,
    ) -> "WebsiteSettings":
        load_env_file(env_path)
        root = Path(os.environ.get("WEBSITE_ARTIFACT_ROOT", "var/artifacts"))
        if not root.is_absolute():
            root = PROJECT_ROOT / root
        secret = _required("WEBSITE_COOKIE_SECRET")
        if len(secret) < 32:
            raise RuntimeError("WEBSITE_COOKIE_SECRET must contain at least 32 characters")
        try:
            port = int(os.environ.get("WEBSITE_API_PORT", "8000"))
        except ValueError as exc:
            raise RuntimeError("WEBSITE_API_PORT must be an integer") from exc
        return cls(
            database_url=_required("WEBSITE_DATABASE_URL"),
            artifact_root=root.resolve(),
            default_cover_media_id=_required("WEBSITE_DEFAULT_COVER_MEDIA_ID"),
            cookie_name=os.environ.get("WEBSITE_COOKIE_NAME", "lvyin_session").strip()
            or "lvyin_session",
            cookie_secret=secret,
            cookie_secure=_boolean("WEBSITE_COOKIE_SECURE", False),
            api_host=os.environ.get("WEBSITE_API_HOST", "127.0.0.1").strip()
            or "127.0.0.1",
            api_port=port,
            log_level=os.environ.get("WEBSITE_LOG_LEVEL", "INFO").strip() or "INFO",
        )
