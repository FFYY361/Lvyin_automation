"""Shared default-cover selection for Auto Preview callers."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from wechat_official import CoverFile, CoverMediaId

DEFAULT_COVER_MEDIA_ID_ENV = "WEBSITE_DEFAULT_COVER_MEDIA_ID"


def default_cover(
    *,
    environment: Mapping[str, str] | None = None,
    fallback_path: str | Path | None = None,
) -> CoverFile | CoverMediaId:
    """Prefer the configured reusable WeChat material, with CLI fallback."""

    values = os.environ if environment is None else environment
    media_id = values.get(DEFAULT_COVER_MEDIA_ID_ENV, "").strip()
    if media_id:
        return CoverMediaId(media_id)
    path = (
        Path(fallback_path)
        if fallback_path is not None
        else Path(__file__).with_name("assets") / "default_cover.png"
    )
    return CoverFile(path)
