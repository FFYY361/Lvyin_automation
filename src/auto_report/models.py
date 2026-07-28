"""Public contracts for the automated report pipeline."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

from auto_preview.models import Competition
from wechat_official import CoverFile, CoverMediaId


class Stage(StrEnum):
    REPORT = "report"
    ARTICLE = "article"
    PUBLISH = "publish"

    @property
    def number(self) -> int:
        return {
            Stage.REPORT: 1,
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
    report_dates: Sequence[date]
    competitions: Sequence[Competition]
    stage: Stage = Stage.ARTICLE
    cover: CoverInput | None = None
    override: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.report_dates, (str, bytes)) or not isinstance(
            self.report_dates, Sequence
        ):
            raise TypeError("report_dates must be a sequence of date values")
        if isinstance(self.competitions, (str, bytes)) or not isinstance(
            self.competitions, Sequence
        ):
            raise TypeError("competitions must be a sequence of Competition values")
        if not self.report_dates:
            raise ValueError("report_dates must not be empty")
        if not self.competitions:
            raise ValueError("competitions must not be empty")
        if any(
            not isinstance(value, date) or isinstance(value, datetime)
            for value in self.report_dates
        ):
            raise TypeError("report_dates must contain only date values")
        if any(not isinstance(value, Competition) for value in self.competitions):
            raise TypeError("competitions must contain only Competition values")
        if not isinstance(self.stage, Stage):
            raise TypeError("stage must be a Stage")
        if self.cover is not None and not isinstance(
            self.cover, (CoverFile, CoverMediaId)
        ):
            raise TypeError("cover must be CoverFile, CoverMediaId, or None")
        if not isinstance(self.override, bool):
            raise TypeError("override must be bool")

        object.__setattr__(
            self,
            "report_dates",
            tuple(sorted(set(self.report_dates))),
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
            (report_date, competition)
            for report_date in self.report_dates
            for competition in self.competitions
        )


@dataclass(frozen=True, slots=True)
class CombinationResult:
    report_date: date
    competition: Competition
    status: Literal["ok", "skipped"]
    completed_stage: Stage
    run_directory: Path
    report_manifest_path: Path
    report_files: tuple[Path, ...] = ()
    article_directory: Path | None = None
    draft_media_id: str | None = None
    reason: Literal["no_games", "no_finished_games"] | None = None


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
    def report_manifest_path(self) -> Path:
        return self._single_run().report_manifest_path

    @property
    def report_files(self) -> tuple[Path, ...]:
        return self._single_run().report_files

    @property
    def article_directory(self) -> Path | None:
        return self._single_run().article_directory
