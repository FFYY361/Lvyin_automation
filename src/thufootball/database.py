"""PostgreSQL storage for canonical football reference data."""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    Text,
    create_engine,
    select,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from .config import load_env_file
from .errors import ConfigurationError
from .rankings import StaticOutcomeCatalog, build_outcome_catalog


class FootballDataBase(DeclarativeBase):
    """Declarative base kept separate from the website workflow schema."""


class InstitutionRecord(FootballDataBase):
    __tablename__ = "institutions"

    name: Mapped[str] = mapped_column(Text, primary_key=True)
    short_name: Mapped[str] = mapped_column(Text, nullable=False)
    male_team_ids: Mapped[list[int]] = mapped_column(
        ARRAY(BigInteger), nullable=False, default=list, server_default="{}"
    )
    female_team_ids: Mapped[list[int]] = mapped_column(
        ARRAY(BigInteger), nullable=False, default=list, server_default="{}"
    )
    futsal_team_ids: Mapped[list[int]] = mapped_column(
        ARRAY(BigInteger), nullable=False, default=list, server_default="{}"
    )
    male_description: Mapped[str] = mapped_column(Text, nullable=False)
    female_description: Mapped[str] = mapped_column(Text, nullable=False)
    futsal_description: Mapped[str] = mapped_column(Text, nullable=False)
    player_descriptions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class TournamentRecord(FootballDataBase):
    __tablename__ = "tournaments"
    __table_args__ = (
        CheckConstraint(
            "competition IN ('male', 'female', 'futsal')",
            name="ck_tournaments_competition",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    competition: Mapped[str] = mapped_column(String(16), nullable=False)
    final_rankings: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    is_finalized: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )


class GameRecord(FootballDataBase):
    __tablename__ = "games"
    __table_args__ = (Index("ix_games_tournament_id", "tournament_id"),)

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False
    )
    tournament_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("tournaments.id", ondelete="RESTRICT"),
        nullable=False,
    )
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


def get_football_database_url() -> str:
    load_env_file()
    value = os.environ.get("WEBSITE_DATABASE_URL", "").strip()
    if not value:
        raise ConfigurationError(
            "WEBSITE_DATABASE_URL is required for football data",
            stage="configuration",
        )
    return value


def create_football_engine(database_url: str | None = None) -> Engine:
    return create_engine(
        database_url or get_football_database_url(),
        pool_pre_ping=True,
        connect_args={"connect_timeout": 10},
    )


def create_football_session_factory(
    engine: Engine,
) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class FootballDataRepository:
    """Read canonical football data through an existing SQLAlchemy session."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_institution(self, name: str) -> InstitutionRecord:
        record = self.session.get(InstitutionRecord, name)
        if record is None:
            raise ConfigurationError(
                f"institution data is missing for {name!r}",
                stage="configuration",
            )
        return record

    def find_institution(
        self, team_id: int, competition: str
    ) -> InstitutionRecord:
        field = {
            "male": "male_team_ids",
            "female": "female_team_ids",
            "futsal": "futsal_team_ids",
        }.get(competition)
        if field is None:
            raise ConfigurationError(
                f"unsupported competition {competition!r}",
                stage="configuration",
            )
        matches = [
            record
            for record in self.list_institutions()
            if team_id in getattr(record, field)
        ]
        if len(matches) != 1:
            raise ConfigurationError(
                f"team_id={team_id} does not resolve to one {competition} institution",
                stage="configuration",
            )
        return matches[0]

    def list_institutions(self) -> list[InstitutionRecord]:
        return list(
            self.session.scalars(
                select(InstitutionRecord).order_by(InstitutionRecord.name)
            )
        )

    def get_tournament(self, tournament_id: int) -> TournamentRecord:
        record = self.session.get(TournamentRecord, tournament_id)
        if record is None:
            raise ConfigurationError(
                f"tournament data is missing for id={tournament_id}",
                stage="configuration",
            )
        return record

    def list_tournaments(self) -> list[TournamentRecord]:
        records = list(self.session.scalars(select(TournamentRecord)))
        return sorted(records, key=lambda item: (_season(item), -item.id), reverse=True)

    def get_game(self, game_id: int) -> GameRecord:
        record = self.session.get(GameRecord, game_id)
        if record is None:
            raise ConfigurationError(
                f"game data is missing for id={game_id}", stage="configuration"
            )
        return record

    def list_games(
        self, tournament_ids: Sequence[int] | None = None
    ) -> list[GameRecord]:
        statement = select(GameRecord)
        if tournament_ids is not None:
            statement = statement.where(GameRecord.tournament_id.in_(tournament_ids))
        return list(self.session.scalars(statement.order_by(GameRecord.id)))

    def load_outcome_catalog(self) -> StaticOutcomeCatalog:
        institutions = [
            {
                "name": record.name,
                "short_name": record.short_name,
                "male_team_ids": record.male_team_ids,
                "female_team_ids": record.female_team_ids,
                "futsal_team_ids": record.futsal_team_ids,
            }
            for record in self.list_institutions()
        ]
        tournaments = [
            {
                "id": record.id,
                "name": record.name,
                "final_rankings": record.final_rankings,
                "data": record.data,
            }
            for record in self.list_tournaments()
        ]
        return build_outcome_catalog(institutions, tournaments)


def _season(record: TournamentRecord) -> str:
    tournament = record.data.get("tournament")
    if isinstance(tournament, dict):
        season = tournament.get("season")
        if isinstance(season, str):
            return season.replace("-", "~")
    return ""
