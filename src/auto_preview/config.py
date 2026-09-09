"""Versioned tournament scope owned by auto_preview."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .models import Competition


@dataclass(frozen=True, slots=True)
class HistoricalSeason:
    label: str
    tournament_ids: tuple[int, ...]
    outcomes_available: bool


@dataclass(frozen=True, slots=True)
class CompetitionConfig:
    competition: Competition
    full_name: str
    short_name: str
    current_tournament_ids: tuple[int, ...]
    current_tournament_names: Mapping[int, str]
    historical_seasons: tuple[HistoricalSeason, ...]

    @property
    def historical_tournament_ids(self) -> tuple[int, ...]:
        return tuple(
            tournament_id
            for season in self.historical_seasons
            for tournament_id in season.tournament_ids
        )

    @property
    def outcome_tournament_ids(self) -> tuple[int, ...]:
        return tuple(
            tournament_id
            for season in self.historical_seasons
            if season.outcomes_available
            for tournament_id in season.tournament_ids
        )


COMPETITIONS = MappingProxyType(
    {
        Competition.MALE: CompetitionConfig(
            competition=Competition.MALE,
            full_name="马约翰杯男子足球赛",
            short_name="马杯男足",
            current_tournament_ids=(122, 124, 126),
            current_tournament_names=MappingProxyType(
                {
                    122: "男足甲级",
                    124: "男足乙级",
                    126: "男足丙级",
                }
            ),
            historical_seasons=(
                HistoricalSeason("2024~2025", (99, 100, 101), True),
                HistoricalSeason("2023~2024", (89, 88), True),
                HistoricalSeason("2022~2023", (72, 73), False),
            ),
        ),
        Competition.FEMALE: CompetitionConfig(
            competition=Competition.FEMALE,
            full_name="马约翰杯女子足球赛",
            short_name="马杯女足",
            current_tournament_ids=(123,),
            current_tournament_names=MappingProxyType({123: "女足"}),
            historical_seasons=(
                HistoricalSeason("2024~2025", (102,), True),
                HistoricalSeason("2023~2024", (90,), True),
                HistoricalSeason("2022~2023", (74,), False),
            ),
        ),
        Competition.FUTSAL: CompetitionConfig(
            competition=Competition.FUTSAL,
            full_name="马约翰杯五人制足球赛",
            short_name="马杯五人制",
            current_tournament_ids=(128,),
            current_tournament_names=MappingProxyType({128: "五人制"}),
            historical_seasons=(
                HistoricalSeason("2024~2025", (111,), True),
                HistoricalSeason("2023~2024", (93,), True),
            ),
        ),
    }
)


def competition_config(competition: Competition) -> CompetitionConfig:
    return COMPETITIONS[competition]
