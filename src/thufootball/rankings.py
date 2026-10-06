"""Validated final-outcome catalog for supported THUFootball tournaments."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from .errors import ConfigurationError
from .models import TeamTournamentOutcome

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
    owner_name: str
    is_predecessor: bool = False


@dataclass(frozen=True)
class StaticOutcomeCatalog:
    tournament_ids: tuple[int, ...]
    teams_by_name: Mapping[str, StaticTeamIdentity]
    team_ids_by_name: Mapping[str, tuple[int, ...]]
    team_names_by_id: Mapping[int, tuple[str, ...]]
    tournaments_by_id: Mapping[int, StaticTournamentRanking]

    def resolve_identity(self, team_id: int, category: str) -> StaticTeamIdentity:
        matches = [
            self.teams_by_name[name]
            for name in self.team_names_by_id.get(team_id, ())
            if self.teams_by_name[name].category == category
        ]
        if len(matches) != 1:
            raise _configuration_error(f"team_id.{team_id}.{category}")
        return matches[0]

    def history_identities(
        self, identity: StaticTeamIdentity
    ) -> tuple[StaticTeamIdentity, ...]:
        if identity.is_predecessor:
            return (identity,)
        return tuple(
            item
            for item in self.teams_by_name.values()
            if item.owner_name == identity.owner_name
            and item.category == identity.category
        )

    def history_ids(self, identity: StaticTeamIdentity) -> tuple[int, ...]:
        return tuple(
            dict.fromkeys(
                team_id
                for item in self.history_identities(identity)
                for team_id in item.team_ids
            )
        )

    def season_outcomes(
        self, identity: StaticTeamIdentity, tournament_ids: tuple[int, ...]
    ) -> tuple[TeamTournamentOutcome, ...]:
        """Select body results first, then the best predecessor result in this season."""
        families = self.history_identities(identity)
        candidates = []
        for tournament_id in tournament_ids:
            tournament = self.tournaments_by_id.get(tournament_id)
            if tournament is None:
                continue
            for item in families:
                rank = tournament.ranks.get(item.team_name)
                if rank is not None:
                    candidates.append(
                        TeamTournamentOutcome(
                            item.team_name,
                            tournament_id,
                            tournament.name,
                            tournament.season,
                            rank,
                        )
                    )
        own = [item for item in candidates if item.team_name == identity.team_name]
        return best_outcomes(own or candidates, identity.category)


_DATABASE_COMPETITIONS = (
    ("male_team_ids", "男足"),
    ("female_team_ids", "女足"),
    ("futsal_team_ids", "五人制"),
)


def build_team_identities(
    institutions: list[Mapping[str, Any]],
) -> dict[str, StaticTeamIdentity]:
    """Keep historical identities distinct from the institution owning their profiles."""
    if not institutions:
        raise _configuration_error("institutions")
    identities: dict[str, StaticTeamIdentity] = {}
    institution_by_id: dict[int, str] = {}
    entries = []
    for institution in institutions:
        predecessors = institution.get("predecessors", [])
        if not isinstance(predecessors, list):
            raise _configuration_error("institutions.predecessors")
        entries.append((institution, institution.get("name"), False))
        for predecessor in predecessors:
            if not isinstance(predecessor, Mapping) or "predecessors" in predecessor:
                raise _configuration_error("institutions.predecessors")
            entries.append((predecessor, institution.get("name"), True))
    for raw, owner_name, is_predecessor in entries:
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
            team_name = f"{name}{category}"
            if team_name in identities:
                raise _configuration_error(f"institutions.{team_name}")
            identities[team_name] = StaticTeamIdentity(
                team_name=team_name,
                institution_name=name,
                category=category,
                brief_name=brief_name,
                team_ids=team_ids,
                owner_name=owner_name,
                is_predecessor=is_predecessor,
            )
            for team_id in team_ids:
                owner = institution_by_id.setdefault(team_id, name)
                if owner != name:
                    raise _configuration_error(f"institutions.team_id.{team_id}")
    return identities


def build_outcome_catalog(
    institutions: list[Mapping[str, Any]],
    tournaments: list[Mapping[str, Any]],
) -> StaticOutcomeCatalog:
    """Validate database rows and build an immutable outcome catalog."""
    identities = build_team_identities(institutions)
    teams = {name: identity.team_ids for name, identity in identities.items()}
    reverse: dict[int, list[str]] = defaultdict(list)
    for name, identity in identities.items():
        for team_id in identity.team_ids:
            reverse[team_id].append(name)

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


def _rank_number(value: str) -> int:
    if value.isdigit() and int(value) > 0:
        return int(value)
    digits = {
        character: index for index, character in enumerate("零一二三四五六七八九")
    }
    if len(value) == 1 and value in digits and digits[value] > 0:
        return digits[value]
    if value.count("十") == 1:
        tens, ones = value.split("十")
        if (not tens or tens in digits) and (not ones or ones in digits):
            return digits.get(tens, 1) * 10 + digits.get(ones, 0)
    raise _configuration_error(f"rank_number.{value}")


def _rank_value(rank: str) -> tuple[str, int]:
    places = {"冠军": 1, "亚军": 2, "季军": 3}
    if rank in places:
        return "finish", places[rank]
    match = re.fullmatch(r"第(\d+|[一二三四五六七八九十]+)名", rank)
    if match:
        return "finish", _rank_number(match[1])
    match = re.fullmatch(r"(\d+|[一二三四五六七八九十]+)强", rank)
    if match:
        return "finish", _rank_number(match[1])
    match = re.fullmatch(r"小组第(\d+|[一二三四五六七八九十]+)", rank)
    if match:
        return "group", _rank_number(match[1])
    if rank in ("升级", "保级", "降级"):
        return "status", ("升级", "保级", "降级").index(rank)
    raise _configuration_error(f"rank.{rank}")


def _compare_outcomes(
    a: TeamTournamentOutcome, b: TeamTournamentOutcome, category: str
) -> int:
    if category == "男足":

        def tier(item: TeamTournamentOutcome) -> int:
            for index, label in enumerate(("甲级", "乙级", "丙级")):
                if label in item.tournament_name:
                    return index
            raise _configuration_error(f"tournaments.{item.tournament_id}.tier")

        difference = tier(a) - tier(b)
        if difference:
            return difference
    kind_a, value_a = _rank_value(a.rank)
    kind_b, value_b = _rank_value(b.rank)
    if kind_a == kind_b:
        return value_a - value_b
    if {kind_a, kind_b} == {"finish", "group"}:
        return -1 if kind_a == "finish" else 1
    raise _configuration_error(f"incomparable_ranks.{a.rank}.{b.rank}")


def best_outcomes(
    candidates: list[TeamTournamentOutcome], category: str
) -> tuple[TeamTournamentOutcome, ...]:
    if not candidates:
        return ()
    best = [candidates[0]]
    for candidate in candidates[1:]:
        comparison = _compare_outcomes(candidate, best[0], category)
        if comparison < 0:
            best = [candidate]
        elif comparison == 0:
            best.append(candidate)
    return tuple(best)


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
