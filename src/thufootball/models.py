"""Immutable domain models exposed by the THUFootball query layer."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Literal


class GameStatus(StrEnum):
    SCHEDULED = "scheduled"
    STARTED = "started"
    FINISHED = "finished"
    UNKNOWN = "unknown"


class MatchResult(StrEnum):
    WIN = "win"
    DRAW = "draw"
    LOSS = "loss"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class GameQuery:
    tournament_ids: tuple[int, ...] = ()
    match_date: date | None = None
    team_ids: tuple[int, ...] = ()
    team_match: Literal["any", "all"] = "any"
    include_unfinished: bool = True


@dataclass(frozen=True)
class UserProbe:
    user_registered: bool


@dataclass(frozen=True)
class TournamentRef:
    tournament_id: int
    name: str
    brief_name: str
    season: str
    begin_date: date
    end_date: date
    record_active: bool
    visible: bool


@dataclass(frozen=True)
class GameSummary:
    game_id: int
    tournament_id: int
    tournament_name: str
    kickoff_utc: datetime
    kickoff_local: datetime
    status: GameStatus
    record_active: bool
    valid: bool
    stage: str | None
    group_name: str | None
    round: int | None
    home_tournament_team_id: int
    home_team_id: int
    home_team_name: str
    away_tournament_team_id: int
    away_team_id: int
    away_team_name: str
    home_score: int | None
    away_score: int | None
    result_text: str | None
    penalty_shootout: bool  # Rule enabled; not proof that a shootout occurred.
    home_penalty: int | None
    away_penalty: int | None
    home_abandon: bool | None
    away_abandon: bool | None
    field_name: str | None
    home_team_brief_name: str | None = None
    away_team_brief_name: str | None = None
    tournament_report_name: str | None = None
    home_team_report_name: str | None = None
    away_team_report_name: str | None = None

    @property
    def decided_by_penalty_shootout(self) -> bool:
        """Whether the finished result was actually decided on penalties."""

        return (
            self.status is GameStatus.FINISHED
            and self.penalty_shootout
            and self.home_abandon is not True
            and self.away_abandon is not True
            and self.home_score is not None
            and self.home_score == self.away_score
            and self.home_penalty is not None
            and self.away_penalty is not None
            and self.home_penalty != self.away_penalty
        )


@dataclass(frozen=True)
class TournamentSnapshot:
    tournament_id: int
    players_per_side: int
    games: tuple[GameSummary, ...]


@dataclass(frozen=True)
class TeamGameResult:
    game: GameSummary
    team_id: int
    opponent_id: int
    opponent_name: str
    venue: Literal["home", "away"]
    goals_for: int | None
    goals_against: int | None
    penalty_goals_for: int | None
    penalty_goals_against: int | None
    score_text: str | None
    result: MatchResult


@dataclass(frozen=True)
class HeadToHeadSummary:
    team_a_wins: int
    draws: int
    team_b_wins: int


@dataclass(frozen=True)
class HeadToHeadHistory:
    team_a_id: int
    team_b_id: int
    tournament_ids: tuple[int, ...]
    matches: tuple[GameSummary, ...]
    summary: HeadToHeadSummary
    by_tournament: Mapping[int, HeadToHeadSummary]


@dataclass(frozen=True)
class TeamTournamentOutcome:
    team_name: str
    tournament_id: int
    tournament_name: str
    season: str
    rank: str


@dataclass(frozen=True)
class GameEvent:
    event_id: int
    tournament_team_id: int
    tournament_team_player_id: int
    player_id: int
    player_name: str
    side: Literal["home", "away"]
    event_type: str
    minute: int
    stoppage_minute: int
    kit_number: int
    during_penalty_shootout: bool
    valid: bool
    note: str | None = None
    tactical_position_id: int | None = None
    sequence: int | None = None
    time_ordering: int | None = None


@dataclass(frozen=True)
class RefereeAssignment:
    referee_id: int
    position: str
    name: str


@dataclass(frozen=True)
class GameEventIssue:
    severity: Literal["warning", "error"]
    code: str
    message: str
    event_ids: tuple[int, ...] = ()
    player_id: int | None = None
    side: Literal["home", "away"] | None = None
    minute: int | None = None
    stoppage_minute: int | None = None


@dataclass(frozen=True)
class GameDetail:
    game: GameSummary
    events: tuple[GameEvent, ...]
    referees: tuple[RefereeAssignment, ...]
    players_per_side: int


@dataclass(frozen=True)
class PreparedGameReport:
    source_detail: GameDetail
    detail: GameDetail
    warnings: tuple[GameEventIssue, ...]
    render_image: bool
    text: str | None


@dataclass(frozen=True)
class ReportSettings:
    include_qr_code: bool = True
    include_time: bool = True
    include_field: bool = True
    include_lineup: bool = True


@dataclass(frozen=True)
class GameReportFile:
    game_id: int
    path: str
    media_type: str
    width: int
    height: int
    refreshed_stats: bool
    warnings: tuple[GameEventIssue, ...] = ()
