"""Helpers for cloning canonical football rows into an isolated test schema."""

from __future__ import annotations

from copy import deepcopy

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from thufootball.database import GameRecord, InstitutionRecord, TournamentRecord


def clone_football_data(source_engine: Engine, target_engine: Engine) -> None:
    """Copy runtime football data without depending on removed archive files."""

    with Session(source_engine) as source:
        institutions = [
            {
                "name": row.name,
                "short_name": row.short_name,
                "male_team_ids": list(row.male_team_ids),
                "female_team_ids": list(row.female_team_ids),
                "futsal_team_ids": list(row.futsal_team_ids),
                "male_description": row.male_description,
                "female_description": row.female_description,
                "futsal_description": row.futsal_description,
                "player_descriptions": deepcopy(row.player_descriptions),
                "predecessors": deepcopy(row.predecessors),
            }
            for row in source.scalars(select(InstitutionRecord))
        ]
        tournaments = [
            {
                "id": row.id,
                "name": row.name,
                "competition": row.competition,
                "final_rankings": deepcopy(row.final_rankings),
                "data": deepcopy(row.data),
                "is_finalized": row.is_finalized,
            }
            for row in source.scalars(select(TournamentRecord))
        ]
        games = [
            {
                "id": row.id,
                "tournament_id": row.tournament_id,
                "data": deepcopy(row.data),
            }
            for row in source.scalars(select(GameRecord))
        ]
    if not institutions or not tournaments or not games:
        raise RuntimeError("canonical football data is required for integration tests")
    with Session(target_engine) as target, target.begin():
        target.add_all(InstitutionRecord(**row) for row in institutions)
        target.add_all(TournamentRecord(**row) for row in tournaments)
        target.flush()
        target.add_all(GameRecord(**row) for row in games)
