"""Local THUFootball credential loading."""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
CREDENTIAL_ENV_NAMES = ("THUFOOTBALL_OPENID", "THUFOOTBALL_SESSION_KEY")


def load_env_file(path: Path = DEFAULT_ENV_FILE) -> None:
    """Load THUFootball credentials without replacing process values."""

    if not path.is_file():
        return

    for line in path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        name = name.strip()
        if name not in CREDENTIAL_ENV_NAMES:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(name, value)


def load_credentials(path: Path = DEFAULT_ENV_FILE) -> tuple[str, str]:
    """Load and return ``(openid, session_key)``; missing values are empty."""

    load_env_file(path)
    return (
        os.environ.get("THUFOOTBALL_OPENID", ""),
        os.environ.get("THUFOOTBALL_SESSION_KEY", ""),
    )
