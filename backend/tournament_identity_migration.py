"""One-time, offline conversion of legacy global-team references for revision v6."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

from sqlalchemy import text

COMPETITIONS = {"male": "男足", "female": "女足", "futsal": "五人制"}
SNAPSHOT_FIELDS = ("home_snapshot", "away_snapshot", "head_to_head_snapshot")


def read_source(connection) -> dict[str, list[dict[str, Any]]]:
    queries = {
        "institutions": "SELECT name, short_name, male_team_ids, female_team_ids, futsal_team_ids, male_description, female_description, futsal_description, player_descriptions, predecessors FROM institutions ORDER BY name",
        "tournaments": "SELECT id, competition, data, final_rankings FROM tournaments ORDER BY id",
        "games": "SELECT id, data FROM games ORDER BY id",
        "matches": "SELECT game_id, home_snapshot, away_snapshot, head_to_head_snapshot FROM matches ORDER BY game_id",
        "articles": "SELECT id, input_snapshot FROM articles WHERE article_type = 'preview' ORDER BY id",
    }
    return {
        table: [dict(row) for row in connection.execute(text(query)).mappings()]
        for table, query in queries.items()
    }


def source_digest(source: object) -> str:
    payload = json.dumps(
        source, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def game_index(source) -> dict[int, dict[str, Any]]:
    result = {
        game["game_id"]: game
        for tournament in source["tournaments"]
        for game in tournament["data"].get("games", [])
    }
    result.update({row["id"]: row["data"]["game"] for row in source["games"]})
    return result


def referenced_games(value: object) -> set[int]:
    if isinstance(value, dict):
        return (
            {value["game_id"]}
            if isinstance(value.get("game_id"), int) and value["game_id"] > 0
            else set()
        ).union(*(referenced_games(child) for child in value.values()))
    if isinstance(value, list):
        return set().union(*(referenced_games(child) for child in value))
    return set()


def convert_snapshot(value: object, games: dict[int, dict[str, Any]], game_id=None):
    """Resolve each reference in its own game, including swapped historical sides."""
    if isinstance(value, list):
        return [convert_snapshot(child, games, game_id) for child in value]
    if not isinstance(value, dict):
        return value
    game_id = value.get("game_id", game_id)
    result = {
        key: convert_snapshot(child, games, game_id)
        for key, child in value.items()
        if key != "team_id"
    }
    if "team_id" in value:
        if game_id == -1:
            result["tournament_team_id"] = None
        else:
            game = games.get(game_id)
            ids = (
                []
                if game is None
                else [
                    game[side + "_tournament_team_id"]
                    for side in ("home", "away")
                    if game.get(side + "_team_id") == value["team_id"]
                ]
            )
            if len(ids) != 1:
                raise ValueError(
                    f"Cannot resolve game={game_id}, legacy team={value['team_id']}"
                )
            result["tournament_team_id"] = ids[0]
    return result


def prepare_manifest(source, external_games=None):
    games = game_index(source)
    games.update(external_games or {})
    identities = {}
    institutions = []
    owners = {}
    for original in source["institutions"]:
        record = {"name": original["name"], "predecessors": []}
        for legacy in [original] + original["predecessors"]:
            mappings = {competition: {} for competition in COMPETITIONS}
            for tournament in source["tournaments"]:
                competition = tournament["competition"]
                ids = set(legacy[competition + "_team_ids"])
                matches = [
                    team
                    for team in tournament["data"].get("registered_teams", [])
                    if team.get("team_id") in ids
                ]
                if len(matches) > 1:
                    raise ValueError(
                        f"Multiple registrations: {legacy['name']}, tournament={tournament['id']}"
                    )
                if matches:
                    registration = matches[0]["tournament_team_id"]
                    if (
                        not isinstance(registration, int)
                        or isinstance(registration, bool)
                        or registration <= 0
                    ):
                        raise ValueError("Invalid registration ID")
                    if registration in owners:
                        raise ValueError(
                            f"Registration has multiple identities: {registration}"
                        )
                    owners[registration] = legacy["name"]
                    mappings[competition][str(tournament["id"])] = registration
            for competition, suffix in COMPETITIONS.items():
                if legacy[competition + "_team_ids"] and not mappings[competition]:
                    raise ValueError(
                        f"No registrations for configured project: {legacy['name']}/{competition}"
                    )
                identities[legacy["name"] + suffix] = mappings[competition]
            if legacy is original:
                record["tournament_teams"] = mappings
            else:
                record["predecessors"].append(
                    {
                        "name": legacy["name"],
                        "short_name": legacy["short_name"],
                        "tournament_teams": mappings,
                    }
                )
        institutions.append(record)
    rankings = []
    for tournament in source["tournaments"]:
        converted = {}
        for name, rank in tournament["final_rankings"].items():
            registration = identities.get(name, {}).get(str(tournament["id"]))
            if registration is None or str(registration) in converted:
                raise ValueError(
                    f"Cannot resolve final ranking: {tournament['id']}/{name}"
                )
            converted[str(registration)] = rank
        rankings.append({"id": tournament["id"], "final_rankings": converted})
    matches = [
        {
            "game_id": row["game_id"],
            **{
                field: convert_snapshot(row[field], games, row["game_id"])
                for field in SNAPSHOT_FIELDS
            },
        }
        for row in source["matches"]
    ]
    articles = [
        {
            "id": row["id"],
            "input_snapshot": convert_snapshot(row["input_snapshot"], games),
        }
        for row in source["articles"]
    ]
    return {
        "revision": "v6_tournament_team_identity",
        "source_sha256": source_digest(source),
        "institutions": institutions,
        "tournaments": rankings,
        "matches": matches,
        "articles": articles,
        "external_games": deepcopy(external_games or {}),
    }


def apply_manifest(connection, manifest):
    if manifest.get("revision") != "v6_tournament_team_identity":
        raise ValueError("Wrong migration manifest revision")
    if source_digest(read_source(connection)) != manifest.get("source_sha256"):
        raise ValueError("Database changed since preparation; prepare a fresh manifest")
    # Rebuild rather than trust edited output, and verify that all references resolve.
    rebuilt = prepare_manifest(
        read_source(connection),
        {int(key): value for key, value in manifest.get("external_games", {}).items()},
    )
    if rebuilt != manifest:
        # JSON round trips convert evidence keys to strings.
        if json.loads(json.dumps(rebuilt)) != manifest:
            raise ValueError("Migration manifest does not match its source")
    old_title_hashes = title_prompt_hashes(connection)
    for table, key, fields in (
        ("institutions", "name", ("tournament_teams", "predecessors")),
        ("tournaments", "id", ("final_rankings",)),
        ("matches", "game_id", SNAPSHOT_FIELDS),
        ("articles", "id", ("input_snapshot",)),
    ):
        assignments = ", ".join(
            f"{field} = CAST(:{field} AS jsonb)" for field in fields
        )
        statement = text(f"UPDATE {table} SET {assignments} WHERE {key} = :row_key")
        for row in manifest[table]:
            connection.execute(
                statement,
                {
                    "row_key": row[key],
                    **{
                        field: json.dumps(row[field], ensure_ascii=False)
                        for field in fields
                    },
                },
            )
    rekey_title_cache(connection, old_title_hashes)


def rekey_title_cache(connection, old_title_hashes: dict[int, str]) -> None:
    new_title_hashes = title_prompt_hashes(connection)
    for batch_id, old_hash in old_title_hashes.items():
        new_hash = new_title_hashes[batch_id]
        if old_hash != new_hash:
            connection.execute(
                text(
                    "UPDATE ai_title_results SET prompt_hash = :new_hash "
                    "WHERE batch_id = :batch_id AND prompt_hash = :old_hash "
                    "AND status = 'succeeded'"
                ),
                {"batch_id": batch_id, "old_hash": old_hash, "new_hash": new_hash},
            )


def title_prompt_hashes(connection) -> dict[int, str]:
    """Rekey only valid cached titles, using the unchanged title material builder."""
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from backend.ai_preview import build_title_material
    from backend.models import Batch

    rows = (
        connection.execute(text("SELECT batch_id, status FROM ai_title_results"))
        .mappings()
        .all()
    )
    if any(row["status"] in {"queued", "running"} for row in rows):
        raise ValueError("Wait for active AI title tasks to finish before migrating")
    batch_ids = {row["batch_id"] for row in rows}
    if not batch_ids:
        return {}
    with Session(
        bind=connection, autoflush=False, join_transaction_mode="create_savepoint"
    ) as session:
        return {
            batch.id: build_title_material(session, batch).prompt_hash
            for batch in session.scalars(select(Batch).where(Batch.id.in_(batch_ids)))
        }
