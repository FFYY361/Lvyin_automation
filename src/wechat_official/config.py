"""Minimal `.env` loading limited to WeChat credential names."""

from __future__ import annotations

import os
from pathlib import Path


DEFAULT_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
WECHAT_CREDENTIAL_NAMES = ("WECHAT_APP_ID", "WECHAT_APP_SECRET")


def load_wechat_env(path: str | Path = DEFAULT_ENV_FILE) -> None:
    env_path = Path(path)
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        name = name.strip()
        if name not in WECHAT_CREDENTIAL_NAMES:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(name, value)
