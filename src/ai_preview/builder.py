"""Assemble AI preview prompt inputs from canonical database records."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from thufootball.database import (
    FootballDataRepository,
    InstitutionRecord,
    create_football_engine,
    create_football_session_factory,
)
from thufootball.rankings import StaticTeamIdentity

from .config import PromptConfig, load_prompt_config

DEFAULT_PROMPT_ROOT = Path(__file__).with_name("prompt")

_PLACEHOLDERS = {"", "待补充", "待补充。", "无", "无。"}
_LINEUP_EVENTS = {"START", "APPEARANCE"}
_GOAL_EVENTS = {"GOAL", "PENALTY", "OWNGOAL"}
_CARD_TYPES = {
    "YELLOWCARD": "yellow",
    "SECONDYELLOWCARD": "second_yellow",
    "REDCARD": "red",
}
_COMPETITION_RULE_FILES = {
    "男足": "men.md",
    "女足": "women.md",
    "五人制": "futsal.md",
}
_COMPETITION_VALUES = {"男足": "male", "女足": "female", "五人制": "futsal"}
_COMPETITION_LABELS = {value: key for key, value in _COMPETITION_VALUES.items()}


@dataclass(frozen=True)
class PromptBundle:
    system_message: str
    match_context: dict[str, object]
    manual_context: dict[str, object]
    automatic_context: dict[str, object]

    def render_user_message(self) -> str:
        match = self.match_context
        match_lines = [
            f"赛事：{match['competition']}",
            f"赛季：{match['season']}",
            f"阶段：{match['stage']}",
        ]
        if match["group"]:
            match_lines.append(f"分组：{match['group']}")
        if match["round"]:
            match_lines.append(f"轮次：{match['round']}")
        match_lines.extend(
            [
                f"主队：{match['home_team']}",
                f"客队：{match['away_team']}",
                f"比赛时间：{match['kickoff']}",
                f"比赛场地：{match['venue']}",
            ]
        )
        manual = json.dumps(self.manual_context, ensure_ascii=False, indent=2)
        automatic = json.dumps(self.automatic_context, ensure_ascii=False, indent=2)
        return (
            "请根据以下资料，撰写一篇完整的比赛前瞻正文。\n\n"
            "<match_context>\n" + "\n".join(match_lines) + "\n</match_context>\n\n"
            "<manual_context>\n```json\n" + manual + "\n```\n</manual_context>\n\n"
            "<automatic_context>\n```json\n" + automatic + "\n```\n</automatic_context>"
        )


@dataclass(frozen=True)
class _StoredMatch:
    game: dict[str, Any]
    tournament: dict[str, Any]
    competition: str
    season: str


class _Repository:
    def __init__(self, database: FootballDataRepository) -> None:
        self.database = database
        self.catalog = database.load_outcome_catalog()
        self._tournaments: dict[int, dict[str, Any]] = {}
        self._game_details: dict[int, dict[str, Any]] = {}
        self.entries: dict[int, dict[str, Any]] = {}
        for record in database.list_tournaments():
            self.entries[record.id] = {
                "tournament_id": record.id,
                "competition": _COMPETITION_LABELS[record.competition],
            }

    def tournament(self, tournament_id: int) -> dict[str, Any]:
        cached = self._tournaments.get(tournament_id)
        if cached is not None:
            return cached
        record = self.database.get_tournament(tournament_id)
        document = dict(record.data)
        document["_final_rankings"] = record.final_rankings
        self._tournaments[tournament_id] = document
        return document

    def game_detail(self, game_id: int) -> dict[str, Any]:
        cached = self._game_details.get(game_id)
        if cached is not None:
            return cached
        document = dict(self.database.get_game(game_id).data)
        self._game_details[game_id] = document
        return document

    def institution(self, team_id: int, competition: str) -> InstitutionRecord:
        return self.database.find_institution(team_id, _COMPETITION_VALUES[competition])

    def configured_game(self, game: dict[str, Any]) -> dict[str, Any]:
        competition = self.entries[
            _integer(game.get("tournament_id"), "tournament_id")
        ]["competition"]
        result = dict(game)
        for side in ("home", "away"):
            identity = self.catalog.resolve_identity(
                _integer(game.get(f"{side}_team_id"), f"{side}_team_id"), competition
            )
            result[f"{side}_team_name"] = identity.institution_name
            result[f"{side}_team_brief_name"] = identity.brief_name
        return result


def build_prompt_bundle(
    match_id: int,
    *,
    config: PromptConfig | None = None,
    repository: FootballDataRepository | None = None,
    prompt_root: Path = DEFAULT_PROMPT_ROOT,
    prompt_documents: dict[str, str] | None = None,
) -> PromptBundle:
    if isinstance(match_id, bool) or not isinstance(match_id, int) or match_id <= 0:
        raise ValueError("MATCH_ID 必须是正整数")
    if repository is None:
        engine = create_football_engine()
        factory = create_football_session_factory(engine)
        try:
            with factory() as session:
                return build_prompt_bundle(
                    match_id,
                    config=config,
                    repository=FootballDataRepository(session),
                    prompt_root=prompt_root,
                    prompt_documents=prompt_documents,
                )
        finally:
            engine.dispose()
    resolved_config = config or load_prompt_config()
    stored = _Repository(repository)
    target_detail = stored.game_detail(match_id)
    target_game = _object(target_detail.get("game"), "game")
    if _integer(target_game.get("game_id"), "game.game_id") != match_id:
        raise ValueError(f"比赛文件与 MATCH_ID 不一致：{match_id}")

    target_tournament_id = _integer(
        target_game.get("tournament_id"), "game.tournament_id"
    )
    target_tournament = stored.tournament(target_tournament_id)
    target_info = _object(target_tournament.get("tournament"), "tournament")
    target_entry = stored.entries.get(target_tournament_id)
    if target_entry is None:
        raise ValueError(f"数据库缺少赛事 {target_tournament_id}")

    competition_kind = _text(target_entry.get("competition"), "competition")
    target_season = _canonical_season(target_info.get("season"))
    target_season_start = _season_start(target_season)
    target_kickoff = _datetime(target_game.get("kickoff_local"), "kickoff_local")
    home_team_id = _integer(target_game.get("home_team_id"), "home_team_id")
    away_team_id = _integer(target_game.get("away_team_id"), "away_team_id")

    home_record = stored.institution(home_team_id, competition_kind)
    away_record = stored.institution(away_team_id, competition_kind)
    home_identity = stored.catalog.resolve_identity(home_team_id, competition_kind)
    away_identity = stored.catalog.resolve_identity(away_team_id, competition_kind)
    home_team_ids = set(stored.catalog.history_ids(home_identity))
    away_team_ids = set(stored.catalog.history_ids(away_identity))
    manual_context = {
        "home_team": _manual_team_context(
            home_record,
            competition_kind,
            home_identity.brief_name,
        ),
        "away_team": _manual_team_context(
            away_record,
            competition_kind,
            away_identity.brief_name,
        ),
    }

    selected_tournaments: list[tuple[dict[str, Any], str, str]] = []
    minimum_season = target_season_start - resolved_config.history_seasons + 1
    for tournament_id, entry in stored.entries.items():
        if entry.get("competition") != competition_kind:
            continue
        document = stored.tournament(tournament_id)
        info = _object(document.get("tournament"), "tournament")
        season = _canonical_season(info.get("season"))
        if minimum_season <= _season_start(season) <= target_season_start:
            selected_tournaments.append(
                (document, season, _competition_label(competition_kind, info))
            )

    matches = _collect_matches(selected_tournaments)
    prior_matches = [
        item
        for item in matches
        if _game_time(item.game) < target_kickoff
        and _is_finished_game(item.game)
        and _integer(item.game.get("game_id"), "game_id") != match_id
    ]
    direct_matches = [
        item
        for item in prior_matches
        if _has_any_team(item.game, home_team_ids)
        and _has_any_team(item.game, away_team_ids)
        and not (home_team_ids & away_team_ids)
    ]
    direct_ids = {
        _integer(item.game.get("game_id"), "game_id") for item in direct_matches
    }

    home_context = _automatic_team_context(
        stored,
        selected_tournaments,
        prior_matches,
        direct_ids,
        target_tournament,
        target_game,
        home_team_id,
        home_identity,
        competition_kind,
        resolved_config,
    )
    away_context = _automatic_team_context(
        stored,
        selected_tournaments,
        prior_matches,
        direct_ids,
        target_tournament,
        target_game,
        away_team_id,
        away_identity,
        competition_kind,
        resolved_config,
    )
    automatic_context = {
        "home_team": home_context,
        "away_team": away_context,
        "head_to_head": [
            _head_to_head_match(
                stored,
                item,
                home_team_ids,
                away_team_ids,
            )
            for item in sorted(
                direct_matches,
                key=lambda item: _game_time(item.game),
                reverse=True,
            )
        ],
    }

    match_context = {
        "competition": _competition_label(competition_kind, target_info),
        "season": target_season,
        "stage": target_game.get("stage") or "未标注",
        "group": target_game.get("group_name"),
        "round": target_game.get("round"),
        "home_team": home_identity.institution_name,
        "away_team": away_identity.institution_name,
        "kickoff": _format_kickoff(target_kickoff),
        "venue": target_game.get("field_name") or "未标注",
    }
    return PromptBundle(
        system_message=build_system_message(
            competition_kind,
            prompt_root=prompt_root,
            prompt_documents=prompt_documents,
        ),
        match_context=match_context,
        manual_context=manual_context,
        automatic_context=automatic_context,
    )


def build_user_message(
    match_id: int,
    *,
    config: PromptConfig | None = None,
    repository: FootballDataRepository | None = None,
    prompt_root: Path = DEFAULT_PROMPT_ROOT,
) -> str:
    return build_prompt_bundle(
        match_id,
        config=config,
        repository=repository,
        prompt_root=prompt_root,
    ).render_user_message()


def build_system_message(
    competition_kind: str,
    *,
    prompt_root: Path = DEFAULT_PROMPT_ROOT,
    prompt_documents: dict[str, str] | None = None,
) -> str:
    rule_file = _COMPETITION_RULE_FILES.get(competition_kind)
    if rule_file is None:
        raise ValueError(f"不支持的比赛项目：{competition_kind}")
    keys = (
        "preview_system",
        f"competition_{_COMPETITION_VALUES[competition_kind]}",
        "preview_writing_rules",
        "preview_data_rules",
    )
    if prompt_documents is not None:
        try:
            return "\n\n".join(prompt_documents[key] for key in keys)
        except KeyError as exc:
            raise ValueError(f"缺少 Prompt 分区：{exc.args[0]}") from exc
    paths = (
        prompt_root / "system.md",
        prompt_root / "competition_rules" / rule_file,
        prompt_root / "writing_rules.md",
        prompt_root / "data_rules.md",
    )
    return "\n\n".join(_read_text(path) for path in paths)


def _automatic_team_context(
    repository: _Repository,
    selected_tournaments: list[tuple[dict[str, Any], str, str]],
    prior_matches: list[_StoredMatch],
    direct_ids: set[int],
    target_tournament: dict[str, Any],
    target_game: dict[str, Any],
    team_id: int,
    identity: StaticTeamIdentity,
    competition_kind: str,
    config: PromptConfig,
) -> dict[str, object]:
    target_tournament_id = _integer(target_game.get("tournament_id"), "tournament_id")
    target_season = _canonical_season(
        _object(target_tournament.get("tournament"), "tournament").get("season")
    )
    current_games = [
        item.game
        for item in prior_matches
        if _integer(item.game.get("tournament_id"), "tournament_id")
        == target_tournament_id
        and _has_team(item.game, team_id)
    ]
    tournament_info = _object(target_tournament.get("tournament"), "tournament")
    histories = []
    non_direct: list[_StoredMatch] = []
    for historical_identity in repository.catalog.history_identities(identity):
        ids = set(historical_identity.team_ids)
        eligible = [
            item
            for item in prior_matches
            if _has_any_team(item.game, ids)
            and (
                historical_identity == identity
                or _season_start(item.season) < _season_start(target_season)
            )
        ]
        histories.append((historical_identity, ids, eligible))
        non_direct.extend(
            item
            for item in eligible
            if _integer(item.game.get("game_id"), "game_id") not in direct_ids
        )
    # A match between two predecessors belongs to both histories, but consumes one detail slot.
    non_direct = list(
        {
            _integer(item.game.get("game_id"), "game_id"): item for item in non_direct
        }.values()
    )
    non_direct.sort(key=lambda item: _game_time(item.game), reverse=True)
    recent_ids = {
        _integer(item.game.get("game_id"), "game_id")
        for item in non_direct[: config.recent_matches_with_events]
    }
    seasons = sorted(
        {
            season
            for _, season, _ in selected_tournaments
            if _season_start(season) < _season_start(target_season)
        },
        reverse=True,
    )
    season_outcomes = []
    for season in seasons:
        tournament_ids = tuple(
            _integer(_object(document.get("tournament"), "tournament").get("id"), "id")
            for document, label, _ in selected_tournaments
            if label == season
        )
        outcomes = repository.catalog.season_outcomes(identity, tournament_ids)
        season_outcomes.append(
            {
                "season": season,
                "outcome": outcomes[0].rank if outcomes else "未参赛",
                "sources": [
                    {
                        "name": repository.catalog.teams_by_name[
                            outcome.team_name
                        ].brief_name,
                        "competition": outcome.tournament_name,
                        "rank": outcome.rank,
                    }
                    for outcome in outcomes
                ],
            }
        )
    history_teams = []
    for historical_identity, ids, eligible in histories:
        matches = sorted(
            (
                item
                for item in eligible
                if _integer(item.game.get("game_id"), "game_id") not in direct_ids
            ),
            key=lambda item: _game_time(item.game),
            reverse=True,
        )
        history_teams.append(
            {
                "name": historical_identity.institution_name,
                "short_name": historical_identity.brief_name,
                "is_predecessor": historical_identity.is_predecessor,
                "past_seasons": _past_seasons(eligible, target_season, ids),
                "recent_matches": [
                    _relative_detailed_match(
                        repository, item, _matching_team_id(item.game, ids)
                    )
                    for item in matches
                    if _integer(item.game.get("game_id"), "game_id") in recent_ids
                ],
                "earlier_matches": [
                    _relative_match_summary(
                        repository, item, _matching_team_id(item.game, ids)
                    )
                    for item in matches
                    if _integer(item.game.get("game_id"), "game_id") not in recent_ids
                ],
            }
        )
    return {
        "name": identity.brief_name,
        "current_tournament": {
            "competition": _competition_label(competition_kind, tournament_info),
            "season": _canonical_season(tournament_info.get("season")),
            "current_stage": target_game.get("stage"),
            "group_standing": _group_standing(target_tournament, target_game, team_id),
            "record_before_match": _record(current_games, {team_id}),
        },
        "season_outcomes": season_outcomes,
        "history_teams": history_teams,
    }


def _past_seasons(
    matches: list[_StoredMatch],
    target_season: str,
    team_ids: set[int],
) -> list[dict[str, object]]:
    result = []
    groups: dict[tuple[int, str, str], list[dict[str, Any]]] = {}
    for item in matches:
        if _season_start(item.season) >= _season_start(target_season):
            continue
        key = (
            _integer(item.game.get("tournament_id"), "tournament_id"),
            item.season,
            item.competition,
        )
        groups.setdefault(key, []).append(item.game)
    for (_, season, competition), games in groups.items():
        entry: dict[str, object] = {
            "season": season,
            "competition": competition,
        }
        entry.update(_record(games, team_ids))
        result.append(entry)
    result.sort(key=lambda item: _season_start(str(item["season"])), reverse=True)
    return result


def _group_standing(
    tournament: dict[str, Any], target_game: dict[str, Any], team_id: int
) -> dict[str, object] | None:
    games = [
        _object(game, "games[]") for game in _array(tournament.get("games"), "games")
    ]
    group = target_game.get("group_name")
    if not group:
        groups = {
            game.get("group_name")
            for game in games
            if game.get("stage") == "小组赛"
            and game.get("group_name")
            and _has_team(game, team_id)
        }
        if len(groups) == 1:
            group = groups.pop()
    if not isinstance(group, str) or not group:
        return None

    target_time = _game_time(target_game)
    group_games = [
        game
        for game in games
        if game.get("stage") == "小组赛" and game.get("group_name") == group
    ]
    participants = {
        _integer(game.get(field), field)
        for game in group_games
        for field in ("home_team_id", "away_team_id")
    }
    table = {participant: _empty_group_record() for participant in participants}
    for game in group_games:
        if _game_time(game) >= target_time or not _is_finished_game(game):
            continue
        home_id = _integer(game.get("home_team_id"), "home_team_id")
        away_id = _integer(game.get("away_team_id"), "away_team_id")
        for current_id in (home_id, away_id):
            score_for, score_against = _relative_score(game, current_id)
            outcome = _outcome(game, current_id)
            record = table[current_id]
            record["played"] += 1
            record[_outcome_count_key(outcome)] += 1
            record["goals_for"] += score_for
            record["goals_against"] += score_against
            record["points"] += {"win": 3, "draw": 1, "loss": 0}[outcome]
    team_record = table.get(team_id)
    if team_record is None:
        return None
    tied = (
        sum(record["points"] == team_record["points"] for record in table.values()) > 1
    )
    rank = None
    rank_note = None
    if tied:
        rank_note = "与其他球队同分，现有资料不足以确定准确顺位"
    else:
        rank = 1 + sum(
            record["points"] > team_record["points"] for record in table.values()
        )
    return {
        "group": group,
        "rank": rank,
        "rank_note": rank_note,
        **team_record,
        "goal_difference": team_record["goals_for"] - team_record["goals_against"],
    }


def _empty_group_record() -> dict[str, int]:
    return {
        "played": 0,
        "wins": 0,
        "draws": 0,
        "losses": 0,
        "goals_for": 0,
        "goals_against": 0,
        "points": 0,
    }


def _record(games: list[dict[str, Any]], team_ids: set[int]) -> dict[str, int]:
    result = {
        "played": 0,
        "wins": 0,
        "draws": 0,
        "losses": 0,
        "goals_for": 0,
        "goals_against": 0,
        "awarded_wins": 0,
        "awarded_losses": 0,
    }
    for game in games:
        team_id = _matching_team_id(game, team_ids)
        score_for, score_against = _relative_score(game, team_id)
        outcome = _outcome(game, team_id)
        result["played"] += 1
        result[_outcome_count_key(outcome)] += 1
        result["goals_for"] += score_for
        result["goals_against"] += score_against
        side = _team_side(game, team_id)
        opponent_side = "away" if side == "home" else "home"
        if outcome == "win" and game.get(f"{opponent_side}_abandon"):
            result["awarded_wins"] += 1
        if outcome == "loss" and game.get(f"{side}_abandon"):
            result["awarded_losses"] += 1
    return result


def _relative_match_summary(
    repository: _Repository, item: _StoredMatch, team_id: int
) -> dict[str, object]:
    game = repository.configured_game(item.game)
    side = _team_side(game, team_id)
    opponent_side = "away" if side == "home" else "home"
    score_for, score_against = _relative_score(game, team_id)
    penalties = _relative_penalties(game, side)
    return {
        "date": _game_time(game).date().isoformat(),
        "season": item.season,
        "competition": item.competition,
        "stage": game.get("stage"),
        "group": game.get("group_name"),
        "opponent": game.get(f"{opponent_side}_team_brief_name")
        or game.get(f"{opponent_side}_team_name"),
        "side": side,
        "goals_for": score_for,
        "goals_against": score_against,
        "outcome": _outcome(game, team_id),
        "result_type": _relative_result_type(game, side),
        "penalty_goals_for": penalties[0] if penalties else None,
        "penalty_goals_against": penalties[1] if penalties else None,
    }


def _relative_detailed_match(
    repository: _Repository, item: _StoredMatch, team_id: int
) -> dict[str, object]:
    game_id = _integer(item.game.get("game_id"), "game_id")
    detail = repository.game_detail(game_id)
    game = repository.configured_game(_object(detail.get("game"), "game"))
    events = _valid_events(detail)
    side = _team_side(game, team_id)
    opponent_side = "away" if side == "home" else "home"
    labels = {side: "self", opponent_side: "opponent"}
    sections = _event_sections(events, labels)
    score_for, score_against = _relative_score(game, team_id)
    penalties = _relative_penalties(game, side)
    return {
        "date": _game_time(game).date().isoformat(),
        "season": item.season,
        "competition": item.competition,
        "stage": game.get("stage"),
        "group": game.get("group_name"),
        "round": game.get("round"),
        "venue": game.get("field_name"),
        "opponent": game.get(f"{opponent_side}_team_brief_name")
        or game.get(f"{opponent_side}_team_name"),
        "side": side,
        "goals_for": score_for,
        "goals_against": score_against,
        "outcome": _outcome(game, team_id),
        "result_type": _relative_result_type(game, side),
        "penalty_shootout": _penalty_shootout(
            sections["penalty_attempts"], "self", "opponent", penalties
        ),
        "starting_lineups": {
            "self": sections["starting_lineups"].get("self", []),
            "opponent": sections["starting_lineups"].get("opponent", []),
        },
        "goals": sections["goals"],
        "substitutions": sections["substitutions"],
        "cards": sections["cards"],
        "other_events": sections["other_events"],
    }


def _head_to_head_match(
    repository: _Repository,
    item: _StoredMatch,
    target_home_ids: set[int],
    target_away_ids: set[int],
) -> dict[str, object]:
    game_id = _integer(item.game.get("game_id"), "game_id")
    detail = repository.game_detail(game_id)
    game = repository.configured_game(_object(detail.get("game"), "game"))
    events = _valid_events(detail)
    historical_target_home_id = _matching_team_id(game, target_home_ids)
    _matching_team_id(game, target_away_ids)
    target_home_side = _team_side(game, historical_target_home_id)
    target_away_side = "away" if target_home_side == "home" else "home"
    labels = {target_home_side: "home_team", target_away_side: "away_team"}
    sections = _event_sections(events, labels)
    home_score, away_score = _relative_score(game, historical_target_home_id)
    penalties = _relative_penalties(game, target_home_side)
    return {
        "date": _game_time(game).date().isoformat(),
        "season": item.season,
        "competition": item.competition,
        "stage": game.get("stage"),
        "group": game.get("group_name"),
        "round": game.get("round"),
        "venue": game.get("field_name"),
        "home_team": {
            "name": game[f"{target_home_side}_team_brief_name"],
            "goals_for": home_score,
            "goals_against": away_score,
            "starting_lineup": sections["starting_lineups"].get("home_team", []),
        },
        "away_team": {
            "name": game[f"{target_away_side}_team_brief_name"],
            "goals_for": away_score,
            "goals_against": home_score,
            "starting_lineup": sections["starting_lineups"].get("away_team", []),
        },
        "outcome_for_home_team": _outcome(game, historical_target_home_id),
        "result_type": _head_to_head_result_type(game, target_home_side),
        "penalty_shootout": _penalty_shootout(
            sections["penalty_attempts"],
            "home_team",
            "away_team",
            penalties,
        ),
        "goals": sections["goals"],
        "substitutions": sections["substitutions"],
        "cards": sections["cards"],
        "other_events": sections["other_events"],
    }


def _event_sections(
    events: list[dict[str, Any]], labels: dict[str, str]
) -> dict[str, Any]:
    starters: dict[str, list[tuple[int, str]]] = {
        label: [] for label in labels.values()
    }
    goals: list[dict[str, object]] = []
    cards: list[dict[str, object]] = []
    other: list[dict[str, object]] = []
    penalty_attempts: list[dict[str, object]] = []
    substitutions: dict[tuple[str, int | None, int], dict[str, object]] = {}

    for event in sorted(events, key=_event_sort_key):
        event_type = str(event.get("event_type") or "").upper()
        side = str(event.get("side") or "")
        team = labels.get(side)
        if team is None:
            continue
        player = event.get("player_name")
        player_name = (
            player.strip() if isinstance(player, str) and player.strip() else None
        )
        minute = event.get("minute") if isinstance(event.get("minute"), int) else None
        stoppage = (
            event.get("stoppage_minute")
            if isinstance(event.get("stoppage_minute"), int)
            else 0
        )
        during_shootout = bool(event.get("during_penalty_shootout"))

        if event_type in _LINEUP_EVENTS:
            if player_name:
                kit = event.get("kit_number")
                order = kit if isinstance(kit, int) and kit >= 0 else 10_000
                starters[team].append((order, player_name))
            continue
        if during_shootout and event_type in {"PENALTY", "MISSPENALTY"}:
            penalty_attempts.append(
                {
                    "team": team,
                    "player": player_name,
                    "result": "scored" if event_type == "PENALTY" else "missed",
                }
            )
            continue
        if event_type in _GOAL_EVENTS:
            scoring_team = team
            if event_type == "OWNGOAL":
                scoring_team = next(label for label in labels.values() if label != team)
            goals.append(
                {
                    "team": scoring_team,
                    "minute": minute,
                    "stoppage_minute": stoppage,
                    "scorer": player_name,
                    "type": {
                        "GOAL": "normal",
                        "PENALTY": "penalty",
                        "OWNGOAL": "own_goal",
                    }[event_type],
                }
            )
            continue
        if event_type in {"ON", "OFF"}:
            key = (team, minute, stoppage)
            group = substitutions.setdefault(
                key,
                {
                    "team": team,
                    "minute": minute,
                    "stoppage_minute": stoppage,
                    "players_on": [],
                    "players_off": [],
                },
            )
            if player_name:
                field = "players_on" if event_type == "ON" else "players_off"
                players = group[field]
                if isinstance(players, list):
                    players.append(player_name)
            continue
        if event_type in _CARD_TYPES:
            cards.append(
                {
                    "team": team,
                    "minute": minute,
                    "stoppage_minute": stoppage,
                    "player": player_name,
                    "type": _CARD_TYPES[event_type],
                }
            )
            continue
        event_data: dict[str, object] = {
            "team": team,
            "minute": minute,
            "stoppage_minute": stoppage,
            "player": player_name,
            "type": event_type,
        }
        note = event.get("note")
        if isinstance(note, str) and note.strip():
            event_data["note"] = note.strip()
        other.append(event_data)

    return {
        "starting_lineups": {
            team: [name for _, name in sorted(players)]
            for team, players in starters.items()
        },
        "goals": goals,
        "substitutions": list(substitutions.values()),
        "cards": cards,
        "other_events": other,
        "penalty_attempts": penalty_attempts,
    }


def _penalty_shootout(
    attempts: list[dict[str, object]],
    first_label: str,
    second_label: str,
    penalties: tuple[int, int] | None,
) -> dict[str, object] | None:
    if penalties is None and not attempts:
        return None
    return {
        f"{first_label}_goals": penalties[0] if penalties else None,
        f"{second_label}_goals": penalties[1] if penalties else None,
        "attempts": attempts,
    }


def _valid_events(detail: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        _object(event, "events[]")
        for event in _array(detail.get("events"), "events")
        if _object(event, "events[]").get("valid") is True
    ]


def _event_sort_key(event: dict[str, Any]) -> tuple[int, int, int, int]:
    minute = event.get("minute")
    sequence = event.get("sequence")
    ordering = event.get("time_ordering")
    return (
        minute if isinstance(minute, int) else 10_000,
        event.get("stoppage_minute")
        if isinstance(event.get("stoppage_minute"), int)
        else 0,
        ordering if isinstance(ordering, int) else 0,
        sequence if isinstance(sequence, int) else 0,
    )


def _collect_matches(
    tournaments: list[tuple[dict[str, Any], str, str]],
) -> list[_StoredMatch]:
    matches: dict[int, _StoredMatch] = {}
    for document, season, competition in tournaments:
        info = _object(document.get("tournament"), "tournament")
        for raw in _array(document.get("games"), "games"):
            game = _object(raw, "games[]")
            game_id = _integer(game.get("game_id"), "game_id")
            matches[game_id] = _StoredMatch(
                game=game,
                tournament=info,
                competition=competition,
                season=season,
            )
    return list(matches.values())


def _manual_team_context(
    institution: InstitutionRecord, competition: str, team_name: str
) -> dict[str, object]:
    value = _COMPETITION_VALUES[competition]
    team_description = getattr(institution, f"{value}_description")
    players = [
        {"name": name, "description": raw["description"]}
        for name, raw in institution.player_descriptions.items()
        if isinstance(raw, dict)
        and isinstance(raw.get("competitions"), list)
        and value in raw["competitions"]
        and isinstance(raw.get("description"), str)
        and raw["description"] not in _PLACEHOLDERS
    ]
    return {
        "name": team_name,
        "institution": institution.name,
        "team_description": team_description,
        "player_descriptions": players,
    }


def _competition_label(kind: str, tournament: dict[str, Any]) -> str:
    name = str(tournament.get("name") or "")
    if kind == "男足":
        tier = next((tier for tier in ("甲级", "乙级", "丙级") if tier in name), "")
        return f"马杯男足{tier}"
    if kind == "女足":
        return "马杯女足"
    if kind == "五人制":
        return "马杯五人制"
    return name or kind


def _canonical_season(value: object) -> str:
    text = _text(value, "season")
    years = re.findall(r"20\d{2}", text)
    if not years:
        raise ValueError(f"无法解析赛季：{text}")
    start = int(years[0])
    end = int(years[1]) if len(years) > 1 else start + 1
    return f"{start}-{end % 100:02d}"


def _season_start(season: str) -> int:
    return int(season.split("-", 1)[0])


def _team_side(game: dict[str, Any], team_id: int) -> str:
    if game.get("home_team_id") == team_id:
        return "home"
    if game.get("away_team_id") == team_id:
        return "away"
    raise ValueError(f"比赛不包含球队：team_id={team_id}")


def _has_team(game: dict[str, Any], team_id: int) -> bool:
    return game.get("home_team_id") == team_id or game.get("away_team_id") == team_id


def _has_any_team(game: dict[str, Any], team_ids: set[int]) -> bool:
    return any(_has_team(game, team_id) for team_id in team_ids)


def _matching_team_id(game: dict[str, Any], team_ids: set[int]) -> int:
    matches = [team_id for team_id in team_ids if _has_team(game, team_id)]
    if len(matches) != 1:
        game_id = game.get("game_id")
        raise ValueError(f"无法唯一定位比赛 {game_id} 中的球队身份")
    return matches[0]


def _relative_score(game: dict[str, Any], team_id: int) -> tuple[int, int]:
    side = _team_side(game, team_id)
    opponent = "away" if side == "home" else "home"
    return (
        _integer(game.get(f"{side}_score"), f"{side}_score"),
        _integer(game.get(f"{opponent}_score"), f"{opponent}_score"),
    )


def _relative_penalties(game: dict[str, Any], side: str) -> tuple[int, int] | None:
    home = game.get("home_penalty")
    away = game.get("away_penalty")
    if not isinstance(home, int) or not isinstance(away, int) or home + away == 0:
        return None
    return (home, away) if side == "home" else (away, home)


def _outcome(game: dict[str, Any], team_id: int) -> str:
    score_for, score_against = _relative_score(game, team_id)
    if score_for > score_against:
        return "win"
    if score_for < score_against:
        return "loss"
    penalties = _relative_penalties(game, _team_side(game, team_id))
    if penalties and penalties[0] != penalties[1]:
        return "win" if penalties[0] > penalties[1] else "loss"
    return "draw"


def _outcome_count_key(outcome: str) -> str:
    return {"win": "wins", "draw": "draws", "loss": "losses"}[outcome]


def _relative_result_type(game: dict[str, Any], side: str) -> str:
    opponent = "away" if side == "home" else "home"
    if game.get(f"{side}_abandon") and game.get(f"{opponent}_abandon"):
        return "both_abandon"
    if game.get(f"{side}_abandon"):
        return "self_abandon"
    if game.get(f"{opponent}_abandon"):
        return "opponent_abandon"
    if _relative_penalties(game, side):
        return "penalty_shootout"
    return "normal"


def _head_to_head_result_type(game: dict[str, Any], home_side: str) -> str:
    opponent = "away" if home_side == "home" else "home"
    if game.get(f"{home_side}_abandon") and game.get(f"{opponent}_abandon"):
        return "both_abandon"
    if game.get(f"{home_side}_abandon"):
        return "home_team_abandon"
    if game.get(f"{opponent}_abandon"):
        return "away_team_abandon"
    if _relative_penalties(game, home_side):
        return "penalty_shootout"
    return "normal"


def _is_finished_game(game: dict[str, Any]) -> bool:
    return (
        game.get("status") == "finished"
        and game.get("valid") is True
        and isinstance(game.get("home_score"), int)
        and isinstance(game.get("away_score"), int)
    )


def _game_time(game: dict[str, Any]) -> datetime:
    return _datetime(game.get("kickoff_local"), "kickoff_local")


def _format_kickoff(value: datetime) -> str:
    return f"{value.year} 年 {value.month} 月 {value.day} 日 {value:%H:%M}"


def _read_text(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ValueError(f"无法读取 Prompt 文档：{path}") from exc
    if not text:
        raise ValueError(f"Prompt 文档为空：{path}")
    return text


def _object(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} 必须是 JSON 对象")
    return value


def _array(value: object, name: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{name} 必须是 JSON 数组")
    return value


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} 必须是整数")
    return value


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} 必须是非空字符串")
    return value.strip()


def _datetime(value: object, name: str) -> datetime:
    text = _text(value, name)
    try:
        return datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{name} 不是有效时间：{text}") from exc
