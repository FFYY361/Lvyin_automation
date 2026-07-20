"""Build strict preview source data from THUFootball domain queries."""

from __future__ import annotations

import logging
import re
from datetime import date, datetime

from preview import (
    PlayedMatch,
    PreviewColumnConfig,
    PreviewCredits,
    PreviewMatch,
    PreviewSourceData,
    PreviewTeam,
    SeasonOutcome,
    TeamRef,
    validate_preview_source,
)
from thufootball import (
    GameQuery,
    GameStatus,
    GameSummary,
    QueryValidationError,
    THUFootballQueryService,
    TeamGameResult,
    TeamTournamentOutcome,
)

from .config import CompetitionConfig
from .errors import NoGamesForDate


# 顶层开关：开启时，每支球队的本赛事历史战绩都从该队的主队视角展示。
CURRENT_RESULTS_TEAM_ALWAYS_HOME = True
MAX_TRUSTED_TEAM_SHORT_NAME_LENGTH = 5

PLACEHOLDER_PREFIX = "【待填写"
HEADLINE_PLACEHOLDER = "【待填写：文章标题】"
WRITER_PLACEHOLDER = "【待填写：作者】"
EDITOR_PLACEHOLDER = "【待填写：编辑】"
REVIEWER_PLACEHOLDER = "【待填写：责编】"
APPROVER_PLACEHOLDER = "【待填写：审核】"
_SEASON_PATTERN = re.compile(r"(20\d{2})[~～-](20\d{2})")


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
        self._team_results: dict[tuple[int, int], list[TeamGameResult]] = {}
        self._team_outcomes: dict[int, list[TeamTournamentOutcome]] = {}
        self._head_to_head: dict[tuple[int, int], tuple[GameSummary, ...]] = {}
        self._outcome_warnings: set[int] = set()
        self.selected_games: tuple[tuple[int, int], ...] = ()

    def _short_name(
        self,
        team_id: int,
        name: str,
        database_short_name: str | None = None,
    ) -> str:
        existing = self._short_names.get(team_id)
        if existing is not None:
            return existing
        clean_name = name.strip()
        candidate = database_short_name.strip() if database_short_name else ""
        trusted = (
            bool(candidate)
            and len(candidate) <= MAX_TRUSTED_TEAM_SHORT_NAME_LENGTH
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
        clean_name = name.strip()
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
            CURRENT_RESULTS_TEAM_ALWAYS_HOME
            and current_results_team_id is not None
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
            season=_season_label(game.tournament_name),
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
            self._logger.info(
                "current_results 查询：team_id=%s tournament_id=%s count=%s",
                team_id,
                tournament_id,
                len(results),
            )
        else:
            self._logger.info(
                "current_results 缓存命中：team_id=%s tournament_id=%s",
                team_id,
                tournament_id,
            )
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
        missing_seasons: list[str] = []
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
                missing_seasons.append(season.label)
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
                    competition_label=_competition_label(
                        outcome.tournament_name
                    ),
                    outcome=outcome.rank,
                )
            )

        if missing_seasons and team_id not in self._outcome_warnings:
            self._logger.warning(
                "team_id=%s 的 %s 无法获取排名，按未参赛展示",
                team_id,
                "、".join(missing_seasons),
            )
            self._outcome_warnings.add(team_id)
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
                if game.status is GameStatus.FINISHED
                and game.kickoff_local < before
            ),
            key=lambda game: (game.kickoff_local, game.game_id),
            reverse=True,
        )
        return tuple(
            self._played_match(game)
            for game in eligible
        )

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
        outcomes = await self._outcomes(team_id)
        results = await self._current_results(team_id, tournament_id, before)
        return PreviewTeam(
            team_id=team_id,
            name=name.strip(),
            short_name=resolved_short_name,
            previous_outcomes=outcomes,
            current_results=results,
        )

    async def build(self, preview_date: date) -> PreviewSourceData:
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

        # 所有目标队先于历史数据注册，确保目标比赛的数据库简称拥有优先级。
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
            schema_version=1,
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


def source_to_dict(source: PreviewSourceData) -> dict[str, object]:
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
        "schema_version": source.schema_version,
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
