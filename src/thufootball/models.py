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
    penalty_shootout: bool
    home_penalty: int | None
    away_penalty: int | None
    home_abandon: bool | None
    away_abandon: bool | None
    field_name: str | None


@dataclass(frozen=True)
class TournamentTeam:
    tournament_team_id: int
    team_id: int
    name: str
    brief_name: str
    group_place: str | None
    wins: int
    draws: int
    losses: int
    goals_for: int
    goals_against: int
    points: int
    reported_rank: int | None


@dataclass(frozen=True)
class TournamentSnapshot:
    tournament_id: int
    name: str
    season: str
    begin_date: date
    end_date: date
    players_per_side: int
    season_ids: Mapping[str, int]
    teams: tuple[TournamentTeam, ...]
    games: tuple[GameSummary, ...]
    invalid_game_ids: tuple[int, ...] = ()


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


@dataclass(frozen=True)
class RefereeAssignment:
    referee_id: int
    position: str
    name: str


@dataclass(frozen=True)
class GameDetail:
    game: GameSummary
    events: tuple[GameEvent, ...]
    referees: tuple[RefereeAssignment, ...]
