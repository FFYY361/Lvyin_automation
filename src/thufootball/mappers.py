"""Whitelist mappers from THUFootball response objects to domain models."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta, timezone
from types import MappingProxyType
from typing import Any, Literal

from .errors import SchemaError
from .models import (
    GameDetail,
    GameEvent,
    GameStatus,
    GameSummary,
    RefereeAssignment,
    TournamentRef,
    TournamentSnapshot,
    TournamentTeam,
    UserProbe,
)

SHANGHAI = timezone(timedelta(hours=8), "Asia/Shanghai")
_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def _schema(path: str) -> SchemaError:
    return SchemaError(path)


def _mapping(value: object, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _schema(path)
    return value


def _sequence(value: object, path: str) -> Sequence[object]:
    if not isinstance(value, list):
        raise _schema(path)
    return value


def _positive_int(value: object, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise _schema(path)
    return value


def _non_negative_int(value: object, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _schema(path)
    return value


def _legacy_counter(value: object, path: str) -> int:
    if value == -1:
        return 0
    return _non_negative_int(value, path)


def _optional_non_negative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _optional_int(value: object, path: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise _schema(path)
    return value


def _bool(value: object, path: str) -> bool:
    if not isinstance(value, bool):
        raise _schema(path)
    return value


def _binary_flag(value: object, path: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    raise _schema(path)


def _optional_binary_flag(value: object) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    return None


def _text(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _schema(path)
    return value.strip()


def _string(value: object, path: str) -> str:
    if not isinstance(value, str):
        raise _schema(path)
    return value.strip()


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _display_name(value: Mapping[str, Any], path: str) -> str:
    name = _optional_text(value.get("name"))
    brief_name = _optional_text(value.get("brief_name"))
    if name is None and brief_name is None:
        raise _schema(path)
    return name or brief_name or ""


def _brief_name(value: Mapping[str, Any], path: str) -> str:
    brief_name = _optional_text(value.get("brief_name"))
    name = _optional_text(value.get("name"))
    if brief_name is None and name is None:
        raise _schema(path)
    return brief_name or name or ""


def _date(value: object, path: str) -> date:
    if not isinstance(value, str):
        raise _schema(path)
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise _schema(path) from exc
    if parsed.isoformat() != value:
        raise _schema(path)
    return parsed


def _kickoff(value: object, path: str) -> datetime:
    if not isinstance(value, str):
        raise _schema(path)
    try:
        return datetime.strptime(value, _TIME_FORMAT).replace(tzinfo=UTC)
    except ValueError as exc:
        raise _schema(path) from exc


def _game_status(
    *,
    record_active: bool,
    valid: bool,
    started: bool,
    ended: bool,
    kickoff_local: datetime,
    now: datetime,
) -> GameStatus:
    if not record_active or not valid:
        return GameStatus.UNKNOWN
    if ended:
        return GameStatus.FINISHED
    if started:
        return GameStatus.STARTED
    if kickoff_local > now.astimezone(SHANGHAI):
        return GameStatus.SCHEDULED
    return GameStatus.UNKNOWN


def map_user_probe(payload: Mapping[str, Any]) -> UserProbe:
    return UserProbe(
        user_registered=_bool(payload.get("user_registered"), "user_registered")
    )


def map_tournament_ref(raw: object, path: str) -> TournamentRef:
    item = _mapping(raw, path)
    return TournamentRef(
        tournament_id=_positive_int(item.get("id"), f"{path}.id"),
        name=_display_name(item, f"{path}.name"),
        brief_name=_brief_name(item, f"{path}.brief_name"),
        season=_string(item.get("season"), f"{path}.season"),
        begin_date=_date(item.get("begin"), f"{path}.begin"),
        end_date=_date(item.get("end"), f"{path}.end"),
        record_active=_bool(item.get("status"), f"{path}.status"),
        visible=_bool(item.get("visible"), f"{path}.visible"),
    )


def map_tournament_refs(payload: Mapping[str, Any]) -> list[TournamentRef]:
    tournaments = _sequence(payload.get("tourns"), "tourns")
    return [
        map_tournament_ref(raw, f"tourns[{index}]")
        for index, raw in enumerate(tournaments)
    ]


def map_game_summary(
    raw: object,
    path: str,
    *,
    now: datetime | None = None,
    tournament_name: str | None = None,
) -> GameSummary:
    item = _mapping(raw, path)
    game_id = _positive_int(item.get("id"), f"{path}.id")
    tournament_id = _positive_int(item.get("tourn_id"), f"{path}.tourn_id")
    if tournament_name is None:
        tournament = _mapping(item.get("tourn_info"), f"{path}.tourn_info")
        resolved_tournament_name = _display_name(tournament, f"{path}.tourn_info.name")
    else:
        resolved_tournament_name = _text(tournament_name, f"{path}.tournament_name")
    kickoff_utc = _kickoff(item.get("time"), f"{path}.time")
    kickoff_local = kickoff_utc.astimezone(SHANGHAI)
    record_active = _bool(item.get("status"), f"{path}.status")
    raw_valid = item.get("valid")
    valid = False if raw_valid is None else _binary_flag(raw_valid, f"{path}.valid")
    started = _bool(item.get("start"), f"{path}.start")
    ended = _bool(item.get("end"), f"{path}.end")
    home_team = _mapping(
        item.get("home_tourn_team_info"), f"{path}.home_tourn_team_info"
    )
    away_team = _mapping(
        item.get("away_tourn_team_info"), f"{path}.away_tourn_team_info"
    )
    field_raw = item.get("field_info")
    field_name = None
    if field_raw is not None:
        field = _mapping(field_raw, f"{path}.field_info")
        field_name = _optional_text(field.get("name")) or _optional_text(
            field.get("brief_name")
        )
    resolved_now = now or datetime.now(UTC)
    if resolved_now.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    raw_penalty_shootout = item.get("penalty_shootout")
    penalty_shootout = (
        False
        if raw_penalty_shootout is None
        else _binary_flag(raw_penalty_shootout, f"{path}.penalty_shootout")
    )
    return GameSummary(
        game_id=game_id,
        tournament_id=tournament_id,
        tournament_name=resolved_tournament_name,
        kickoff_utc=kickoff_utc,
        kickoff_local=kickoff_local,
        status=_game_status(
            record_active=record_active,
            valid=valid,
            started=started,
            ended=ended,
            kickoff_local=kickoff_local,
            now=resolved_now,
        ),
        record_active=record_active,
        valid=valid,
        stage=_optional_text(item.get("stage")),
        group_name=_optional_text(item.get("group_name")),
        round=_optional_int(item.get("round"), f"{path}.round"),
        home_tournament_team_id=_positive_int(
            item.get("home_tourn_team_id"), f"{path}.home_tourn_team_id"
        ),
        home_team_id=_positive_int(
            home_team.get("team_id"), f"{path}.home_tourn_team_info.team_id"
        ),
        home_team_name=_display_name(home_team, f"{path}.home_tourn_team_info.name"),
        away_tournament_team_id=_positive_int(
            item.get("away_tourn_team_id"), f"{path}.away_tourn_team_id"
        ),
        away_team_id=_positive_int(
            away_team.get("team_id"), f"{path}.away_tourn_team_info.team_id"
        ),
        away_team_name=_display_name(away_team, f"{path}.away_tourn_team_info.name"),
        home_score=_optional_non_negative_int(item.get("home_goal")),
        away_score=_optional_non_negative_int(item.get("away_goal")),
        result_text=_optional_text(item.get("result")),
        penalty_shootout=penalty_shootout,
        home_penalty=_optional_non_negative_int(item.get("home_penalty")),
        away_penalty=_optional_non_negative_int(item.get("away_penalty")),
        home_abandon=_optional_binary_flag(item.get("home_abandon")),
        away_abandon=_optional_binary_flag(item.get("away_abandon")),
        field_name=field_name,
        home_team_brief_name=_optional_text(home_team.get("brief_name")),
        away_team_brief_name=_optional_text(away_team.get("brief_name")),
    )


def map_current_games(payload: Mapping[str, Any]) -> list[GameSummary]:
    games = _sequence(payload.get("current_games"), "current_games")
    now = datetime.now(UTC)
    return [
        map_game_summary(raw, f"current_games[{index}]", now=now)
        for index, raw in enumerate(games)
    ]


def map_tournament_team(
    raw: object,
    path: str,
    *,
    tournament_id: int,
) -> TournamentTeam:
    item = _mapping(raw, path)
    raw_tournament_id = _positive_int(item.get("tourn_id"), f"{path}.tourn_id")
    if raw_tournament_id != tournament_id:
        raise _schema(f"{path}.tourn_id")
    rank = _non_negative_int(item.get("rank"), f"{path}.rank")
    return TournamentTeam(
        tournament_team_id=_positive_int(item.get("id"), f"{path}.id"),
        team_id=_positive_int(item.get("team_id"), f"{path}.team_id"),
        name=_display_name(item, f"{path}.name"),
        brief_name=_brief_name(item, f"{path}.brief_name"),
        group_place=_optional_text(item.get("group_place")),
        wins=_legacy_counter(item.get("win"), f"{path}.win"),
        draws=_legacy_counter(item.get("draw"), f"{path}.draw"),
        losses=_legacy_counter(item.get("lose"), f"{path}.lose"),
        goals_for=_legacy_counter(item.get("goal"), f"{path}.goal"),
        goals_against=_legacy_counter(item.get("concede"), f"{path}.concede"),
        points=_legacy_counter(item.get("point"), f"{path}.point"),
        reported_rank=rank if rank > 0 else None,
    )


def _map_season_ids(raw: object) -> dict[str, int]:
    seasons = _mapping(raw, "season_ids")
    result: dict[str, int] = {}
    for season, value in seasons.items():
        if not isinstance(season, str):
            raise _schema("season_ids.<key>")
        normalised = season.strip()
        if not normalised:
            continue
        result[normalised] = _positive_int(value, f"season_ids.{normalised}")
    return result


def _map_snapshot_games(
    raw_games: Sequence[object],
    teams: tuple[TournamentTeam, ...],
    *,
    tournament_name: str,
) -> tuple[tuple[GameSummary, ...], tuple[int, ...]]:
    now = datetime.now(UTC)
    teams_by_tournament_id = {team.tournament_team_id: team for team in teams}
    mapped_games: list[GameSummary] = []
    invalid_game_ids: list[int] = []
    for index, raw in enumerate(raw_games):
        path = f"games[{index}]"
        item = _mapping(raw, path)
        game_item: Mapping[str, Any] = item
        unresolved_team_identity = False
        for side in ("home", "away"):
            info_key = f"{side}_tourn_team_info"
            if isinstance(item.get(info_key), Mapping):
                continue
            tournament_team_id = item.get(f"{side}_tourn_team_id")
            fallback = (
                teams_by_tournament_id.get(tournament_team_id)
                if isinstance(tournament_team_id, int)
                and not isinstance(tournament_team_id, bool)
                else None
            )
            if fallback is None:
                unresolved_team_identity = True
                break
            if game_item is item:
                game_item = dict(item)
            assert isinstance(game_item, dict)
            game_item[info_key] = {
                "team_id": fallback.team_id,
                "name": fallback.name,
                "brief_name": fallback.brief_name,
            }
        if unresolved_team_identity:
            invalid_game_ids.append(_positive_int(item.get("id"), f"{path}.id"))
            continue
        mapped_games.append(
            map_game_summary(
                game_item,
                path,
                now=now,
                tournament_name=tournament_name,
            )
        )
    return tuple(mapped_games), tuple(invalid_game_ids)


def map_tournament_snapshot(
    payload: Mapping[str, Any],
    *,
    expected_tournament_id: int | None = None,
) -> TournamentSnapshot:
    tournament = _mapping(payload.get("tourn_info"), "tourn_info")
    tournament_id = _positive_int(tournament.get("id"), "tourn_info.id")
    if expected_tournament_id is not None and tournament_id != expected_tournament_id:
        raise _schema("tourn_info.id")
    tournament_name = _display_name(tournament, "tourn_info.name")

    season_ids = _map_season_ids(payload.get("season_ids"))

    raw_teams = _sequence(payload.get("registered_teams"), "registered_teams")
    teams = tuple(
        map_tournament_team(
            raw,
            f"registered_teams[{index}]",
            tournament_id=tournament_id,
        )
        for index, raw in enumerate(raw_teams)
    )
    raw_games = _sequence(payload.get("games"), "games")
    games, invalid_game_ids = _map_snapshot_games(
        raw_games,
        teams,
        tournament_name=tournament_name,
    )
    if any(game.tournament_id != tournament_id for game in games):
        raise _schema("games[].tourn_id")

    return TournamentSnapshot(
        tournament_id=tournament_id,
        name=tournament_name,
        season=_string(tournament.get("season"), "tourn_info.season"),
        begin_date=_date(tournament.get("begin"), "tourn_info.begin"),
        end_date=_date(tournament.get("end"), "tourn_info.end"),
        players_per_side=_non_negative_int(
            tournament.get("players"), "tourn_info.players"
        ),
        season_ids=MappingProxyType(season_ids),
        teams=teams,
        games=games,
        invalid_game_ids=invalid_game_ids,
    )


def _map_event(raw: object, path: str, *, game_id: int) -> GameEvent:
    item = _mapping(raw, path)
    if _positive_int(item.get("game_id"), f"{path}.game_id") != game_id:
        raise _schema(f"{path}.game_id")
    raw_side = _text(item.get("side"), f"{path}.side").upper()
    if raw_side not in {"HOME", "AWAY"}:
        raise _schema(f"{path}.side")
    side: Literal["home", "away"] = "home" if raw_side == "HOME" else "away"
    return GameEvent(
        event_id=_positive_int(item.get("id"), f"{path}.id"),
        tournament_team_id=_positive_int(
            item.get("tourn_team_id"), f"{path}.tourn_team_id"
        ),
        tournament_team_player_id=_positive_int(
            item.get("tourn_team_player_id"), f"{path}.tourn_team_player_id"
        ),
        player_id=_positive_int(item.get("player_id"), f"{path}.player_id"),
        player_name=_text(item.get("name"), f"{path}.name"),
        side=side,
        event_type=_text(item.get("type"), f"{path}.type"),
        minute=_non_negative_int(item.get("time"), f"{path}.time"),
        stoppage_minute=_non_negative_int(
            item.get("stoppage_time"), f"{path}.stoppage_time"
        ),
        kit_number=_non_negative_int(item.get("kitnum"), f"{path}.kitnum"),
        during_penalty_shootout=_binary_flag(
            item.get("during_penalty_shootout"),
            f"{path}.during_penalty_shootout",
        ),
        valid=_bool(item.get("valid"), f"{path}.valid"),
    )


def _map_referee(raw: object, path: str, *, game_id: int) -> RefereeAssignment:
    item = _mapping(raw, path)
    if _positive_int(item.get("game_id"), f"{path}.game_id") != game_id:
        raise _schema(f"{path}.game_id")
    return RefereeAssignment(
        referee_id=_positive_int(item.get("referee_id"), f"{path}.referee_id"),
        position=_text(item.get("position"), f"{path}.position"),
        name=_text(item.get("name"), f"{path}.name"),
    )


def map_game_detail(
    payload: Mapping[str, Any],
    *,
    expected_game_id: int | None = None,
) -> GameDetail:
    tournament = _mapping(payload.get("tourn_info"), "tourn_info")
    tournament_id = _positive_int(tournament.get("id"), "tourn_info.id")
    tournament_name = _display_name(tournament, "tourn_info.name")
    game = map_game_summary(
        payload.get("game_info"),
        "game_info",
        tournament_name=tournament_name,
    )
    if game.tournament_id != tournament_id:
        raise _schema("tourn_info.id")
    if expected_game_id is not None and game.game_id != expected_game_id:
        raise _schema("game_info.id")
    raw_events = _sequence(payload.get("events"), "events")
    raw_referees = _sequence(payload.get("referees"), "referees")
    return GameDetail(
        game=game,
        events=tuple(
            _map_event(raw, f"events[{index}]", game_id=game.game_id)
            for index, raw in enumerate(raw_events)
        ),
        referees=tuple(
            _map_referee(raw, f"referees[{index}]", game_id=game.game_id)
            for index, raw in enumerate(raw_referees)
        ),
    )
