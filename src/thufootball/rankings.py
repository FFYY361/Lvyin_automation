"""Validated static final-outcome data for supported THUFootball tournaments."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .errors import ConfigurationError


_NOTES_ROOT = Path(__file__).with_name("notes")
_SEASON_PATTERN = re.compile(r"(20\d{2}~20\d{2})$")
_TEAM_SUFFIXES = ("男足", "女足", "五人制")


class _DuplicateKeyError(ValueError):
    pass


def _configuration_error(location: str) -> ConfigurationError:
    return ConfigurationError(
        f"Static THUFootball outcome data is invalid at {location}",
        stage="configuration",
    )


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(key)
        result[key] = value
    return result


def _read_json(path: Path, location: str) -> object:
    try:
        return json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object
        )
    except (OSError, UnicodeError, json.JSONDecodeError, _DuplicateKeyError) as exc:
        raise _configuration_error(location) from exc


def _positive_int(value: object, location: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise _configuration_error(location)
    return value


@dataclass(frozen=True)
class StaticTournamentRanking:
    tournament_id: int
    name: str
    season: str
    ranks: Mapping[str, str]


@dataclass(frozen=True)
class StaticOutcomeCatalog:
    tournament_ids: tuple[int, ...]
    team_ids_by_name: Mapping[str, tuple[int, ...]]
    team_names_by_id: Mapping[int, tuple[str, ...]]
    tournaments_by_id: Mapping[int, StaticTournamentRanking]


def _load_teams(
    notes_root: Path,
) -> tuple[dict[str, tuple[int, ...]], dict[int, tuple[str, ...]]]:
    raw = _read_json(notes_root / "teams.json", "teams.json")
    if not isinstance(raw, dict) or not raw:
        raise _configuration_error("teams.json")

    teams: dict[str, tuple[int, ...]] = {}
    reverse: dict[int, list[str]] = defaultdict(list)
    for team_name, raw_ids in raw.items():
        if (
            not isinstance(team_name, str)
            or not team_name.strip()
            or not team_name.endswith(_TEAM_SUFFIXES)
            or not isinstance(raw_ids, list)
            or not raw_ids
        ):
            raise _configuration_error("teams.json")
        team_ids = tuple(
            _positive_int(team_id, f"teams.json.{team_name}")
            for team_id in raw_ids
        )
        if len(set(team_ids)) != len(team_ids):
            raise _configuration_error(f"teams.json.{team_name}")
        teams[team_name] = team_ids
        for team_id in team_ids:
            reverse[team_id].append(team_name)

    return teams, {
        team_id: tuple(team_names) for team_id, team_names in reverse.items()
    }


def _validate_identity_audit(
    notes_root: Path,
    teams: Mapping[str, tuple[int, ...]],
    team_names_by_id: Mapping[int, tuple[str, ...]],
) -> None:
    raw = _read_json(notes_root / "identity_audit.json", "identity_audit.json")
    if not isinstance(raw, dict) or set(raw) != {"merged_teams", "shared_team_ids"}:
        raise _configuration_error("identity_audit.json")
    if not isinstance(raw["merged_teams"], list) or not isinstance(
        raw["shared_team_ids"], list
    ):
        raise _configuration_error("identity_audit.json")

    audited_shared: dict[int, tuple[str, ...]] = {}
    for item in raw["shared_team_ids"]:
        if not isinstance(item, dict) or set(item) != {
            "team_id",
            "institution",
            "team_names",
        }:
            raise _configuration_error("identity_audit.json.shared_team_ids")
        team_id = _positive_int(
            item["team_id"], "identity_audit.json.shared_team_ids.team_id"
        )
        institution = item["institution"]
        team_names = item["team_names"]
        if (
            not isinstance(institution, str)
            or not institution.strip()
            or not isinstance(team_names, list)
            or len(team_names) < 2
            or any(name not in teams for name in team_names)
            or team_id in audited_shared
        ):
            raise _configuration_error("identity_audit.json.shared_team_ids")
        audited_shared[team_id] = tuple(team_names)

    actual_shared = {
        team_id: team_names
        for team_id, team_names in team_names_by_id.items()
        if len(team_names) > 1
    }
    if audited_shared != actual_shared:
        raise _configuration_error("identity_audit.json.shared_team_ids")

    audited_merged: set[str] = set()
    for item in raw["merged_teams"]:
        if not isinstance(item, dict) or set(item) != {
            "team_name",
            "team_ids",
            "observations",
        }:
            raise _configuration_error("identity_audit.json.merged_teams")
        team_name = item["team_name"]
        team_ids = item["team_ids"]
        observations = item["observations"]
        if (
            not isinstance(team_name, str)
            or team_name not in teams
            or team_name in audited_merged
            or not isinstance(team_ids, list)
            or not team_ids
            or not isinstance(observations, list)
            or not observations
        ):
            raise _configuration_error("identity_audit.json.merged_teams")
        audited_ids = tuple(
            _positive_int(
                team_id, "identity_audit.json.merged_teams.team_ids"
            )
            for team_id in team_ids
        )
        if (
            len(set(audited_ids)) != len(audited_ids)
            or audited_ids
            != tuple(
                team_id
                for team_id in teams[team_name]
                if team_id in audited_ids
            )
        ):
            raise _configuration_error("identity_audit.json.merged_teams")

        observed_ids: set[int] = set()
        for observation in observations:
            if not isinstance(observation, dict) or set(observation) != {
                "source_names",
                "team_id",
                "tournament_ids",
            }:
                raise _configuration_error("identity_audit.json.merged_teams")
            source_names = observation["source_names"]
            observed_team_id = _positive_int(
                observation["team_id"],
                "identity_audit.json.merged_teams.observations.team_id",
            )
            observed_tournament_ids = observation["tournament_ids"]
            if (
                observed_team_id not in audited_ids
                or observed_team_id in observed_ids
                or not isinstance(source_names, list)
                or not source_names
                or any(
                    not isinstance(source_name, str) or not source_name.strip()
                    for source_name in source_names
                )
                or not isinstance(observed_tournament_ids, list)
                or not observed_tournament_ids
            ):
                raise _configuration_error("identity_audit.json.merged_teams")
            validated_tournament_ids = tuple(
                _positive_int(
                    tournament_id,
                    "identity_audit.json.merged_teams.observations.tournament_ids",
                )
                for tournament_id in observed_tournament_ids
            )
            if len(set(validated_tournament_ids)) != len(
                validated_tournament_ids
            ):
                raise _configuration_error("identity_audit.json.merged_teams")
            observed_ids.add(observed_team_id)
        if observed_ids != set(audited_ids):
            raise _configuration_error("identity_audit.json.merged_teams")
        audited_merged.add(team_name)

    if {
        team_name for team_name, team_ids in teams.items() if len(team_ids) > 1
    } - audited_merged:
        raise _configuration_error("identity_audit.json.merged_teams")


def _load_tournaments(
    notes_root: Path, teams: Mapping[str, tuple[int, ...]]
) -> tuple[tuple[int, ...], dict[int, StaticTournamentRanking]]:
    raw_tournaments = _read_json(notes_root / "tourns.json", "tourns.json")
    if not isinstance(raw_tournaments, dict) or not raw_tournaments:
        raise _configuration_error("tourns.json")

    tournament_ids: list[int] = []
    metadata: dict[int, tuple[str, str]] = {}
    for name, raw_tournament_id in raw_tournaments.items():
        if not isinstance(name, str) or not name.strip():
            raise _configuration_error("tourns.json")
        tournament_id = _positive_int(raw_tournament_id, "tourns.json")
        season_match = _SEASON_PATTERN.search(name)
        if tournament_id in metadata or season_match is None:
            raise _configuration_error("tourns.json")
        tournament_ids.append(tournament_id)
        metadata[tournament_id] = (name, season_match.group(1))

    ranks_root = notes_root / "ranks"
    try:
        rank_paths = tuple(ranks_root.glob("*.json"))
    except OSError as exc:
        raise _configuration_error("ranks") from exc
    if any(not path.stem.isdecimal() for path in rank_paths):
        raise _configuration_error("ranks")
    rank_file_ids = {int(path.stem) for path in rank_paths}
    if rank_file_ids != set(tournament_ids):
        raise _configuration_error("ranks")

    tournaments: dict[int, StaticTournamentRanking] = {}
    for tournament_id in tournament_ids:
        location = f"ranks/{tournament_id}.json"
        raw_ranks = _read_json(ranks_root / f"{tournament_id}.json", location)
        if not isinstance(raw_ranks, dict) or not raw_ranks:
            raise _configuration_error(location)
        ranks: dict[str, str] = {}
        for team_name, rank in raw_ranks.items():
            if (
                team_name not in teams
                or not isinstance(rank, str)
                or not rank.strip()
            ):
                raise _configuration_error(location)
            ranks[team_name] = rank
        name, season = metadata[tournament_id]
        tournaments[tournament_id] = StaticTournamentRanking(
            tournament_id=tournament_id,
            name=name,
            season=season,
            ranks=MappingProxyType(ranks),
        )
    return tuple(tournament_ids), tournaments


@lru_cache(maxsize=1)
def load_static_outcome_catalog() -> StaticOutcomeCatalog:
    teams, team_names_by_id = _load_teams(_NOTES_ROOT)
    _validate_identity_audit(_NOTES_ROOT, teams, team_names_by_id)
    tournament_ids, tournaments = _load_tournaments(_NOTES_ROOT, teams)
    return StaticOutcomeCatalog(
        tournament_ids=tournament_ids,
        team_ids_by_name=MappingProxyType(teams),
        team_names_by_id=MappingProxyType(team_names_by_id),
        tournaments_by_id=MappingProxyType(tournaments),
    )
