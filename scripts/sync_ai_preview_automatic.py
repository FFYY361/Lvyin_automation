"""Synchronise complete automatic football data into PostgreSQL."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for import_root in (PROJECT_ROOT, PROJECT_ROOT / "src"):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from backend.config import load_env_file
from backend.credentials import (
    AutomaticCredentialManager,
    AutoRefreshingTHUFootballClient,
)
from thufootball.cli import _jsonable
from thufootball.database import (
    GameRecord,
    TournamentRecord,
    create_football_engine,
    create_football_session_factory,
)
from thufootball.errors import ConfigurationError, THUFootballError
from thufootball.mappers import map_game_detail, map_tournament_snapshot

GAME_CONCURRENCY = 6
SOFTWARE_ABANDON_GAME_ID = 3497
SOFTWARE_REMOVED_EVENT_IDS = {124696, 124697}
SCHWARZMAN_ABANDON_GAME_ID = 4152
ADJUSTED_GAME_IDS = {SOFTWARE_ABANDON_GAME_ID, SCHWARZMAN_ABANDON_GAME_ID}


def _sync_target_tournament_ids(session: Session) -> list[int]:
    return list(
        session.scalars(
            select(TournamentRecord.id)
            .where(TournamentRecord.is_finalized.is_(False))
            .order_by(TournamentRecord.id)
        )
    )


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


def _adjust_game_payload(payload: Mapping[str, Any], game_id: int) -> Mapping[str, Any]:
    if game_id not in ADJUSTED_GAME_IDS:
        return payload

    game = _object(payload.get("game_info"), "game_info")
    if game_id == SCHWARZMAN_ABANDON_GAME_ID:
        home_team = _object(
            game.get("home_tourn_team_info"), "game_info.home_tourn_team_info"
        )
        if (
            game.get("id") != game_id
            or game.get("tourn_id") != 124
            or game.get("home_tourn_team_id") != 1751
            or home_team.get("team_id") != 119
            or game.get("result") != "1:0"
        ):
            raise ValueError(f"unexpected source data for adjusted game {game_id}")
        adjusted_game = dict(game)
        adjusted_game.update(
            {
                "home_goal": 0,
                "away_goal": 3,
                "result": "0:3",
                "home_penalty": None,
                "away_penalty": None,
                "home_abandon": 1,
            }
        )
        adjusted_payload = dict(payload)
        adjusted_payload["game_info"] = adjusted_game
        return adjusted_payload

    away_team = _object(
        game.get("away_tourn_team_info"), "game_info.away_tourn_team_info"
    )
    if (
        game.get("id") != game_id
        or game.get("tourn_id") != 100
        or game.get("away_tourn_team_id") != 1528
        or away_team.get("team_id") != 55
        or game.get("result") != "3:0"
    ):
        raise ValueError(f"unexpected source data for adjusted game {game_id}")

    events = _array(payload.get("events"), "events")
    parsed_events = [_object(event, "events[]") for event in events]
    removed = [
        event
        for event in parsed_events
        if event.get("id") in SOFTWARE_REMOVED_EVENT_IDS
    ]
    if (
        len(removed) != 2
        or {event.get("id") for event in removed} != SOFTWARE_REMOVED_EVENT_IDS
        or any(
            event.get("type") != "GOAL"
            or event.get("side") != "HOME"
            or event.get("time") != 81
            or event.get("tourn_team_player_id") is not None
            or event.get("player_id") is not None
            for event in removed
        )
    ):
        raise ValueError(f"unexpected erroneous events for adjusted game {game_id}")

    adjusted_game = dict(game)
    adjusted_game["away_abandon"] = 1
    adjusted_payload = dict(payload)
    adjusted_payload["game_info"] = adjusted_game
    adjusted_payload["events"] = [
        event
        for event in parsed_events
        if event.get("id") not in SOFTWARE_REMOVED_EVENT_IDS
    ]
    return adjusted_payload


def _adjust_tournament_games(document: dict[str, object]) -> None:
    games = _array(document.get("games"), "games")
    for raw_game in games:
        if not isinstance(raw_game, dict):
            raise ValueError("games[] must be an object")
        game = raw_game
        game_id = game.get("game_id")
        if game_id == SOFTWARE_ABANDON_GAME_ID:
            game["away_abandon"] = True
        elif game_id == SCHWARZMAN_ABANDON_GAME_ID:
            game.update(
                {
                    "home_score": 0,
                    "away_score": 3,
                    "result_text": "0:3",
                    "home_penalty": None,
                    "away_penalty": None,
                    "home_abandon": True,
                }
            )


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


async def _read_game(
    client: AutoRefreshingTHUFootballClient,
    semaphore: asyncio.Semaphore,
    game_id: int,
) -> tuple[int, dict[str, object]]:
    async with semaphore:
        last_error: THUFootballError | None = None
        for attempt in range(3):
            try:
                if game_id in ADJUSTED_GAME_IDS:
                    payload = await client._request_json(
                        "GetGameInfo",
                        {"game_id": game_id},
                        authentication_required=True,
                    )
                    detail = map_game_detail(
                        _adjust_game_payload(payload, game_id),
                        expected_game_id=game_id,
                    )
                else:
                    detail = await client.get_game_info(game_id)
                return game_id, _jsonable(detail)
            except THUFootballError as exc:
                last_error = exc
                if not exc.retryable or attempt == 2:
                    raise RuntimeError(
                        f"failed to read complete game detail for game {game_id}"
                    ) from exc
                await asyncio.sleep(0.5 * (attempt + 1))
        raise AssertionError("unreachable") from last_error


async def _synchronise() -> dict[str, object]:
    load_env_file()
    engine = create_football_engine()
    session_factory = create_football_session_factory(engine)
    try:
        with session_factory() as session:
            target_tournament_ids = _sync_target_tournament_ids(session)
    finally:
        engine.dispose()
    if not target_tournament_ids:
        raise ConfigurationError(
            "no unfinalized tournaments are available for automatic-data synchronisation",
            stage="configuration",
        )

    credential_manager = AutomaticCredentialManager.from_environment()
    openid, session_key = await credential_manager.refresh()
    tournament_documents: dict[int, dict[str, object]] = {}

    async with AutoRefreshingTHUFootballClient(
        openid=openid,
        session_key=session_key,
        load_environment=False,
        credential_refresher=credential_manager.refresh,
    ) as client:
        for tournament_id in target_tournament_ids:
            payload = await client._request_json(
                "GetTournInfo",
                {"tourn_id": tournament_id},
                authentication_required=True,
            )
            tournament_documents[tournament_id] = _tournament_document(
                payload,
                tournament_id=tournament_id,
            )
            _adjust_tournament_games(tournament_documents[tournament_id])

        game_tournaments: dict[int, int] = {}
        game_summaries: dict[int, Mapping[str, Any]] = {}
        for tournament_id, document in tournament_documents.items():
            games = document["games"]
            if not isinstance(games, list):
                raise ValueError("games must be an array")
            for game in games:
                item = _object(game, "games[]")
                game_id = item.get("game_id")
                if not isinstance(game_id, int):
                    raise ValueError("games[].game_id must be an integer")
                previous = game_tournaments.setdefault(game_id, tournament_id)
                if previous != tournament_id:
                    raise ValueError(f"game {game_id} belongs to multiple tournaments")
                game_summaries[game_id] = item

        semaphore = asyncio.Semaphore(GAME_CONCURRENCY)
        game_items = await asyncio.gather(
            *(
                _read_game(client, semaphore, game_id)
                for game_id in sorted(game_tournaments)
            )
        )

    game_documents = dict(game_items)
    if set(game_documents) != set(game_tournaments):
        raise ValueError("not every tournament game has a complete detail snapshot")

    _validate_game_documents(game_tournaments, game_summaries, game_documents)
    _persist_documents(
        tournament_documents,
        game_documents,
    )
    return _synchronisation_summary(tournament_documents, game_documents)


def _validate_game_documents(
    game_tournaments: Mapping[int, int],
    game_summaries: Mapping[int, Mapping[str, Any]],
    game_documents: Mapping[int, Mapping[str, Any]],
) -> None:
    for game_id, tournament_id in game_tournaments.items():
        document = _object(game_documents[game_id], f"game {game_id}")
        game = _object(document.get("game"), f"game {game_id}.game")
        if game.get("game_id") != game_id:
            raise ValueError(f"game document ID mismatch for game {game_id}")
        if game.get("tournament_id") != tournament_id:
            raise ValueError(f"game tournament mismatch for game {game_id}")
        if game != game_summaries[game_id]:
            raise ValueError(f"game summary and detail differ for game {game_id}")


def _persist_documents(
    tournament_documents: Mapping[int, dict[str, object]],
    game_documents: Mapping[int, dict[str, object]],
    *,
    session_factory: sessionmaker[Session] | None = None,
) -> None:
    engine = create_football_engine() if session_factory is None else None
    factory = session_factory or create_football_session_factory(engine)
    try:
        with factory.begin() as session:
            records = list(
                session.scalars(
                    select(TournamentRecord).where(
                        TournamentRecord.id.in_(tournament_documents),
                        TournamentRecord.is_finalized.is_(False),
                    )
                )
            )
            if {record.id for record in records} != set(tournament_documents):
                raise ConfigurationError(
                    "tournament targets changed or were finalized before "
                    "synchronisation commit",
                    stage="configuration",
                )
            for record in records:
                record.data = tournament_documents[record.id]

            for game_id, document in game_documents.items():
                game = _object(document.get("game"), f"game {game_id}.game")
                tournament_id = game.get("tournament_id")
                if not isinstance(tournament_id, int):
                    raise ValueError(
                        f"game {game_id}.game.tournament_id must be an integer"
                    )
                record = session.get(GameRecord, game_id)
                if record is None:
                    session.add(
                        GameRecord(
                            id=game_id,
                            tournament_id=tournament_id,
                            data=document,
                        )
                    )
                else:
                    record.tournament_id = tournament_id
                    record.data = document

            stale_ids = set(
                session.scalars(
                    select(GameRecord.id).where(
                        GameRecord.tournament_id.in_(tournament_documents)
                    )
                )
            ) - set(game_documents)
            if stale_ids:
                session.execute(delete(GameRecord).where(GameRecord.id.in_(stale_ids)))
    finally:
        if engine is not None:
            engine.dispose()


def _synchronisation_summary(
    tournament_documents: Mapping[int, dict[str, object]],
    game_documents: Mapping[int, dict[str, object]],
) -> dict[str, int]:
    return {
        "tournament_count": len(tournament_documents),
        "registered_team_count": sum(
            len(_array(document.get("registered_teams"), "registered_teams"))
            for document in tournament_documents.values()
        ),
        "registered_player_count": sum(
            len(_array(document.get("registered_players"), "registered_players"))
            for document in tournament_documents.values()
        ),
        "game_count": len(game_documents),
        "event_count": sum(
            len(_array(document.get("events"), "events"))
            for document in game_documents.values()
        ),
    }


def main() -> int:
    summary = asyncio.run(_synchronise())
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
