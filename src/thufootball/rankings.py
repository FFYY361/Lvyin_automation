"""Validated final-outcome catalog for supported THUFootball tournaments."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from .errors import ConfigurationError

_SEASON_PATTERN = re.compile(r"(20\d{2}~20\d{2})$")
def _configuration_error(location: str) -> ConfigurationError:
    return ConfigurationError(
        f"Static THUFootball outcome data is invalid at {location}",
        stage="configuration",
    )


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
class StaticTeamIdentity:
    team_name: str
    institution_name: str
    category: str
    brief_name: str
    team_ids: tuple[int, ...]


@dataclass(frozen=True)
class StaticOutcomeCatalog:
    tournament_ids: tuple[int, ...]
    teams_by_name: Mapping[str, StaticTeamIdentity]
    team_ids_by_name: Mapping[str, tuple[int, ...]]
    team_names_by_id: Mapping[int, tuple[str, ...]]
    tournaments_by_id: Mapping[int, StaticTournamentRanking]


_DATABASE_COMPETITIONS = (
    ("male_team_ids", "男足"),
    ("female_team_ids", "女足"),
    ("futsal_team_ids", "五人制"),
)


def build_outcome_catalog(
    institutions: list[Mapping[str, Any]],
    tournaments: list[Mapping[str, Any]],
) -> StaticOutcomeCatalog:
    """Validate database rows and build an immutable outcome catalog."""

    if not institutions:
        raise _configuration_error("institutions")
    teams: dict[str, tuple[int, ...]] = {}
    identities: dict[str, StaticTeamIdentity] = {}
    reverse: dict[int, list[str]] = defaultdict(list)
    institution_by_id: dict[int, str] = {}
    for raw in institutions:
        name = raw.get("name")
        brief_name = raw.get("short_name")
        if (
            not isinstance(name, str)
            or not name.strip()
            or name != name.strip()
            or not isinstance(brief_name, str)
            or not brief_name.strip()
            or brief_name != brief_name.strip()
        ):
            raise _configuration_error("institutions")
        for field, category in _DATABASE_COMPETITIONS:
            raw_ids = raw.get(field)
            if not isinstance(raw_ids, (list, tuple)):
                raise _configuration_error(f"institutions.{name}.{field}")
            team_ids = tuple(
                _positive_int(team_id, f"institutions.{name}.{field}")
                for team_id in raw_ids
            )
            if len(set(team_ids)) != len(team_ids):
                raise _configuration_error(f"institutions.{name}.{field}")
            if not team_ids:
                continue
            team_name = f"{name}{category}"
            teams[team_name] = team_ids
            identities[team_name] = StaticTeamIdentity(
                team_name=team_name,
                institution_name=name,
                category=category,
                brief_name=brief_name,
                team_ids=team_ids,
            )
            for team_id in team_ids:
                owner = institution_by_id.setdefault(team_id, name)
                if owner != name:
                    raise _configuration_error(f"institutions.team_id.{team_id}")
                reverse[team_id].append(team_name)

    if not tournaments:
        raise _configuration_error("tournaments")
    tournament_ids: list[int] = []
    tournament_rankings: dict[int, StaticTournamentRanking] = {}
    for raw in tournaments:
        tournament_id = _positive_int(raw.get("id"), "tournaments.id")
        name = raw.get("name")
        raw_ranks = raw.get("final_rankings")
        if (
            tournament_id in tournament_rankings
            or not isinstance(name, str)
            or not name.strip()
            or not isinstance(raw_ranks, dict)
            or not raw_ranks
        ):
            raise _configuration_error(f"tournaments.{tournament_id}")
        season_match = _SEASON_PATTERN.search(name)
        if season_match is None:
            raise _configuration_error(f"tournaments.{tournament_id}.name")
        ranks: dict[str, str] = {}
        for team_name, rank in raw_ranks.items():
            if team_name not in teams or not isinstance(rank, str) or not rank.strip():
                raise _configuration_error(
                    f"tournaments.{tournament_id}.final_rankings"
                )
            ranks[team_name] = rank
        season = season_match.group(1)
        tournament_ids.append(tournament_id)
        tournament_rankings[tournament_id] = StaticTournamentRanking(
            tournament_id=tournament_id,
            name=name,
            season=season,
            ranks=MappingProxyType(ranks),
        )
    return StaticOutcomeCatalog(
        tournament_ids=tuple(tournament_ids),
        teams_by_name=MappingProxyType(identities),
        team_ids_by_name=MappingProxyType(teams),
        team_names_by_id=MappingProxyType(
            {team_id: tuple(names) for team_id, names in reverse.items()}
        ),
        tournaments_by_id=MappingProxyType(tournament_rankings),
    )


def load_outcome_catalog() -> StaticOutcomeCatalog:
    """Load the canonical outcome catalog from PostgreSQL."""

    from .database import (
        FootballDataRepository,
        create_football_engine,
        create_football_session_factory,
    )

    engine = create_football_engine()
    factory = create_football_session_factory(engine)
    try:
        with factory() as session:
            return FootballDataRepository(session).load_outcome_catalog()
    finally:
        engine.dispose()
