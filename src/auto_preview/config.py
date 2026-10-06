"""Versioned tournament scope owned by auto_preview."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from thufootball.errors import ConfigurationError

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
    season: str = "2026-2027"

    def require_current_tournaments(self) -> None:
        if not self.current_tournament_ids:
            raise ConfigurationError(
                f"{self.short_name}尚未接入 {self.season} 赛季",
                stage="configuration",
            )

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
            current_tournament_ids=(139, 140, 141),
            current_tournament_names=MappingProxyType(
                {
                    139: "男足甲级",
                    140: "男足乙级",
                    141: "男足丙级",
                }
            ),
            historical_seasons=(
                HistoricalSeason("2025~2026", (122, 124, 126), True),
                HistoricalSeason("2024~2025", (99, 100, 101), True),
                HistoricalSeason("2023~2024", (89, 88), True),
            ),
        ),
        Competition.FEMALE: CompetitionConfig(
            competition=Competition.FEMALE,
            full_name="马约翰杯女子足球赛",
            short_name="马杯女足",
            current_tournament_ids=(142,),
            current_tournament_names=MappingProxyType({142: "女足"}),
            historical_seasons=(
                HistoricalSeason("2025~2026", (123,), True),
                HistoricalSeason("2024~2025", (102,), True),
                HistoricalSeason("2023~2024", (90,), True),
            ),
        ),
        Competition.FUTSAL: CompetitionConfig(
            competition=Competition.FUTSAL,
            full_name="马约翰杯五人制足球赛",
            short_name="马杯五人制",
            current_tournament_ids=(),
            current_tournament_names=MappingProxyType({}),
            historical_seasons=(
                HistoricalSeason("2025~2026", (128,), True),
                HistoricalSeason("2024~2025", (111,), True),
                HistoricalSeason("2023~2024", (93,), True),
            ),
        ),
    }
)


def competition_config(competition: Competition) -> CompetitionConfig:
    return COMPETITIONS[competition]
