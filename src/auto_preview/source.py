"""Build strict preview source data from THUFootball domain queries."""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from datetime import date, datetime

from preview import (
    PlayedMatch,
    PreviewColumnConfig,
    PreviewCredits,
    PreviewMatch,
    PreviewSourceData,
    PreviewTeam,
    PreviewValidationError,
    SeasonOutcome,
    TeamRef,
    matchup_key,
    preview_article_file,
    validate_preview_source,
)
from thufootball import (
    GameQuery,
    GameStatus,
    GameSummary,
    QueryValidationError,
    TeamGameResult,
    TeamTournamentOutcome,
    THUFootballQueryService,
)
from thufootball.rankings import (
    StaticTeamIdentity,
    load_static_outcome_catalog,
)

from .config import CompetitionConfig
from .errors import NoGamesForDate

MAX_TRUSTED_TEAM_SHORT_NAME_LENGTH = 5

PLACEHOLDER_PREFIX = "【待填写"
HEADLINE_PLACEHOLDER = "【待填写：文章标题】"
WRITER_PLACEHOLDER = "【待填写：作者】"
EDITOR_PLACEHOLDER = "【待填写：编辑】"
REVIEWER_PLACEHOLDER = "【待填写：责编】"
APPROVER_PLACEHOLDER = "【待填写：审核】"
_SEASON_PATTERN = re.compile(r"(20\d{2})[~～-](20\d{2})")
_TEAM_CATEGORY_BY_COMPETITION = {
    "male": "男足",
    "female": "女足",
    "futsal": "五人制",
}


def _season_label(value: str) -> str | None:
    match = _SEASON_PATTERN.search(value)
    if match is None:
        return None
    return f"{match.group(1)[2:]}-{match.group(2)[2:]}"


def _competition_label(name: str) -> str:
    if "甲级" in name:
        return "甲"
    if "乙级" in name:
        return "乙"
    if "丙级" in name:
        return "丙"
    if "五人制" in name:
        return "五人制"
    if "女足" in name or "女子" in name:
        return "女足"
    return name


def _stage(game: GameSummary) -> str:
    if game.stage:
        return game.stage
    if game.group_name:
        return game.group_name
    if game.round is not None:
        return f"第{game.round}轮"
    return "阶段待定"


class PreviewSourceBuilder:
    def __init__(
        self,
        queries: THUFootballQueryService,
        config: CompetitionConfig,
        *,
        logger: logging.Logger,
    ) -> None:
        self._queries = queries
        self._config = config
        self._logger = logger
        self._short_names: dict[int, str] = {}
        category = _TEAM_CATEGORY_BY_COMPETITION[config.competition.value]
        catalog = load_static_outcome_catalog()
        self._official_teams: dict[int, StaticTeamIdentity] = {}
        for team_id, team_names in catalog.team_names_by_id.items():
            for team_name in team_names:
                identity = catalog.teams_by_name[team_name]
                if identity.category == category:
                    self._official_teams[team_id] = identity
                    break
        self._team_results: dict[tuple[int, int], list[TeamGameResult]] = {}
        self._team_outcomes: dict[int, list[TeamTournamentOutcome]] = {}
        self._head_to_head: dict[tuple[int, int], tuple[GameSummary, ...]] = {}
        self._historical_seasons_by_tournament_id = {
            tournament_id: _season_label(season.label) or season.label
            for season in config.historical_seasons
            for tournament_id in season.tournament_ids
        }
        self.selected_games: tuple[tuple[int, int], ...] = ()

    def _short_name(
        self,
        team_id: int,
        name: str,
        database_short_name: str | None = None,
    ) -> str:
        official = self._official_teams.get(team_id)
        if official is not None:
            self._short_names[team_id] = official.brief_name
            return official.brief_name
        existing = self._short_names.get(team_id)
        if existing is not None:
            return existing
        clean_name = name.strip()
        candidate = database_short_name.strip() if database_short_name else ""
        trusted = (
            bool(candidate) and len(candidate) <= MAX_TRUSTED_TEAM_SHORT_NAME_LENGTH
        )
        short_name = candidate if trusted else clean_name[:2]
        self._short_names[team_id] = short_name
        if not trusted:
            reason = "缺失" if not candidate else "超过5个字符"
            self._logger.warning(
                "球队简称不可信：team_id=%s，全称=%s，数据库简称=%s，原因=%s，使用=%s",
                team_id,
                clean_name,
                candidate or "<空>",
                reason,
                short_name,
            )
        return short_name

    def _team_ref(
        self,
        team_id: int,
        name: str,
        short_name: str | None,
    ) -> TeamRef:
        official = self._official_teams.get(team_id)
        clean_name = official.institution_name if official is not None else name.strip()
        return TeamRef(
            team_id=team_id,
            name=clean_name,
            short_name=self._short_name(team_id, clean_name, short_name),
        )

    def _played_match(
        self,
        game: GameSummary,
        *,
        current_results_team_id: int | None = None,
    ) -> PlayedMatch:
        swap_sides = (
            current_results_team_id is not None
            and game.away_team_id == current_results_team_id
        )
        if swap_sides:
            home = self._team_ref(
                game.away_team_id,
                game.away_team_name,
                game.away_team_brief_name,
            )
            away = self._team_ref(
                game.home_team_id,
                game.home_team_name,
                game.home_team_brief_name,
            )
            home_score, away_score = game.away_score, game.home_score
            home_penalty, away_penalty = game.away_penalty, game.home_penalty
            # 原始 result_text 按原主客方向编码；交换后让渲染层从已交换比分生成。
            result_text = None
        else:
            home = self._team_ref(
                game.home_team_id,
                game.home_team_name,
                game.home_team_brief_name,
            )
            away = self._team_ref(
                game.away_team_id,
                game.away_team_name,
                game.away_team_brief_name,
            )
            home_score, away_score = game.home_score, game.away_score
            home_penalty, away_penalty = game.home_penalty, game.away_penalty
            result_text = game.result_text
        return PlayedMatch(
            game_id=game.game_id,
            home=home,
            away=away,
            home_score=home_score,
            away_score=away_score,
            home_penalty=home_penalty,
            away_penalty=away_penalty,
            result_text=result_text,
            season=(
                _season_label(game.tournament_name)
                or self._historical_seasons_by_tournament_id.get(game.tournament_id)
            ),
            competition_label=_competition_label(game.tournament_name),
            stage=_stage(game),
        )

    async def _current_results(
        self,
        team_id: int,
        tournament_id: int,
        before: datetime,
    ) -> tuple[PlayedMatch, ...]:
        key = (team_id, tournament_id)
        results = self._team_results.get(key)
        if results is None:
            results = await self._queries.query_team_matches(
                team_id,
                tournament_id,
                include_unfinished=False,
            )
            self._team_results[key] = results
        eligible = sorted(
            (
                result
                for result in results
                if result.game.status is GameStatus.FINISHED
                and result.game.kickoff_local < before
            ),
            key=lambda result: (result.game.kickoff_local, result.game.game_id),
            reverse=True,
        )
        return tuple(
            self._played_match(
                result.game,
                current_results_team_id=team_id,
            )
            for result in eligible
        )

    async def _outcomes(self, team_id: int) -> tuple[SeasonOutcome, ...]:
        outcomes = self._team_outcomes.get(team_id)
        if outcomes is None:
            try:
                outcomes = await self._queries.query_team_outcomes(
                    team_id,
                    self._config.outcome_tournament_ids,
                )
            except QueryValidationError:
                # 新队伍可能尚未进入静态历史成绩目录；视为三届均未参赛。
                outcomes = []
            self._team_outcomes[team_id] = outcomes

        outcomes_by_tournament_id = {
            outcome.tournament_id: outcome for outcome in outcomes
        }
        resolved: list[SeasonOutcome] = []
        for season in self._config.historical_seasons:
            outcome = next(
                (
                    outcomes_by_tournament_id[tournament_id]
                    for tournament_id in season.tournament_ids
                    if tournament_id in outcomes_by_tournament_id
                ),
                None,
            )
            season_label = _season_label(season.label) or season.label
            if outcome is None:
                resolved.append(
                    SeasonOutcome(
                        season=season_label,
                        competition_label=None,
                        outcome="未参赛",
                    )
                )
                continue
            resolved.append(
                SeasonOutcome(
                    season=season_label,
                    competition_label=_competition_label(outcome.tournament_name),
                    outcome=outcome.rank,
                )
            )

        return tuple(resolved)

    async def _meetings(
        self,
        team_a_id: int,
        team_b_id: int,
        before: datetime,
    ) -> tuple[PlayedMatch, ...]:
        key = tuple(sorted((team_a_id, team_b_id)))
        matches = self._head_to_head.get(key)
        if matches is None:
            history = await self._queries.query_team_to_team_matches(
                key[0],
                key[1],
                self._config.historical_tournament_ids,
                include_unfinished=False,
            )
            matches = history.matches
            self._head_to_head[key] = matches
        eligible = sorted(
            (
                game
                for game in matches
                if game.status is GameStatus.FINISHED and game.kickoff_local < before
            ),
            key=lambda game: (game.kickoff_local, game.game_id),
            reverse=True,
        )
        return tuple(self._played_match(game) for game in eligible)

    async def _preview_team(
        self,
        *,
        team_id: int,
        name: str,
        short_name: str | None,
        tournament_id: int,
        before: datetime,
    ) -> PreviewTeam:
        resolved_short_name = self._short_name(team_id, name, short_name)
        official = self._official_teams.get(team_id)
        resolved_name = (
            official.institution_name if official is not None else name.strip()
        )
        outcomes = await self._outcomes(team_id)
        results = await self._current_results(team_id, tournament_id, before)
        return PreviewTeam(
            team_id=team_id,
            name=resolved_name,
            short_name=resolved_short_name,
            previous_outcomes=outcomes,
            current_results=results,
        )

    async def query_current_games(self) -> tuple[GameSummary, ...]:
        """Load the configured competition once for batch date filtering."""

        return tuple(
            await self._queries.query_games(
                GameQuery(
                    tournament_ids=self._config.current_tournament_ids,
                    include_unfinished=True,
                )
            )
        )

    async def build(
        self,
        preview_date: date,
        *,
        games: Sequence[GameSummary] | None = None,
    ) -> PreviewSourceData:
        if games is None:
            games = await self._queries.query_games(
                GameQuery(
                    tournament_ids=self._config.current_tournament_ids,
                    match_date=preview_date,
                    include_unfinished=True,
                )
            )
        eligible = [
            game
            for game in games
            if game.tournament_id in self._config.current_tournament_ids
            and game.kickoff_local.date() == preview_date
        ]
        if not eligible:
            raise NoGamesForDate(
                f"{preview_date.isoformat()} 没有符合条件的 {self._config.competition.value} 比赛",
                stage="data",
            )
        self.selected_games = tuple(
            (game.game_id, game.tournament_id) for game in eligible
        )

        # 目标队先注册；静态官方简称优先，未登记球队再采用可信数据库简称。
        for game in eligible:
            self._short_name(
                game.home_team_id,
                game.home_team_name,
                game.home_team_brief_name,
            )
            self._short_name(
                game.away_team_id,
                game.away_team_name,
                game.away_team_brief_name,
            )

        preview_matches: list[PreviewMatch] = []
        for game in eligible:
            home = await self._preview_team(
                team_id=game.home_team_id,
                name=game.home_team_name,
                short_name=game.home_team_brief_name,
                tournament_id=game.tournament_id,
                before=game.kickoff_local,
            )
            away = await self._preview_team(
                team_id=game.away_team_id,
                name=game.away_team_name,
                short_name=game.away_team_brief_name,
                tournament_id=game.tournament_id,
                before=game.kickoff_local,
            )
            meetings = await self._meetings(
                game.home_team_id,
                game.away_team_id,
                game.kickoff_local,
            )
            preview_matches.append(
                PreviewMatch(
                    game_id=game.game_id,
                    competition_name=self._config.current_tournament_names[
                        game.tournament_id
                    ],
                    stage=_stage(game),
                    kickoff=game.kickoff_local,
                    venue=game.field_name or "场地待定",
                    home=home,
                    away=away,
                    head_to_head=meetings,
                    preview_paragraphs=(
                        f"【待填写：{home.short_name} 对阵 {away.short_name} 前瞻】",
                    ),
                    writers=(WRITER_PLACEHOLDER,),
                )
            )

        source = PreviewSourceData(
            column=PreviewColumnConfig(
                competition_full_name=self._config.full_name,
                competition_short_name=self._config.short_name,
            ),
            preview_date=preview_date,
            headline=HEADLINE_PLACEHOLDER,
            weather=None,
            matches=tuple(preview_matches),
            credits=PreviewCredits(
                editors=(EDITOR_PLACEHOLDER,),
                reviewers=(REVIEWER_PLACEHOLDER,),
                approvers=(APPROVER_PLACEHOLDER,),
            ),
        )
        validate_preview_source(source)
        return source


def contains_placeholders(source: PreviewSourceData) -> bool:
    values = [
        source.headline,
        *source.credits.editors,
        *source.credits.reviewers,
        *source.credits.approvers,
    ]
    for match in source.matches:
        values.extend(match.preview_paragraphs)
        values.extend(match.writers)
    return any(value.startswith(PLACEHOLDER_PREFIX) for value in values)


def preview_data_to_dict(source: PreviewSourceData) -> dict[str, object]:
    def team_ref(team: TeamRef) -> dict[str, object]:
        return {
            "team_id": team.team_id,
            "name": team.name,
            "short_name": team.short_name,
        }

    def played(match: PlayedMatch) -> dict[str, object]:
        result: dict[str, object] = {
            "game_id": match.game_id,
            "home": team_ref(match.home),
            "away": team_ref(match.away),
        }
        for name in (
            "season",
            "competition_label",
            "stage",
            "home_score",
            "away_score",
            "home_penalty",
            "away_penalty",
            "result_text",
        ):
            value = getattr(match, name)
            if value is not None:
                result[name] = value
        return result

    def preview_team(team: PreviewTeam) -> dict[str, object]:
        return {
            "team_id": team.team_id,
            "name": team.name,
            "short_name": team.short_name,
            "previous_outcomes": [
                {
                    "season": item.season,
                    **(
                        {"competition_label": item.competition_label}
                        if item.competition_label is not None
                        else {}
                    ),
                    "outcome": item.outcome,
                }
                for item in team.previous_outcomes
            ],
            "current_results": [played(item) for item in team.current_results],
        }

    return {
        "column": {
            "competition_full_name": source.column.competition_full_name,
            "competition_short_name": source.column.competition_short_name,
            **(
                {"weekday_label_override": source.column.weekday_label_override}
                if source.column.weekday_label_override is not None
                else {}
            ),
        },
        "preview_date": source.preview_date.isoformat(),
        "headline": source.headline,
        "weather": (
            None
            if source.weather is None
            else {
                "forecast_date": source.weather.forecast_date.isoformat(),
                "low_c": source.weather.low_c,
                "high_c": source.weather.high_c,
                "wind_direction": source.weather.wind_direction,
                "wind_level": source.weather.wind_level,
            }
        ),
        "matches": [
            {
                "game_id": match.game_id,
                "competition_name": match.competition_name,
                "stage": match.stage,
                "kickoff": match.kickoff.isoformat(),
                "venue": match.venue,
                "home": preview_team(match.home),
                "away": preview_team(match.away),
                "head_to_head": [played(item) for item in match.head_to_head],
                "preview_paragraphs": list(match.preview_paragraphs),
                "writers": list(match.writers),
            }
            for match in source.matches
        ],
        "credits": {
            "editors": list(source.credits.editors),
            "reviewers": list(source.credits.reviewers),
            "approvers": list(source.credits.approvers),
        },
    }


def _manual_preview_entries(
    source: PreviewSourceData,
) -> tuple[tuple[PreviewMatch, str, str], ...]:
    entries: list[tuple[PreviewMatch, str, str]] = []
    keys: dict[str, list[int]] = {}
    files: dict[str, tuple[str, list[int]]] = {}
    for match in source.matches:
        key = matchup_key(match)
        try:
            article_file = preview_article_file(
                match.home.short_name,
                match.away.short_name,
            )
        except ValueError as exc:
            raise PreviewValidationError(
                f"$.previews[{key!r}]: {exc}",
                stage="data-build",
            ) from exc
        keys.setdefault(key, []).append(match.game_id)
        _, file_game_ids = files.setdefault(
            article_file.casefold(),
            (article_file, []),
        )
        file_game_ids.append(match.game_id)
        entries.append((match, key, article_file))

    duplicate_keys = {
        key: game_ids for key, game_ids in keys.items() if len(game_ids) > 1
    }
    duplicate_files = {
        path: game_ids for path, game_ids in files.values() if len(game_ids) > 1
    }
    if duplicate_keys or duplicate_files:
        details = [
            *(f"{key} game_ids={ids}" for key, ids in duplicate_keys.items()),
            *(
                f"{path} game_ids={ids}"
                for path, ids in duplicate_files.items()
                if path not in duplicate_keys
            ),
        ]
        raise PreviewValidationError(
            "$.previews: 对阵简称重复或 Markdown 文件名重复：" + "；".join(details),
            stage="data-build",
        )
    return tuple(entries)


def preview_article_files(source: PreviewSourceData) -> dict[str, str]:
    """Return generated Markdown paths and initial placeholder content."""

    return {
        article_file: "\n\n".join(match.preview_paragraphs).strip() + "\n"
        for match, _, article_file in _manual_preview_entries(source)
    }


def source_to_dict(source: PreviewSourceData) -> dict[str, object]:
    """Serialise query results for the preview template's source schema."""

    full = preview_data_to_dict(source)
    raw_matches = full["matches"]
    assert isinstance(raw_matches, list)
    previews: dict[str, object] = {}
    matches: list[dict[str, object]] = []
    entries = _manual_preview_entries(source)
    for (match, key, article_file), raw_match in zip(
        entries,
        raw_matches,
        strict=True,
    ):
        assert isinstance(raw_match, dict)
        previews[key] = {
            "article_file": article_file,
            "authors": list(match.writers),
        }
        payload = dict(raw_match)
        payload.pop("preview_paragraphs")
        payload.pop("writers")
        matches.append(payload)

    result: dict[str, object] = {
        "column": full["column"],
        "preview_date": full["preview_date"],
        "headline": full["headline"],
        "previews": previews,
        "matches": matches,
    }
    return result
