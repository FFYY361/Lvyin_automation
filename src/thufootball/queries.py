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
    ConfigurationError,
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
    blacklisted_tournament_ids,
)
from .rankings import StaticOutcomeCatalog, load_outcome_catalog


@dataclass(frozen=True)
class _ValidatedGameQuery:
    tournament_ids: tuple[int, ...]
    match_date: date | None
    tournament_team_ids: tuple[int, ...]
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
    tournament_team_id: int,
    home_result: MatchResult | None,
) -> TeamGameResult:
    is_home = game.home_tournament_team_id == tournament_team_id
    if is_home:
        opponent_tournament_team_id = game.away_tournament_team_id
        opponent_name = game.away_team_name
        venue = "home"
    else:
        opponent_tournament_team_id = game.home_tournament_team_id
        opponent_name = game.home_team_name
        venue = "away"

    if home_result is None:
        return TeamGameResult(
            game=game,
            tournament_team_id=tournament_team_id,
            opponent_tournament_team_id=opponent_tournament_team_id,
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
        tournament_team_id=tournament_team_id,
        opponent_tournament_team_id=opponent_tournament_team_id,
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
    tournament_team_ids = _normalise_ids(
        query.tournament_team_ids, "tournament_team_ids"
    )
    match_date = query.match_date
    if match_date is not None and (
        isinstance(match_date, datetime) or not isinstance(match_date, date)
    ):
        raise _validation_error("match_date must be a date")
    if len(tournament_team_ids) > 2:
        raise _validation_error(
            "tournament_team_ids may contain at most two distinct IDs"
        )
    if query.team_match not in {"any", "all"}:
        raise _validation_error("team_match must be either 'any' or 'all'")
    if not isinstance(query.include_unfinished, bool):
        raise _validation_error("include_unfinished must be a boolean")
    return _ValidatedGameQuery(
        tournament_ids=tournament_ids,
        match_date=match_date,
        tournament_team_ids=tournament_team_ids,
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
        outcome_catalog: StaticOutcomeCatalog | None = None,
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
        self._outcome_catalog = outcome_catalog
        self._close_client = _close_client
        self._tournament_semaphore = asyncio.Semaphore(max_concurrency)
        self._tournament_cache: dict[int, TournamentSnapshot] = {}
        self._tournament_tasks: dict[int, asyncio.Task[TournamentSnapshot]] = {}

    @property
    def outcome_catalog(self) -> StaticOutcomeCatalog:
        if self._outcome_catalog is None:
            self._outcome_catalog = load_outcome_catalog()
        return self._outcome_catalog

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
            if validated.tournament_team_ids:
                for registration in validated.tournament_team_ids:
                    known = self.outcome_catalog.registration_tournament_ids.get(
                        registration
                    )
                    if known is not None and known not in validated.tournament_ids:
                        raise _validation_error(
                            "tournament_team_id does not belong to the requested tournaments"
                        )
            snapshots = await self._read_tournaments(validated.tournament_ids)
            batch = self._tournament_batch(snapshots)
            games = batch.games
        elif validated.match_date is not None:
            games = await self._client.get_current_games(
                history_bound=validated.match_date - timedelta(days=1),
                future_bound=validated.match_date + timedelta(days=1),
            )
        else:
            if not validated.tournament_team_ids:
                raise _validation_error(
                    "tournament_ids, match_date or tournament_team_ids is required"
                )
            tournament_ids = tuple(
                dict.fromkeys(
                    self._registration_tournament(value)
                    for value in validated.tournament_team_ids
                )
            )
            snapshots = await self._read_tournaments(tournament_ids)
            batch = self._tournament_batch(snapshots)
            games = batch.games

        filtered: list[GameSummary] = []
        requested_teams = set(validated.tournament_team_ids)
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
                game_teams = {
                    game.home_tournament_team_id,
                    game.away_tournament_team_id,
                }
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
        tournament_team_id: int,
        tournament_id: int | None = None,
        *,
        include_unfinished: bool = False,
    ) -> list[TeamGameResult]:
        tournament_team_id = _positive_id(tournament_team_id, "tournament_team_id")
        include_unfinished = _include_unfinished(include_unfinished)
        tournament_id = self._registration_tournament(tournament_team_id, tournament_id)
        snapshots = await self._read_tournaments((tournament_id,))

        batch = self._tournament_batch(snapshots)

        results: list[TeamGameResult] = []
        for game in batch.games:
            if tournament_team_id not in {
                game.home_tournament_team_id,
                game.away_tournament_team_id,
            }:
                continue
            if not game.record_active or game.valid is not True:
                continue
            if game.home_abandon is True and game.away_abandon is True:
                continue
            if game.status is GameStatus.FINISHED:
                resolved = _resolve_finished_game(
                    game,
                    players_per_side=batch.players_per_side[game.tournament_id],
                )
                results.append(
                    _team_game_result(
                        resolved.game,
                        tournament_team_id=tournament_team_id,
                        home_result=resolved.home_result,
                    )
                )
            elif include_unfinished:
                results.append(
                    _team_game_result(
                        game, tournament_team_id=tournament_team_id, home_result=None
                    )
                )

        return sorted(
            results,
            key=lambda result: (result.game.kickoff_local, result.game.game_id),
            reverse=True,
        )

    def _registration_tournament(
        self, tournament_team_id: int, tournament_id: int | None = None
    ) -> int:
        if tournament_id is not None:
            tournament_id = _positive_id(tournament_id, "tournament_id")
            _reject_blacklisted_tournaments((tournament_id,))
        known = self.outcome_catalog.registration_tournament_ids.get(tournament_team_id)
        if known is not None and tournament_id is not None and known != tournament_id:
            raise _validation_error(
                "tournament_team_id does not belong to the requested tournament"
            )
        resolved = tournament_id if tournament_id is not None else known
        if resolved is None:
            raise _validation_error(
                "Unknown tournament_team_id; supply its tournament_id"
            )
        _reject_blacklisted_tournaments((resolved,))
        return resolved

    async def query_team_outcomes(
        self, tournament_team_id: int, tournament_ids: Sequence[int] | None = None
    ) -> list[TeamTournamentOutcome]:
        tournament_team_id = _positive_id(tournament_team_id, "tournament_team_id")
        catalog = self.outcome_catalog
        try:
            identity = catalog.resolve_identity(tournament_team_id)
        except ConfigurationError as exc:
            raise _validation_error(
                "Unknown tournament_team_id in the outcome catalog"
            ) from exc
        tournament_id = catalog.registration_tournament_ids[tournament_team_id]
        if tournament_ids is not None:
            selected = _normalise_sequence_ids(tournament_ids, "tournament_ids")
            if selected != (tournament_id,):
                raise _validation_error(
                    "A registration outcome belongs to exactly one tournament"
                )
        tournament = catalog.tournaments_by_id.get(tournament_id)
        rank = tournament.ranks.get(str(tournament_team_id)) if tournament else None
        if rank is None:
            return []
        return [
            TeamTournamentOutcome(
                identity.team_name,
                tournament_id,
                tournament.name,
                tournament.season,
                rank,
                tournament_team_id,
            )
        ]

    async def query_team_to_team_matches(
        self,
        team_a_tournament_team_id: int,
        team_b_tournament_team_id: int,
        tournament_ids: Sequence[int] | None = None,
        *,
        include_unfinished: bool = False,
    ) -> HeadToHeadHistory:
        a = _positive_id(team_a_tournament_team_id, "team_a_tournament_team_id")
        b = _positive_id(team_b_tournament_team_id, "team_b_tournament_team_id")
        if a == b:
            raise _validation_error("The two registrations must be different")
        requested = (
            None
            if tournament_ids is None
            else _normalise_sequence_ids(tournament_ids, "tournament_ids")
        )
        if requested is not None and len(requested) != 1:
            raise _validation_error(
                "A registration pair belongs to exactly one tournament"
            )
        tournament_id = self._registration_tournament(
            a, requested[0] if requested else None
        )
        self._registration_tournament(b, tournament_id)
        return await self.query_team_sets_matches(
            (a,), (b,), (tournament_id,), include_unfinished=include_unfinished
        )

    def _institution_scope(
        self,
        institution_name: str,
        competition: str | None,
        tournament_ids: Sequence[int] | None,
    ):
        if competition is None and tournament_ids is None:
            raise _validation_error("competition or tournament_ids is required")
        catalog = self.outcome_catalog
        names = catalog.institution_identities(institution_name, competition)
        identities = [catalog.teams_by_name[name] for name in names]
        if tournament_ids is None:
            selected = tuple(
                dict.fromkeys(
                    tid for item in identities for tid in item.tournament_teams
                )
            )
        else:
            selected = _normalise_sequence_ids(tournament_ids, "tournament_ids")
            if not selected:
                raise _validation_error("tournament_ids must not be empty")
            if competition is not None:
                category = {"male": "男足", "female": "女足", "futsal": "五人制"}.get(
                    competition
                )
                if any(
                    item.category != category
                    and set(selected) & item.tournament_teams.keys()
                    for item in catalog.teams_by_name.values()
                ):
                    raise _validation_error(
                        "competition does not match the requested tournaments"
                    )
        _reject_blacklisted_tournaments(selected)
        registrations = tuple(
            registration
            for item in identities
            for tid, registration in item.tournament_teams.items()
            if tid in selected
        )
        return selected, registrations

    async def query_institution_games(
        self,
        institution_name: str,
        *,
        competition: str | None = None,
        tournament_ids: Sequence[int] | None = None,
        include_unfinished: bool = False,
    ) -> list[GameSummary]:
        include_unfinished = _include_unfinished(include_unfinished)
        selected, registrations = self._institution_scope(
            institution_name, competition, tournament_ids
        )
        if not registrations:
            return []
        snapshots = await self._read_tournaments(selected)
        wanted = set(registrations)
        return sorted(
            (
                game
                for game in self._tournament_batch(snapshots).games
                if wanted & {game.home_tournament_team_id, game.away_tournament_team_id}
                and (include_unfinished or game.status is GameStatus.FINISHED)
            ),
            key=lambda game: (game.kickoff_local, game.tournament_id, game.game_id),
        )

    async def query_institution_head_to_head(
        self,
        home_institution_name: str,
        away_institution_name: str,
        *,
        competition: str,
        tournament_ids: Sequence[int],
        include_unfinished: bool = False,
    ) -> HeadToHeadHistory:
        selected, home = self._institution_scope(
            home_institution_name, competition, tournament_ids
        )
        _, away = self._institution_scope(away_institution_name, competition, selected)
        if not home or not away:
            return HeadToHeadHistory(
                home,
                away,
                selected,
                (),
                HeadToHeadSummary(0, 0, 0),
                MappingProxyType({tid: HeadToHeadSummary(0, 0, 0) for tid in selected}),
            )
        return await self.query_team_sets_matches(
            home, away, selected, include_unfinished=include_unfinished
        )

    async def query_team_sets_matches(
        self,
        team_a_tournament_team_ids: Sequence[int],
        team_b_tournament_team_ids: Sequence[int],
        tournament_ids: Sequence[int] | None = None,
        *,
        include_unfinished: bool = False,
    ) -> HeadToHeadHistory:
        """Query explicit identity sets without implicitly adding other institutions."""
        team_a_tournament_team_ids = frozenset(
            _normalise_sequence_ids(
                team_a_tournament_team_ids, "team_a_tournament_team_ids"
            )
        )
        team_b_tournament_team_ids = frozenset(
            _normalise_sequence_ids(
                team_b_tournament_team_ids, "team_b_tournament_team_ids"
            )
        )
        if not team_a_tournament_team_ids or not team_b_tournament_team_ids:
            raise _validation_error("team ID sets must not be empty")
        include_unfinished = _include_unfinished(include_unfinished)
        if team_a_tournament_team_ids & team_b_tournament_team_ids:
            raise _validation_error(
                "The two registration sets must not overlap"
            )

        if tournament_ids is None:
            catalog = self.outcome_catalog
            normalised_tournament_ids = tuple(
                dict.fromkeys(
                    catalog.registration_tournament_ids[registration]
                    for registration in sorted(
                        team_a_tournament_team_ids | team_b_tournament_team_ids
                    )
                    if registration in catalog.registration_tournament_ids
                )
            )
            if not normalised_tournament_ids:
                raise _validation_error(
                    "tournament_ids is required for unknown registrations"
                )
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
        seen: set[int] = set()
        for game in batch.games:
            if game.game_id in seen:
                continue
            seen.add(game.game_id)
            if (
                game.home_tournament_team_id in team_a_tournament_team_ids
                and game.away_tournament_team_id in team_b_tournament_team_ids
            ):
                team_a_is_home = True
            elif (
                game.home_tournament_team_id in team_b_tournament_team_ids
                and game.away_tournament_team_id in team_a_tournament_team_ids
            ):
                team_a_is_home = False
            else:
                continue
            if not game.record_active or game.valid is not True:
                continue
            if game.home_abandon is True and game.away_abandon is True:
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
            team_a_tournament_team_ids=tuple(sorted(team_a_tournament_team_ids)),
            team_b_tournament_team_ids=tuple(sorted(team_b_tournament_team_ids)),
            tournament_ids=normalised_tournament_ids,
            matches=tuple(matches),
            summary=_summary(overall_counts),
            by_tournament=by_tournament,
        )
