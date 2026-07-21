"""Public contracts for auto_preview."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
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
_COMPETITION_ORDER = {
    Competition.MALE: 0,
    Competition.FEMALE: 1,
    Competition.FUTSAL: 2,
}


@dataclass(frozen=True, slots=True)
class PipelineRequest:
    preview_dates: Sequence[date]
    competitions: Sequence[Competition]
    stage: Stage = Stage.ARTICLE
    cover: CoverInput | None = None
    override: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.preview_dates, (str, bytes)) or not isinstance(
            self.preview_dates, Sequence
        ):
            raise TypeError("preview_dates must be a sequence of date values")
        if isinstance(self.competitions, (str, bytes)) or not isinstance(
            self.competitions, Sequence
        ):
            raise TypeError("competitions must be a sequence of Competition values")
        if not self.preview_dates:
            raise ValueError("preview_dates must not be empty")
        if not self.competitions:
            raise ValueError("competitions must not be empty")
        if any(
            not isinstance(value, date) or isinstance(value, datetime)
            for value in self.preview_dates
        ):
            raise TypeError("preview_dates must contain only date values")
        if any(not isinstance(value, Competition) for value in self.competitions):
            raise TypeError("competitions must contain only Competition values")
        if not isinstance(self.stage, Stage):
            raise TypeError("stage must be a Stage")
        if not isinstance(self.override, bool):
            raise TypeError("override must be bool")
        object.__setattr__(
            self, "preview_dates", tuple(sorted(set(self.preview_dates)))
        )
        object.__setattr__(
            self,
            "competitions",
            tuple(
                sorted(
                    set(self.competitions),
                    key=_COMPETITION_ORDER.__getitem__,
                )
            ),
        )

    @property
    def combinations(self) -> tuple[tuple[date, Competition], ...]:
        return tuple(
            (preview_date, competition)
            for preview_date in self.preview_dates
            for competition in self.competitions
        )


@dataclass(frozen=True, slots=True)
class CombinationResult:
    preview_date: date
    competition: Competition
    status: Literal["ok", "skipped"]
    completed_stage: Stage
    run_directory: Path
    source_path: Path | None = None
    article_directory: Path | None = None
    draft_media_id: str | None = None
    reason: Literal["no_games"] | None = None


@dataclass(frozen=True, slots=True)
class PipelineResult:
    status: Literal["ok", "skipped"]
    completed_stage: Stage
    runs: tuple[CombinationResult, ...]
    draft_media_id: str | None = None
    next_command: str | None = None

    def _single_run(self) -> CombinationResult:
        if len(self.runs) != 1:
            raise AttributeError("batch result does not have a single run")
        return self.runs[0]

    @property
    def run_directory(self) -> Path:
        return self._single_run().run_directory

    @property
    def source_path(self) -> Path | None:
        return self._single_run().source_path

    @property
    def article_directory(self) -> Path | None:
        return self._single_run().article_directory
