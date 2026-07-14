"""High-level read queries built on :mod:`thufootball.client`."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .client import THUFootballClient
from .errors import (
    BatchQueryError,
    DataConflict,
    InvalidResponse,
    QueryValidationError,
    THUFootballError,
)
from .models import GameQuery, GameStatus, GameSummary, TournamentSnapshot


@dataclass(frozen=True)
class _ValidatedGameQuery:
    tournament_ids: tuple[int, ...]
    match_date: date | None
    team_ids: tuple[int, ...]
    team_match: str
    include_unfinished: bool


def _validation_error(message: str) -> QueryValidationError:
    return QueryValidationError(message, stage="validation")


def _normalise_ids(values: object, name: str) -> tuple[int, ...]:
    if not isinstance(values, tuple):
        raise _validation_error(f"{name} must be a tuple of positive integers")
    result: list[int] = []
    seen: set[int] = set()
    for value in values:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise _validation_error(f"{name} must contain positive integers")
        if value not in seen:
            seen.add(value)
            result.append(value)
    return tuple(result)


def _validate_query(query: object) -> _ValidatedGameQuery:
    if not isinstance(query, GameQuery):
        raise _validation_error("query must be a GameQuery")
    tournament_ids = _normalise_ids(query.tournament_ids, "tournament_ids")
    team_ids = _normalise_ids(query.team_ids, "team_ids")
    match_date = query.match_date
    if match_date is not None and (
        isinstance(match_date, datetime) or not isinstance(match_date, date)
    ):
        raise _validation_error("match_date must be a date")
    if not tournament_ids and match_date is None:
        raise _validation_error(
            "at least one of tournament_ids or match_date is required"
        )
    if len(team_ids) > 2:
        raise _validation_error("team_ids may contain at most two distinct IDs")
    if query.team_match not in {"any", "all"}:
        raise _validation_error("team_match must be either 'any' or 'all'")
    if not isinstance(query.include_unfinished, bool):
        raise _validation_error("include_unfinished must be a boolean")
    return _ValidatedGameQuery(
        tournament_ids=tournament_ids,
        match_date=match_date,
        team_ids=team_ids,
        team_match=query.team_match,
        include_unfinished=query.include_unfinished,
    )


def _core_game_fields(game: GameSummary) -> tuple[object, ...]:
    return (
        game.game_id,
        game.tournament_id,
        game.tournament_name,
        game.kickoff_utc,
        game.status,
        game.record_active,
        game.valid,
        game.stage,
        game.group_name,
        game.round,
        game.home_tournament_team_id,
        game.home_team_id,
        game.away_tournament_team_id,
        game.away_team_id,
        game.home_score,
        game.away_score,
        game.penalty_shootout,
        game.home_penalty,
        game.away_penalty,
        game.home_abandon,
        game.away_abandon,
    )


class THUFootballQueryService:
    """Provide validated, deterministic THUFootball game queries."""

    def __init__(
        self,
        client: THUFootballClient,
        *,
        max_concurrency: int = 4,
    ) -> None:
        if not isinstance(client, THUFootballClient):
            raise TypeError("client must be a THUFootballClient")
        if (
            isinstance(max_concurrency, bool)
            or not isinstance(max_concurrency, int)
            or max_concurrency <= 0
        ):
            raise _validation_error("max_concurrency must be a positive integer")
        self._client = client
        self._max_concurrency = max_concurrency

    async def _read_tournaments(
        self, tournament_ids: tuple[int, ...]
    ) -> tuple[TournamentSnapshot, ...]:
        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def read(tournament_id: int) -> TournamentSnapshot:
            async with semaphore:
                return await self._client.get_tournament_info(tournament_id)

        tasks = {
            tournament_id: asyncio.create_task(read(tournament_id))
            for tournament_id in tournament_ids
        }
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        failures: dict[int, THUFootballError] = {}
        snapshots: list[TournamentSnapshot] = []
        for tournament_id, result in zip(tasks, results, strict=True):
            if isinstance(result, BaseException):
                if isinstance(result, THUFootballError):
                    failures[tournament_id] = result
                else:
                    failures[tournament_id] = InvalidResponse(
                        "Tournament read failed unexpectedly",
                        stage="query_games",
                        tournament_id=tournament_id,
                    )
            else:
                snapshots.append(result)

        if failures:
            if len(tournament_ids) == 1:
                raise next(iter(failures.values()))
            raise BatchQueryError(failures)
        return tuple(snapshots)

    @staticmethod
    def _deduplicate(games: list[GameSummary]) -> list[GameSummary]:
        unique: dict[int, GameSummary] = {}
        for game in games:
            existing = unique.get(game.game_id)
            if existing is None:
                unique[game.game_id] = game
                continue
            if _core_game_fields(existing) != _core_game_fields(game):
                raise DataConflict(
                    f"Conflicting data for game ID {game.game_id}",
                    stage="query_games",
                    game_id=game.game_id,
                )
        return list(unique.values())

    async def query_games(self, query: GameQuery) -> list[GameSummary]:
        validated = _validate_query(query)
        if validated.tournament_ids:
            snapshots = await self._read_tournaments(validated.tournament_ids)
            games = [game for snapshot in snapshots for game in snapshot.games]
        else:
            assert validated.match_date is not None
            games = await self._client.get_current_games(
                history_bound=validated.match_date - timedelta(days=1),
                future_bound=validated.match_date + timedelta(days=1),
            )

        filtered: list[GameSummary] = []
        requested_teams = set(validated.team_ids)
        for game in self._deduplicate(games):
            if (
                validated.match_date is not None
                and game.kickoff_local.date() != validated.match_date
            ):
                continue
            if not validated.include_unfinished and game.status is not GameStatus.FINISHED:
                continue
            if requested_teams:
                game_teams = {game.home_team_id, game.away_team_id}
                if validated.team_match == "any":
                    matches = bool(requested_teams & game_teams)
                else:
                    matches = requested_teams <= game_teams
                if not matches:
                    continue
            filtered.append(game)

        return sorted(
            filtered,
            key=lambda game: (game.kickoff_local, game.tournament_id, game.game_id),
        )
