"""High-level read queries built on :mod:`thufootball.client`."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from types import MappingProxyType

from .client import THUFootballClient
from .errors import (
    BatchQueryError,
    DataConflict,
    InvalidResponse,
    QueryValidationError,
    SchemaError,
    THUFootballError,
)
from .models import (
    GameQuery,
    GameStatus,
    GameSummary,
    HeadToHeadHistory,
    HeadToHeadSummary,
    MatchResult,
    TeamGameResult,
    TeamTournamentOutcome,
    TournamentSnapshot,
)
from .policy import (
    BLACKLISTED_TOURNAMENT_IDS,
    blacklisted_tournament_ids,
)
from .rankings import load_static_outcome_catalog


@dataclass(frozen=True)
class _ValidatedGameQuery:
    tournament_ids: tuple[int, ...]
    match_date: date | None
    team_ids: tuple[int, ...]
    team_match: str
    include_unfinished: bool


@dataclass(frozen=True)
class _ResolvedGame:
    game: GameSummary
    home_result: MatchResult


@dataclass(frozen=True)
class _TournamentBatch:
    games: list[GameSummary]
    players_per_side: dict[int, int]


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


def _normalise_sequence_ids(values: object, name: str) -> tuple[int, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise _validation_error(f"{name} must be a sequence of positive integers")
    return _normalise_ids(tuple(values), name)


def _positive_id(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise _validation_error(f"{name} must be a positive integer")
    return value


def _include_unfinished(value: object) -> bool:
    if not isinstance(value, bool):
        raise _validation_error("include_unfinished must be a boolean")
    return value


def _team_alias_ids(team_id: int) -> frozenset[int]:
    catalog = load_static_outcome_catalog()
    team_names = catalog.team_names_by_id.get(team_id)
    if team_names is None:
        return frozenset((team_id,))
    return frozenset(
        alias_id
        for team_name in team_names
        for alias_id in catalog.team_ids_by_name[team_name]
    )


def _reject_blacklisted_tournaments(tournament_ids: tuple[int, ...]) -> None:
    blocked_ids = blacklisted_tournament_ids(tournament_ids)
    if blocked_ids:
        raise _validation_error(
            f"tournament_ids contains blacklisted IDs {blocked_ids}"
        )


def _opposite_result(result: MatchResult) -> MatchResult:
    if result is MatchResult.WIN:
        return MatchResult.LOSS
    if result is MatchResult.LOSS:
        return MatchResult.WIN
    return result


def _resolve_finished_game(
    game: GameSummary,
    *,
    players_per_side: int,
) -> _ResolvedGame:
    def invalid(field: str) -> SchemaError:
        return SchemaError(
            f"game.{field}",
            tournament_id=game.tournament_id,
            game_id=game.game_id,
        )

    if game.status is not GameStatus.FINISHED:
        raise invalid("status")

    home_abandon = game.home_abandon is True
    away_abandon = game.away_abandon is True
    if home_abandon and away_abandon:
        raise invalid("home_abandon")
    if home_abandon or away_abandon:
        awarded_goals = 5 if players_per_side == 5 else 3
        home_score = 0 if home_abandon else awarded_goals
        away_score = 0 if away_abandon else awarded_goals
        normalised = replace(
            game,
            home_score=home_score,
            away_score=away_score,
            result_text=f"{home_score}:{away_score}",
            home_penalty=None,
            away_penalty=None,
        )
        return _ResolvedGame(
            game=normalised,
            home_result=(MatchResult.LOSS if home_abandon else MatchResult.WIN),
        )

    home_score = game.home_score
    away_score = game.away_score
    if home_score is None:
        raise invalid("home_score")
    if away_score is None:
        raise invalid("away_score")
    if home_score != away_score:
        normalised = replace(
            game,
            result_text=f"{home_score}:{away_score}",
            home_penalty=None,
            away_penalty=None,
        )
        return _ResolvedGame(
            game=normalised,
            home_result=(
                MatchResult.WIN if home_score > away_score else MatchResult.LOSS
            ),
        )

    if not game.decided_by_penalty_shootout:
        normalised = replace(
            game,
            result_text=f"{home_score}:{away_score}",
            home_penalty=None,
            away_penalty=None,
        )
        return _ResolvedGame(game=normalised, home_result=MatchResult.DRAW)

    home_penalty = game.home_penalty
    away_penalty = game.away_penalty
    if home_penalty is None:
        raise invalid("home_penalty")
    if away_penalty is None or home_penalty == away_penalty:
        raise invalid("away_penalty")
    normalised = replace(
        game,
        result_text=(f"{home_score}({home_penalty}):{away_score}({away_penalty})"),
    )
    return _ResolvedGame(
        game=normalised,
        home_result=(
            MatchResult.WIN if home_penalty > away_penalty else MatchResult.LOSS
        ),
    )


def _team_game_result(
    game: GameSummary,
    *,
    team_id: int,
    home_result: MatchResult | None,
) -> TeamGameResult:
    is_home = game.home_team_id == team_id
    if is_home:
        opponent_id = game.away_team_id
        opponent_name = game.away_team_name
        venue = "home"
    else:
        opponent_id = game.home_team_id
        opponent_name = game.home_team_name
        venue = "away"

    if home_result is None:
        return TeamGameResult(
            game=game,
            team_id=team_id,
            opponent_id=opponent_id,
            opponent_name=opponent_name,
            venue=venue,
            goals_for=None,
            goals_against=None,
            penalty_goals_for=None,
            penalty_goals_against=None,
            score_text=None,
            result=MatchResult.UNKNOWN,
        )

    assert game.home_score is not None and game.away_score is not None
    goals_for = game.home_score if is_home else game.away_score
    goals_against = game.away_score if is_home else game.home_score
    if game.decided_by_penalty_shootout:
        assert game.home_penalty is not None and game.away_penalty is not None
        penalty_for = game.home_penalty if is_home else game.away_penalty
        penalty_against = game.away_penalty if is_home else game.home_penalty
        score_text = f"{goals_for}({penalty_for}):{goals_against}({penalty_against})"
    else:
        penalty_for = None
        penalty_against = None
        score_text = f"{goals_for}:{goals_against}"
    return TeamGameResult(
        game=game,
        team_id=team_id,
        opponent_id=opponent_id,
        opponent_name=opponent_name,
        venue=venue,
        goals_for=goals_for,
        goals_against=goals_against,
        penalty_goals_for=penalty_for,
        penalty_goals_against=penalty_against,
        score_text=score_text,
        result=home_result if is_home else _opposite_result(home_result),
    )


def _summary(counts: list[int]) -> HeadToHeadSummary:
    return HeadToHeadSummary(
        team_a_wins=counts[0],
        draws=counts[1],
        team_b_wins=counts[2],
    )


def _record_result(counts: list[int], result: MatchResult) -> None:
    if result is MatchResult.WIN:
        counts[0] += 1
    elif result is MatchResult.DRAW:
        counts[1] += 1
    elif result is MatchResult.LOSS:
        counts[2] += 1


def _validate_query(query: object) -> _ValidatedGameQuery:
    if not isinstance(query, GameQuery):
        raise _validation_error("query must be a GameQuery")
    tournament_ids = _normalise_ids(query.tournament_ids, "tournament_ids")
    _reject_blacklisted_tournaments(tournament_ids)
    team_ids = _normalise_ids(query.team_ids, "team_ids")
    match_date = query.match_date
    if match_date is not None and (
        isinstance(match_date, datetime) or not isinstance(match_date, date)
    ):
        raise _validation_error("match_date must be a date")
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
        _close_client: bool = False,
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
        self._close_client = _close_client
        self._tournament_semaphore = asyncio.Semaphore(max_concurrency)
        self._tournament_cache: dict[int, TournamentSnapshot] = {}
        self._tournament_tasks: dict[int, asyncio.Task[TournamentSnapshot]] = {}

    @classmethod
    def from_environment(
        cls,
        *,
        max_concurrency: int = 4,
    ) -> "THUFootballQueryService":
        """Create a service that owns a client configured from `.env`."""

        return cls(
            THUFootballClient(),
            max_concurrency=max_concurrency,
            _close_client=True,
        )

    async def __aenter__(self) -> "THUFootballQueryService":
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        pending = tuple(self._tournament_tasks.values())
        for task in pending:
            if not task.done():
                task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._tournament_tasks.clear()
        self._tournament_cache.clear()
        if self._close_client:
            await self._client.aclose()

    async def _all_accessible_tournament_ids(self) -> tuple[int, ...]:
        tournaments = await self._client.get_accessible_tournaments()
        return tuple(
            tournament_id
            for tournament_id in dict.fromkeys(
                tournament.tournament_id for tournament in tournaments
            )
            if tournament_id not in BLACKLISTED_TOURNAMENT_IDS
        )

    async def _read_tournaments(
        self, tournament_ids: tuple[int, ...]
    ) -> tuple[TournamentSnapshot, ...]:
        _reject_blacklisted_tournaments(tournament_ids)

        async def read(tournament_id: int) -> TournamentSnapshot:
            cached = self._tournament_cache.get(tournament_id)
            if cached is not None:
                return cached

            task = self._tournament_tasks.get(tournament_id)
            if task is None:

                async def fetch() -> TournamentSnapshot:
                    async with self._tournament_semaphore:
                        snapshot = await self._client.get_tournament_info(tournament_id)
                    self._tournament_cache[tournament_id] = snapshot
                    return snapshot

                task = asyncio.create_task(fetch())
                self._tournament_tasks[tournament_id] = task

                def discard(completed: asyncio.Task[TournamentSnapshot]) -> None:
                    if self._tournament_tasks.get(tournament_id) is completed:
                        self._tournament_tasks.pop(tournament_id, None)

                task.add_done_callback(discard)
            return await asyncio.shield(task)

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

    def _tournament_batch(
        self,
        snapshots: Sequence[TournamentSnapshot],
    ) -> _TournamentBatch:
        return _TournamentBatch(
            games=self._deduplicate(
                [game for snapshot in snapshots for game in snapshot.games]
            ),
            players_per_side={
                snapshot.tournament_id: snapshot.players_per_side
                for snapshot in snapshots
            },
        )

    async def query_games(self, query: GameQuery) -> list[GameSummary]:
        validated = _validate_query(query)
        if validated.tournament_ids:
            snapshots = await self._read_tournaments(validated.tournament_ids)
            batch = self._tournament_batch(snapshots)
            games = batch.games
        elif validated.match_date is not None:
            games = await self._client.get_current_games(
                history_bound=validated.match_date - timedelta(days=1),
                future_bound=validated.match_date + timedelta(days=1),
            )
        else:
            tournament_ids = await self._all_accessible_tournament_ids()
            snapshots = await self._read_tournaments(tournament_ids)
            batch = self._tournament_batch(snapshots)
            games = batch.games

        filtered: list[GameSummary] = []
        requested_teams = set(validated.team_ids)
        for game in self._deduplicate(games):
            if (
                validated.match_date is not None
                and game.kickoff_local.date() != validated.match_date
            ):
                continue
            if (
                not validated.include_unfinished
                and game.status is not GameStatus.FINISHED
            ):
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

    async def query_team_matches(
        self,
        team_id: int,
        tournament_id: int | None = None,
        *,
        include_unfinished: bool = False,
    ) -> list[TeamGameResult]:
        team_id = _positive_id(team_id, "team_id")
        include_unfinished = _include_unfinished(include_unfinished)
        if tournament_id is None:
            tournament_ids = await self._all_accessible_tournament_ids()
            snapshots = await self._read_tournaments(tournament_ids)
        else:
            tournament_id = _positive_id(tournament_id, "tournament_id")
            snapshots = await self._read_tournaments((tournament_id,))

        batch = self._tournament_batch(snapshots)

        results: list[TeamGameResult] = []
        for game in batch.games:
            if team_id not in {game.home_team_id, game.away_team_id}:
                continue
            if not game.record_active or not game.valid:
                continue
            if game.status is GameStatus.FINISHED:
                resolved = _resolve_finished_game(
                    game,
                    players_per_side=batch.players_per_side[game.tournament_id],
                )
                results.append(
                    _team_game_result(
                        resolved.game,
                        team_id=team_id,
                        home_result=resolved.home_result,
                    )
                )
            elif include_unfinished:
                results.append(
                    _team_game_result(game, team_id=team_id, home_result=None)
                )

        return sorted(
            results,
            key=lambda result: (result.game.kickoff_local, result.game.game_id),
            reverse=True,
        )

    async def query_team_outcomes(
        self,
        team_id: int,
        tournament_ids: Sequence[int] | None = None,
    ) -> list[TeamTournamentOutcome]:
        team_id = _positive_id(team_id, "team_id")
        catalog = load_static_outcome_catalog()
        team_names = catalog.team_names_by_id.get(team_id)
        if team_names is None:
            raise _validation_error(
                "team_id is not present in the supported team catalog"
            )

        if tournament_ids is None:
            selected_tournament_ids = catalog.tournament_ids
        else:
            selected_tournament_ids = _normalise_sequence_ids(
                tournament_ids, "tournament_ids"
            )
            if not selected_tournament_ids:
                raise _validation_error("tournament_ids must not be empty")
            unsupported_ids = tuple(
                tournament_id
                for tournament_id in selected_tournament_ids
                if tournament_id not in catalog.tournaments_by_id
            )
            if unsupported_ids:
                raise _validation_error(
                    f"tournament_ids contains unsupported IDs {unsupported_ids}"
                )

        outcomes: list[TeamTournamentOutcome] = []
        seen: set[tuple[str, int]] = set()
        for tournament_id in selected_tournament_ids:
            tournament = catalog.tournaments_by_id[tournament_id]
            for team_name in team_names:
                rank = tournament.ranks.get(team_name)
                identity = (team_name, tournament_id)
                if rank is None or identity in seen:
                    continue
                seen.add(identity)
                outcomes.append(
                    TeamTournamentOutcome(
                        team_name=team_name,
                        tournament_id=tournament_id,
                        tournament_name=tournament.name,
                        season=tournament.season,
                        rank=rank,
                    )
                )
        return outcomes

    async def query_team_to_team_matches(
        self,
        team_a_id: int,
        team_b_id: int,
        tournament_ids: Sequence[int] | None = None,
        *,
        include_unfinished: bool = False,
    ) -> HeadToHeadHistory:
        team_a_id = _positive_id(team_a_id, "team_a_id")
        team_b_id = _positive_id(team_b_id, "team_b_id")
        if team_a_id == team_b_id:
            raise _validation_error("team_a_id and team_b_id must be different")
        include_unfinished = _include_unfinished(include_unfinished)
        team_a_ids = _team_alias_ids(team_a_id)
        team_b_ids = _team_alias_ids(team_b_id)
        if team_a_ids & team_b_ids:
            raise _validation_error(
                "team_a_id and team_b_id resolve to overlapping team ID aliases"
            )

        if tournament_ids is None:
            normalised_tournament_ids = await self._all_accessible_tournament_ids()
        else:
            normalised_tournament_ids = _normalise_sequence_ids(
                tournament_ids, "tournament_ids"
            )
            if not normalised_tournament_ids:
                raise _validation_error("tournament_ids must not be empty")
            _reject_blacklisted_tournaments(normalised_tournament_ids)

        snapshots = await self._read_tournaments(normalised_tournament_ids)
        batch = self._tournament_batch(snapshots)
        overall_counts = [0, 0, 0]
        tournament_counts = {
            tournament_id: [0, 0, 0] for tournament_id in normalised_tournament_ids
        }
        matches: list[GameSummary] = []
        for game in batch.games:
            if game.home_team_id in team_a_ids and game.away_team_id in team_b_ids:
                team_a_is_home = True
            elif game.home_team_id in team_b_ids and game.away_team_id in team_a_ids:
                team_a_is_home = False
            else:
                continue
            if not game.record_active or not game.valid:
                continue
            if game.status is GameStatus.FINISHED:
                resolved = _resolve_finished_game(
                    game,
                    players_per_side=batch.players_per_side[game.tournament_id],
                )
                matches.append(resolved.game)
                team_a_result = (
                    resolved.home_result
                    if team_a_is_home
                    else _opposite_result(resolved.home_result)
                )
                _record_result(overall_counts, team_a_result)
                _record_result(tournament_counts[game.tournament_id], team_a_result)
            elif include_unfinished:
                matches.append(game)

        matches.sort(key=lambda game: (game.kickoff_local, game.game_id), reverse=True)
        by_tournament = MappingProxyType(
            {
                tournament_id: _summary(tournament_counts[tournament_id])
                for tournament_id in normalised_tournament_ids
            }
        )
        return HeadToHeadHistory(
            team_a_id=team_a_id,
            team_b_id=team_b_id,
            tournament_ids=normalised_tournament_ids,
            matches=tuple(matches),
            summary=_summary(overall_counts),
            by_tournament=by_tournament,
        )
