"""Shared default-cover selection for Auto Preview callers."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from wechat_official import CoverMediaId

from .errors import PipelineError

DEFAULT_COVER_MEDIA_ID_ENV = "WEBSITE_DEFAULT_COVER_MEDIA_ID"
_DEFAULT_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def _env_file_media_id(path: Path | None = None) -> str:
    path = _DEFAULT_ENV_FILE if path is None else path
    if not path.is_file():
        return ""
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        if name.strip() != DEFAULT_COVER_MEDIA_ID_ENV:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        return value.strip()
    return ""


def default_cover(
    *,
    environment: Mapping[str, str] | None = None,
) -> CoverMediaId:
    """Return the configured reusable WeChat cover material."""

    values = os.environ if environment is None else environment
    media_id = values.get(DEFAULT_COVER_MEDIA_ID_ENV, "").strip()
    if not media_id and environment is None:
        media_id = _env_file_media_id()
    if media_id:
        return CoverMediaId(media_id)
    raise PipelineError(
        f"{DEFAULT_COVER_MEDIA_ID_ENV} is required when no cover is provided",
        stage="article",
    )
