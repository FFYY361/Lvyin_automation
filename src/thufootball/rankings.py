"""Validated final-outcome catalog for supported THUFootball tournaments."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
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
    tournament_teams: Mapping[int, int]
    owner_name: str
    is_predecessor: bool = False

    @property
    def tournament_team_ids(self) -> tuple[int, ...]:
        return tuple(self.tournament_teams.values())


@dataclass(frozen=True)
class StaticOutcomeCatalog:
    tournament_ids: tuple[int, ...]
    teams_by_name: Mapping[str, StaticTeamIdentity]
    tournament_team_ids_by_name: Mapping[str, tuple[int, ...]]
    team_names_by_tournament_team_id: Mapping[int, str]
    registration_tournament_ids: Mapping[int, int]
    tournaments_by_id: Mapping[int, StaticTournamentRanking]

    def resolve_identity(
        self, tournament_team_id: int, category: str | None = None
    ) -> StaticTeamIdentity:
        name = self.team_names_by_tournament_team_id.get(tournament_team_id)
        identity = self.teams_by_name.get(name)
        if identity is None or (category is not None and identity.category != category):
            raise _configuration_error(
                f"tournament_team_id.{tournament_team_id}.{category}"
            )
        return identity

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

    def history_tournament_team_ids(
        self, identity: StaticTeamIdentity, tournament_ids: Sequence[int] | None = None
    ) -> tuple[int, ...]:
        return tuple(
            registration
            for item in self.history_identities(identity)
            for tournament_id, registration in item.tournament_teams.items()
            if tournament_ids is None or tournament_id in tournament_ids
        )

    def institution_identities(
        self, name: str, competition: str | None = None
    ) -> tuple[str, ...]:
        category = dict(_DATABASE_COMPETITIONS).get(competition)
        if competition is not None and category is None:
            raise _configuration_error(f"competition.{competition}")
        matches = [
            item
            for item in self.teams_by_name.values()
            if item.institution_name == name
            and (category is None or item.category == category)
        ]
        if not matches:
            raise _configuration_error(f"institution.{name}")
        return tuple(
            dict.fromkeys(
                candidate.team_name
                for item in matches
                for candidate in self.history_identities(item)
            )
        )

    def display_history_tournament_team_ids(
        self,
        identity: StaticTeamIdentity,
        seasons: Sequence[Sequence[int]],
    ) -> tuple[int, ...]:
        """Select display history newest first, stopping at a multi-predecessor season.

        Each season must include all tiers of the same competition. Count registered
        predecessors even when their final rankings are missing.
        """
        history = self.history_identities(identity)
        registrations = []
        for tournament_ids in seasons:
            predecessors = [
                item
                for item in history
                if item.is_predecessor
                and any(tid in item.tournament_teams for tid in tournament_ids)
            ]
            if len(predecessors) > 1:
                break
            participants = (
                (identity,)
                if any(tid in identity.tournament_teams for tid in tournament_ids)
                else predecessors
            )
            registrations.extend(
                item.tournament_teams[tid]
                for item in participants
                for tid in tournament_ids
                if tid in item.tournament_teams
            )
        return tuple(registrations)

    def season_outcomes(
        self, identity: StaticTeamIdentity, tournament_ids: tuple[int, ...]
    ) -> tuple[TeamTournamentOutcome, ...]:
        """Return factual rankings for all related identities without selecting a best."""
        candidates = []
        for tournament_id in tournament_ids:
            tournament = self.tournaments_by_id.get(tournament_id)
            if tournament is None:
                continue
            for item in self.history_identities(identity):
                registration = item.tournament_teams.get(tournament_id)
                rank = tournament.ranks.get(str(registration))
                if rank is not None:
                    candidates.append(
                        TeamTournamentOutcome(
                            item.team_name,
                            tournament_id,
                            tournament.name,
                            tournament.season,
                            rank,
                            tournament_team_id=registration,
                        )
                    )
        return tuple(candidates)


_DATABASE_COMPETITIONS = (("male", "男足"), ("female", "女足"), ("futsal", "五人制"))


def build_team_identities(
    institutions: list[Mapping[str, Any]],
) -> dict[str, StaticTeamIdentity]:
    if not institutions:
        raise _configuration_error("institutions")
    identities = {}
    registration_owners = {}
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
        name, brief_name = raw.get("name"), raw.get("short_name")
        if any(
            not isinstance(value, str) or not value.strip() or value != value.strip()
            for value in (name, brief_name)
        ):
            raise _configuration_error("institutions")
        projects = raw.get("tournament_teams")
        if not isinstance(projects, Mapping) or set(projects) != {
            "male",
            "female",
            "futsal",
        }:
            raise _configuration_error(f"institutions.{name}.tournament_teams")
        for competition, category in _DATABASE_COMPETITIONS:
            mapping = projects[competition]
            if not isinstance(mapping, Mapping):
                raise _configuration_error(f"institutions.{name}.{competition}")
            registrations = {}
            team_name = name + category
            if team_name in identities:
                raise _configuration_error(f"institutions.{team_name}")
            for tournament_key, registration in mapping.items():
                if (
                    not isinstance(tournament_key, str)
                    or not tournament_key.isdecimal()
                    or str(int(tournament_key)) != tournament_key
                ):
                    raise _configuration_error(f"institutions.{name}.tournament_id")
                tournament_id = _positive_int(int(tournament_key), "tournament_id")
                registration = _positive_int(registration, "tournament_team_id")
                if registration in registration_owners:
                    raise _configuration_error(
                        f"institutions.tournament_team_id.{registration}"
                    )
                registration_owners[registration] = team_name
                registrations[tournament_id] = registration
            identities[team_name] = StaticTeamIdentity(
                team_name,
                name,
                category,
                brief_name,
                MappingProxyType(registrations),
                owner_name,
                is_predecessor,
            )
    return identities


def build_outcome_catalog(
    institutions: list[Mapping[str, Any]],
    tournaments: list[Mapping[str, Any]],
    *,
    registration_tournaments: list[Mapping[str, Any]] | None = None,
) -> StaticOutcomeCatalog:
    identities = build_team_identities(institutions)
    reverse = {
        registration: name
        for name, item in identities.items()
        for registration in item.tournament_team_ids
    }
    registration_tournament_ids = {
        registration: tournament_id
        for item in identities.values()
        for tournament_id, registration in item.tournament_teams.items()
    }
    if registration_tournaments is not None:
        documents = {item["id"]: item for item in registration_tournaments}
        competitions = {
            category: competition for competition, category in _DATABASE_COMPETITIONS
        }
        for item in identities.values():
            for tournament_id, registration in item.tournament_teams.items():
                document = documents.get(tournament_id)
                if (
                    document is None
                    or document.get("competition") != competitions[item.category]
                ):
                    raise _configuration_error(
                        f"registration.{registration}.competition"
                    )
                matches = [
                    team
                    for team in document.get("data", {}).get("registered_teams", [])
                    if team.get("tournament_team_id") == registration
                    and team.get("tournament_id") == tournament_id
                ]
                if len(matches) != 1:
                    raise _configuration_error(
                        f"registration.{registration}.tournament_id"
                    )
    tournament_ids = []
    rankings = {}
    for raw in tournaments:
        tournament_id = _positive_int(raw.get("id"), "tournaments.id")
        name, ranks = raw.get("name"), raw.get("final_rankings")
        if (
            tournament_id in rankings
            or not isinstance(name, str)
            or not name.strip()
            or not isinstance(ranks, dict)
            or not ranks
        ):
            raise _configuration_error(f"tournaments.{tournament_id}")
        season = _SEASON_PATTERN.search(name)
        if season is None:
            raise _configuration_error(f"tournaments.{tournament_id}.name")
        for key, rank in ranks.items():
            if (
                not isinstance(key, str)
                or not key.isdecimal()
                or registration_tournament_ids.get(int(key)) != tournament_id
                or not isinstance(rank, str)
                or not rank.strip()
            ):
                raise _configuration_error(
                    f"tournaments.{tournament_id}.final_rankings"
                )
        tournament_ids.append(tournament_id)
        rankings[tournament_id] = StaticTournamentRanking(
            tournament_id, name, season.group(1), MappingProxyType(dict(ranks))
        )
    return StaticOutcomeCatalog(
        tuple(tournament_ids),
        MappingProxyType(identities),
        MappingProxyType(
            {name: item.tournament_team_ids for name, item in identities.items()}
        ),
        MappingProxyType(reverse),
        MappingProxyType(registration_tournament_ids),
        MappingProxyType(rankings),
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
