"""Explicit 2025–2026 scopes for existing pipeline scenario fixtures."""

from dataclasses import replace

import pytest

import auto_preview.config as config
from auto_preview.models import Competition

CURRENT_SCOPE = config.COMPETITIONS


@pytest.fixture(autouse=True)
def legacy_scope(monkeypatch):
    previous = {
        Competition.MALE: (
            (122, 124, 126),
            {122: "男足甲级", 124: "男足乙级", 126: "男足丙级"},
            (72, 73),
        ),
        Competition.FEMALE: ((123,), {123: "女足"}, (74,)),
        Competition.FUTSAL: ((128,), {128: "五人制"}, ()),
    }
    monkeypatch.setattr(
        config,
        "COMPETITIONS",
        {
            kind: replace(
                scope,
                current_tournament_ids=previous[kind][0],
                current_tournament_names=previous[kind][1],
                historical_seasons=scope.historical_seasons[1:]
                + (
                    (config.HistoricalSeason("2022~2023", previous[kind][2], False),)
                    if previous[kind][2]
                    else ()
                ),
                season="2025-2026",
            )
            for kind, scope in CURRENT_SCOPE.items()
        },
    )
