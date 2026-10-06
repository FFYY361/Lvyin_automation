"""Canonical tournament documents shared by sync and live prompt reads."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .cli import _jsonable
from .mappers import map_tournament_snapshot


def _object(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _array(value: object, name: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be an array")
    return value


def _tournament_info(raw: object) -> dict[str, object]:
    item = _object(raw, "tourn_info")
    fields = (
        "id",
        "name",
        "report_name",
        "brief_name",
        "acronym",
        "season",
        "begin",
        "end",
        "gender",
        "players",
        "rule",
        "intro",
        "minonfield",
        "has_kitnum",
        "ordinary_time",
        "extra_time",
        "penalty_condition",
        "penalty_round",
        "status",
        "visible",
    )
    return {field: item.get(field) for field in fields}


def _registered_team(raw: object) -> dict[str, object]:
    item = _object(raw, "registered_teams[]")
    return {
        "tournament_team_id": item.get("id"),
        "tournament_id": item.get("tourn_id"),
        "team_id": item.get("team_id"),
        "name": item.get("name"),
        "report_name": item.get("report_name"),
        "brief_name": item.get("brief_name"),
        "acronym": item.get("acronym"),
        "status": item.get("status"),
        "group_place": item.get("group_place"),
        "win": item.get("win"),
        "draw": item.get("draw"),
        "lose": item.get("lose"),
        "goal": item.get("goal"),
        "concede": item.get("concede"),
        "point": item.get("point"),
        "assist": item.get("assist"),
        "own_goal": item.get("own_goal"),
        "penalty": item.get("penalty"),
        "penalty_miss": item.get("penalty_miss"),
        "yellow_card": item.get("yellow_card"),
        "red_card": item.get("red_card"),
        "rank": item.get("rank"),
    }


def _registered_player(raw: object) -> dict[str, object]:
    item = _object(raw, "registered_players[]")
    return {
        "tournament_team_player_id": item.get("id"),
        "tournament_team_id": item.get("tourn_team_id"),
        "player_id": item.get("player_id"),
        "name": item.get("name"),
        "team_name": item.get("team_name"),
        "position": item.get("position"),
        "kit_number": item.get("kitnum"),
        "valid": bool(item.get("valid")),
        "start": item.get("start"),
        "appearance": item.get("appearance"),
        "minute": item.get("minute"),
        "goal": item.get("goal"),
        "assist": item.get("assist"),
        "own_goal": item.get("own_goal"),
        "penalty": item.get("penalty"),
        "penalty_miss": item.get("penalty_miss"),
        "yellow_card": item.get("yellow_card"),
        "red_card": item.get("red_card"),
        "suspension": item.get("suspension"),
        "note": item.get("note"),
    }


def _suspension(raw: object) -> dict[str, object]:
    item = _object(raw, "suspensions[]")
    player = item.get("player_info")
    player_info = player if isinstance(player, Mapping) else {}
    registered_player = item.get("tourn_team_player_info")
    registered_player_info = (
        registered_player if isinstance(registered_player, Mapping) else {}
    )
    team = item.get("tourn_team_info")
    team_info = team if isinstance(team, Mapping) else {}
    return {
        "suspension_id": item.get("id"),
        "tournament_id": item.get("tourn_id"),
        "tournament_team_player_id": item.get("tourn_team_player_id"),
        "player_id": player_info.get("id") or registered_player_info.get("player_id"),
        "player_name": player_info.get("name"),
        "tournament_team_id": team_info.get("id"),
        "team_id": team_info.get("team_id"),
        "team_name": team_info.get("name"),
        "is_additional": bool(item.get("is_additional")),
        "begin": item.get("begin"),
        "reason": item.get("reason"),
        "number": item.get("number"),
        "valid": bool(item.get("valid")),
    }


def _tournament_document(
    payload: Mapping[str, Any],
    *,
    tournament_id: int,
) -> dict[str, object]:
    snapshot = map_tournament_snapshot(
        payload,
        expected_tournament_id=tournament_id,
    )
    season_ids = _object(payload.get("season_ids"), "season_ids")
    return {
        "tournament": _tournament_info(payload.get("tourn_info")),
        "season_ids": dict(season_ids),
        "registered_teams": [
            _registered_team(item)
            for item in _array(payload.get("registered_teams"), "registered_teams")
        ],
        "registered_players": [
            _registered_player(item)
            for item in _array(payload.get("registered_players"), "registered_players")
        ],
        "games": _jsonable(snapshot.games),
        "suspensions": [
            _suspension(item)
            for item in _array(payload.get("suspensions"), "suspensions")
        ],
    }
