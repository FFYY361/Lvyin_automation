"""Public contracts for auto_preview."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Literal

from wechat_official import CoverFile, CoverMediaId


class Competition(StrEnum):
    MALE = "male"
    FEMALE = "female"
    FUTSAL = "futsal"


class Stage(StrEnum):
    DATA = "data"
    ARTICLE = "article"
    PUBLISH = "publish"

    @property
    def number(self) -> int:
        return {
            Stage.DATA: 1,
            Stage.ARTICLE: 2,
            Stage.PUBLISH: 3,
        }[self]


CoverInput = CoverFile | CoverMediaId


@dataclass(frozen=True, slots=True)
class PipelineRequest:
    preview_date: date
    competition: Competition
    stage: Stage = Stage.ARTICLE
    cover: CoverInput | None = None
    override: bool = False


@dataclass(frozen=True, slots=True)
class PipelineResult:
    status: Literal["ok", "paused"]
    completed_stage: Stage
    run_directory: Path
    source_path: Path
    article_directory: Path | None = None
    draft_media_id: str | None = None
    next_command: str | None = None
